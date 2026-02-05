import asyncio
import os
import json
from google import genai
from playwright.async_api import async_playwright

HOTEL_NAME = os.environ.get("EMAIL_INPUT", "The Reeds at Shelter Haven")
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

async def ask_gemini_with_retry(name, retries=3):
    """Primary data pull: GDS, Phone, and Direct Website URL."""
    prompt = (f"Provide GDS codes, official phone, and official website URL for '{name}'. "
              "Return ONLY JSON: {'chain': 'PW', 'sabre': '192496', 'amadeus': 'WWDRSH', "
              "'apollo': '44708', 'worldspan': 'ACYRS', 'phone': '609-368-0100', "
              "'url': 'https://reedsatshelterhaven.com/'}")
    for i in range(retries):
        try:
            print(f"🤖 AI Lookup (Attempt {i+1})...")
            response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
            return json.loads(response.text.strip().replace('```json', '').replace('```', ''))
        except Exception as e:
            if "429" in str(e): await asyncio.sleep(10 * (i + 1))
            else: break
    return None

async def main():
    os.makedirs("screenshots", exist_ok=True)
    
    # 1. GET DATA
    data = await ask_gemini_with_retry(HOTEL_NAME)
    
    # 2. SAVE REPORT IMMEDIATELY
    if data:
        c = data.get('chain', '??')
        report = (
            f"--- GDS PROPERTY SNAPSHOT ---\n"
            f"PROPERTY:  {HOTEL_NAME}\n"
            f"PHONE:     {data.get('phone', 'N/A')}\n"
            f"URL:       {data.get('url', 'N/A')}\n"
            f"CHAIN:     {c}\n"
            f"-----------------------------\n"
            f"SABRE:     {c}{data.get('sabre', 'N/A')}\n"
            f"AMADEUS:   {c}{data.get('amadeus', 'N/A')}\n"
            f"APOLLO:    {c}{data.get('apollo', 'N/A')}\n"
            f"WORLDSPAN: {c}{data.get('worldspan', 'N/A')}\n"
            f"-----------------------------"
        )
        with open("screenshots/GDS_REPORT.txt", "w") as f:
            f.write(report)
        print("✅ GDS_REPORT.txt saved.")

    # 3. DIRECT WEB PROOF (Bypassing Search Engines completely)
    if data and data.get('url'):
        print(f"🌐 Navigating directly to: {data['url']}")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                # Go directly to the URL the AI found
                await page.goto(data['url'], wait_until="load", timeout=30000)
                await page.wait_for_timeout(2000) # Short wait for elements to settle
                await page.screenshot(path="screenshots/Booking_Engine_Proof.png", full_page=True)
                print("📸 Direct visual proof saved.")
            except Exception as e:
                print(f"⚠️ Direct navigation failed: {e}")
                await page.screenshot(path="screenshots/web_error_debug.png")
            finally:
                await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
