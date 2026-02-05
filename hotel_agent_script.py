import asyncio
import os
import json
from google import genai
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
HOTEL_NAME = os.environ.get("EMAIL_INPUT", "The Reeds at Shelter Haven")
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

async def ask_gemini_for_gds(name):
    """AI Step: The primary goal."""
    prompt = (f"Provide ACTUAL GDS codes for '{name}'. Return ONLY JSON: "
              "{'found': true, 'chain': 'PW', 'sabre': '192496', 'amadeus': 'WWDRSH', 'apollo': '44708', 'worldspan': 'ACYRS'}")
    try:
        response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        clean_json = response.text.strip().replace('```json', '').replace('```', '')
        data = json.loads(clean_json)
        # Check for real data, not placeholder '123'
        if data.get('found') and "123" not in str(data.get('sabre')):
            return data
    except:
        return None

async def find_booking_engine(name):
    """Web Step: Backup search with aggressive cookie handling."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            print(f"🌐 AI unsure. Hunting for {name} booking engine...")
            await page.goto(f"https://www.google.com/search?q={name.replace(' ', '+')}+official+site")
            
            # --- THE COOKIE CRUSHER ---
            # Try to click common 'Accept' buttons immediately to clear the view
            for text in ["Accept all", "I agree", "Accept Cookies", "Allow all"]:
                try:
                    btn = page.get_by_role("button", name=text, exact=False).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click()
                        break
                except: continue

            # Click the first organic result
            await page.locator("h3").first.click()
            await page.wait_for_load_state("networkidle")
            
            # Save the proof screenshot
            await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_Engine.png", full_page=True)
            print("📸 Booking engine screenshot saved.")
        except Exception as e:
            print(f"❌ Web Search Error: {e}")
        finally:
            await browser.close()

async def main():
    os.makedirs("screenshots", exist_ok=True)
    
    # 1. Try AI First
    gds_data = await ask_gemini_for_gds(HOTEL_NAME)
    
    if gds_data:
        # SUCCESS: Save and STOP
        print(f"✨ AI SUCCESS: Codes found for {HOTEL_NAME}")
        c = gds_data['chain']
        report = f"PROPERTY: {HOTEL_NAME}\nCHAIN: {c}\nSABRE: {c}{gds_data['sabre']}\nAMADEUS: {c}{gds_data['amadeus']}"
        
        with open(f"screenshots/{HOTEL_NAME.replace(' ', '_')}_GDS_Report.txt", "w") as f:
            f.write(report)
    else:
        # FAIL: Go to Web
        print("🔄 AI could not verify codes. Switching to browser search...")
        await find_booking_engine(HOTEL_NAME)

if __name__ == "__main__":
    asyncio.run(main())
