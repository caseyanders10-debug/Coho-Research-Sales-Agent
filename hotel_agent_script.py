import asyncio
import os
import json
import re
from google import genai
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
EMAIL_BODY = os.environ.get("EMAIL_INPUT", "The Reeds at Shelter Haven")
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

async def ask_gemini_for_gds(hotel_name):
    """Commands Gemini with a strict format to prevent parsing errors."""
    await asyncio.sleep(5) # Cooldown to prevent 'Resource Exhausted'
    
    prompt = (
        f"Search for GDS codes for: '{hotel_name}'. "
        "Return ONLY this exact JSON format, no extra text: "
        '{"found": true, "chain": "PW", "sabre": "123", "amadeus": "123", "apollo": "123", "worldspan": "123"}'
    )
    
    try:
        response = client.models.generate_content(model='gemini-2.0-flash', contents=prompt)
        # CLEANER: Removes markdown code blocks if the AI includes them
        raw_text = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(raw_text)
    except Exception as e:
        print(f"⚠️ AI Step failed: {e}")
        return {"found": False}

async def conduct_web_research(hotel_name):
    """Backup: Hits TravelWeekly and forces a click on the correct property link."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            print(f"🔎 AI failed. Searching Travel Weekly for: {hotel_name}")
            await page.goto("https://www.travelweekly.com/Hotels", wait_until="networkidle")
            
            # Kill the dark filter that causes timeouts (image_b02403.png)
            await page.evaluate("document.querySelectorAll('.onetrust-pc-dark-filter').forEach(el => el.remove())")
            
            # Fill search
            await page.locator("input[placeholder*='name or destination']").first.fill(hotel_name)
            await page.locator("button:has-text('Search Hotels')").first.click()
            await page.wait_for_timeout(5000)

            # FORCE CLICK the property in the 'Hotels' list (image_a52f1d.png)
            if await page.get_by_text("Hotel Search Selection").is_visible():
                print("📋 Selection page detected. Forcing click...")
                # Specifically targets the list under the 'Hotels' header
                await page.locator("h3:has-text('Hotels') + ul li a").first.click(force=True)
                await page.wait_for_timeout(3000)

            # Final 'View Details' to reach the GDS table
            details = page.get_by_text("View Hotel Details").first
            if await details.is_visible():
                await details.click(force=True)
                await page.wait_for_load_state("networkidle")

            await page.screenshot(path=f"screenshots/{hotel_name.replace(' ', '_')}_Final.png", full_page=True)
            print(f"✅ GDS captured for {hotel_name}")
        finally:
            await browser.close()

async def main():
    os.makedirs("screenshots", exist_ok=True)
    # Using a list based on your email input
    hotel_list = [{"name": EMAIL_BODY}] 
    
    for hotel in hotel_list:
        name = hotel['name']
        print(f"--- Starting: {name} ---")
        
        data = await ask_gemini_for_gds(name)
        
        if data.get('found') and data.get('chain'):
            # Formats the clean table you wanted with the 2-letter code (e.g. PW)
            c = data['chain']
            report = f"PROPERTY: {name}\nCHAIN: {c}\nSABRE: {c}{data['sabre']}\nAMADEUS: {c}{data['amadeus']}"
            with open(f"screenshots/{name.replace(' ', '_')}_GDS.txt", "w") as f:
                f.write(report)
            print(f"✨ SUCCESS: AI found {c} codes.")
        else:
            await conduct_web_research(name)
        
        await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
