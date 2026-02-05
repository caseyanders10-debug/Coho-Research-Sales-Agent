import asyncio
import os
import json
from google import genai
from tenacity import retry, stop_after_attempt, wait_random_exponential
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
EMAIL_BODY = os.environ.get("EMAIL_INPUT", "No email provided")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")

# Initialize the 2026 unified Gemini Client
client = genai.Client(api_key=GEMINI_KEY)

@retry(wait=wait_random_exponential(min=1, max=60), stop=stop_after_attempt(5))
async def get_hotel_info_from_ai(text):
    """Extracts hotel details with automatic retry for rate limits."""
    prompt = (
        "Extract the hotel name and official website URL from this email. "
        "Return ONLY a JSON list of objects: [{'name': '...', 'url': '...'}]. "
        f"Email text: {text}"
    )
    response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
    clean_text = response.text.strip().replace('```json', '').replace('```', '')
    return json.loads(clean_text)

async def conduct_research(hotel):
    async with async_playwright() as p:
        # Launch with specific 2026 Stealth Settings
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            viewport={'width': 1280, 'height': 800}
        )
        page = await context.new_page()
        name = hotel.get("name")
        official_url = hotel.get("url")
        os.makedirs("screenshots", exist_ok=True)

        # --- STEP 1: OFFICIAL SITE & BOOKING ENGINE ---
        try:
            print(f"🏠 Navigating to {name} official site...")
            await page.goto(official_url, wait_until="networkidle", timeout=60000)
            
            # Look for common 'Booking' triggers to find the reservation engine
            booking_keywords = ["Book", "Reserve", "Availability", "Stay", "Check Dates"]
            for word in booking_keywords:
                btn = page.get_by_text(word, exact=False).first
                if await btn.is_visible():
                    await btn.click()
                    await page.wait_for_timeout(4000) # Wait for engine to load
                    break
            
            await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_Official_Site.png", full_page=True)
        except Exception as e:
            print(f"❌ Official Site Error: {e}")

        # --- STEP 2: TRAVEL WEEKLY GDS CODES ---
        try:
            print(f"🔎 Hunting GDS codes for {name} on Travel Weekly...")
            await page.goto("https://www.travelweekly.com/Hotels", wait_until="domcontentloaded", timeout=60000)
            
            # Locate the search box from your reference image
            search_box = page.locator("#hotelName, input[placeholder*='Hotel Name']").first
            await search_box.wait_for(state="visible", timeout=15000)
            
            # Human-mimic typing (essential for their suggestive search)
            await search_box.click()
            await page.keyboard.type(name, delay=120) 
            
            # Click the actual 'Search Hotels' button
            search_btn = page.locator("button:has-text('Search'), .search-button, .btn-primary").first
            await search_btn.click()
            
            # Wait for results and click the details link
            await page.wait_for_timeout(6000)
            details_link = page.get_by_text("View Hotel Details").first
            if await details_link.is_visible():
                await details_link.click()
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(3000) # Ensure GDS table renders

            await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_GDS_Data.png", full_page=True)
            print(f"✅ GDS captured for {name}")

        except Exception as e:
            print(f"❌ GDS Error: {e}")
            await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_GDS_FAILED.png")

        await browser.close()

async def main():
    os.makedirs("screenshots", exist_ok=True)
    try:
        hotels = await get_hotel_info_from_ai(EMAIL_BODY)
        for hotel in hotels:
            await conduct_research(hotel)
            await asyncio.sleep(2) # Small gap to stay under API limits
    except Exception as e:
        print(f"FATAL ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(main())
