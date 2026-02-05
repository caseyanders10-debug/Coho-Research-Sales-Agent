
async def handle_selection_page(page, target_name):
    """
    Specifically handles the page in your screenshot.
    It looks for the 'Hotels' section and clicks the first match.
    """
    print(f"📋 Selection page detected. Clicking match for '{target_name}'...")
    try:
        # This locator targets the link exactly where 'The Reeds' appears in your screenshot
        hotel_link = page.locator("h3:has-text('Hotels') + ul li a").first
        
        if await hotel_link.is_visible():
            # We use force=True because of the persistent overlays on this site
            await hotel_link.click(force=True)
            print(f"✅ Clicked property link for {target_name}")
            await page.wait_for_load_state("networkidle")
            return True
    except Exception as e:
        print(f"⚠️ Could not click hotel link: {e}")
    return False

async def search_travel_weekly(page, name):
    print(f"🔎 Searching Travel Weekly: {name}")
    await page.goto("https://www.travelweekly.com/Hotels", wait_until="domcontentloaded")
    
    # Nuclear Clear: Removes the 'dark-filter' that was intercepting your clicks
    await page.evaluate("""() => {
        const blockers = document.querySelectorAll('.onetrust-pc-dark-filter, #onetrust-consent-sdk');
        blockers.forEach(el => el.remove());
    }""")
    
    # Fill search box using placeholder from your previous screenshots
    search_box = page.locator("input[placeholder*='name or destination']").first
    await search_box.fill(name)
    
    # Force the search button click
    await page.locator("button:has-text('Search Hotels')").first.click(force=True)
    await page.wait_for_timeout(5000)

    # NEW LOGIC: Instead of snapping here, we check for the list and click through
    if "Selection" in await page.title() or await page.get_by_text("Hotel Search Selection").is_visible():
        clicked = await handle_selection_page(page, name)
        if clicked:
            # Wait for the actual property page to load after the click
            await page.wait_for_timeout(3000)

    # Find the final 'View Hotel Details' or GDS table
    details = page.get_by_text("View Hotel Details").first
    if await details.is_visible():
        await details.click(force=True)
        await page.wait_for_load_state("networkidle")
        return True
    
    return await page.get_by_text("GDS Reservation Codes").is_visible()
