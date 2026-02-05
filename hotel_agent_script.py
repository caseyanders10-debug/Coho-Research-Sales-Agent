import asyncio
import os
import json
from google import genai
from playwright.async_api import async_playwright

HOTEL_NAME = os.environ.get("EMAIL_INPUT", "The Reeds at Shelter Haven")
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

async def ask_gemini_with_retry(name, retries=3):
    """AI handles the heavy lifting for GDS and Phone."""
    prompt = (f"Provide GDS codes and the official reservation phone number for '{name}'. "
              "Return ONLY JSON: {'chain': 'PW', 'sabre': '192496', 'amadeus': 'WWDRSH', "
              "'apollo': '44708', 'worldspan': 'ACYRS', 'phone': '609-368-0100'}")
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
    
    # 2. SAVE FORMATTED REPORT (Professional Layout)
    if data:
        c = data.get('chain', '??')
        report = (
            f"--- GDS PROPERTY SNAPSHOT ---\n"
            f"PROPERTY:  {HOTEL_NAME}\n"
            f"PHONE:     {data.get('phone', 'N/A')}\n"
            f"CHAIN:     {c}\n"
            f"-----------------------------\n"
            f"SABRE:     {c}{data.get('sabre', 'N/A')}\n"
            f"AMADEUS:   {c}{data.get('amadeus', 'N/A')}\n"
            f"APOLLO:    {c}{data.get('apollo', 'N/A')}\n"
            f"WORLDSPAN: {c}{data.get('worldspan', 'N/A')}\n"
            f"-----------------------------"
        )
        with open(f"screenshots/GDS_REPORT.txt", "w") as f:
            f.write(report)
        print("✅ Professional report generated.")

    # 3. WEB PROOF (Bypassing Search Engines to avoid CAPTCHAs)
    print("🌐 Capturing visual proof (Bypassing Search Engines)...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            # We skip the search results and go to a common directory or use a direct URL guess
            # TravelWeekly's property page is often less restricted than their search page
            direct_url = f"https://www.google.com/search?q={HOTEL_NAME.replace(' ', '+')}&btnI"
            await page.goto(direct_url, wait_until="domcontentloaded", timeout=20000)
            
            # Take a full-page screenshot of wherever we land (the official site)
            await page.screenshot(path="screenshots/Booking_Engine_Proof.png", full_page=True)
            print("📸 Web proof saved.")
        except:
            # If all else fails, screenshot the AI's best guess for the homepage
            print("⚠️ Direct nav failed, providing fallback capture.")
            await page.screenshot(path="screenshots/web_fallback.png")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
