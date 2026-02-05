import asyncio
import os
import json
import re
from google import genai
from tenacity import retry, stop_after_attempt, wait_random_exponential
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
EMAIL_BODY = os.environ.get("EMAIL_INPUT", "No email provided")
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

@retry(wait=wait_random_exponential(min=1, max=60), stop=stop_after_attempt(5))
async def get_hotel_list_from_ai(text):
    """Step 1: Extract the hotels from your email."""
    prompt = f"Extract hotel name and official URL. Return ONLY a JSON list: [{{'name': '...', 'url': '...'}}]. Text: {text}"
    response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
    return json.loads(response.text.strip().replace('```json', '').replace('```', ''))

async def ask_gemini_for_gds(hotel_name):
    """Step 2: Ask Gemini directly for the codes (The Fast Track)."""
    prompt = (
        f"Provide the GDS Chain Code and Property IDs (Sabre, Amadeus, Apollo/Galileo, Worldspan) "
        f"for the property: '{hotel_name}'. "
        "Return ONLY a JSON object with these keys: 'found' (boolean), 'chain', 'sabre', 'amadeus', 'apollo', 'worldspan'. "
        "If you are not 100% certain of the codes, set 'found' to false."
    )
    try:
        response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        data = json.loads(response.text.strip().replace('```json', '').replace('```', ''))
        return data
    except:
        return {"found": False}

async def nuclear_clear_blockers(page):
    """Removes the dark filter/overlays that cause timeouts."""
    try:
        await page.evaluate("""() => {
            const blockers = document.querySelectorAll('.onetrust-pc-dark-filter, #onetrust-consent-sdk, .optanon-alert-box-wrapper');
            blockers.forEach(el => el.remove());
        }""")
    except: pass

async def conduct_web_research(hotel_name, official_url):
    """Step 3: The Backup - Full Playwright search on Travel Weekly."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/121.0.0.0 Safari/537.36")
        page = await context.new_page()
        
        try:
            # 1. Official Site Screenshot (Always good for the report)
            if official_url:
                await page.goto(official_url, wait_until="networkidle", timeout=30000)
                await page.screenshot(path=f"screenshots/{hotel_name}_Official_Site.png")

            # 2. Travel Weekly Search
            print(f"🔎 AI was unsure. Searching Travel Weekly for: {hotel_name}")
            await page.goto("https://www.travelweekly.com/Hotels", wait_until="domcontentloaded")
            await nuclear_clear_blockers(page)
            
            search_box = page.locator("input[placeholder*='name or destination']").first
            await search_box.fill(hotel_name)
            await page.locator("button:has-text('Search Hotels')").first.click(force=True)
            await page.wait_for_timeout(5000)

            # Handle the 'The Reeds' Selection List issue
            if "Selection" in await page.title() or await page.get_by_text("Hotel Search Selection").is_visible():
                hotel_link = page.locator("h3:has-text('Hotels') + ul li a").first
                if await hotel_link.is_visible():
                    await hotel_link.click(force=True)
                    await page.wait_for_load_state("networkidle")

            # Click Details to get to the table
            details = page.get_by_text("View Hotel Details").first
            if await details.is_visible():
                await details.click(force=True)
                await page.wait_for_load_state("networkidle")

            await page.screenshot(path=f"screenshots/{hotel_name}_GDS_Table.png", full_page=True)
            return True
        except Exception as e:
            print(f"❌ Web research failed for {hotel_name}: {e}")
            return False
        finally:
            await browser.close()

async def main():
    os.makedirs("screenshots", exist_ok=True)
    hotels = await get_hotel_list_from_ai(EMAIL_BODY)
    
    for hotel in hotels:
        name = hotel['name']
        print(f"\n--- Processing: {name} ---")
        
        # TRY AI FIRST
        gds_data = await ask_gemini_for_gds(name)
        
        if gds_data.get('found'):
            print(f"✨ Gemini found the codes! Chain: {gds_data['chain']}")
            # Save the AI data to a text file for your records
            with open(f"screenshots/{name}_GDS_CODES_AI.txt", "w") as f:
                f.write(json.dumps(gds_data, indent=4))
        else:
            # RUN PLAYWRIGHT AS BACKUP
            await conduct_web_research(name, hotel['url'])
        
        await asyncio.sleep(2) # Prevent rate limiting

if __name__ == "__main__":
    asyncio.run(main())
