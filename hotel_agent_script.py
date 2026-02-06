import asyncio
import os
import json
import re
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urljoin, urlparse, quote_plus

import httpx
from bs4 import BeautifulSoup
from google import genai
from openpyxl import Workbook


# ==============================
# PURPOSE (Step 1 + Step 2 only)
# Step 1: Chain code -> CHAIN_CODE.txt
# Step 2: Booking engine link -> BOOKING_ENGINE_URL.txt
# Also writes an Excel file -> HOTEL_OUTPUT.xlsx (single-row for now)
# ==============================

VERSION = "2026-02-05.8"
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

def write_excel_single_row(
    filename: str,
    hotel_name: str,
    chain_code: str,
    booking_url: str,
    notes: str
) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Hotels"

    headers = ["Hotel Name", "GDS Chain Code", "Booking Engine URL", "Notes"]
    ws.append(headers)
    ws.append([hotel_name, chain_code, booking_url, notes])

    # basic column width
    widths = [40, 16, 70, 50]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i)].width = w

    wb.save(os.path.join(ART_DIR, filename))

# Always create an artifact immediately so upload never fails
write_text("RUN_STATUS.txt", "starting\n")

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# Bot-wall signals (we do NOT bypass; we only detect and avoid)
BOT_BLOCK_PATTERNS = [
    "are you a human",
    "verify you are human",
    "verification required",
    "captcha",
    "access denied",
    "unusual traffic",
    "cloudflare",
    "checking your browser",
]

# Vendor hints: these are often the *real* booking engine providers
VENDOR_HOST_HINTS = [
    "synxis.com",
    "travelclick.com",
    "ihotelier.com",
    "secure-reservation",
    "reservations.",
    "be.",
    "cloudbeds.com",
    "webrezpro.com",
    "stayntouch",
    "roomkey",
    "bookingsuite",
    "bookingengine",
]

# Affiliate/OTA hints (still “booking pages” but not official engine)
AFFILIATE_HINTS = [
    "guestreservations.com",
    "reservationdesk.com",
    "hotelplanner.com",
    "reservations.com",
]

BOOKING_HINT_PATTERNS = [
    r"/booking", r"/book", r"/reservations", r"/reservation", r"/reserve", r"/availability",
    r"synxis", r"travelclick", r"ihotelier", r"webrezpro", r"cloudbeds",
    r"secure-reservation", r"bookingengine",
]

def strip_code_fences(text: str) -> str:
    if not text:
        return ""
    return text.strip().replace("```json", "").replace("```JSON", "").replace("```", "").strip()

def looks_like_bot_block(html: str) -> bool:
    if not html:
        return False
    s = html.lower()
    return any(p in s for p in BOT_BLOCK_PATTERNS)

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

def host(url: str) -> str:
    return (urlparse(url).netloc or "").lower()

def is_vendor(url: str) -> bool:
    h = host(url)
    return any(v in h for v in VENDOR_HOST_HINTS)

def is_affiliate(url: str) -> bool:
    h = host(url)
    return any(a in h for a in AFFILIATE_HINTS)

def looks_like_booking_url(url: str) -> bool:
    s = (url or "").lower()
    return any(re.search(p, s) for p in BOOKING_HINT_PATTERNS)

