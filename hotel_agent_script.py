import asyncio
import os
import json
from google import genai
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
HOTEL_NAME = os.environ.get("EMAIL_INPUT", "The Reeds at Shelter Haven")
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

async def ask_gemini_for_gds(name):
    """AI Step: Fast track for GDS codes."""
    prompt = (f"Provide ACTUAL GDS codes for '{name}'. Return ONLY JSON: "
              "{'found': true, 'chain': 'PW', 'sabre': '192496', 'amadeus': 'WWDRSH', 'apollo': '44708', 'worldspan': 'ACYRS'}")
    try:
        response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        clean_json = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(clean_json)
    except:
        return None

async def find_booking_engine(name):
    """Web Step: Accepts cookies and hunts for the booking button."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # Use a real browser window size to ensure buttons aren't hidden in mobile menus
        context = await browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = await context.new_page()
        
        try:
            print(f"🌐 Searching for {name} official website...")
            # We'll use Google to find the official site first
            search_url = f"https://www.google.com/search?q={name.replace(' ', '+')}+official+site"
            await page.goto(search_url, wait_until="networkidle")
            
            # Click the first organic search result
            await page.locator("h3").first.click()
            await page.wait_for_load_state("networkidle")
            print(f"🔗 Arrived at: {page.url}")

            # --- STEP 1: ACCEPT COOKIES ---
            # We look for common 'Accept' buttons
            cookie_selectors = [
                "text=Accept All", "text=Accept Cookies", "text=Agree", 
                "button:has-text('Accept')", "#onetrust-accept-btn-handler"
            ]
            for selector in cookie_selectors:
                try:
                    button = page.locator(selector).first
                    if await button.is_visible(timeout=3000):
                        await button.click()
                        print("🍪 Cookies Accepted.")
                        break
                except: continue

            # --- STEP 2: FIND BOOKING ENGINE ---
            # We look for the main call-to-action button
            booking_selectors = [
                "text=Book Now", "text=Reservations", "text=Check Availability", 
                "text=Book Your Stay", ".booking-button", "a[href*='booking']"
            ]
            
            found_booking = False
            for selector in booking_selectors:
                try:
                    btn = page.locator(selector).first
                    if await btn.is_visible(timeout=3000):
                        print(f"🎯 Booking Engine found: {selector}")
                        # Take a screenshot highlighting the button
                        await btn.screenshot(path=f"screenshots/{name.replace(' ', '_')}_Booking_Button.png")
                        # Click it to see where it leads
                        await btn.click()
                        await page.wait_for_load_state("networkidle")
                        await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_Booking_Engine.png", full_page=True)
                        found_booking = True
                        break
                except: continue

            if not found_booking:
                print("⚠️ Could not find a clear booking button. Taking general screenshot.")
                await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_Home_Page.png", full_page=True)

        except Exception as e:
            print(f"❌ Web Research Error: {e}")
        finally:
            await browser.close()

async def main():
    os.makedirs("screenshots", exist_ok=True)
    
    # 1. Get GDS data via AI (Fastest way to get PW codes)
    gds_data = await ask_gemini_for_gds(HOTEL_NAME)
    if gds_data:
        c = gds_data['chain']
        report = f"PROPERTY: {HOTEL_NAME}\nCHAIN: {c}\nSABRE: {c}{gds_data['sabre']}\nAMADEUS: {c}{gds_data['amadeus']}"
        with open(f"screenshots/{HOTEL_NAME.replace(' ', '_')}_GDS_Report.txt", "w") as f:
            f.write(report)
        print("✨ GDS Data Saved.")

    # 2. Always run the Booking Engine hunt
    await find_booking_engine(HOTEL_NAME)

if __name__ == "__main__":
    asyncio.run(main())
