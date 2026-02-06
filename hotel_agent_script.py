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

VERSION = "2026-02-05.7"
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
    "reservationdesk.com",
    "guestreservations.com",
    "hotelplanner.com",
    "reservations.com",
]

BOOKING_HINT_PATTERNS = [
    r"/book", r"/booking", r"/reservations", r"/reservation", r"/reserve", r"/availability",
    r"/book-now", r"/booknow", r"/rooms", r"/rates",
    r"synxis", r"travelclick", r"ihotelier", r"webrezpro", r"cloudbeds",
    r"secure-reservation", r"bookingengine", r"reservation",
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
    return text.strip().replace("```json", "").replace("```JSON", "").replace("```", "").strip()

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

def is_vendor_host(url: str) -> bool:
    host = (urlparse(url).netloc or "").lower()
    return any(h in host for h in VENDOR_HOST_HINTS)

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
    prompt = f"Provide the official website URL for '{hotel_name}'. Return ONLY JSON: {{\"url\": \"https://example.com\"}}"
    data = await gemini_json(prompt)
    if isinstance(data, dict):
        return (data.get("url") or "").strip() or None
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
        "Prefer vendor booking URLs.\n"
        f"HOTEL: {hotel_name}\n"
        f"OFFICIAL SITE (if known): {official_url or 'unknown'}\n"
    )
    data = await gemini_json(prompt)
    urls: List[str] = []
    if isinstance(data, dict):
        urls = data.get("booking_urls") or []
    out = []
    seen = set()
    for u in urls:
        nu = normalize_url(u, base=official_url or None)
        if nu and nu not in seen:
            seen.add(nu)
            out.append(nu)
    return out

# ----------------------------
# FREE DDG search (minimal)
# ----------------------------
def build_ddg_queries(hotel_name: str) -> List[str]:
    return [
        f"\"{hotel_name}\" booking",
        f"\"{hotel_name}\" reservations",
        f"\"{hotel_name}\" guest reservations booking",
        f"\"{hotel_name}\" reservationdesk booking",
        f"\"{hotel_name}\" iHotelier",
        f"\"{hotel_name}\" SynXis",
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
        if "uddg=" in href:
            m = re.search(r"uddg=([^&]+)", href)
            if m:
                try:
                    decoded = httpx.URL("https://x/?" + "uddg=" + m.group(1)).params.get("uddg")
                    if decoded:
                        links.append(str(decoded))
                except Exception:
                    pass
        elif href.startswith(("http://", "https://")):
            links.append(href)
    out = []
    seen = set()
    for u in links:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

def score_url(url: str) -> int:
    s = url.lower()
    score = 0
    if is_vendor_host(url):
        score += 100
    if any(k in s for k in ["synxis", "ihotelier", "travelclick", "secure-reservation", "bookingengine"]):
        score += 50
    if any(k in s for k in ["/booking", "/reservations", "/reservation", "/availability", "/book"]):
        score += 20
    return score

# ----------------------------
# Playwright attempt loop (verbose)
# ----------------------------
async def try_booking_candidates(candidates: List[str], max_tries: int = 15) -> str:
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

                    final_url = page.url
                    title = await page.title()
                    html = await page.content()

                    print(f"   ↳ FINAL URL: {final_url}")
                    print(f"   ↳ TITLE: {title}")

                    # Always save a screenshot for each try (so you can inspect)
                    await page.screenshot(path=os.path.join(ART_DIR, f"BOOKING_TRY_{tag}.png"), full_page=True)
                    write_text(f"BOOKING_TRY_{tag}.html", html[:200000])

                    if looks_like_bot_block(html):
                        print(f"   🧱 BLOCKED (verification)")
                        continue

                    # More forgiving booking detection:
                    # If vendor host OR URL contains booking/reservation, we accept.
                    is_booking = (
                        looks_like_booking_ui(html)
                        or is_vendor_host(final_url)
                        or "/booking" in final_url.lower()
                        or "/reservations" in final_url.lower()
                        or "/reservation" in final_url.lower()
                    )

                    if is_booking:
                        print(f"   ✅ BOOKING ENGINE ACCEPTED on TRY {tag}")
                        await page.screenshot(path=os.path.join(ART_DIR, "BOOKING_ENGINE.png"), full_page=True)
                        write_text("BOOKING_ENGINE_URL.txt", final_url + "\n")
                        return final_url

                    print(f"   ⚠️ Not identified as booking engine, continuing...")

                except Exception as e:
                    print(f"   ⚠️ ERROR: {repr(e)}")
                    write_text(f"BOOKING_TRY_{tag}_ERROR.txt", repr(e))
                    continue

        finally:
            await browser.close()

    return ""

# ----------------------------
# MAIN
# ----------------------------
async def main() -> None:
    print("✅ ENTERED main()")

    if not EMAIL_INPUT:
        write_text("RUN_STATUS.txt", "EMAIL_INPUT missing\n")
        return

    hotel_name = await extract_hotel_name(EMAIL_INPUT)
    print(f"🏨 Property: {hotel_name}")

    chain_code = await gemini_chain_code_only(hotel_name) if client else None
    write_text("CHAIN_CODE.txt", (chain_code or "UNKNOWN") + "\n")
    print(f"✅ Chain code: {chain_code or 'UNKNOWN'}")

    official_url = await gemini_official_url(hotel_name) if client else None
    official_url = normalize_url(official_url) if official_url else ""
    write_json("PROPERTY_META.json", {"hotel": hotel_name, "official_url": official_url})

    candidates: List[str] = []

    # Pull free search results (DDG)
    for q in build_ddg_queries(hotel_name):
        links = await ddg_html_search(q)
        for u in links[:20]:
            if is_vendor_host(u) or likely_booking_url(u):
                candidates.append(u)

    # Gemini suggestions as extra
    candidates.extend(await gemini_booking_urls(hotel_name, official_url or None) if client else [])

    # Add common paths on official URL as last resort
    if official_url:
        parsed = urlparse(official_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        candidates.extend([
            base + "/book",
            base + "/booking",
            base + "/reservations",
            base + "/availability",
        ])

    # Normalize + de-dupe
    cleaned = []
    seen = set()
    for u in candidates:
        nu = normalize_url(u, base=official_url if official_url else None)
        if nu and nu not in seen:
            seen.add(nu)
            cleaned.append(nu)

    cleaned.sort(key=score_url, reverse=True)

    write_json("BOOKING_CANDIDATES.json", {"hotel": hotel_name, "candidates": cleaned})

    booking_url = await try_booking_candidates(cleaned, max_tries=15)

    if booking_url:
        write_text("RUN_STATUS.txt", f"booking_url={booking_url}\n")
        print(f"🎯 SUCCESS: {booking_url}")
    else:
        write_text("RUN_STATUS.txt", "no_accessible_booking_engine\n")
        print("❌ No accessible booking engine found (without verification).")

if __name__ == "__main__":
    print("✅ ENTERED __main__")
    try:
        asyncio.run(main())
    except Exception as e:
        write_text("CRASH.txt", f"Script crashed:\n{repr(e)}\n")
        raise


