import asyncio
import os
import json
import re
from google import genai
from tenacity import retry, stop_after_attempt, wait_random_exponential
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
EMAIL_BODY = os.environ.get("EMAIL_INPUT", "No email provided")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

client = genai.Client(api_key=GEMINI_KEY)

@retry(wait=wait_random_exponential(min=5, max=60), stop=stop_after_attempt(7))
async def get_hotel_list_from_ai(text):
    """Extracts hotels with exponential backoff for rate limits."""
    prompt = f"Extract hotel name and official URL. Return ONLY a JSON list: [{{'name': '...', 'url': '...'}}]. Text: {text}"
    response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
    await asyncio.sleep(5) # Mandatory cooldown after API call
    return json.loads(response.text.strip().replace('```json', '').replace('```', ''))

async def ask_gemini_for_gds(hotel_name):
    """Highly confident GDS search with built-in delay."""
    prompt = (
        f"Search your knowledge for the GDS Chain Code (2-letter) and Property IDs "
        f"for: '{hotel_name}'. Return ONLY a JSON object: "
        "{'found': true, 'chain': '...', 'sabre': '...', 'amadeus': '...', 'apollo': '...', 'worldspan': '...'}. "
        "Only set 'found' to false if the property absolutely cannot be identified."
    )
    try:
        response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        await asyncio.sleep(5) # Prevent 429 Resource Exhausted
        return json.loads(response.text.strip().replace('```json', '').replace('```', ''))
    except:
        return {"found": False}

async def handle_selection_page(page, target_name):
    """Targets the 'Hotels' list link seen in your screenshot."""
    print(f"📋 Selection page detected. Forcing click on property link...")
    try:
        # Targets the link under the 'Hotels' header specifically (image_a52f1d.png)
        hotel_link = page.locator("h3:has-text('Hotels') + ul li a").first
        if await hotel_link.count() > 0:
            await hotel_link.click(force=True)
            await page.wait_for_load_state("networkidle")
            return True
    except: pass
    return False

async def conduct_web_research(hotel_name, official_url):
    """Fail-safe browser search with aggressive overlay removal."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/121.0.0.0 Safari/537.36")
        page = await context.new_page()
        
        try:
            # Search Travel Weekly
            await page.goto("https://www.travelweekly.com/Hotels", wait_until="domcontentloaded")
            
            # Remove the 'onetrust-pc-dark-filter' that caused your previous timeout (image_b02403.png)
            await page.evaluate("""() => {
                document.querySelectorAll('.onetrust-pc-dark-filter, #onetrust-consent-sdk').forEach(el => el.remove());
            }""")
            
            search_box = page.locator("input[placeholder*='name or destination']").first
            await search_box.fill(hotel_name)
            await page.locator("button:has-text('Search Hotels')").first.click(force=True)
            await page.wait_for_timeout(5000)

            # Handle result list (image_a52f1d.png)
            if "Selection" in await page.title() or await page.get_by_text("Hotel Search Selection").is_visible():
                await handle_selection_page(page, hotel_name)

            # Click Details for final table
            details = page.get_by_text("View Hotel Details").first
            if await details.is_visible():
                await details.click(force=True)
                await page.wait_for_load_state("networkidle")

            await page.screenshot(path=f"screenshots/{hotel_name.replace(' ', '_')}_GDS_Table.png", full_page=True)
            return True
        finally:
            await browser.close()

async def main():
    os.makedirs("screenshots", exist_ok=True)
    try:
        hotels = await get_hotel_list_from_ai(EMAIL_BODY)
        for hotel in hotels:
            name = hotel['name']
            print(f"\n--- Processing: {name} ---")
            
            gds_data = await ask_gemini_for_gds(name)
            
            if gds_data.get('found'):
                chain = gds_data['chain']
                report = f"PROPERTY: {name}\nCHAIN: {chain}\nSABRE: {chain}{gds_data['sabre']}\nAMADEUS: {chain}{gds_data['amadeus']}"
                with open(f"screenshots/{name.replace(' ', '_')}_GDS.txt", "w") as f:
                    f.write(report)
                print(f"✨ AI Found Data for {name}")
            else:
                await conduct_web_research(name, hotel['url'])
            
            await asyncio.sleep(10) # Heavy cooldown to prevent 429 errors
    except Exception as e:
        print(f"🛑 Critical Failure: {e}")

if __name__ == "__main__":
    asyncio.run(main())
