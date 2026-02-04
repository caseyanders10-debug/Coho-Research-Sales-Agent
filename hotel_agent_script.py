import asyncio
import os
import json
import google.generativeai as genai
from playwright.async_api import async_playwright

# SETUP: Get the email and API Key
EMAIL_BODY = os.environ.get("EMAIL_INPUT", "No email provided")
GEN_API_KEY = os.environ.get("GEMINI_API_KEY")

async def get_hotel_info_from_ai(text):
    """Uses Gemini 2.0 to extract hotel names and FIND their URLs."""
    if not GEN_API_KEY:
        return []
    
    genai.configure(api_key=GEN_API_KEY)
    model = genai.GenerativeModel('gemini-2.0-flash')
    
    # IMPROVED PROMPT: Tells Gemini to provide the URL even if it's not in the text
    prompt = f"""
    Extract the hotel name and its official website URL from this email. 
    If a URL is not explicitly in the email, provide the most likely official website URL for that hotel.
    Return ONLY a JSON list of objects with 'name' and 'url'. 
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
    """Navigates to the hotel site and takes a photo."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        page = await context.new_page()
        
        name = hotel.get("name", "Unknown_Hotel")
        url = hotel.get("url")

        # CHECK: Skip if URL is still missing
        if not url or url == "None":
            print(f"⚠️ Skipping {name}: No URL found.")
            return

        try:
            print(f"Searching for: {name} at {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            
            # Take the Snapshot
            os.makedirs("screenshots", exist_ok=True)
            filename = f"screenshots/{name.replace(' ', '_')}.png"
            await page.screenshot(path=filename, full_page=True)
            print(f"✅ Success: Saved {filename}")

        except Exception as e:
            print(f"❌ Failed to capture {name}: {e}")
        finally:
            await browser.close()

async def main():
    os.makedirs("screenshots", exist_ok=True)
    hotels = await get_hotel_info_from_ai(EMAIL_BODY)
    for hotel in hotels:
        await capture_hotel_snapshot(hotel)
    
    with open('screenshots/run_log.txt', 'w') as f:
        f.write(f"Agent finished. Processed: {hotels}")

if __name__ == "__main__":
    asyncio.run(main())
