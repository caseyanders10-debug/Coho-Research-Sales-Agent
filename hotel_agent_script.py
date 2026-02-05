import asyncio
import os
import json
from google import genai
from tenacity import retry, stop_after_attempt, wait_random_exponential
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
EMAIL_BODY = os.environ.get("EMAIL_INPUT", "No email provided")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

client = genai.Client(api_key=GEMINI_KEY)

@retry(wait=wait_random_exponential(min=1, max=60), stop=stop_after_attempt(5))
async def get_hotel_info_from_ai(text):
    """Refined AI Extraction to ensure URLs are never empty."""
    prompt = (
        "Extract the hotel name and official website URL. "
        "IMPORTANT: If no URL is found, search for the most likely official site URL. "
        "Return ONLY a JSON list: [{'name': '...', 'url': '...'}]. "
        f"Text: {text}"
    )
    response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
    clean_text = response.text.strip().replace('```json', '').replace('```', '')
    return json.loads(clean_text)

async def clear_blockers(page):
    """Specialized function to click 'Close' or 'Accept' on cookie banners."""
    try:
        # Common selectors for cookie buttons like the 'Close' button in your screenshot
        blocker_selectors = [
            "button:has-text('Close')", "button:has-text('Accept')", 
            "#onetrust-accept-btn-handler", ".cookie-banner-close",
            "button:has-text('Agree')", "button:has-text('OK')"
        ]
        for selector in blocker_selectors:
            btn = page.locator(selector).first
            if await btn.is_visible():
                await btn.click(force=True)
                await asyncio.sleep(1)
    except:
        pass

async def conduct_research(hotel):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        name = hotel.get("name")
        url = hotel.get("url")
        os.makedirs("screenshots", exist_ok=True)

        # 1. OFFICIAL SITE & BOOKING ENGINE
        if url and url != "None":
            try:
                print(f"🏠 Navigating to {name} official site...")
                await page.goto(url, wait_until="networkidle", timeout=60000)
                await clear_blockers(page) # Clear that 'Close' button from your screenshot
                
                # Try to find the Booking Engine
                for trigger in ["Book", "Reserve", "Availability", "Dates"]:
                    btn = page.get_by_text(trigger, exact=False).first
                    if await btn.is_visible():
                        await btn.click(force=True)
                        await page.wait_for_timeout(3000)
                        break
                
                await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_Official.png")
            except Exception as e:
                print(f"❌ Site Error: {e}")

        # 2. TRAVEL WEEKLY GDS SEARCH
        try:
            print(f"🔎 Searching Travel Weekly for {name}...")
            await page.goto("https://www.travelweekly.com/Hotels", wait_until="domcontentloaded")
            await clear_blockers(page) # Essential: Clears the banner blocking the search box
            
            # Target the search box shown in your reference image
            search_box = page.locator("#hotelName, input[placeholder*='Hotel Name']").first
            await search_box.wait_for(state="visible", timeout=20000)
            await search_box.click()
            await page.keyboard.type(name, delay=100)
            
            # Click the 'Search Hotels' button from your image
            await page.locator("button:has-text('Search Hotels'), .btn-primary").first.click()
            
            await page.wait_for_timeout(5000)
            details = page.get_by_text("View Hotel Details").first
            if await details.is_visible():
                await details.click()
                await page.wait_for_load_state("networkidle")

            await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_GDS_Data.png", full_page=True)
        except Exception as e:
            print(f"❌ Travel Weekly Error: {e}")
            await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_GDS_FAIL.png")

        await browser.close()

async def main():
    os.makedirs("screenshots", exist_ok=True)
    try:
        hotels = await get_hotel_info_from_ai(EMAIL_BODY)
        for hotel in hotels:
            await conduct_research(hotel)
            await asyncio.sleep(2)
    except Exception as e:
        print(f"Main Run Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
