import asyncio
import os
import json
import re
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urlparse, urljoin, quote_plus

import httpx
from bs4 import BeautifulSoup
from google import genai
from playwright.async_api import async_playwright

# ----------------------------
# Configuration / Inputs
# ----------------------------
EMAIL_INPUT = os.environ.get("EMAIL_INPUT", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

# Gemini client (used for name extraction, URL discovery, and (optionally) GDS guess)
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# Directory for artifacts
ART_DIR = "screenshots"
os.makedirs(ART_DIR, exist_ok=True)

# ----------------------------
# Detection patterns
# ----------------------------
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

# ----------------------------
# Small helpers
# ----------------------------
def _strip_code_fences(text: str) -> str:
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
    if base and not u.startswith(("http://", "https://")):
        return urljoin(base, u)
    if u.startswith("//"):
        return "https:" + u
    if not u.startswith(("http://", "https://")):
        return "https://" + u
    return u

def write_text(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def write_json(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)

# ----------------------------
# HTTP fetch
# ----------------------------
async def fetch(url: str, timeout_s: float = 20.0) -> Tuple[int, str]:
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
async def gemini_json(prompt: str, retries: int = 3, base_delay_s: int = 10) -> Optional[Dict[str, Any]]:
    """
    Gemini call that returns a JSON object, with retry/backoff.
    Handles occasional 429 RESOURCE_EXHAUSTED.
    """
    if not client:
        return None

    for attempt in range(1, retries + 1):
        try:
            print(f"🤖 Gemini request (attempt {attempt}/{retries})...")
            resp = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
            raw = _strip_code_fences(getattr(resp, "text", "") or "")
            return json.loads(raw)
        except Exception as e:
            print(f"⏳ Gemini attempt {attempt} failed: {e}")
            await asyncio.sleep(base_delay_s * attempt)
    return None

async def extract_hotel_name(raw_email_or_name: str) -> str:
    # If it's already short and single-line, treat as hotel name.
    if raw_email_or_name and len(raw_email_or_name) <= 140 and "\n" not in raw_email_or_name:
        return raw_email_or_name.strip()

    # If no Gemini key, we can't extract—fallback.
    if not client:
        return "UNKNOWN_PROPERTY"

    prompt = (
        "Extract the hotel/property name from the email below.\n"
        "Return ONLY JSON like: {\"hotel_name\": \"The Reeds at Shelter Haven\"}.\n\n"
        f"EMAIL:\n{raw_email_or_name}"
    )
    data = await gemini_json(prompt)
    if data and isinstance(data, dict):
        name = (data.get("hotel_name") or "").strip()
        if name:
            return name
    return "UNKNOWN_PROPERTY"

async def gemini_official_url(hotel_name: str) -> Optional[str]:
    if not client:
        return None
    prompt = (
        f"Provide the official website URL for '{hotel_name}'. "
        "Return ONLY JSON: {\"url\": \"https://example.com\"} (use null if unknown)."
    )
    data = await gemini_json(prompt)
    if data and isinstance(data, dict):
        url = (data.get("url") or "").strip()
        return url or None
    return None

async def gemini_gds_guess(hotel_name: str) -> Optional[Dict[str, Any]]:
    if not client:
        return None
    prompt = (
        f"Provide GDS codes and chain code for '{hotel_name}'.\n"
        "Return ONLY JSON with keys exactly:\n"
        "chain, sabre, amadeus, apollo, worldspan\n"
        "Use null for unknown.\n"
        "Example:\n"
        "{\"chain\":\"PW\",\"sabre\":\"192496\",\"amadeus\":\"WWDRSH\",\"apollo\":\"44708\",\"worldspan\":\"ACYRS\"}"
    )
    data = await gemini_json(prompt)
    if data and isinstance(data, dict):
        return data
    return None

async def gemini_booking_urls(hotel_name: str, official_url: str) -> List[str]:
    if not client:
        return []
    prompt = (
        "Find the DIRECT booking engine URL(s) for this hotel (page where guests select dates/rooms).\n"
        "Return ONLY JSON: {\"booking_urls\": [\"https://...\", \"https://...\"]}.\n"
        "Prefer direct vendor booking URLs (SynXis/iHotelier/TravelClick/Cloudbeds/WebRezPro/etc) "
        "or a /book /reservations /availability page.\n\n"
        f"HOTEL: {hotel_name}\n"
        f"OFFICIAL SITE: {official_url}\n"
    )
    data = await gemini_json(prompt)
    urls = []
    if data and isinstance(data, dict):
        urls = data.get("booking_urls") or []
    out, seen = [], set()
    for u in urls:
        nu = normalize_url(u, base=official_url)
        if nu and nu not in seen:
            seen.add(nu)
            out.append(nu)
    return out

# ----------------------------
# Travel Weekly fallback (deterministic)
# ----------------------------
def _parse_gds_from_travelweekly_html(html: str) -> Optional[Dict[str, Any]]:
    """
    Parse Travel Weekly hotel page HTML and extract GDS codes.
    This is best-effort because TW layout can change.
    """
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    # Try to find and pull values after labels
    def grab(label: str) -> Optional[str]:
        # label like "Amadeus:" "Sabre:" etc.
        idx = text.find(label)
        if idx == -1:
            return None
        tail = text[idx + len(label):].lstrip()

        # Value typically a token; allow letters/numbers
        m = re.match(r"([A-Z0-9]{3,12})", tail, re.IGNORECASE)
        return m.group(1).strip() if m else None

    # Some pages show "Galileo/Apollo:" label
    out = {
        "chain": None,
        "sabre": grab("Sabre:"),
        "amadeus": grab("Amadeus:"),
        "apollo": grab("Galileo/Apollo:") or grab("Apollo:"),
        "worldspan": grab("Worldspan:"),
        "source": "travelweekly",
        "verified": True,
    }

    # Chain code sometimes appears as part of GDS code lines; if you see "PW 192496" etc.
    # We'll attempt to infer chain code as 2-letter token before a GDS value.
    # Example patterns: "Amadeus: PW WWDRSH" or "Sabre: PW 192496"
    chain_candidates = []
    for label in ["Sabre:", "Amadeus:", "Galileo/Apollo:", "Worldspan:"]:
        idx = text.find(label)
        if idx != -1:
            tail = text[idx + len(label):].strip()
            # Look for a 2-3 letter chain token before the code
            m = re.match(r"([A-Z]{2,3})\s+([A-Z0-9]{3,12})", tail)
            if m:
                chain_candidates.append(m.group(1))

    if chain_candidates:
        out["chain"] = chain_candidates[0]

    # If we got at least one code, return
    if out["sabre"] or out["amadeus"] or out["apollo"] or out["worldspan"]:
        return out

    return None

async def travelweekly_find_hotel_page_url(hotel_name: str) -> Optional[str]:
    """
    Find the best TravelWeekly Hotels page for this hotel.
    Strategy:
      1) Use TravelWeekly site search page (no Google/Bing UI).
      2) Pick first result that looks like a Hotels detail page.
    """
    q = quote_plus(hotel_name)
    search_url = f"https://www.travelweekly.com/Search?q={q}"

    try:
        status, html = await fetch(search_url)
        if status >= 400 or not html:
            return None

        soup = BeautifulSoup(html, "html.parser")

        # Find links to hotel detail pages
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/Hotels/" in href:
                full = normalize_url(href, base="https://www.travelweekly.com")
                links.append(full)

        # De-dupe preserve order
        dedup = []
        seen = set()
        for u in links:
            if u not in seen:
                seen.add(u)
                dedup.append(u)

        return dedup[0] if dedup else None
    except Exception as e:
        print(f"⚠️ TravelWeekly search failed: {e}")
        return None

async def lookup_gds_from_travelweekly(hotel_name: str) -> Optional[Dict[str, Any]]:
    """
    Deterministically fetch TravelWeekly hotel detail page and extract GDS codes.
    """
    tw_url = await travelweekly_find_hotel_page_url(hotel_name)
    if not tw_url:
        return None

    try:
        status, html = await fetch(tw_url)
        if status >= 400:
            return None

        data = _parse_gds_from_travelweekly_html(html)
        if data:
            data["travelweekly_url"] = tw_url
            return data
        return None
    except Exception as e:
        print(f"⚠️ TravelWeekly lookup failed: {e}")
        return None

# ----------------------------
# Booking engine discovery + screenshot
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
    ]

async def choose_accessible_booking_url(candidates: List[str]) -> str:
    """
    Choose the first candidate that:
      - returns not-4xx
      - is not a bot verification page
      - looks like booking UI (or URL strongly suggests booking)
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

async def screenshot_booking_engine(url: str, out_png: str, blocked_png: str, blocked_html: str) -> None:
    """
    Open the booking engine page and screenshot the booking UI.
    If a verification page appears, save evidence instead.
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
                print("🧱 Booking engine is protected (verification page). Capturing evidence.")
                await page.screenshot(path=blocked_png, full_page=True)
                write_text(blocked_html, html)
            else:
                await page.screenshot(path=out_png, full_page=True)
                print(f"📸 Saved: {out_png}")
        except Exception as e:
            print(f"⚠️ Booking engine visit failed: {e}")
            try:
                await page.screenshot(path=os.path.join(ART_DIR, "BOOKING_VISIT_ERROR.png"), full_page=True)
            except Exception:
                pass
        finally:
            await browser.close()

# ----------------------------
# Main workflow
# ----------------------------
async def main():
    # Always create at least one artifact
    write_text(os.path.join(ART_DIR, "RUN_STATUS.txt"), "starting\n")

    # 0) Basic checks
    if not EMAIL_INPUT:
        write_text(os.path.join(ART_DIR, "RUN_STATUS.txt"), "EMAIL_INPUT missing\n")
        print("❌ EMAIL_INPUT is missing.")
        return

    hotel_name = await extract_hotel_name(EMAIL_INPUT)
    print(f"Property name: {hotel_name}")

    # 1) GDS step: AI guess first (optional), then TravelWeekly override if available
    gds_unverified = await gemini_gds_guess(hotel_name) if client else None

    # If Gemini is throttled or incomplete, TravelWeekly will be your deterministic fallback
    print("🔎 Checking Travel Weekly for verified GDS codes...")
    gds_verified = await lookup_gds_from_travelweekly(hotel_name)

    final_gds = None
    if gds_verified:
        final_gds = gds_verified
        final_gds["note"] = "Verified from Travel Weekly directory."
    elif gds_unverified:
        final_gds = {**gds_unverified}
        final_gds["source"] = "gemini"
        final_gds["verified"] = False
        final_gds["note"] = "Unverified (Travel Weekly not found/parsed)."
    else:
        final_gds = {
            "chain": None,
            "sabre": None,
            "amadeus": None,
            "apollo": None,
            "worldspan": None,
            "source": "none",
            "verified": False,
            "note": "No GDS data found (Gemini unavailable and Travel Weekly lookup failed).",
        }

    write_json(os.path.join(ART_DIR, "GDS_REPORT.json"), {
        "hotel": hotel_name,
        "gds": final_gds
    })
    print(f"✅ Saved: {os.path.join(ART_DIR, 'GDS_REPORT.json')}")

    # 2) Official URL: prefer TravelWeekly page’s “official site” if you later add parsing,
    #    but for now use Gemini. If Gemini is throttled, you can still proceed with booking URLs
    #    discovered by Gemini only if Gemini worked.
    official_url = await gemini_official_url(hotel_name) if client else None
    if official_url:
        official_url = normalize_url(official_url)
    else:
        official_url = ""

    write_json(os.path.join(ART_DIR, "PROPERTY_META.json"), {
        "hotel": hotel_name,
        "official_url": official_url,
    })

    # 3) Booking engine: AI generates candidate URLs + common paths. Then we open and screenshot booking UI.
    if not official_url and not client:
        prin

