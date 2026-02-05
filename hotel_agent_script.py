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
    prompt = f"Extract the hotel name and official website URL. Return ONLY a JSON list: [{{'name': '...', 'url': '...'}}]. Email: {text}"
    try:
        response = model.generate_content(prompt)
        clean_text = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(clean_text)
    except: return []

async def conduct_research(hotel):
    async with async_playwright() as p:
        # 1. LAUNCH STEALTH BROWSER
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            viewport={'width': 1280, 'height': 800}
        )
        page = await context.new_page()
        name = hotel.get("name")
        os.makedirs("screenshots", exist_ok=True)

        # 2. OFFICIAL SITE (Snapshot 1)
        try:
            await page.goto(hotel.get("url"), wait_until="domcontentloaded", timeout=45000)
            await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_site.png")
        except: pass

        # 3. TRAVEL WEEKLY GDS (Snapshot 2)
        try:
            print(f"🔎 Hunting GDS codes for {name}...")
            # Go directly to the search landing page you provided
            await page.goto("https://www.travelweekly.com/hotels", wait_until="networkidle", timeout=60000)
            
            # Use a 'force' fill to bypass hidden overlays
            search_input = page.locator("input[name*='HotelName'], #hotelName").first
            await search_input.fill(name)
            await page.keyboard.press("Enter")
            
            # Wait for results and CLICK the first "View Hotel Details" link
            # This is where the GDS codes live!
            await page.wait_for_timeout(5000)
            details_btn = page.get_by_text("View Hotel Details").first
            if await details_btn.is_visible():
                await details_btn.click()
                # Wait for the table containing Sabre/Amadeus codes to appear
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(3000)
            
            await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_GDS_CODES.png", full_page=True)
            print(f"✅ GDS codes captured for {name}")

        except Exception as e:
            print(f"❌ GDS Error: {e}")
            await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_GDS_FAILED.png")
        
        await browser.close()

async def main():
    os.makedirs("screenshots", exist_ok=True)
    hotels = await get_hotel_info_from_ai(EMAIL_BODY)
    for hotel in hotels:
        await conduct_research(hotel)
    with open('screenshots/run_log.txt', 'w') as f: f.write("Run Complete")

if __name__ == "__main__":
    asyncio.run(main())
