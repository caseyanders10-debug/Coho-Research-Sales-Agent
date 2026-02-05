import asyncio
import os
import json
import re
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urljoin, urlparse, quote_plus

import httpx
from bs4 import BeautifulSoup
from google import genai
from playwright.async_api import async_playwright

# ==========================================================
# GOALS:
# 1) Chain code -> CHAIN_CODE.txt
# 2) Booking engine screenshot -> BOOKING_ENGINE.png
#    If blocked, capture evidence per candidate and keep trying.
#
# This version DOES NOT rely on search engines.
# It tries multiple sources for booking URLs and then actually
# opens candidates in Playwright until it finds the booking UI.
#
# Required env:
#   GEMINI_API_KEY
#   EMAIL_INPUT
#
# requirements.txt:
#   playwright
#   google-genai
#   httpx
#   beautifulsoup4
# ==========================================================

VERSION = "2026-02-05.4"
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

# Always create at least one artifact immediately
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

BOOKING_HINT_PATTERNS = [
    r"/book", r"/booking", r"/reservations", r"/reservation", r"/reserve", r"/availability",
    r"/book-now", r"/booknow", r"/rooms", r"/rates",
    r"synxis", r"sabre", r"travelclick", r"ihotelier", r"webrezpro", r"cloudbeds",
    r"roomkey", r"stayntouch", r"opera", r"reservations\.", r"be\.", r"bookingengine", r"rez",
]

BOOKING_UI_SIGNALS = [
    "check-in", "check in", "check-out", "check out",
    "arrival", "departure",
    "promo code", "rate", "rates",
    "rooms", "guests",
    "availability",
    "book now", "reserve",
]

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

def looks_like_bot_block(html: str) -> bool:
    if not html:
        return False
    s = html.lower()
    return any(p in s for p in BOT_BLOCK_PATTERNS)

def looks_like_booking_ui(html: str) -> bool:
    if not html:
        return False
    s = html.lower()
    hits = sum(sig in s for sig in BOOKING_UI_SIGNALS)
    return hits >= 2

def likely_booking_url(url: str) -> bool:
    s = (url or "").lower()
    return any(re.search(p, s) for p in BOOKING_HINT_PATTERNS)

def normalize_url(u: str, base: Optional[str] = None) -> str:
    u = (u or "").strip()
    if not u:
        return ""
    if base and u.startswith("/"):
        return urljoin(base, u)
    if u.startswith("//"):
        return "https:" + u
    if not u.startswith(("http://", "https://")):
        return "https://" + u
    return u

