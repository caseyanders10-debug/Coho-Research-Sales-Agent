import asyncio
import os
import json
import time
from google import genai
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
HOTEL_NAME = os.environ.get("EMAIL_INPUT", "The Reeds at Shelter Haven")
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

async def ask_gemini_with_retry(name, retries=3):
    """Handles Rate Limits (429) by waiting and retrying."""
    prompt = (f"Provide ACTUAL GDS codes for '{name}'. Return ONLY JSON: "
              "{'found': true, 'chain': 'PW', 'sabre': '192496', 'amadeus': 'WWDRSH', 'apollo': '44708', 'worldspan': 'ACYRS'}")
    for i in range(retries):
        try:
            print(f"🤖 AI Attempt {i+1} for {name}...")
            response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
            clean_json = response.text.strip().replace('```json', '').replace('```', '')
            data = json.loads(clean_json)
            if data.get('found') and data.get('sabre') != "123":
                return data
        except Exception as e:
            if "429" in str(e):
                wait_time = 10 * (i + 1)
                print(f"⏳ Rate limited (429). Waiting {wait_time}s before retry...")
                await asyncio.sleep(wait_time)
            else:
                print(f"⚠️ AI Error: {e}")
                break
    return None

async def capture_booking_proof(name):
    """Uses Playwright to get visual proof, bypassing cookie walls."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1280, 'height': 800})
        page = await context.new_page()
        try:
            print(f"🌐 Navigating to official site search...")
            await page.goto(f"https://www.google.com/search?q={name.replace(' ', '+')}+official+site")
            
            # --- THE COOKIE CRUSHER ---
            # Removes overlays that cause the 'Timeout 30000ms' errors
            await page.evaluate("""() => {
                const selectors = ['[id*="cookie"]', '[class*="cookie"]', '[id*="onetrust"]', '[id*="consent"]'];
                selectors.forEach(s => document.querySelectorAll(s).forEach(el => el.remove()));
            }""")
            
            # Click the first official result
            await page.locator("h3").first.click(timeout=15000)
            await page.wait_for_load_state("networkidle")
            
            # Save proof for GitHub Artifacts
            path = f"screenshots/{name.replace(' ', '_')}_Site_Proof.png"
            await page.screenshot(path=path, full_page=True)
            print(f"📸 Screenshot saved: {path}")
        except Exception as e:
            print(f"❌ Web Proof Error: {e}")
            await page.screenshot(path="screenshots/ERROR_STUCK.png")
        finally:
            await browser.close()

async def main():
    # Create folder immediately to avoid "No files found" warning
    os.makedirs("screenshots", exist_ok=True)
    
    # 1. AI GDS Lookup
    gds_data = await ask_gemini_with_retry(HOTEL_NAME)
    
    if gds_data:
        print(f"✨ AI SUCCESS: Found {gds_data['chain']}{gds_data['sabre']}")
        report = f"PROPERTY: {HOTEL_NAME}\nCHAIN: {gds_data['chain']}\nSABRE: {gds_data['chain']}{gds_data['sabre']}\nAMADEUS: {gds_data['amadeus']}"
        with open(f"screenshots/{HOTEL_NAME.replace(' ', '_')}_Report.txt", "w") as f:
            f.write(report)
    
    # 2. THE DELAY: 5-second cooling period
    print("⏲️ Cooling down for 5 seconds to prevent rate limits...")
    await asyncio.sleep(5)
    
    # 3. Web Search Proof
    await capture_booking_proof(HOTEL_NAME)

if __name__ == "__main__":
    asyncio.run(main())
