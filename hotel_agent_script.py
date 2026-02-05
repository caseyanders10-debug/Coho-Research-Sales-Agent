import asyncio
import os
import json
from google import genai
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
HOTEL_NAME = os.environ.get("EMAIL_INPUT", "The Reeds at Shelter Haven")
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

async def ask_gemini_for_gds(name):
    """Step 1: AI lookup - The fastest path to GDS codes."""
    prompt = (f"Provide ACTUAL GDS codes for '{name}'. Return ONLY JSON: "
              "{'found': true, 'chain': 'PW', 'sabre': '192496', 'amadeus': 'WWDRSH', 'apollo': '44708', 'worldspan': 'ACYRS'}")
    try:
        response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        clean_json = response.text.strip().replace('```json', '').replace('```', '')
        data = json.loads(clean_json)
        # Only return if data isn't a placeholder
        if data.get('found') and data.get('sabre') not in ["123", "placeholder"]:
            return data
    except:
        return None

async def find_booking_engine(name):
    """Step 2: Browser lookup - Accepts cookies and finds the 'Book Now' button."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # Desktop size ensures buttons aren't hidden in mobile menus
        context = await browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = await context.new_page()
        
        try:
            print(f"🌐 Navigating to {name} official site...")
            # Using a direct search to find the official site
            await page.goto(f"https://www.google.com/search?q={name.replace(' ', '+')}+official+site")
            await page.locator("h3").first.click()
            await page.wait_for_load_state("networkidle")

            # --- COOKIE ACCEPTANCE ---
            # List of common 'Accept' button patterns
            cookie_buttons = ["Accept All", "Accept Cookies", "Agree", "Allow All", "I Accept"]
            for text in cookie_buttons:
                btn = page.get_by_role("button", name=text, exact=False).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    print(f"🍪 Clicked '{text}' cookie button.")
                    break

            # --- FIND BOOKING ENGINE ---
            # Searching for standard call-to-action buttons
            booking_selectors = ["text=Book Now", "text=Reservations", "text=Check Availability", "a[href*='booking']"]
            for selector in booking_selectors:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=3000):
                    print(f"🎯 Found Booking Button: {selector}")
                    # Screenshot the homepage highlighting the button
                    await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_Home.png")
                    # Click to reach the engine
                    await btn.click()
                    await page.wait_for_load_state("networkidle")
                    # Final screenshot of the booking engine itself
                    await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_Booking_Engine.png", full_page=True)
                    return True
            
            # Fallback screenshot if no button is found
            await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_Fallback.png")
        except Exception as e:
            print(f"⚠️ Web Search Error: {e}")
        finally:
            await browser.close()

async def main():
    os.makedirs("screenshots", exist_ok=True)
    
    # 1. AI Logic
    gds_data = await ask_gemini_for_gds(HOTEL_NAME)
    if gds_data:
        print(f"✨ SUCCESS: AI identified codes.")
        c = gds_data['chain']
        report = f"PROPERTY: {HOTEL_NAME}\nCHAIN: {c}\nSABRE: {c}{gds_data['sabre']}\nAMADEUS: {c}{gds_data['amadeus']}"
        # This creates the artifact file for GitHub
        with open(f"screenshots/{HOTEL_NAME.replace(' ', '_')}_GDS_Report.txt", "w") as f:
            f.write(report)
    
    # 2. Web Logic (Always run this for your visual booking engine proof)
    await find_booking_engine(HOTEL_NAME)

if __name__ == "__main__":
    asyncio.run(main())
