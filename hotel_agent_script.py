import asyncio
import os
import json
from google import genai
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
HOTEL_NAME = "The Reeds at Shelter Haven" # Keeping it constant for your test
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

async def ask_gemini_for_gds(name):
    """AI Step: Now with a built-in retry for 429 errors."""
    prompt = (
        f"Provide GDS codes for '{name}'. Return ONLY JSON: "
        "{'found': true, 'chain': 'PW', 'sabre': '192496', 'amadeus': 'WWDRSH', 'apollo': '44708', 'worldspan': 'ACYRS'}"
    )
    
    for attempt in range(3): # Try 3 times before falling back to web
        try:
            print(f"🤖 AI Attempt {attempt + 1}...")
            response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
            clean_json = response.text.strip().replace('```json', '').replace('```', '')
            data = json.loads(clean_json)
            
            # Verify we didn't get placeholder data
            if data.get('sabre') == '123':
                print("⚠️ AI provided placeholder data. Switching to web search.")
                return {"found": False}
            return data
        except Exception as e:
            if "429" in str(e):
                print(f"⏳ Rate limited. Waiting 10s... (Attempt {attempt + 1}/3)")
                await asyncio.sleep(10)
            else:
                print(f"❌ AI Error: {e}")
                break
    return {"found": False}

async def conduct_web_research(name):
    """Web Step: Clears hidden banners and forces the click."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = await context.new_page()
        
        try:
            print(f"🌐 Loading Travel Weekly for {name}...")
            await page.goto("https://www.travelweekly.com/Hotels", wait_until="networkidle", timeout=60000)
            
            # --- THE NUCLEAR CLEAR ---
            # Deletes the cookie banner so it doesn't block the click
            await page.evaluate("""() => {
                const blockers = document.querySelectorAll('.onetrust-pc-dark-filter, #onetrust-consent-sdk, .optanon-alert-box-wrapper');
                blockers.forEach(el => el.remove());
                document.body.style.overflow = 'visible';
            }""")
            
            # Search
            await page.locator("input[placeholder*='name or destination']").first.fill(name)
            await page.locator("button:has-text('Search Hotels')").first.click(force=True)
            print("🔍 Search submitted. Waiting for results...")
            await asyncio.sleep(5)

            # Selection Page: Click the first hotel link
            if "Selection" in await page.title() or await page.get_by_text("Hotel Search Selection").is_visible():
                print("📋 Selection list found. Clicking first hotel...")
                # Targets the link specifically under the 'Hotels' header
                hotel_link = page.locator("h3:has-text('Hotels') + ul li a").first
                await hotel_link.click(force=True)
                await page.wait_for_load_state("networkidle")

            # Final Table Page
            print("📄 Reached Property Page. Capturing GDS Table...")
            details = page.get_by_text("View Hotel Details").first
            if await details.is_visible():
                await details.click(force=True)
                await page.wait_for_load_state("networkidle")

            await page.screenshot(path=f"screenshots/{name}_DEBUG.png", full_page=True)
            print(f"✅ Success! Check screenshots/{name}_DEBUG.png")
            
        except Exception as e:
            print(f"❌ Web Failure: {e}")
        finally:
            await browser.close()

async def main():
    os.makedirs("screenshots", exist_ok=True)
    
    # Run the logic
    gds_data = await ask_gemini_for_gds(HOTEL_NAME)
    
    if gds_data.get('found'):
        c = gds_data['chain']
        print(f"✨ AI FOUND CODES: {c}{gds_data['sabre']}")
        # (Your table saving code here)
    else:
        print("🔄 AI could not provide real codes. Starting browser...")
        await conduct_web_research(HOTEL_NAME)

if __name__ == "__main__":
    asyncio.run(main())
