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
    """Extracts hotels from email."""
    prompt = f"Extract hotel name and official URL. Return ONLY a JSON list: [{{'name': '...', 'url': '...'}}]. Text: {text}"
    response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
    return json.loads(response.text.strip().replace('```json', '').replace('```', ''))

async def ask_gemini_for_gds(hotel_name):
    """
    NEW PROMPT: Higher confidence.
    Commands Gemini to search and provide the best available data.
    """
    prompt = (
        f"Search your knowledge and the web for the GDS Chain Code and Property IDs "
        f"(Sabre, Amadeus, Apollo/Galileo, Worldspan) for: '{hotel_name}'. "
        "Return ONLY a JSON object: {'found': true, 'chain': '...', 'sabre': '...', 'amadeus': '...', 'apollo': '...', 'worldspan': '...'}. "
        "Only set 'found' to false if the property absolutely cannot be identified."
    )
    try:
        # We use the flash model for speed, but tell it to be thorough
        response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        data = json.loads(response.text.strip().replace('```json', '').replace('```', ''))
        return data
    except:
        return {"found": False}

async def nuclear_clear_blockers(page):
    """Removes 'OneTrust' and other dark overlays preventing clicks."""
    try:
        await page.evaluate("""() => {
            const blockers = document.querySelectorAll('.onetrust-pc-dark-filter, #onetrust-consent-sdk, .optanon-alert-box-wrapper');
            blockers.forEach(el => el.remove());
            document.body.style.overflow = 'visible'; // Ensure we can scroll
        }""")
    except: pass

async def handle_selection_page(page, target_name):
    """
    NAVIGATES THE LIST: Fixes the 'The Reeds' issue.
    Clicks the first link under the 'Hotels' h3 header.
    """
    print(f"📋 Selection page detected. Forcing click on property link...")
    try:
        # Targets the exact structure in your screenshot (h3 Hotels -> ul -> li -> a)
        hotel_link = page.locator("h3:has-text('Hotels') + ul li a").first
        
        if await hotel_link.count() > 0:
            await hotel_link.scroll_into_view_if_needed()
            # Force click is mandatory here because of the invisible layers
            await hotel_link.click(force=True)
            print(f"🔗 Link clicked. Waiting for property page load...")
            await page.wait_for_load_state("networkidle")
            return True
    except Exception as e:
        print(f"⚠️ Navigation error: {e}")
    return False

async def conduct_web_research(hotel_name, official_url):
    """The Fail-Safe: Full browser search."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/121.0.0.0 Safari/537.36")
        page = await context.new_page()
        
        try:
            # 1. Official Site Snapshot
            if official_url:
                await page.goto(official_url, wait_until="networkidle", timeout=30000)
                await page.screenshot(path=f"screenshots/{hotel_name}_Official.png")

            # 2. Travel Weekly Search
            print(f"🔎 AI data unavailable. Searching Travel Weekly for: {hotel_name}")
            await page.goto("https://www.travelweekly.com/Hotels", wait_until="domcontentloaded")
            await nuclear_clear_blockers(page)
            
            search_box = page.locator("input[placeholder*='name or destination']").first
            await search_box.fill(hotel_name)
            await page.locator("button:has-text('Search Hotels')").first.click(force=True)
            await page.wait_for_timeout(5000)

            # 3. CLICK THE PROPERTY (Fixes the screenshot-only issue)
            if "Selection" in await page.title() or await page.get_by_text("Hotel Search Selection").is_visible():
                await handle_selection_page(page, hotel_name)
                await page.wait_for_timeout(3000)

            # 4. Final 'View Details' Click
            details = page.get_by_text("View Hotel Details").first
            if await details.is_visible():
                await details.click(force=True)
                await page.wait_for_load_state("networkidle")

            # 5. Take Final Screenshot of the GDS Table
            await page.screenshot(path=f"screenshots/{hotel_name}_GDS_Table.png", full_page=True)
            print(f"✅ GDS captured for {hotel_name}")
            return True

        except Exception as e:
            print(f"❌ Web research failed: {e}")
            return False
        finally:
            await browser.close()

async def main():
    os.makedirs("screenshots", exist_ok=True)
    hotels = await get_hotel_list_from_ai(EMAIL_BODY)
    
    for hotel in hotels:
        name = hotel['name']
        print(f"\n--- Processing: {name} ---")
        
        # TRY AI FIRST (With new aggressive prompt)
        gds_data = await ask_gemini_for_gds(name)
        
        if gds_data.get('found'):
            print(f"✨ Gemini identified codes: {gds_data['chain']} / {gds_data['sabre']}")
            with open(f"screenshots/{name}_GDS_AI_DATA.txt", "w") as f:
                f.write(json.dumps(gds_data, indent=4))
        else:
            await conduct_web_research(name, hotel['url'])
        
        await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(main())
