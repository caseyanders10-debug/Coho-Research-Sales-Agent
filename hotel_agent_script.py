import asyncio
import os
import json
import google.generativeai as genai
from playwright.async_api import async_playwright

# 1. SETUP: Get the email from GitHub and configure AI
EMAIL_BODY = os.environ.get("EMAIL_INPUT", "No email provided")
GEN_API_KEY = os.environ.get("GEMINI_API_KEY")

async def get_hotel_info_from_ai(text):
    """Uses Gemini 2.0 to extract hotel name and URL from raw email text."""
    if not GEN_API_KEY:
        return [{"name": "Error", "url": "Missing API Key"}]
    
    genai.configure(api_key=GEN_API_KEY)
    # UPDATED: Using gemini-2.0-flash to avoid 404 errors
    model = genai.GenerativeModel('gemini-2.0-flash')
    
    prompt = f"""
    Extract the hotel name and its official website URL from this email. 
    Return ONLY a JSON list of objects with 'name' and 'url'. 
    If multiple hotels are mentioned, list them all.
    Email: {text}
    """
    try:
        response = model.generate_content(prompt)
        clean_text = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(clean_text)
    except Exception as e:
        print(f"AI Parsing Error: {e}")
        return []

async def capture_hotel_snapshot(hotel):
    """Navigates to the hotel site in 'Stealth Mode' and takes a photo."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        name = hotel.get("name", "Unknown_Hotel")
        url = hotel.get("url")

        try:
            print(f"Searching for: {name} at {url}")
            # FIX: Using 'domcontentloaded' to bypass Ritz-Carlton redirect loops
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            
            # Look for the Booking engine
            booking_btn = page.locator("button, a").filter(has_text="/Book|Reserve|Check Rates/i").first
            if await booking_btn.is_visible():
                await booking_btn.click()
                await page.wait_for_timeout(4000)

            os.makedirs("screenshots", exist_ok=True)
            filename = f"screenshots/{name.replace(' ', '_')}.png"
            await page.screenshot(path=filename, full_page=True)
            print(f"✅ Success: {filename}")

        except Exception as e:
            print(f"❌ Failed to capture {name}: {e}")
        finally:
            await browser.close()

async def main():
    os.makedirs("screenshots", exist_ok=True)
    print("Reading email content...")
    hotels = await get_hotel_info_from_ai(EMAIL_BODY)
    for hotel in hotels:
        await capture_hotel_snapshot(hotel)
    
    # Final log to ensure GitHub Artifacts section appears
    with open('screenshots/run_log.txt', 'w') as f:
        f.write(f"Agent finished processing {len(hotels)} hotels.")

if __name__ == "__main__":
    asyncio.run(main())
