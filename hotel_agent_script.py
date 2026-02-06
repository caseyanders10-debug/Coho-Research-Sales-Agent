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
# FREE BOOKING-ENGINE DISCOVERY (NO PAID APIs)
#
# Goals:
# 1) Chain code -> CHAIN_CODE.txt
# 2) Screenshot booking engine -> BOOKING_ENGINE.png
#    If blocked, save evidence for each try.
#
# Strategy:
# - Chain code: Gemini (focused)
# - Booking URL discovery (FREE):
#   A) TravelWeekly internal search -> hotel detail page -> extract any booking/vendor links
#   B) DuckDuckGo HTML search (no API key) -> extract vendor booking URLs
#   C) DuckDuckGo Lite fallback
#   D) Gemini booking URL suggestions (optional helper)
#   E) Common paths on official site (last resort, often blocked)
#
# Then:
# - Try top N candidates in Playwright and screenshot the first that
#   looks like booking UI or is a vendor booking URL.
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

VERSION = "2026-02-05.5"
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

# Bot-block detection (for evidence only; we do not bypass)
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

# Booking/vendor patterns
VENDOR_HOST_HINTS = [
    "synxis.com",
    "travelclick.com",
    "ihotelier.com",
    "secure-reservation",
    "reservations.",
    "be.",
    "cloudbeds.com",
    "webrezpro.com",
    "stayntouch.com",
    "roomkeypms.com",
    "sabre",
    "opera",
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