async def fetch(url: str, timeout_s: float = 25.0) -> Tuple[int, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    async with httpx.AsyncClient(follow_redirects=True, headers=headers, timeout=timeout_s) as c:
        r = await c.get(url)
        return r.status_code, (r.text or "")

# ----------------------------
# Gemini helpers
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

async def gemini_official_url(hotel_name: str) -> Optional[str]:
    if not client:
        return None
    prompt = (
        f"Provide the official website URL for '{hotel_name}'. "
        "Return ONLY JSON: {\"url\": \"https://example.com\"} (use null if unknown)."
    )
    data = await gemini_json(prompt)
    if isinstance(data, dict):
        u = (data.get("url") or "").strip()
        return u or None
    return None

async def gemini_chain_code_only(hotel_name: str) -> Optional[str]:
    if not client:
        return None
    prompt = (
        f"What is the GDS chain code for '{hotel_name}'?\n"
        "Return ONLY JSON: {\"chain_code\": \"PW\"}.\n"
        "chain_code must be 2-3 uppercase letters, or null if unknown."
    )
    data = await gemini_json(prompt)
    if isinstance(data, dict):
        cc = (data.get("chain_code") or "").strip()
        return cc or None
    return None

async def gemini_booking_urls(hotel_name: str, official_url: Optional[str]) -> List[str]:
    if not client:
        return []
    prompt = (
        "Find the DIRECT booking engine URL(s) for this hotel (page where guests pick dates/rooms).\n"
        "Return ONLY JSON: {\"booking_urls\": [\"https://...\", \"https://...\"]}.\n"
        "Prefer vendor booking URLs (SynXis/iHotelier/TravelClick/Cloudbeds/WebRezPro/etc).\n\n"
        f"HOTEL: {hotel_name}\n"
        f"OFFICIAL SITE (if known): {official_url or 'unknown'}\n"
    )
    data = await gemini_json(prompt)
    urls: List[str] = []
    if isinstance(data, dict):
        urls = data.get("booking_urls") or []
    out = []
    seen = set()
    base = official_url or ""
    for u in urls:
        nu = normalize_url(u, base=base)
        if nu and nu not in seen:
            seen.add(nu)
            out.append(nu)
    return out

# ----------------------------
# TravelWeekly helper (only used to scrape candidate links if reachable)
# ----------------------------
async def travelweekly_internal_search(hotel_name: str) -> Optional[str]:
    q = quote_plus(hotel_name)
    url = f"https://www.travelweekly.com/Search?q={q}"
    status, html = await fetch(url, timeout_s=25.0)
    if status >= 400 or not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/Hotels/" in href:
            return urljoin("https://www.travelweekly.com", href)
    return None

def extract_booking_links_from_html(html: str, base_url: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href:
            continue
        full = href if href.startswith(("http://", "https://")) else urljoin(base_url, href)
        if likely_booking_url(full):
            links.append(full)
    # de-dupe
    out = []
    seen = set()
    for u in links:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

# ----------------------------
# Booking candidates (big list)
# ----------------------------
def common_booking_paths(official_url: str) -> List[str]:
    parsed = urlparse(official_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    return [
        base + "/book",
        base + "/booking",
        base + "/reservations",
        base + "/reservation",
        base + "/reserve",
        base + "/availability",
        base + "/book-now",
        base + "/booknow",
        base + "/rooms",
        base + "/rates",
    ]

# ----------------------------
# Playwright attempt loop
# ----------------------------
async def try_booking_candidates_with_playwright(candidates: List[str], max_tries: int = 10) -> str:
    """
    Attempts the first N candidates in Playwright.
    Saves evidence for each attempt.
    Returns the winning booking URL if successful, else "".
    """
    attempts = candidates[:max_tries]
    print(f"🧪 Trying {len(attempts)} booking candidates in Playwright...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        try:
            for i, url in enumerate(attempts, start=1):
                tag = f"{i:02d}"
                print(f"➡️ TRY {tag}: {url}")

                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    await asyncio.sleep(3)
                    html = await page.content()

                    if looks_like_bot_block(html):
                        print(f"🧱 BLOCKED {tag}")
                        await page.screenshot(path=os.path.join(ART_DIR, f"BOOKING_TRY_{tag}_BLOCKED.png"), full_page=True)
                        write_text(f"BOOKING_TRY_{tag}_BLOCKED.html", html)
                        continue

                    # If it looks like booking UI, we won.
                    if looks_like_booking_ui(html) or likely_booking_url(url):
                        print(f"✅ BOOKING ENGINE FOUND on TRY {tag}")
                        await page.screenshot(path=os.path.join(ART_DIR, "BOOKING_ENGINE.png"), full_page=True)
                        return url

                    # Not blocked, but not clearly booking UI
                    await page.screenshot(path=os.path.join(ART_DIR, f"BOOKING_TRY_{tag}_NOT_BOOKING.png"), full_page=True)
                    write_text(f"BOOKING_TRY_{tag}_NOT_BOOKING.html", html[:200000])

                except Exception as e:
                    print(f"⚠️ ERROR {tag}: {repr(e)}")
                    try:
                        await page.screenshot(path=os.path.join(ART_DIR, f"BOOKING_TRY_{tag}_ERROR.png"), full_page=True)
                    except Exception:
                        pass
                    continue

        finally:
            await browser.close()

    return ""

# ----------------------------
# Main
# ----------------------------
async def main() -> None:
    print("✅ ENTERED main()")

    if not EMAIL_INPUT:
        write_text("RUN_STATUS.txt", "EMAIL_INPUT missing\n")
        print("❌ EMAIL_INPUT missing.")
        return

    hotel_name = await extract_hotel_name(EMAIL_INPUT)
    print(f"🏨 Property: {hotel_name}")

    # Chain code: ask Gemini for chain code only (fast and focused)
    chain_code = await gemini_chain_code_only(hotel_name) if client else None
    if chain_code:
        write_text("CHAIN_CODE.txt", chain_code + "\n")
        print(f"✅ Chain code: {chain_code}")
    else:
        write_text("CHAIN_CODE.txt", "UNKNOWN\n")
        print("❌ Chain code not found.")

    # Official URL (for extra candidates)
    official_url = await gemini_official_url(hotel_name) if client else None
    official_url = normalize_url(official_url) if official_url else ""
    write_json("PROPERTY_META.json", {"hotel": hotel_name, "official_url": official_url})

    # Build booking candidates
    candidates: List[str] = []

    # 1) Try TravelWeekly internal search (sometimes gives booking-related links)
    tw_url = await travelweekly_internal_search(hotel_name)
    tw_html = ""
    if tw_url:
        print(f"📰 TravelWeekly result: {tw_url}")
        status, tw_html = await fetch(tw_url, timeout_s=25.0)
        if status < 400 and tw_html:
            candidates.extend(extract_booking_links_from_html(tw_html, tw_url))
        else:
            print(f"⚠️ TravelWeekly fetch failed: HTTP {status}")

    # 2) Gemini booking URLs (most important)
    candidates.extend(await gemini_booking_urls(hotel_name, official_url or None) if client else [])

    # 3) Common paths on official domain (even if homepage is blocked, sometimes /reservations works)
    if official_url:
        candidates.extend(common_booking_paths(official_url))

    # Normalize + de-dupe
    cleaned: List[str] = []
    seen = set()
    for u in candidates:
        nu = normalize_url(u, base=official_url if official_url else None)
        if not nu:
            continue
        if nu in seen:
            continue
        seen.add(nu)
        cleaned.append(nu)

    # Save candidates
    write_json("BOOKING_CANDIDATES.json", {"hotel": hotel_name, "official_url": official_url, "candidates": cleaned})

    # Now actually TRY candidates in Playwright and keep evidence
    booking_url = await try_booking_candidates_with_playwright(cleaned, max_tries=10)

    if booking_url:
        write_text("RUN_STATUS.txt", f"booking_url={booking_url}\n")
        print(f"🎯 SUCCESS: {booking_url}")
    else:
        write_text("RUN_STATUS.txt", "no_accessible_booking_engine\n")
        print("❌ No accessible booking engine found in top candidates (without verification).")

if __name__ == "__main__":
    print("✅ ENTERED __main__")
    try:
        asyncio.run(main())
    except Exception as e:
        write_text("CRASH.txt", f"Script crashed:\n{repr(e)}\n")
        raise


