import asyncio
import os
import json
from google import genai
from tenacity import retry, stop_after_attempt, wait_random_exponential
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
EMAIL_BODY = os.environ.get("EMAIL_INPUT", "No email provided")
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

async def ask_gemini_for_gds(hotel_name):
    """Commands Gemini to search and provide the codes directly."""
    # We add a delay here to avoid the 429 Resource Exhausted error
    await asyncio.sleep(2) 
    prompt = (
        f"Provide the GDS Chain Code (2-letter) and Property IDs for: '{hotel_name}'. "
        "Return ONLY a JSON object: {'found': true, 'chain': '...', 'sabre': '...', 'amadeus': '...', 'apollo': '...', 'worldspan': '...'}. "
        "Only set 'found' to false if the property absolutely cannot be identified."
    )
    try:
        response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        return json.loads(response.text.strip().replace('```json', '').replace('```', ''))
    except Exception as e:
        print(f"⚠️ AI Step skipped (Rate Limit/Error): {e}")
        return {"found": False}

async def handle_selection_page(page, target_name):
    """Specifically targets the list link seen in your results screenshot."""
    print(f"📋 Selection page detected. Forcing click on property link...")
    try:
        # This targets the <a> tag inside the first <li> under the 'Hotels' <h3>
        hotel_link = page.locator("h3:has-text('Hotels') + ul li a").first
        await hotel_link.scroll_into_view_if_needed()
        # Force click is mandatory due to remaining hidden overlays
        await hotel_link.click(force=True)
        print(f"✅ Property clicked successfully.")
        return True
    except: return False

async def conduct_web_research(hotel_name):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            print(f"🔎 Searching Travel Weekly for: {hotel_name}")
            await page.goto("https://www.travelweekly.com/Hotels", wait_until="domcontentloaded")
            
            # Delete the dark filter overlay that blocks clicks
            await page.evaluate("document.querySelectorAll('.onetrust-pc-dark-filter, #onetrust-consent-sdk').forEach(el => el.remove())")
            
            await page.locator("input[placeholder*='name or destination']").first.fill(hotel_name)
            await page.locator("button:has-text('Search Hotels')").first.click(force=True)
            await page.wait_for_timeout(5000)

            # HANDLE THE SELECTION PAGE
            if await page.get_by_text("Hotel Search Selection").is_visible():
                await handle_selection_page(page, hotel_name)
                await page.wait_for_timeout(3000)

            # Click Details for the final table
            details = page.get_by_text("View Hotel Details").first
            if await details.is_visible():
                await details.click(force=True)
                await page.wait_for_load_state("networkidle")

            await page.screenshot(path=f"screenshots/{hotel_name}_Final_GDS.png", full_page=True)
        finally:
            await browser.close()

async def main():
    os.makedirs("screenshots", exist_ok=True)
    # Extract hotel names (Simplified for this example)
    hotel_list = [{"name": "The Reeds at Shelter Haven"}] 
    
    for hotel in hotel_list:
        name = hotel['name']
        gds_data = await ask_gemini_for_gds(name)
        
        if gds_data.get('found'):
            chain = gds_data['chain']
            report = f"PROPERTY: {name}\nCHAIN: {chain}\nSABRE: {chain}{gds_data['sabre']}\nAMADEUS: {chain}{gds_data['amadeus']}"
            with open(f"screenshots/{name}_GDS.txt", "w") as f: f.write(report)
            print(f"✨ AI Found Data for {name}")
        else:
            await conduct_web_research(name)
        
        # MANDATORY COOLDOWN between hotels to prevent 429 error
        await asyncio.sleep(10) 

if __name__ == "__main__":
    asyncio.run(main())
