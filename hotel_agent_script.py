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
async def get_hotel_info_from_ai(text):
    prompt = f"Extract hotel name and official URL. Return ONLY a JSON list: [{{'name': '...', 'url': '...'}}]. Text: {text}"
    response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
    return json.loads(response.text.strip().replace('```json', '').replace('```', ''))

async def nuclear_clear_blockers(page):
    """Removes the specific OneTrust dark filter intercepting clicks."""
    try:
        # 1. Attempt to click the 'Accept' or 'Close' buttons
        selectors = ["#onetrust-accept-btn-handler", "button:has-text('Close')", "button:has-text('Accept')"]
        for s in selectors:
            btn = page.locator(s).first
            if await btn.is_visible():
                await btn.click(force=True)
                await asyncio.sleep(1)
        
        # 2. BRUTE FORCE: Delete the intercepting overlay from the code
        # This removes the 'dark filter' mentioned in your error log (image_b02403.png)
        await page.evaluate("""() => {
            const blockers = document.querySelectorAll('.onetrust-pc-dark-filter, #onetrust-consent-sdk, .optanon-alert-box-wrapper');
            blockers.forEach(el => el.remove());
        }""")
    except: pass

async def handle_selection_page(page, target_name):
    """Handles the 'Hotel Search Selection' screen by clicking the closest match."""
    try:
        # Looks for links specifically under the 'Hotels' header
        hotel_links = page.locator("h3:has-text('Hotels') + ul li a")
        count = await hotel_links.count()
        for i in range(count):
            link = hotel_links.nth(i)
            text = await link.inner_text()
            if target_name[:6].lower() in text.lower():
                await link.click()
                return True
    except: pass
    return False

async def conduct_research(hotel):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/121.0.0.0 Safari/537.36")
        page = await context.new_page()
        name = hotel.get("name")
        os.makedirs("screenshots", exist_ok=True)

        # 1. Official Site Screenshot
        try:
            if hotel.get("url"):
                await page.goto(hotel.get("url"), wait_until="networkidle", timeout=30000)
                await nuclear_clear_blockers(page)
                await page.screenshot(path=f"screenshots/{name}_Site.png")
        except: pass

        # 2. Travel Weekly GDS Search
        try:
            print(f"🔎 Searching Travel Weekly for: {name}")
            await page.goto("https://www.travelweekly.com/Hotels", wait_until="domcontentloaded")
            await nuclear_clear_blockers(page) # Remove the dark filter first
            
            # Find the input box using the placeholder from your screenshot
            search_box = page.locator("input[placeholder*='name or destination']").first
            await search_box.scroll_into_view_if_needed()
            await search_box.fill(name)
            
            # Force click the Search button to bypass any remaining overlays
            await page.locator("button:has-text('Search Hotels')").first.click(force=True)
            await page.wait_for_timeout(5000)

            # Check if we are on the selection page (The Reeds issue)
            if "Selection" in await page.title() or await page.get_by_text("Search Selection").is_visible():
                await handle_selection_page(page, name)

            # Final check for details or codes
            details = page.get_by_text("View Hotel Details").first
            if await details.is_visible():
                await details.click(force=True)
                await page.wait_for_load_state("networkidle")

            await page.screenshot(path=f"screenshots/{name}_GDS_Final.png", full_page=True)
            print(f"✅ Finished research for {name}")

        except Exception as e:
            print(f"❌ Error for {name}: {e}")
            # Take a debug snapshot to see if the filter is still there
            await page.screenshot(path=f"screenshots/{name}_DEBUG_ERR.png")

        await browser.close()

async def main():
    os.makedirs("screenshots", exist_ok=True)
    hotels = await get_hotel_info_from_ai(EMAIL_BODY)
    for h in hotels:
        await conduct_research(h)

if __name__ == "__main__":
    asyncio.run(main())
