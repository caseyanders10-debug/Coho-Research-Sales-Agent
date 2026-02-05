import asyncio
import os
import json
import google.generativeai as genai
from playwright.async_api import async_playwright

EMAIL_BODY = os.environ.get("EMAIL_INPUT", "No email provided")
GEN_API_KEY = os.environ.get("GEMINI_API_KEY")

async def get_hotel_info_from_ai(text):
    if not GEN_API_KEY: return []
    genai.configure(api_key=GEN_API_KEY)
    model = genai.GenerativeModel('gemini-2.0-flash')
    prompt = f"Extract the hotel name and official website URL from this email. If no URL is found, provide the official one. Return ONLY a JSON list of objects with 'name' and 'url'. Email: {text}"
    try:
        response = model.generate_content(prompt)
        clean_text = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(clean_text)
    except: return []

async def conduct_research(hotel):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # We use a persistent context to handle the Travel Weekly redirects better
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        page = await context.new_page()
        
        name = hotel.get("name")
        url = hotel.get("url")
        os.makedirs("screenshots", exist_ok=True)

        # PART 1: Official Hotel Site
        try:
            print(f"📸 Snapping official site for {name}...")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_site.png")
        except: pass

        # PART 2: Travel Weekly GDS Search
        try:
            print(f"🔎 Researching GDS codes for {name} on Travel Weekly...")
            # Navigate to the specific URL you provided
            await page.goto("https://www.travelweekly.com/hotels", wait_until="domcontentloaded")
            
            # The Travel Weekly search box often uses a specific ID 'hotelName'
            # We wait for it to be ready so we don't get a Timeout
            search_input = page.locator("input[name*='HotelName'], #hotelName, .hotel-search-input").first
            await search_input.wait_for(state="visible", timeout=20000)
            await search_input.fill(name)
            await search_input.press("Enter")
            
            # Wait for the results to load (Travel Weekly is a bit slow)
            await page.wait_for_timeout(6000) 
            
            # If there's a "View Hotel Details" link, we click it to get the CODES
            details_link = page.get_by_text("View Hotel Details").first
            if await details_link.is_visible():
                await details_link.click()
                await page.wait_for_timeout(4000)

            await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_GDS_RESEARCH.png", full_page=True)
            print(f"✅ GDS Research completed for {name}")
        except Exception as e:
            print(f"❌ GDS Research failed: {e}")
            await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_ERROR_VIEW.png")

        await browser.close()

async def main():
    os.makedirs("screenshots", exist_ok=True)
    hotels = await get_hotel_info_from_ai(EMAIL_BODY)
    for hotel in hotels:
        await conduct_research(hotel)
    open('screenshots/run_log.txt', 'w').write("Complete")

if __name__ == "__main__":
    asyncio.run(main())
