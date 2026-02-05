import asyncio
import os
import json
from google import genai
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
EMAIL_BODY = os.environ.get("EMAIL_INPUT", "The Reeds at Shelter Haven")
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

async def ask_gemini_for_gds(hotel_name):
    """Commands Gemini for REAL codes (no placeholders)."""
    await asyncio.sleep(5) # Rate limit protection
    prompt = (
        f"Identify the ACTUAL GDS Chain Code and Property IDs for: '{hotel_name}'. "
        "Do not use placeholders like '123'. Search for real values. "
        "Return ONLY a JSON object: {'found': true, 'chain': '...', 'sabre': '...', 'amadeus': '...', 'apollo': '...', 'worldspan': '...'}"
    )
    try:
        response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        # Clean any markdown formatting from the AI
        clean_json = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(clean_json)
    except:
        return {"found": False}

async def conduct_web_research(hotel_name):
    """The 'Nuclear' Browser Option: Bypasses overlays and clicks the property list."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            print(f"🔎 Navigating to Travel Weekly for: {hotel_name}")
            await page.goto("https://www.travelweekly.com/Hotels", wait_until="domcontentloaded")
            
            # --- NUCLEAR OVERLAY REMOVAL ---
            # This deletes the OneTrust banner that causes your 30s timeouts
            await page.evaluate("""() => {
                const blockers = document.querySelectorAll('.onetrust-pc-dark-filter, #onetrust-consent-sdk, .optanon-alert-box-wrapper');
                blockers.forEach(el => el.remove());
                document.body.style.overflow = 'visible';
            }""")
            
            # Perform Search
            search_input = page.locator("input[placeholder*='name or destination']").first
            await search_input.fill(hotel_name)
            await page.locator("button:has-text('Search Hotels')").first.click(force=True)
            
            # Wait for results to populate
            await page.wait_for_timeout(5000)

            # --- THE SELECTION PAGE FIX ---
            # This clicks the property link under the 'Hotels' header (image_a52f1d.png)
            if await page.get_by_text("Hotel Search Selection").is_visible():
                print("📋 Selection page detected. Forcing click on first hotel result...")
                await page.locator("h3:has-text('Hotels') + ul li a").first.click(force=True)
                await page.wait_for_timeout(3000)

            # Click Details for the final GDS Table
            details = page.get_by_text("View Hotel Details").first
            if await details.is_visible():
                await details.click(force=True)
                await page.wait_for_load_state("networkidle")

            # Final Proof Screenshot
            path = f"screenshots/{hotel_name.replace(' ', '_')}_Final_GDS.png"
            await page.screenshot(path=path, full_page=True)
            print(f"✅ GDS captured successfully at {path}")
            
        finally:
            await browser.close()

async def main():
    os.makedirs("screenshots", exist_ok=True)
    name = EMAIL_BODY
    
    # Step 1: Try AI
    data = await ask_gemini_for_gds(name)
    
    # Step 2: Validate if AI gave real data (not '123')
    if data.get('found') and data.get('sabre') != "123":
        c = data['chain']
        report = f"PROPERTY: {name}\nCHAIN: {c}\nSABRE: {c}{data['sabre']}\nAMADEUS: {c}{data['amadeus']}"
        with open(f"screenshots/{name.replace(' ', '_')}_GDS.txt", "w") as f:
            f.write(report)
        print(f"✨ SUCCESS: AI identified the {c} codes.")
    else:
        # Step 3: Run the updated web search if AI failed or gave fake data
        await conduct_web_research(name)

if __name__ == "__main__":
    asyncio.run(main())
