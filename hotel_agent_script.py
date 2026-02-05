import asyncio
import os
import json
import re
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urljoin, quote_plus

import httpx
from bs4 import BeautifulSoup
from google import genai
from playwright.async_api import async_playwright

# ==========================================================
# GOAL (per your requirements):
# 1) Output ONLY the CHAIN CODE you care about (CHAIN_CODE.txt)
# 2) Navigate to the BOOKING ENGINE and screenshot the booking UI
#    (BOOKING_ENGINE.png), OR record proof it's protected.
#
# Design:
# - Prefer TravelWeekly as authoritative directory
# - Use Gemini ONLY to locate the TravelWeekly page URL
# - Extract chain code deterministically from TravelWeekly HTML
# - Extract booking engine candidates from TravelWeekly outbound links
# - Only then try Gemini booking candidates/common paths
#
# Required env:
#   GEMINI_API_KEY
#   EMAIL_INPUT  (hotel name OR raw email body)
#
# Required requirements.txt:
#   playwright
#   google-genai
#   httpx
#   beautifulsoup4
# ==========================================================

VERSION = "2026-02-05.3"
print(f"🔥 HOTEL AGENT VERSION: {VERSION} 🔥")

EMAIL_INPUT = os.environ.get("EMAIL_INPUT", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

ART_DIR = "screenshots"
os.makedirs(ART_DIR, exist_ok=True)

def write_text(filename: str, content: str) -> None:
    with open(os.path.join(ART_DIR, filename), "w", encoding="utf-8") as f:
        f.write(content)

def write_json(filename: str, obj: Any) -> None:
    with open(os.path.join(ART_DIR, filename), "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)

# Always create an artifact immediately
write_text("RUN_STATUS.txt", "starting\n")

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

BOT_BLOCK_PATTERNS = [
    "are you a human",
    "verify you are human",
    "verification required",
    "captcha",
    "access denied",
    "unusual traffic",
    "press and hold",
    "cloudflare",
    "checking your browser",
]

# “Likely booking engine” patterns — vendor + common paths
BOOKING_HINT_PATTERNS = [
    r"/book", r"/booking", r"/reservations", r"/reservation", r"/reserve", r"/availability",
    r"synxis", r"sabre", r"travelclick", r"ihotelier", r"webrezpro", r"cloudbeds",
    r"roomkey", r"stayntouch", r"opera", r"reservations\.", r"be\.", r"bookingengine", r"rez",
]

# Booking UI signals
BOOKING_UI_SIGNALS = [
    "check-in", "check in", "check-out", "check out",
    "arrival", "departure",
    "promo code", "rate", "rates",
    "rooms", "guests",
    "availability",
    "book now", "reserve",
]

def looks_like_bot_block(html: str) -> bool:
    if not html:
        return False
    s = html.lower()
    return any(p in s for p in BOT_BLOCK_PATTERNS)

def likely_booking_url(url: str) -> bool:
    s = (url or "").lower()
    return any(re.search(p, s) for p in BOOKING_HINT_PATTERNS)

def looks_like_booking_ui(html: str) -> bool:
    if not html:
        return False
    s = html.lower()
    hits = sum(sig in s for sig in BOOKING_UI_SIGNALS)
    return hits >= 2

def strip_code_fences(text: str) -> str:
    if not text:
        return ""
    return (
        text.strip()
        .replace("```json", "")
        .replace("```JSON", "")
        .replace("```", "")
        .strip()
    )

async def fetch(url: str, timeout_s: float = 25.0) -> Tuple[int, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    async with httpx.AsyncClient(follow_redirects=True, headers=headers, timeout=timeout_s) as c:
        r = await c.get(url)
        return r.status_code, (r.text or "")

# ----------------------------
# Gemini helpers (URL discovery only)
# ----------------------------
async def gemini_json(prompt: str, retries: int = 3, base_delay_s: int = 12) -> Optional[Dict[str, Any]]:
    if not client:
        return None
    for attempt in range(1, retries + 1):
        try:
            print(f"🤖 Gemini request (attempt {attempt}/{retries})...")
            resp = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
            raw = strip_code_fences(getattr(resp, "text", "") or "")
            return json.loads(raw)
        except Exception as e:
            print(f"⏳ Gemini attempt {attempt} failed: {e}")
            await asyncio.sleep(base_delay_s * attempt)
    return None

async def extract_hotel_name(raw_email_or_name: str) -> str:
    # If already a clean name, don’t waste tokens
    if raw_email_or_name and len(raw_email_or_name) <= 140 and "\n" not in raw_email_or_name:
        return raw_email_or_name.strip()

    if not client:
        return "UNKNOWN_PROPERTY"

    prompt = (
        "Extract the hotel/property name from the email below.\n"
        "Return ONLY JSON like: {\"hotel_name\": \"The Reeds at Shelter Haven\"}.\n\n"
        f"EMAIL:\n{raw_email_or_name}"
    )
    data = await gemini_json(prompt)
    name = (data or {}).get("hotel_name") if isinstance(data, dict) else None
    return (name or "UNKNOWN_PROPERTY").strip()

async def gemini_find_travelweekly_url(hotel_name: str) -> Optional[str]:
    """
    We use Gemini ONLY to locate the TravelWeekly hotel detail page URL.
    This avoids needing search engines (CAPTCHA), and TW is usually crawlable.
    """
    if not client:
        return None
    prompt = (
        "Find the TravelWeekly Hotels detail page URL for this hotel.\n"
        "Return ONLY JSON: {\"travelweekly_url\": \"https://www.travelweekly.com/Hotels/...\"}.\n"
        "If unsure, return {\"travelweekly_url\": null}.\n\n"
        f"HOTEL: {hotel_name}\n"
    )
    data = await gemini_json(prompt)
    if not isinstance(data, dict):
        return None
    u = (data.get("travelweekly_url") or "").strip()
    return u or None

# ----------------------------
# TravelWeekly parsing (deterministic)
# ----------------------------
def parse_chain_code_from_travelweekly(html: str) -> Optional[str]:
    """
    TravelWeekly often formats like:
      Sabre: PW 192496
      Amadeus: PW WWDRSH
    We only need the CHAIN CODE (e.g., PW).
    """
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    # Look for "Sabre:" etc and capture chain token before the code
    # Example: "Sabre: PW 192496" -> PW
    patterns = [
        r"Sabre:\s*([A-Z]{2,3})\s+[A-Z0-9]{3,12}",
        r"Amadeus:\s*([A-Z]{2,3})\s+[A-Z0-9]{3,12}",
        r"Worldspan:\s*([A-Z]{2,3})\s+[A-Z0-9]{3,12}",
        r"Galileo/Apollo:\s*([A-Z]{2,3})\s+[A-Z0-9]{3,12}",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()

    return None

def extract_booking_candidates_from_travelweekly(html: str) -> List[str]:
    """
    Pull outbound links from TravelWeekly that look like booking/reservations/vendors.
    """
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")

    cands: List[str] = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href:
            continue
        full = href
        if href.startswith("/"):
            full = urljoin("https://www.travelweekly.com", href)
        if full and likely_booking_url(full):
            if full not in seen:
                seen.add(full)
                cands.append(full)

    return cands

async def travelweekly_search_fallback(hotel_name: str) -> Optional[str]:
    """
    If Gemini didn’t find TW URL, try TW internal search (not Google/Bing UI).
    """
    q = quote_plus(hotel_name)
    search_url = f"https://www.travelweekly.com/Search?q={q}"

    status, html = await fetch(search_url)
    if status >= 400 or not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/Hotels/" in href:
            links.append(urljoin("https://www.travelweekly.com", href))

    # De-dupe preserve order
    seen = set()
    for u in links:
        if u not in seen:
            seen.add(u)
            return u
    return None

# ----------------------------
# Booking engine selection + screenshot
# ----------------------------
async def choose_accessible_booking_url(candidates: List[str]) -> str:
    """
    Pick the first candidate that:
    - returns non-4xx
    - is not a verification page
    - looks like booking UI OR is strongly booking-like
    """
    for url in candidates:
        try:
            status, html = await fetch(url, timeout_s=20.0)
            if status >= 400:
                continue
            if looks_like_bot_block(html):
                continue
            if looks_like_booking_ui(html) or likely_booking_url(url):
                return url
        except Exception:
            continue
    return ""

async def screenshot_booking_engine(url: str) -> None:
    """
    Navigate to booking engine and screenshot booking UI.
    If blocked, capture BLOCKED evidence.
    """
    print(f"🧾 Opening booking engine: {url}")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(3)
            html = await page.content()

            if looks_like_bot_block(html):
                print("🧱 Booking engine protected (verification). Capturing evidence.")
                await page.screenshot(path=os.path.join(ART_DIR, "BOOKING_BLOCKED.png"), full_page=True)
                write_text("BOOKING_BLOCKED.html", html)
            else:
                await page.screenshot(path=os.path.join(ART_DIR, "BOOKING_ENGINE.png"), full_page=True)
                print("📸 Saved: screenshots/BOOKING_ENGINE.png")
        finally:
            await browser.close()

# ----------------------------
# Main
# ----------------------------
async def main() -> None:
    write_text("RUN_STATUS.txt", "entered_main\n")
    print("✅ ENTERED main()")

    if not EMAIL_INPUT:
        write_text("RUN_STATUS.txt", "EMAIL_INPUT missing\n")
        print("❌ EMAIL_INPUT missing.")
        return

    hotel_name = await extract_hotel_name(EMAIL_INPUT)
    print(f"🏨 Property: {hotel_name}")
    write_json("PROPERTY.json", {"hotel": hotel_name})

    # --- Step 1: Find TravelWeekly hotel page URL (Gemini first, then TW internal search fallback)
    tw_url = await gemini_find_travelweekly_url(hotel_name) if client else None
    if not tw_url:
        print("ℹ️ Gemini did not provide TravelWeekly URL. Trying TravelWeekly internal search...")
        tw_url = await travelweekly_search_fallback(hotel_name)

    write_json("TRAVELWEEKLY_META.json", {"travelweekly_url": tw_url})

    tw_html = ""
    if tw_url:
        print(f"📰 TravelWeekly URL: {tw_url}")
        status, tw_html = await fetch(tw_url, timeout_s=25.0)
        if status >= 400 or not tw_html:
            print(f"⚠️ TravelWeekly fetch failed: HTTP {status}")
            tw_html = ""

    # --- Step 2: Extract CHAIN CODE (this is the ONLY GDS thing you want)
    chain_code = parse_chain_code_from_travelweekly(tw_html) if tw_html else None

    # If TravelWeekly failed, ask Gemini for chain code only as a last resort
    if not chain_code and client:
        print("ℹ️ TravelWeekly chain code not found. Asking Gemini for CHAIN CODE only...")
        prompt = (
            f"What is the GDS chain code for '{hotel_name}'?\n"
            "Return ONLY JSON: {\"chain_code\": \"PW\"}.\n"
            "chain_code must be 2-3 uppercase letters, or null if unknown."
        )
        data = await gemini_json(prompt)
        cc = (data or {}).get("chain_code") if isinstance(data, dict) else None
        chain_code = (cc or "").strip() or None

    if chain_code:
        write_text("CHAIN_CODE.txt", chain_code + "\n")
        print(f"✅ Chain code: {chain_code}")
    else:
        write_text("CHAIN_CODE.txt", "UNKNOWN\n")
        print("❌ Chain code not found.")

    # --- Step 3: Build booking engine candidates
    candidates: List[str] = []

    # 3a) From TravelWeekly outbound links (best chance to avoid protected official site)
    if tw_html:
        candidates.extend(extract_booking_candidates_from_travelweekly(tw_html))

    # 3b) Ask Gemini directly for booking engine URLs (does not require visiting official site)
    if client:
        prompt = (
            "Find the DIRECT booking engine URL(s) for this hotel (page where guests pick dates/rooms).\n"
            "Return ONLY JSON: {\"booking_urls\": [\"https://...\", \"https://...\"]}.\n"
            "Prefer vendor booking URLs (SynXis/iHotelier/TravelClick/Cloudbeds/WebRezPro/etc).\n\n"
            f"HOTEL: {hotel_name}\n"
        )
        data = await gemini_json(prompt)
        urls = []
        if isinstance(data, dict):
            urls = data.get("booking_urls") or []
        for u in urls:
            u = (u or "").strip()
            if u:
                candidates.append(u)

    # De-dupe + keep only things that look booking-related
    cleaned: List[str] = []
    seen = set()
    for u in candidates:
        u = u.strip()
        if not u:
            continue
        # Normalize relative URLs to TravelWeekly base if needed
        if u.startswith("/"):
            u = urljoin("https://www.travelweekly.com", u)
        if not u.startswith(("http://", "https://")):
            u = "https://" + u
        if u not in seen and likely_booking_url(u):
            seen.add(u)
            cleaned.append(u)

    write_json("BOOKING_CANDIDATES.json", {"hotel": hotel_name, "candidates": cleaned})

    # --- Step 4: Choose and screenshot booking engine
    booking_url = await choose_accessible_booking_url(cleaned) if cleaned else ""
    if booking_url:
        write_text("RUN_STATUS.txt", f"booking_url={booking_url}\n")
        await screenshot_booking_engine(booking_url)
    else:
        # If nothing accessible, record that clearly
        write_text("RUN_STATUS.txt", "no_accessible_booking_engine\n")
        print("❌ No accessible booking engine URL found (without verification).")

        # Save evidence (TravelWeekly HTML) for debugging if available
        if tw_html:
            write_text("TRAVELWEEKLY_PAGE.html", tw_html[:200000])

if __name__ == "__main__":
    print("✅ ENTERED __main__")
    try:
        asyncio.run(main())
    except Exception as e:
        # Always produce a crash artifact
        write_text("CRASH.txt", f"Script crashed:\n{repr(e)}\n")
        raise


