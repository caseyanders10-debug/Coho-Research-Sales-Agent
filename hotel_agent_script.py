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
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        page = await context.new_page()
        
        name = hotel.get("name")
        url = hotel.get("url")
        os.makedirs("screenshots", exist_ok=True)

        # --- PART 1: OFFICIAL SITE SNAPSHOT ---
        try:
            print(f"📸 Taking snapshot of {name}...")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_site.png", full_page=True)
        except Exception as e: print(f"❌ Site Error: {e}")

        # --- PART 2: TRAVEL WEEKLY (GDS CODES) ---
        try:
            print(f"🔎 Searching Travel Weekly for {name} GDS codes...")
            await page.goto("https://www.travelweekly.com/Hotels", wait_until="domcontentloaded")
            
            # Find the search box and type the name
            search_box = page.get_by_placeholder("Hotel Name, City, Zip or Airport Code")
            await search_box.fill(name)
            await search_box.press("Enter")
            
            # Wait for results to load
            await page.wait_for_timeout(5000) 
            await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_GDS_Search.png", full_page=True)
            print(f"✅ GDS Search saved for {name}")
        except Exception as e: print(f"❌ GDS Error: {e}")

        await browser.close()

async def main():
    os.makedirs("screenshots", exist_ok=True)
    hotels = await get_hotel_info_from_ai(EMAIL_BODY)
    for hotel in hotels:
        await conduct_research(hotel)
    open('screenshots/run_log.txt', 'w').write(f"Processed {len(hotels)} hotels.")

if __name__ == "__main__":
    asyncio.run(main())
