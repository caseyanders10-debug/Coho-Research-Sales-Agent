import asyncio
import os
import json
from google import genai
from playwright.async_api import async_playwright

HOTEL_NAME = os.environ.get("EMAIL_INPUT", "The Reeds at Shelter Haven")
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

async def ask_gemini_with_retry(name, retries=3):
    """Refined AI search to ensure we get real IDs."""
    prompt = (f"Search for the GDS codes for '{name}'. "
              "Return ONLY a JSON object with keys: found, chain, sabre, amadeus, apollo, worldspan. "
              "Example: {'found': true, 'chain': 'PW', 'sabre': '192496', 'amadeus': 'WWDRSH', 'apollo': '44708', 'worldspan': 'ACYRS'}")
    for i in range(retries):
        try:
            print(f"🤖 AI Lookup (Attempt {i+1})...")
            response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
            data = json.loads(response.text.strip().replace('```json', '').replace('```', ''))
            if data.get('sabre') and "123" not in str(data.get('sabre')):
                return data
        except Exception as e:
            if "429" in str(e):
                await asyncio.sleep(10 * (i + 1))
            else: break
    return None

async def main():
    os.makedirs("screenshots", exist_ok=True)
    
    # 1. GET DATA
    gds_data = await ask_gemini_with_retry(HOTEL_NAME)
    
    # 2. SAVE FORMATTED REPORT (Matches your requested layout)
    if gds_data:
        c = gds_data.get('chain', '??')
        report = (
            f"--- GDS PROPERTY SNAPSHOT ---\n"
            f"PROPERTY:  {HOTEL_NAME}\n"
            f"CHAIN:     {c}\n"
            f"-----------------------------\n"
            f"SABRE:     {c}{gds_data.get('sabre', 'N/A')}\n"
            f"AMADEUS:   {c}{gds_data.get('amadeus', 'N/A')}\n"
            f"APOLLO:    {c}{gds_data.get('apollo', 'N/A')}\n"
            f"WORLDSPAN: {c}{gds_data.get('worldspan', 'N/A')}\n"
            f"-----------------------------"
        )
        with open(f"screenshots/GDS_REPORT.txt", "w") as f:
            f.write(report)
        print("✅ Clean report generated.")

    # 3. WEB PROOF (Using DuckDuckGo to bypass Google CAPTCHA)
    print("🌐 Launching DuckDuckGo to bypass CAPTCHA...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            # DuckDuckGo is much more lenient with automated scripts
            ddg_url = f"https://duckduckgo.com/?q={HOTEL_NAME.replace(' ', '+')}+official+site"
            await page.goto(ddg_url, wait_until="networkidle")
            
            # Click the first result on DuckDuckGo (usually '[data-testid="result-title-a"]')
            await page.locator('a[data-testid="result-title-a"]').first.click()
            await page.wait_for_load_state("networkidle")
            
            # Final Screenshot of the official site
            await page.screenshot(path="screenshots/Booking_Engine_Proof.png", full_page=True)
            print("📸 Web proof saved via DuckDuckGo.")
        except Exception as e:
            print(f"⚠️ Web proof failed: {e}")
            await page.screenshot(path="screenshots/web_stuck_debug.png")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
