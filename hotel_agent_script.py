import asyncio
import os
import json
from google import genai
from playwright.async_api import async_playwright

HOTEL_NAME = os.environ.get("EMAIL_INPUT", "The Reeds at Shelter Haven")
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

async def ask_gemini_with_retry(name, retries=3):
    """Primary data pull: GDS + Phone."""
    prompt = (f"Provide GDS codes and the official phone number for '{name}'. "
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
    # 1. Setup folder immediately
    os.makedirs("screenshots", exist_ok=True)
    
    # 2. GET DATA & SAVE IMMEDIATELY
    data = await ask_gemini_with_retry(HOTEL_NAME)
    
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
        # We write this NOW so it exists even if the browser crashes later
        with open("screenshots/GDS_REPORT.txt", "w") as f:
            f.write(report)
        print("✅ GDS_REPORT.txt saved to folder.")

    # 3. WEB PROOF (Using Bing to bypass Google's Redirect Notice)
    print("🌐 Capturing visual proof via Bing...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            # Bing is less likely to show 'Redirect Notices' or 'Duck CAPTCHAs'
            bing_url = f"https://www.bing.com/search?q={HOTEL_NAME.replace(' ', '+')}+official+site"
            await page.goto(bing_url, wait_until="networkidle")
            
            # Click the first big result link
            await page.locator("li.b_algo h2 a").first.click()
            await page.wait_for_load_state("networkidle")
            
            # Final Screenshot
            await page.screenshot(path="screenshots/Booking_Engine_Proof.png", full_page=True)
            print("📸 Booking_Engine_Proof.png saved.")
        except Exception as e:
            print(f"⚠️ Web proof encountered an issue: {e}")
            await page.screenshot(path="screenshots/web_stuck_debug.png")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
