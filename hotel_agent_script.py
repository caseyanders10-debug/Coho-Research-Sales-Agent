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

def clean_hotel_name(name):
    """Removes common 'noise' words to help Travel Weekly find a match."""
    noise_words = r"\b(the|hotel|resort|spa|llc|inc|suites|club|inn|and)\b"
    cleaned = re.sub(noise_words, "", name, flags=re.IGNORECASE)
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    return " ".join(cleaned.split())

async def clear_blockers(page):
    """Clicks 'Close' or 'Accept' on the banner seen in your screenshot."""
    selectors = ["button:has-text('Close')", "button:has-text('Accept')", "#onetrust-accept-btn-handler"]
    for s in selectors:
        try:
            btn = page.locator(s).first
            if await btn.is_visible(): await btn.click(force=True)
        except: pass

async def handle_selection_page(page, original_name):
    """Handles the 'Hotel Search Selection' list from your screenshot."""
    print(f"📋 Selection page detected. Looking for {original_name}...")
    # Look for the hotel name specifically under the 'Hotels' header
    try:
        # This finds the link in the 'Hotels' list that matches your property
        hotel_link = page.locator("h3:has-text('Hotels') + ul li a, .hotel-list a").filter(has_text=original_name[:10]).first
        if await hotel_link.is_visible():
            await hotel_link.click()
            await page.wait_for_load_state("networkidle")
            return True
    except:
        pass
    return False

async def search_travel_weekly(page, name):
    """Improved search logic that handles the selection list."""
    print(f"🔎 Searching Travel Weekly for: {name}")
    await page.goto("https://www.travelweekly.com/Hotels", wait_until="domcontentloaded")
    await clear_blockers(page)
    
    search_box = page.locator("#hotelName, input[placeholder*='Hotel Name']").first
    await search_box.wait_for(state="visible", timeout=15000)
    await search_box.fill("") 
    await page.keyboard.type(name, delay=100)
    await page.locator("button:has-text('Search Hotels'), .btn-primary").first.click()
    
    await page.wait_for_timeout(5000)

    # CHECK 1: Are we on the selection page from your screenshot?
    if "Search Selection" in await page.title() or await page.get_by_text("Results for").is_visible():
        await handle_selection_page(page, name)

    # CHECK 2: Look for the final 'View Hotel Details' button
    details = page.get_by_text("View Hotel Details").first
    if await details.is_visible():
        await details.click()
        await page.wait_for_load_state("networkidle")
        return True
    
    # Check if we landed directly on a page with GDS codes
    if await page.get_by_text("GDS Reservation Codes").is_visible():
        return True
        
    return False

async def conduct_research(hotel):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        page = await context.new_page()
        name = hotel.get("name")
        os.makedirs("screenshots", exist_ok=True)

        # 1. Official Site (Finding the Booking Engine)
        try:
            await page.goto(hotel.get("url"), wait_until="networkidle", timeout=45000)
            await clear_blockers(page)
            # Try to click a booking trigger
            for t in ["Book", "Reserve", "Availability"]:
                btn = page.get_by_text(t, exact=False).first
                if await btn.is_visible():
                    await btn.click(force=True)
                    await page.wait_for_timeout(3000)
                    break
            await page.screenshot(path=f"screenshots/{name}_Official_Booking.png")
        except: pass

        # 2. Travel Weekly (Handling the selection list)
        try:
            success = await search_travel_weekly(page, name)
            
            if not success:
                cleaned = clean_hotel_name(name)
                success = await search_travel_weekly(page, cleaned)

            suffix = "GDS_DATA" if success else "NOT_FOUND"
            await page.screenshot(path=f"screenshots/{name}_{suffix}.png", full_page=True)
        except Exception as e:
            print(f"❌ Research Failed: {e}")

        await browser.close()

async def main():
    os.makedirs("screenshots", exist_ok=True)
    hotels = await get_hotel_info_from_ai(EMAIL_BODY)
    for h in hotels:
        await conduct_research(h)
        await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(main())
