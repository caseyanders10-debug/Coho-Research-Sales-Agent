import asyncio
import os
from playwright.async_api import async_playwright

# Configuration - Replace these with your actual data or email parser output
HOTELS = [
    {"name": "The Ritz-Carlton New York", "url": "https://www.ritzcarlton.com/en/hotels/nycsh-the-ritz-carlton-new-york-central-park/overview/"},
    # Add more hotels here
]

async def capture_hotel_data(hotel):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True) # GitHub Actions needs headless=True
        context = await browser.new_context(viewport={'width': 1280, 'height': 800})
        page = await context.new_page()

        print(f"--- Processing: {hotel['name']} ---")

        # --- STEP 1: Property Website & Screenshot ---
        try:
            await page.goto(hotel['url'], wait_until="networkidle")
            # Logic to find "Book" buttons (looks for common keywords)
            book_button = page.get_by_role("button", name="Book").or_(page.get_by_text("Reserve", exact=False))
            
            if await book_button.is_visible():
                await book_button.click()
                await page.wait_for_timeout(3000) # Wait for engine to load
            
            # Save the screenshot
            os.makedirs("screenshots", exist_ok=True)
            screenshot_path = f"screenshots/{hotel['name'].replace(' ', '_')}_booking.png"
            await page.screenshot(path=screenshot_path)
            print(f"Successfully captured snapshot: {screenshot_path}")
        except Exception as e:
            print(f"Error on property site: {e}")

        # --- STEP 2: Travel Weekly GDS Search ---
        try:
            await page.goto("https://www.travelweekly.com/hotels", wait_until="networkidle")
            
            # Fill the search bar
            search_input = page.get_by_placeholder("Hotel Name or Location")
            await search_input.fill(hotel['name'])
            await page.keyboard.press("Enter")
            await page.wait_for_load_state("networkidle")

            # Click the first result
            first_result = page.locator(".hotel-search-results a").first
            await first_result.click()
            await page.wait_for_load_state("networkidle")

            # Extract GDS Codes
            # Travel Weekly often puts these in a specific table or list
            gds_section = page.locator("text=GDS Reservation Codes")
            if await gds_section.is_visible():
                content = await page.locator(".hotel-details-table").inner_text()
                print(f"GDS Info found for {hotel['name']}")
                # You can use regex here to specifically grab the 2-digit chain code
            else:
                print(f"GDS section not found for {hotel['name']}")

        except Exception as e:
            print(f"Error on Travel Weekly: {e}")

        await browser.close()

async def main():
    for hotel in HOTELS:
        await capture_hotel_data(hotel)

if __name__ == "__main__":
    asyncio.run(main())
