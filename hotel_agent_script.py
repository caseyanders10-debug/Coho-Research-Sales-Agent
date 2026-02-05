import asyncio
import os
import json
from google import genai
from playwright.async_api import async_playwright

# SETUP: New 2026 Client logic
EMAIL_BODY = os.environ.get("EMAIL_INPUT", "No email provided")
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

async def get_hotel_info_from_ai(text):
    """Uses Gemini 2.0 to extract hotel info using the new stateless function."""
    prompt = f"Extract the hotel name and official website URL from this text. Return ONLY a JSON list of objects: [{{'name': '...', 'url': '...'}}]. Text: {text}"
    try:
        # UPDATED: Using the new Client.models.generate_content method
        response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        clean_text = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(clean_text)
    except Exception as e:
        print(f"AI Extraction Error: {e}")
        return []

async def conduct_research(hotel):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # 2026 Stealth User Agent
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        page = await context.new_page()
        
        name = hotel.get("name")
        os.makedirs("screenshots", exist_ok=True)

        # PART 1: Official Site Snapshot
        try:
            await page.goto(hotel.get("url"), wait_until="domcontentloaded", timeout=45000)
            await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_site.png")
        except: pass

        # PART 2: Travel Weekly GDS Search (The "Chain Code" Hunt)
        try:
            print(f"🔎 Researching {name} on Travel Weekly...")
            await page.goto("https://www.travelweekly.com/hotels", wait_until="domcontentloaded")
            
            # Select the search box and "type" like a human
            search_box = page.locator("#hotelName, input[placeholder*='Hotel Name']").first
            await search_box.click()
            await page.keyboard.type(name, delay=100) # Type with human-like delays
            await page.keyboard.press("Enter")
            
            # Wait for results to actually load
            await page.wait_for_timeout(5000)
            
            # Look for GDS table or the "Details" link
            if await page.get_by_text("View Hotel Details").first.is_visible():
                await page.get_by_text("View Hotel Details").first.click()
                await page.wait_for_timeout(3000)

            await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_GDS_RESEARCH.png", full_page=True)
        except Exception as e:
            print(f"❌ GDS Fail for {name}: {e}")

        await browser.close()

async def main():
    os.makedirs("screenshots", exist_ok=True)
    hotels = await get_hotel_info_from_ai(EMAIL_BODY)
    for hotel in hotels:
        await conduct_research(hotel)

if __name__ == "__main__":
    asyncio.run(main())
