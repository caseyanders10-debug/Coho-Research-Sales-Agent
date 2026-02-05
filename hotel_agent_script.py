import asyncio
import os
import json
from google import genai
from tenacity import retry, stop_after_attempt, wait_random_exponential
from playwright.async_api import async_playwright

# Initialize the 2026 Client
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

@retry(wait=wait_random_exponential(min=1, max=60), stop=stop_after_attempt(5))
async def get_hotel_info_from_ai(text):
    """Retries up to 5 times if 'Resource Exhausted' occurs."""
    prompt = f"Extract hotel names and official URLs from this text. Return ONLY a JSON list: [{{'name': '...', 'url': '...'}}]. Text: {text}"
    
    # Using the unified 2.0 client
    response = client.models.generate_content(
        model='gemini-2.0-flash', 
        contents=prompt
    )
    clean_text = response.text.strip().replace('```json', '').replace('```', '')
    return json.loads(clean_text)

async def conduct_research(hotel):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        page = await context.new_page()
        
        name = hotel.get("name")
        os.makedirs("screenshots", exist_ok=True)

        # PART 1: Official Site
        try:
            await page.goto(hotel.get("url"), wait_until="domcontentloaded", timeout=45000)
            await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_site.png")
        except: pass

        # PART 2: Travel Weekly GDS Search
        try:
            print(f"🔎 Researching {name} on Travel Weekly...")
            await page.goto("https://www.travelweekly.com/hotels", wait_until="domcontentloaded")
            
            search_box = page.locator("#hotelName, input[placeholder*='Hotel Name']").first
            await search_box.click()
            await page.keyboard.type(name, delay=100) 
            await page.keyboard.press("Enter")
            
            await page.wait_for_timeout(5000)
            
            details_link = page.get_by_text("View Hotel Details").first
            if await details_link.is_visible():
                await details_link.click()
                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(3000)

            await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_GDS_RESEARCH.png", full_page=True)
        except Exception as e:
            print(f"❌ GDS Fail for {name}: {e}")

        await browser.close()

async def main():
    os.makedirs("screenshots", exist_ok=True)
    try:
        hotels = await get_hotel_info_from_ai(os.environ.get("EMAIL_INPUT", ""))
        for hotel in hotels:
            await conduct_research(hotel)
            # Add a small sleep between hotels to stay under the RPM limit
            await asyncio.sleep(2) 
    except Exception as e:
        print(f"Final Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
