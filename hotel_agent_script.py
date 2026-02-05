import asyncio
import os
import json
from google import genai
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
EMAIL_BODY = os.environ.get("EMAIL_INPUT", "The Reeds at Shelter Haven")
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

async def ask_gemini_for_gds(hotel_name):
    """Commands Gemini for real codes with rate-limit protection."""
    await asyncio.sleep(5) # Cooldown after starting
    prompt = (
        f"Provide the ACTUAL GDS Chain Code (2-letter) and Property IDs for: '{hotel_name}'. "
        "No placeholders. Return ONLY JSON: {'found': true, 'chain': '...', 'sabre': '...', 'amadeus': '...', 'apollo': '...', 'worldspan': '...'}"
    )
    try:
        response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        # Scrub markdown code blocks if present
        clean_json = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(clean_json)
    except Exception as e:
        print(f"⚠️ AI Step failed/skipped (Rate Limit): {e}")
        return {"found": False}

async def conduct_web_research(hotel_name):
    """Bypasses cookie banners and forces the search results click."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            print(f"🔎 AI data unavailable. Searching Travel Weekly for: {hotel_name}")
            await page.goto("https://www.travelweekly.com/Hotels", wait_until="domcontentloaded")
            
            # --- NUCLEAR OVERLAY REMOVAL ---
            # Physically deletes the OneTrust filter that causes the 30s timeout
            await page.evaluate("""() => {
                document.querySelectorAll('.onetrust-pc-dark-filter, #onetrust-consent-sdk').forEach(el => el.remove());
                document.body.style.overflow = 'visible';
            }""")
            
            # Type and Search
            search_box = page.locator("input[placeholder*='name or destination']").first
            await search_box.fill(hotel_name)
            await page.locator("button:has-text('Search Hotels')").first.click(force=True)
            await page.wait_for_timeout(5000)

            # --- SELECT THE CORRECT PROPERTY ---
            # Specifically targets the list under the 'Hotels' header in your screenshot
            if await page.get_by_text("Hotel Search Selection").is_visible():
                print("📋 Selection page detected. Forcing click on property link...")
                await page.locator("h3:has-text('Hotels') + ul li a").first.click(force=True)
                await page.wait_for_timeout(3000)

            # Final 'View Details' to reach GDS table
            details = page.get_by_text("View Hotel Details").first
            if await details.is_visible():
                await details.click(force=True)
                await page.wait_for_load_state("networkidle")

            # Capture the actual GDS Table
            filename = f"screenshots/{hotel_name.replace(' ', '_')}_Final_Table.png"
            await page.screenshot(path=filename, full_page=True)
            print(f"✅ GDS captured at {filename}")
        finally:
            await browser.close()

async def main():
    os.makedirs("screenshots", exist_ok=True)
    # Step 1: Try AI first
    data = await ask_gemini_for_gds(EMAIL_BODY)
    
    # Check for real data (not placeholders)
    if data.get('found') and data.get('sabre') not in ["123", "placeholder"]:
        c = data['chain']
        report = f"PROPERTY: {EMAIL_BODY}\nCHAIN: {c}\nSABRE: {c}{data['sabre']}\nAMADEUS: {c}{data['amadeus']}"
        with open(f"screenshots/{EMAIL_BODY.replace(' ', '_')}_GDS.txt", "w") as f:
            f.write(report)
        print(f"✨ AI SUCCESS: Found {c} codes.")
    else:
        # Step 2: Full web search backup
        await conduct_web_research(EMAIL_BODY)
    
    # Final cooldown for rate limits
    await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
