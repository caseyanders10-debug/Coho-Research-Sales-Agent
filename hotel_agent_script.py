import asyncio
import os
import json
import re
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse, urljoin

import httpx
from google import genai
from playwright.async_api import async_playwright

EMAIL_INPUT = os.environ.get("EMAIL_INPUT", "").strip()
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))

# Patterns commonly seen in booking-engine URLs / vendors
BOOKING_HINT_PATTERNS = [
    r"/book", r"/booking", r"/reservations", r"/reservation", r"/reserve", r"/availability",
    r"synxis", r"sabre", r"travelclick", r"ihotelier", r"webrezpro", r"cloudbeds",
    r"roomkey", r"stayntouch", r"opera", r"reservations\.", r"be\.", r"bookingengine", r"rez",
]

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

BOOKING_UI_SIGNALS = [
    "check-in", "check in", "check-out", "check out",
    "arrival", "departure",
    "promo code", "rate", "rates",
    "rooms", "guests",
    "availability",
    "book now", "reserve",
]


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


async def gemini_json(prompt: str, retries: int = 3, base_delay_s: int = 8) -> Optional[Dict[str, Any]]:
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
    # If it's short and single-line, treat it as a name.
    if raw_email_or_name and len(raw_email_or_name) <= 120 and "\n" not in raw_email_or_name:
        return raw_email_or_name.strip()

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


async def lookup_property_details(hotel_name: str) -> Optional[Dict[str, Any]]:
    prompt = (
        f"Provide the official website URL for '{hotel_name}'.\n"
        "Return ONLY JSON with keys exactly: phone, url\n"
        "Use null for unknown values.\n"
        "Example: {\"phone\":\"609-368-0100\",\"url\":\"https://reedsatshelterhaven.com/\"}"
    )
    return await gemini_json(prompt)


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


def likely_booking_url(url: str) -> bool:
    s = (url or "").lower()
    return any(re.search(p, s) for p in BOOKING_HINT_PATTERNS)


async def fetch_html(url: str, timeout_s: float = 15.0) -> tuple[int, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    }
    async with httpx.AsyncClient(follow_redirects=True, headers=headers, timeout=timeout_s) as c:
        r = await c.get(url)
        return r.status_code, (r.text or "")[:200000]


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


async def discover_booking_urls_with_gemini(hotel_name: str, official_url: str) -> List[str]:
    prompt = (
        "Find the DIRECT booking engine URL(s) for this hotel (the page where guests pick dates/rooms).\n"
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
    # Normalize & de-dupe
    out = []
    seen = set()
    for u in urls:
        nu = normalize_url(u, base=official_url)
        if nu and nu not in seen:
            seen.add(nu)
            out.append(nu)
    return out


async def choose_accessible_booking_url(candidates: List[str]) -> str:
    """
    Pick the first candidate that:
    - returns 2xx/3xx
    - is NOT a bot-verification page
    - looks like a booking UI (or URL strongly suggests booking)
    """
    for url in candidates:
        try:
            status, html = await fetch_html(url)
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
    This is the actual goal: open the booking engine page and screenshot the booking UI.
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
                await page.screenshot(path="screenshots/BOOKING_BLOCKED.png", full_page=True)
                with open("screenshots/BOOKING_BLOCKED.html", "w", encoding="utf-8") as f:
                    f.write(html)
            else:
                # Full page screenshot of the booking engine UI
                await page.screenshot(path="screenshots/BOOKING_ENGINE.png", full_page=True)
                print("📸 Saved: screenshots/BOOKING_ENGINE.png")

        except Exception as e:
            print(f"⚠️ Booking engine visit failed: {e}")
            try:
                await page.screenshot(path="screenshots/BOOKING_VISIT_ERROR.png", full_page=True)
            except Exception:
                pass
        finally:
            await browser.close()


async def main():
    os.makedirs("screenshots", exist_ok=True)

    if not os.environ.get("GEMINI_API_KEY"):
        print("❌ GEMINI_API_KEY is missing. Add it to GitHub Secrets.")
        with open("screenshots/GDS_REPORT.txt", "w", encoding="utf-8") as f:
            f.write("Missing GEMINI_API_KEY\n")
        return

    hotel_name = await extract_hotel_name(EMAIL_INPUT)
    print(f"Property name: {hotel_name}")

    details = await lookup_property_details(hotel_name)
    official_url = (details or {}).get("url") or ""
    official_url = official_url.strip()

    # Always write something so your artifact step always has files
    with open("screenshots/GDS_REPORT.txt", "w", encoding="utf-8") as f:
        f.write(json.dumps({"hotel": hotel_name, "official_url": official_url, "phone": (details or {}).get("phone")}, indent=2))
    print("Report saved: screenshots/GDS_REPORT.txt")

    if not official_url:
        print("❌ No official URL found; cannot discover booking engine.")
        return

    # 1) Ask AI for booking engine URLs
    ai_candidates = await discover_booking_urls_with_gemini(hotel_name, official_url)

    # 2) Add common paths on same domain
    path_candidates = common_booking_paths(official_url)

    # Combine (AI first)
    candidates = []
    for u in ai_candidates + path_candidates:
        if u and u not in candidates:
            candidates.append(u)

    # Save candidates for debugging
    with open("screenshots/BOOKING_CANDIDATES.json", "w", encoding="utf-8") as f:
        json.dump({"hotel": hotel_name, "official_url": official_url, "candidates": candidates}, f, indent=2)

    booking_url = await choose_accessible_booking_url(candidates)

    if booking_url:
        await screenshot_booking_engine(booking_url)
    else:
        print("❌ No accessible booking engine URL found (without verification pages).")
        # Optional: try official URL anyway just to capture what block looks like there
        await screenshot_booking_engine(official_url)


if __name__ == "__main__":
    asyncio.run(main())

