import asyncio
import os
import json
import re
from google import genai
from tenacity import retry, stop_after_attempt, wait_random_exponential
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
# Ensure these environment variables are set in your GitHub Secrets or local environment
EMAIL_BODY = os.environ.get("EMAIL_INPUT", "No email provided")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

client = genai.Client(api_key=GEMINI_KEY)

@retry(wait=wait_random_exponential(min=1, max=60), stop=stop_after_attempt(5))
async def get_hotel_list_from_ai(text):
    """Step 1: Extract hotel names and URLs from the email body."""
    prompt = f"Extract hotel name and official URL. Return ONLY a JSON list: [{{'name': '...', 'url': '...'}}]. Text: {text}"
    response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
    return json.loads(response.text.strip().replace('```json', '').replace('```', ''))

async def ask_gemini_for_gds(hotel_name):
    """Step 2: Command Gemini to provide GDS codes directly (The Fast Track)."""
    prompt = (
        f"Search your knowledge and the web for the GDS Chain Code and Property IDs "
        f"(Sabre, Amadeus, Apollo/Galileo, Worldspan) for the property: '{hotel_name}'. "
        "Return ONLY a JSON object with these exact keys: "
        "'found' (boolean), 'chain', 'sabre', 'amadeus', 'apollo', 'worldspan'. "
        "Use your search capabilities. Only set 'found' to false if the property cannot be found."
    )
    try:
        response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        data = json.loads(response.text.strip().replace('```json', '').replace('```', ''))
        return data
    except:
        return {"found": False}

async def nuclear_clear_blockers(page):
    """Removes invisible overlays (OneTrust/Cookie banners) that block clicks."""
    try:
        await page.evaluate("""() => {
            const blockers = document.querySelectorAll('.onetrust-pc-dark-filter, #onetrust-consent-sdk, .optanon-alert-box-wrapper');
            blockers.forEach(el => el.remove());
            document.body.style.overflow = 'visible';
        }""")
    except: pass

async def handle_selection_page(page, target_name):
    """Navigates the intermediate search results list (e.g., 'The Reeds' selection)."""
    print(f"📋 Selection page detected. Navigating to property details...")
    try:
        # Targets the link under the 'Hotels' header specifically
        hotel_link = page.locator("h3:has-text('Hotels') + ul li a").first
        if await hotel_link.count() > 0:
            await hotel_link.scroll_into_view_if_needed()
            await hotel_link.click(force=True)
            await page.wait_for_load_state("networkidle")
            return True
    except: pass
    return False

async def conduct_web_research(hotel_name, official_url):
    """Step 3: The Backup - Full Playwright browser search on Travel Weekly."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/121.0.0.0 Safari/537.36")
        page = await context.new_page()
        
        try:
            # 1. Official Site Snapshot
            if official_url:
                await page.goto(official_url, wait_until="networkidle", timeout=30000)
                await page.screenshot(path=f"screenshots/{hotel_name.replace(' ', '_')}_Official.png")

            # 2. Travel Weekly Search
            print(f"🔎 AI data unavailable. Searching Travel Weekly for: {hotel_name}")
            await page.goto("https://www.travelweekly.com/Hotels", wait_until="domcontentloaded")
            await nuclear_clear_blockers(page)
            
            search_box = page.locator("input[placeholder*='name or destination']").first
            await search_box.fill(hotel_name)
            await page.locator("button:has-text('Search Hotels')").first.click(force=True)
            await page.wait_for_timeout(5000)

            # 3. Handle the 'The Reeds' Selection List
            if "Selection" in await page.title() or await page.get_by_text("Hotel Search Selection").is_visible():
                await handle_selection_page(page, hotel_name)
                await page.wait_for_timeout(3000)

            # 4. Click 'View Hotel Details'
            details = page.get_by_text("View Hotel Details").first
            if await details.is_visible():
                await details.click(force=True)
                await page.wait_for_load_state("networkidle")

            # 5. Take Final Screenshot
            await page.screenshot(path=f"screenshots/{hotel_name.replace(' ', '_')}_GDS_Table.png", full_page=True)
            return True
        except Exception as e:
            print(f"❌ Web research failed for {hotel_name}: {e}")
            return False
        finally:
            await browser.close()

def save_formatted_report(name, data):
    """Saves the GDS data in a professional table format with two-letter codes."""
    chain = data.get('chain', '??')
    report = (
        f"GDS REPORT: {name}\n"
        f"{'='*40}\n"
        f"CHAIN CODE: {chain}\n"
        f"{'-'*40}\n"
        f"AMADEUS:    {chain} {data.get('amadeus', 'N/A')}\n"
        f"SABRE:      {chain} {data.get('sabre', 'N/A')}\n"
        f"GALILEO:    {chain} {data.get('apollo', 'N/A')}\n"
        f"WORLDSPAN:  {chain} {data.get('worldspan', 'N/A')}\n"
        f"{'='*40}\n"
        f"Host Entry Format: {chain}{data.get('sabre', '')}\n"
    )
    filename = f"screenshots/{name.replace(' ', '_')}_GDS_SUMMARY.txt"
    with open(filename, "w") as f:
        f.write(report)
    print(f"📄 Saved professional report to {filename}")

async def main():
    os.makedirs("screenshots", exist_ok=True)
    hotels = await get_hotel_list_from_ai(EMAIL_BODY)
    
    for hotel in hotels:
        name = hotel['name']
        print(f"\n--- Processing: {name} ---")
        
        # TRY AI FIRST
        gds_data = await ask_gemini_for_gds(name)
        
        if gds_data.get('found'):
            print(f"✨ AI Found Data! Chain: {gds_data['chain']}")
            save_formatted_report(name, gds_data)
        else:
            # RUN WEB SEARCH BACKUP
            await conduct_web_research(name, hotel['url'])
        
        await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(main())
