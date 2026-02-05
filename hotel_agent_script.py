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

@retry(wait=wait_random_exponential(min=1, max=60), stop=stop_after_attempt(5))
async def get_hotel_info_from_ai(text):
    """Stateless extraction with retry logic for 'Resource Exhausted' errors."""
    prompt = (
        "Extract hotel name and official website URL. Return ONLY a JSON list: "
        "[{'name': '...', 'url': '...'}]. If no URL is found, guess the official one. "
        f"Text: {text}"
    )
    response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
    return json.loads(response.text.strip().replace('```json', '').replace('```', ''))

def clean_hotel_name(name):
    """The Name Cleaner: Strips noise words to improve search matching."""
    noise = r"\b(the|hotel|resort|spa|llc|inc|suites|club|inn|and|at|haven)\b"
    cleaned = re.sub(noise, "", name, flags=re.IGNORECASE)
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    return " ".join(cleaned.split())

async def clear_blockers(page):
    """Specifically targets the green 'Close' button seen in your screenshots."""
    try:
        # Targeting the exact 'Close' button and common cookie banners
        selectors = ["button:has-text('Close')", "button:has-text('Accept')", "#onetrust-accept-btn-handler"]
        for s in selectors:
            btn = page.locator(s).first
            if await btn.is_visible():
                await btn.click(force=True)
                await asyncio.sleep(1)
    except: pass

async def handle_selection_page(page, original_name):
    """Navigates the 'Hotel Search Selection' screen from your screenshot."""
    print(f"📋 Selection page detected. Picking best match for '{original_name}'...")
    try:
        # Targets the link under the 'Hotels' header specifically
        hotel_links = page.locator("h3:has-text('Hotels') + ul li a")
        count = await hotel_links.count()
        for i in range(count):
            link = hotel_links.nth(i)
            text = await link.inner_text()
            # If the link text overlaps significantly with our hotel name
            if original_name[:5].lower() in text.lower():
                await link.click()
                await page.wait_for_load_state("networkidle")
                return True
    except: pass
    return False

async def search_travel_weekly(page, name):
    """Comprehensive search including the 'Selection' page logic."""
    print(f"🔎 Searching Travel Weekly: {name}")
    await page.goto("https://www.travelweekly.com/Hotels", wait_until="domcontentloaded")
    await clear_blockers(page)
    
    # Using the exact placeholder from your reference image
    search_box = page.locator("input[placeholder*='Hotel name or destination'], #hotelName").first
    await search_box.wait_for(state="visible", timeout=15000)
    await search_box.fill("")
    await page.keyboard.type(name, delay=100)
    
    # Clicking the 'Search Hotels' button from your image
    await page.locator("button:has-text('Search Hotels'), .btn-primary").first.click()
    await page.wait_for_timeout(5000)

    # If we land on the 'Selection' list (your latest screenshot)
    if await page.get_by_text("Hotel Search Selection").is_visible():
        await handle_selection_page(page, name)

    # Look for the 'View Hotel Details' or GDS table
    details = page.get_by_text("View Hotel Details").first
    if await details.is_visible():
        await details.click()
        await page.wait_for_load_state("networkidle")
        return True
    
    return await page.get_by_text("GDS Reservation Codes").is_visible()

async def conduct_research(hotel):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/121.0.0.0 Safari/537.36")
        page = await context.new_page()
        name = hotel.get("name")
        os.makedirs("screenshots", exist_ok=True)

        # 1. Official Site & Booking Engine
        try:
            await page.goto(hotel.get("url"), wait_until="networkidle", timeout=45000)
            await clear_blockers(page)
            for t in ["Book", "Reserve", "Availability"]:
                btn = page.get_by_text(t, exact=False).first
                if await btn.is_visible():
                    await btn.click(force=True)
                    break
            await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_Official.png")
        except: pass

        # 2. Travel Weekly with Name Cleaner Retry
        try:
            success = await search_travel_weekly(page, name)
            if not success:
                cleaned = clean_hotel_name(name)
                print(f"⚠️ Retrying with Cleaned Name: {cleaned}")
                success = await search_travel_weekly(page, cleaned)

            label = "GDS_SUCCESS" if success else "GDS_NOT_FOUND"
            await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_{label}.png", full_page=True)
        except Exception as e:
            print(f"❌ Travel Weekly Error: {e}")

        await browser.close()

async def main():
    os.makedirs("screenshots", exist_ok=True)
    hotels = await get_hotel_info_from_ai(EMAIL_BODY)
    for h in hotels:
        await conduct_research(h)
        await asyncio.sleep(2) # Prevent 'Resource Exhausted'

if __name__ == "__main__":
    asyncio.run(main())
