import asyncio
import os
from playwright.async_api import async_playwright

async def capture_hotel_data(hotel_name, hotel_url):
    async with async_playwright() as p:
        # We use a real "User Agent" to avoid being blocked as a bot
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        page = await context.new_page()

        try:
            print(f"Visiting {hotel_name}...")
            await page.goto(hotel_url, wait_until="domcontentloaded", timeout=60000)

            # 1. Handle Cookie Banners (Crucial for Ritz/Marriott)
            for text in ["Accept", "Agree", "OK"]:
                banner_button = page.get_by_role("button", name=text, exact=False)
                if await banner_button.is_visible():
                    await banner_button.click()
                    break

            # 2. Find ANY button that looks like a Booking button
            # This looks for 'Book', 'Reserve', or 'Check Rates'
            booking_btn = page.locator("button, a").filter(has_text="/Book|Reserve|Check Rates/i").first
            
            if await booking_btn.is_visible():
                await booking_btn.click()
                await page.wait_for_timeout(5000) # Wait for engine to slide in
            
            # 3. Take the Snapshot
            os.makedirs("screenshots", exist_ok=True)
            path = f"screenshots/{hotel_name.replace(' ', '_')}.png"
            await page.screenshot(path=path, full_page=True)
            print(f"✅ Saved snapshot to {path}")

        except Exception as e:
            print(f"❌ Error: {e}")
        finally:
            await browser.close()

# Run it!
if __name__ == "__main__":
    asyncio.run(capture_hotel_data("Ritz Carlton NY", "https://www.ritzcarlton.com/en/hotels/nycsh-the-ritz-carlton-new-york-central-park/overview/"))
