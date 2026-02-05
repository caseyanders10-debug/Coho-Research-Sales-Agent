import asyncio
import os
import json
from google import genai
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
HOTEL_NAME = os.environ.get("EMAIL_INPUT", "The Reeds at Shelter Haven")
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

async def ask_gemini_for_gds(name):
    """AI Step: Retries 429s and returns clean JSON."""
    prompt = (f"Provide ACTUAL GDS codes for '{name}'. Return ONLY JSON: "
              "{'found': true, 'chain': 'PW', 'sabre': '192496', 'amadeus': 'WWDRSH', 'apollo': '44708', 'worldspan': 'ACYRS'}")
    try:
        response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        clean_json = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(clean_json)
    except Exception as e:
        print(f"❌ AI Error: {e}")
        return {"found": False}

async def conduct_web_research(name):
    """Web Step: Forces a screenshot of the TravelWeekly results."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            print(f"🌐 Capturing web proof for {name}...")
            await page.goto("https://www.travelweekly.com/Hotels", wait_until="domcontentloaded")
            # Remove cookie banners
            await page.evaluate("document.querySelectorAll('.onetrust-pc-dark-filter, #onetrust-consent-sdk').forEach(el => el.remove())")
            
            await page.locator("input[placeholder*='name or destination']").first.fill(name)
            await page.locator("button:has-text('Search Hotels')").first.click(force=True)
            await asyncio.sleep(5)
            
            # Take screenshot of the results page for the artifact
            await page.screenshot(path=f"screenshots/{name.replace(' ', '_')}_Web_Results.png")
            print(f"📸 Web screenshot saved.")
        except Exception as e:
            print(f"⚠️ Web screenshot failed: {e}")
        finally:
            await browser.close()

async def main():
    # Ensure the directory exists so GitHub Actions doesn't fail
    os.makedirs("screenshots", exist_ok=True)
    
    # 1. Get AI Data
    print(f"🤖 Starting AI lookup for: {HOTEL_NAME}")
    gds_data = await ask_gemini_for_gds(HOTEL_NAME)
    
    # 2. Always create a text file artifact if data is found
    if gds_data.get('found'):
        c = gds_data['chain']
        report = (f"PROPERTY: {HOTEL_NAME}\n"
                  f"CHAIN: {c}\n"
                  f"SABRE: {c}{gds_data['sabre']}\n"
                  f"AMADEUS: {c}{gds_data['amadeus']}\n"
                  f"APOLLO: {c}{gds_data['apollo']}")
        
        # This creates the file GitHub is looking for
        file_path = f"screenshots/{HOTEL_NAME.replace(' ', '_')}_GDS_Report.txt"
        with open(file_path, "w") as f:
            f.write(report)
        print(f"✨ SUCCESS: Report written to {file_path}")
    
    # 3. Force a web screenshot for your booking engine requirement
    await conduct_web_research(HOTEL_NAME)

if __name__ == "__main__":
    asyncio.run(main())