async def fetch(url: str, timeout_s: float = 25.0) -> Tuple[int, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    async with httpx.AsyncClient(follow_redirects=True, headers=headers, timeout=timeout_s) as c:
        r = await c.get(url)
        return r.status_code, (r.text or "")

# ----------------------------
# Gemini helpers (focused)
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

async def gemini_chain_code_only(hotel_name: str) -> str:
    if not client:
        return "UNKNOWN"
    prompt = (
        f"What is the GDS chain code for '{hotel_name}'?\n"
        "Return ONLY JSON: {\"chain_code\": \"PW\"}.\n"
        "chain_code must be 2-3 uppercase letters, or null if unknown."
    )
    data = await gemini_json(prompt)
    cc = ""
    if isinstance(data, dict):
        cc = (data.get("chain_code") or "").strip()
    return cc or "UNKNOWN"

async def gemini_booking_urls_only(hotel_name: str) -> List[str]:
    """
    IMPORTANT: We are not asking Gemini to browse; just to provide likely direct booking URLs.
    """
    if not client:
        return []
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
    out, seen = [], set()
    for u in urls:
        nu = normalize_url(u)
        if nu and nu not in seen:
            seen.add(nu)
            out.append(nu)
    return out

# ----------------------------
# TravelWeekly internal search (free)
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
        # Only hotel detail pages, not travel news
        if "/Hotels/" in href and "/Travel-News/" not in href:
            return urljoin("https://www.travelweekly.com", href)
    return None

def extract_bookingish_links_from_html(html: str, base_url: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    found = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href:
            continue
        full = href if href.startswith(("http://", "https://")) else urljoin(base_url, href)
        if looks_like_booking_url(full) or is_vendor(full) or is_affiliate(full):
            found.append(full)

    # de-dupe preserve order
    out, seen = [], set()
    for u in found:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

# ----------------------------
# FREE web discovery: DuckDuckGo HTML + Lite (no API key)
# ----------------------------
def ddg_queries(hotel_name: str) -> List[str]:
    return [
        f"\"{hotel_name}\" booking engine",
        f"\"{hotel_name}\" reservations",
        f"\"{hotel_name}\" synxis",
        f"\"{hotel_name}\" ihotelier",
        f"\"{hotel_name}\" travelclick",
        f"\"{hotel_name}\" cloudbeds",
        f"\"{hotel_name}\" webrezpro",
    ]

async def ddg_html_search(query: str) -> List[str]:
    q = quote_plus(query)
    url = f"https://duckduckgo.com/html/?q={q}"
    status, html = await fetch(url, timeout_s=25.0)
    if status >= 400 or not html:
        return []
    soup = BeautifulSoup(html, "html.parser")

    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # often direct links appear; sometimes DDG wraps them, but we keep it simple
        if href.startswith(("http://", "https://")):
            links.append(href)

    out, seen = [], set()
    for u in links:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

async def ddg_lite_search(query: str) -> List[str]:
    q = quote_plus(query)
    url = f"https://lite.duckduckgo.com/lite/?q={q}"
    status, html = await fetch(url, timeout_s=25.0)
    if status >= 400 or not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("http://", "https://")):
            links.append(href)
    out, seen = [], set()
    for u in links:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

# ----------------------------
# Choose best booking engine link
# ----------------------------
def score_candidate(url: str) -> int:
    """
    Prefer vendor booking engines.
    Fall back to affiliate booking pages if that’s all we can find.
    """
    s = url.lower()
    score = 0
    if is_vendor(url):
        score += 100
    if is_affiliate(url):
        score += 25
    if "/booking" in s or "/reservations" in s or "/reservation" in s:
        score += 15
    if looks_like_booking_url(url):
        score += 10
    return score

async def filter_reachable_nonblocked(urls: List[str], max_check: int = 25) -> List[Tuple[str, int]]:
    """
    Try to fetch pages (httpx). Remove obvious bot walls.
    Return list of (final_url, score) in preference order.
    """
    results: List[Tuple[str, int]] = []
    for u in urls[:max_check]:
        try:
            status, html = await fetch(u, timeout_s=20.0)
            if status >= 400 or not html:
                continue
            if looks_like_bot_block(html):
                continue
            # If the page contains vendor host links, treat as strong
            # (sometimes the booking link is embedded in HTML)
            bonus = 0
            lower = html.lower()
            if any(v in lower for v in VENDOR_HOST_HINTS):
                bonus += 50
            results.append((u, score_candidate(u) + bonus))
        except Exception:
            continue
    results.sort(key=lambda x: x[1], reverse=True)
    return results

async def main() -> None:
    print("✅ ENTERED main()")

    if not EMAIL_INPUT:
        write_text("RUN_STATUS.txt", "EMAIL_INPUT missing\n")
        print("❌ EMAIL_INPUT missing.")
        return

    hotel_name = await extract_hotel_name(EMAIL_INPUT)
    print(f"🏨 Property: {hotel_name}")

    # Step 1: Chain code
    chain_code = await gemini_chain_code_only(hotel_name)
    write_text("CHAIN_CODE.txt", chain_code + "\n")
    print(f"✅ Chain code: {chain_code}")

    # Step 2: Booking engine link discovery (free sources)
    candidates: List[str] = []

    # A) TravelWeekly hotel page -> booking-ish links
    tw_url = await travelweekly_internal_search(hotel_name)
    if tw_url:
        print(f"📰 TravelWeekly hotel page: {tw_url}")
        status, tw_html = await fetch(tw_url, timeout_s=25.0)
        if status < 400 and tw_html:
            tw_links = extract_bookingish_links_from_html(tw_html, tw_url)
            candidates.extend(tw_links)

    # B) DuckDuckGo results (HTML + Lite fallback)
    for q in ddg_queries(hotel_name):
        links = await ddg_html_search(q)
        if not links:
            links = await ddg_lite_search(q)
        for u in links[:20]:
            u2 = normalize_url(u)
            if u2 and (looks_like_booking_url(u2) or is_vendor(u2) or is_affiliate(u2)):
                candidates.append(u2)

    # C) Gemini suggestions (not browsing, just candidate URLs)
    candidates.extend(await gemini_booking_urls_only(hotel_name))

    # De-dupe
    dedup, seen = [], set()
    for u in candidates:
        u = normalize_url(u)
        if u and u not in seen:
            seen.add(u)
            dedup.append(u)

    # Save what we tried
    write_json("BOOKING_CANDIDATES.json", {"hotel": hotel_name, "candidates": dedup})

    # Pick best reachable non-bot-wall link
    ranked = sorted([(u, score_candidate(u)) for u in dedup], key=lambda x: x[1], reverse=True)
    write_json("BOOKING_CANDIDATES_RANKED.json", {"hotel": hotel_name, "ranked": ranked})

    reachable_ranked = await filter_reachable_nonblocked([u for u, _ in ranked], max_check=25)
    booking_url = reachable_ranked[0][0] if reachable_ranked else ""

    notes = ""
    if booking_url:
        notes = "Booking URL found (best reachable non-bot-wall candidate)."
        print(f"✅ Booking URL: {booking_url}")
        write_text("BOOKING_ENGINE_URL.txt", booking_url + "\n")
        write_text("RUN_STATUS.txt", f"booking_url={booking_url}\n")
    else:
        notes = "No reachable booking URL found (candidates blocked or unreachable)."
        print("❌ Booking URL not found (reachable).")
        write_text("BOOKING_ENGINE_URL.txt", "NOT_FOUND\n")
        write_text("RUN_STATUS.txt", "no_booking_url_found\n")

    # Write the Excel row (your end goal format)
    write_excel_single_row(
        "HOTEL_OUTPUT.xlsx",
        hotel_name=hotel_name,
        chain_code=chain_code,
        booking_url=booking_url or "NOT_FOUND",
        notes=notes,
    )
    print("✅ Saved: screenshots/HOTEL_OUTPUT.xlsx")

if __name__ == "__main__":
    print("✅ ENTERED __main__")
    try:
        asyncio.run(main())
    except Exception as e:
        write_text("CRASH.txt", f"Script crashed:\n{repr(e)}\n")
        raise



