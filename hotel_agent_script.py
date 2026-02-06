import asyncio
import os
import re
import json
from dataclasses import dataclass, asdict
from typing import List, Optional, Tuple, Dict
from urllib.parse import urljoin, urlparse, quote_plus

import httpx
from bs4 import BeautifulSoup
from google import genai
from openpyxl import Workbook


# ==========================================================
# HOTEL INTELLIGENCE AGENT (Phase 1)
#
# Input:
#   EMAIL_INPUT (either a single hotel name OR ZoomInfo weekly email body)
#
# Output artifacts (in screenshots/):
#   - HOTEL_OUTPUT.xlsx  (single sheet, many rows when list is provided)
#   - RUN_STATUS.txt
#   - PARSED_PROPERTIES.json
#   - BOOKING_EVIDENCE.json
#
# What it collects per property:
#   - Hotel Name
#   - ZoomInfo Category (if available)
#   - ZoomInfo Score (if available)
#   - GDS Chain Code (Gemini)
#   - Booking Vendor (fingerprinted from evidence)
#   - Vendor Evidence URL
#   - Confidence (High/Medium/Low)
#   - Notes
#
# IMPORTANT:
# - This does NOT bypass CAPTCHAs or verification pages.
# - It fingerprints vendors using HTML evidence + free search sources.
# ==========================================================

VERSION = "2026-02-06.1"
print(f"🔥 HOTEL AGENT VERSION: {VERSION} 🔥")

EMAIL_INPUT = (os.environ.get("EMAIL_INPUT") or "").strip()
GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()

ART_DIR = "screenshots"
os.makedirs(ART_DIR, exist_ok=True)

def write_text(filename: str, content: str) -> None:
    with open(os.path.join(ART_DIR, filename), "w", encoding="utf-8") as f:
        f.write(content)

def write_json(filename: str, obj) -> None:
    with open(os.path.join(ART_DIR, filename), "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)

# Ensure at least one artifact exists
write_text("RUN_STATUS.txt", "starting\n")

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# --- Bot wall indicators (we do not bypass, only detect) ---
BOT_BLOCK_PATTERNS = [
    "are you a human",
    "verify you are human",
    "verification required",
    "captcha",
    "access denied",
    "unusual traffic",
    "cloudflare",
    "checking your browser",
    "security check",
]

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

# --- Booking vendor fingerprints ---
VENDOR_PATTERNS: Dict[str, List[str]] = {
    "SynXis (Sabre Hospitality)": [
        "synxis.com", "be.synxis.com", "synxis.com/rez", "synxis.com/reservations",
    ],
    "iHotelier / TravelClick (Amadeus)": [
        "ihotelier.com", "travelclick.com", "reservations.travelclick.com", "secure-reservation",
    ],
    "Cloudbeds": [
        "cloudbeds.com", "hotels.cloudbeds.com",
    ],
    "WebRezPro": [
        "webrezpro.com", "reservations.webrezpro.com",
    ],
    "StayNTouch": [
        "stayntouch", "stayntouch.com",
    ],
    "SHR / Windsurfer": [
        "windsurfer", "windsurfercrs", "shrgroup", "shr.global", "shr",
    ],
}

AFFILIATE_PATTERNS = [
    "guestreservations.com",
    "reservationdesk.com",
    "hotelplanner.com",
    "reservations.com",
]

def classify_vendor_from_url(url: str) -> Tuple[str, str]:
    """
    Returns (vendor_name, confidence_band).
    confidence_band is only based on URL match strength.
    """
    u = (url or "").lower()
    h = host(url)

    for vendor, patterns in VENDOR_PATTERNS.items():
        for p in patterns:
            if p.lower() in u or p.lower() in h:
                return vendor, "High"

    for a in AFFILIATE_PATTERNS:
        if a in h:
            return "Affiliate/OTA (Not official CRS)", "Low"

    return "Unknown", "Low"

def best_vendor_from_evidence(evidence_urls: List[str]) -> Tuple[str, str, str]:
    """
    Pick best vendor + evidence URL + confidence based on evidence list.
    Preference order:
      1) Vendor match (High)
      2) Booking-ish on official domain (Medium)
      3) Affiliate (Low)
      4) Unknown (Low)
    """
    if not evidence_urls:
        return "Unknown", "", "Low"

    # Score each URL
    scored = []
    for u in evidence_urls:
        vendor, conf = classify_vendor_from_url(u)
        score = 0
        if conf == "High":
            score += 100
        if vendor == "Affiliate/OTA (Not official CRS)":
            score += 10
        # booking-ish hint
        if any(x in (u.lower()) for x in ["/book", "/booking", "/reservations", "reservation", "availability"]):
            score += 15
        scored.append((score, vendor, conf, u))

    scored.sort(key=lambda x: x[0], reverse=True)
    _, vendor, conf, url = scored[0]

    # If it’s unknown but still booking-ish, bump to Medium
    if vendor == "Unknown" and any(x in url.lower() for x in ["/book", "/booking", "/reservations", "availability"]):
        conf = "Medium"

    return vendor, url, conf

# --- ZoomInfo email parsing ---
@dataclass
class PropertyRow:
    hotel_name: str
    category: Optional[str] = None
    score: Optional[int] = None

def parse_zoominfo_email(body: str) -> List[PropertyRow]:
    """
    Tries to parse a ZoomInfo weekly email body containing a list like:
      <a>Hotel Name</a>  Category  Score
    Works with:
      - HTML email text (anchors)
      - plain text variants
    """
    body = (body or "").strip()
    if not body:
        return []

    # If it looks like HTML with links, parse anchors
    rows: List[PropertyRow] = []
    if "<a" in body.lower() and "</a>" in body.lower():
        soup = BeautifulSoup(body, "html.parser")
        # ZoomInfo list usually uses anchors for names
        anchors = soup.find_all("a")
        for a in anchors:
            name = (a.get_text(" ", strip=True) or "").strip()
            if not name:
                continue
            # Ignore obvious navigation links
            if len(name) < 2:
                continue
            rows.append(PropertyRow(hotel_name=name))
        # De-dupe while preserving order
        seen = set()
        out = []
        for r in rows:
            if r.hotel_name.lower() not in seen:
                seen.add(r.hotel_name.lower())
                out.append(r)
        return out

    # Plain text fallback:
    # Attempt to capture lines like:
    # "The Reeds  Property Management Software  64"
    # We’ll accept: <name><spaces><category><spaces><score>
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    # Collapse multiple spaces for regex
    for ln in lines:
        compact = re.sub(r"\s+", " ", ln).strip()
        m = re.match(r"^(.*?)(?:\s{1,}|\t+)(Reservation System|Property Management Software|Global Distribution System|.*?)(?:\s{1,}|\t+)(\d{1,3})$", compact, re.IGNORECASE)
        if m:
            name = m.group(1).strip()
            cat = m.group(2).strip()
            score = int(m.group(3))
            if name:
                rows.append(PropertyRow(hotel_name=name, category=cat, score=score))

    # If we found nothing, treat entire input as a single property name
    return rows

def detect_input_mode(body: str) -> str:
    """
    Returns:
      - "list" if it looks like a ZoomInfo list
      - "single" otherwise
    """
    b = (body or "").strip()
    if not b:
        return "single"
    # crude heuristics
    if ("Property Management Software" in b) or ("Reservation System" in b) or ("Global Distribution System" in b):
        return "list"
    if "<a" in b.lower() and "</a>" in b.lower():
        return "list"
    # if short-ish and no line breaks, it's likely a single hotel
    if "\n" not in b and len(b) <= 140:
        return "single"
    return "single"

# --- HTTP helpers ---
async def fetch(url: str, timeout_s: float = 25.0) -> Tuple[int, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    async with httpx.AsyncClient(follow_redirects=True, headers=headers, timeout=timeout_s) as c:
        r = await c.get(url)
        return r.status_code, (r.text or "")

# --- FREE search: DuckDuckGo HTML + Lite ---
async def ddg_html_search(query: str) -> List[str]:
    q = quote_plus(query)
    url = f"https://duckduckgo.com/html/?q={q}"
    try:
        status, html = await fetch(url, timeout_s=25.0)
        if status >= 400 or not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith(("http://", "https://")):
                links.append(href)
        # de-dupe
        out, seen = [], set()
        for u in links:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out
    except Exception:
        return []

async def ddg_lite_search(query: str) -> List[str]:
    q = quote_plus(query)
    url = f"https://lite.duckduckgo.com/lite/?q={q}"
    try:
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
    except Exception:
        return []

def build_vendor_queries(hotel_name: str) -> List[str]:
    return [
        f"\"{hotel_name}\" synxis booking",
        f"\"{hotel_name}\" ihotelier booking",
        f"\"{hotel_name}\" travelclick reservations",
        f"\"{hotel_name}\" cloudbeds booking",
        f"\"{hotel_name}\" webrezpro reservations",
        f"\"{hotel_name}\" booking engine",
        f"\"{hotel_name}\" reservations",
    ]

# --- TravelWeekly internal search (free) ---
async def travelweekly_internal_search(hotel_name: str) -> Optional[str]:
    q = quote_plus(hotel_name)
    url = f"https://www.travelweekly.com/Search?q={q}"
    try:
        status, html = await fetch(url, timeout_s=25.0)
        if status >= 400 or not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/Hotels/" in href and "/Travel-News/" not in href:
                return urljoin("https://www.travelweekly.com", href)
        return None
    except Exception:
        return None

def extract_vendorish_links_from_html(html: str, base_url: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    found = []
    for tag in soup.find_all(["a", "script", "iframe", "link"]):
        url = None
        if tag.name == "a" and tag.get("href"):
            url = tag.get("href")
        elif tag.name == "script" and tag.get("src"):
            url = tag.get("src")
        elif tag.name == "iframe" and tag.get("src"):
            url = tag.get("src")
        elif tag.name == "link" and tag.get("href"):
            url = tag.get("href")

        if not url:
            continue

        full = normalize_url(url, base=base_url)
        h = host(full)
        # Keep anything that looks vendor/booking/affiliate
        if any(p.lower() in full.lower() for plist in VENDOR_PATTERNS.values() for p in plist):
            found.append(full)
        elif any(a in h for a in AFFILIATE_PATTERNS):
            found.append(full)
        elif any(x in full.lower() for x in ["/booking", "/book", "/reservations", "/availability", "reservation"]):
            found.append(full)

    out, seen = [], set()
    for u in found:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

# --- Gemini: Chain code only (simple, focused) ---
async def gemini_chain_code_only(hotel_name: str) -> str:
    if not client:
        return "UNKNOWN"
    prompt = (
        f"What is the GDS chain code for '{hotel_name}'?\n"
        "Return ONLY JSON: {\"chain_code\": \"PW\"}.\n"
        "chain_code must be 2-3 uppercase letters, or null if unknown."
    )
    for attempt in range(1, 4):
        try:
            print(f"🤖 Gemini chain code (attempt {attempt}/3)...")
            resp = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
            text = (resp.text or "").strip()
            text = text.replace("```json", "").replace("```", "").strip()
            data = json.loads(text)
            cc = (data.get("chain_code") or "").strip()
            return cc or "UNKNOWN"
        except Exception as e:
            print(f"⏳ Gemini chain code failed: {e}")
            await asyncio.sleep(6 * attempt)
    return "UNKNOWN"

# --- Gemini: official URL (optional helper) ---
async def gemini_official_url(hotel_name: str) -> Optional[str]:
    if not client:
        return None
    prompt = f"Provide the official website URL for '{hotel_name}'. Return ONLY JSON: {{\"url\": \"https://example.com\"}}"
    for attempt in range(1, 4):
        try:
            print(f"🤖 Gemini official URL (attempt {attempt}/3)...")
            resp = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
            text = (resp.text or "").strip()
            text = text.replace("```json", "").replace("```", "").strip()
            data = json.loads(text)
            u = (data.get("url") or "").strip()
            return normalize_url(u) if u else None
        except Exception as e:
            print(f"⏳ Gemini official URL failed: {e}")
            await asyncio.sleep(6 * attempt)
    return None

# --- Per-property booking vendor fingerprinting ---
@dataclass
class BookingFinding:
    hotel_name: str
    evidence_urls: List[str]
    vendor: str
    vendor_evidence_url: str
    confidence: str
    notes: str

async def fingerprint_booking_vendor(hotel_name: str) -> BookingFinding:
    evidence: List[str] = []
    notes: List[str] = []

    # 1) TravelWeekly hotel page -> extract vendor-ish links
    tw_url = await travelweekly_internal_search(hotel_name)
    if tw_url:
        notes.append(f"TravelWeekly hotel page found.")
        try:
            status, html = await fetch(tw_url, timeout_s=25.0)
            if status < 400 and html and not looks_like_bot_block(html):
                evidence.extend(extract_vendorish_links_from_html(html, tw_url))
            else:
                notes.append(f"TravelWeekly fetch blocked/unavailable (HTTP {status}).")
        except Exception as e:
            notes.append(f"TravelWeekly fetch error: {repr(e)}")
    else:
        notes.append("TravelWeekly hotel page not found.")

    # 2) Official website HTML (via Gemini URL) -> look for scripts/iframes/booking links
    official_url = await gemini_official_url(hotel_name)
    if official_url:
        notes.append(f"Official URL candidate: {official_url}")
        try:
            status, html = await fetch(official_url, timeout_s=25.0)
            if status < 400 and html:
                if looks_like_bot_block(html):
                    notes.append("Official site HTML appears bot-blocked; skipping deep parse.")
                else:
                    evidence.extend(extract_vendorish_links_from_html(html, official_url))
            else:
                notes.append(f"Official site fetch failed (HTTP {status}).")
        except Exception as e:
            notes.append(f"Official site fetch error: {repr(e)}")
    else:
        notes.append("Official URL not available from Gemini.")

    # 3) Free search (DuckDuckGo HTML + lite fallback) -> collect vendor/affiliate/booking URLs
    for q in build_vendor_queries(hotel_name):
        links = await ddg_html_search(q)
        if not links:
            links = await ddg_lite_search(q)
        # Keep only strong candidates (vendor/affiliate/booking-ish)
        for u in links[:25]:
            u2 = normalize_url(u)
            if not u2:
                continue
            h = host(u2)
            if any(p.lower() in u2.lower() for plist in VENDOR_PATTERNS.values() for p in plist):
                evidence.append(u2)
            elif any(a in h for a in AFFILIATE_PATTERNS):
                evidence.append(u2)
            elif any(x in u2.lower() for x in ["/booking", "/book", "/reservations", "reservation", "availability"]):
                evidence.append(u2)

    # De-dupe evidence
    dedup, seen = [], set()
    for u in evidence:
        if u not in seen:
            seen.add(u)
            dedup.append(u)
    evidence = dedup

    vendor, vendor_url, conf = best_vendor_from_evidence(evidence)

    # Notes adjustments:
    if vendor.startswith("Affiliate"):
        notes.append("Top evidence appears affiliate/OTA; may not reflect official CRS.")
    if vendor == "Unknown":
        notes.append("No strong vendor fingerprint found; evidence may be generic booking paths.")

    return BookingFinding(
        hotel_name=hotel_name,
        evidence_urls=evidence[:80],
        vendor=vendor,
        vendor_evidence_url=vendor_url,
        confidence=conf,
        notes=" ".join(notes)[:2000],
    )

# --- Excel output ---
def write_excel(filename: str, rows: List[dict]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Hotels"

    headers = [
        "Hotel Name",
        "ZoomInfo Category",
        "ZoomInfo Score",
        "GDS Chain Code",
        "Booking Vendor",
        "Vendor Evidence URL",
        "Confidence",
        "Notes",
    ]
    ws.append(headers)

    for r in rows:
        ws.append([
            r.get("hotel_name", ""),
            r.get("zoominfo_category", ""),
            r.get("zoominfo_score", ""),
            r.get("gds_chain_code", ""),
            r.get("booking_vendor", ""),
            r.get("vendor_evidence_url", ""),
            r.get("confidence", ""),
            r.get("notes", ""),
        ])

    # widths
    widths = [40, 28, 16, 16, 28, 70, 12, 70]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i)].width = w

    wb.save(os.path.join(ART_DIR, filename))

async def main():
    if not EMAIL_INPUT:
        write_text("RUN_STATUS.txt", "EMAIL_INPUT missing\n")
        print("❌ EMAIL_INPUT missing.")
        return

    mode = detect_input_mode(EMAIL_INPUT)
    properties: List[PropertyRow] = []

    if mode == "list":
        properties = parse_zoominfo_email(EMAIL_INPUT)
        if not properties:
            # fallback to single name if parsing failed
            properties = [PropertyRow(hotel_name=EMAIL_INPUT)]
    else:
        properties = [PropertyRow(hotel_name=EMAIL_INPUT)]

    # De-dupe and keep order
    seen = set()
    clean_props = []
    for p in properties:
        k = p.hotel_name.strip().lower()
        if not k or k in seen:
            continue
        seen.add(k)
        clean_props.append(p)
    properties = clean_props

    write_json("PARSED_PROPERTIES.json", [asdict(p) for p in properties])
    print(f"✅ Parsed {len(properties)} propertie(s).")

    output_rows = []
    all_booking_findings = []

    # Process sequentially for stability (easy to parallelize later)
    for idx, prop in enumerate(properties, start=1):
        hotel_name = prop.hotel_name.strip()
        print(f"\n🏨 [{idx}/{len(properties)}] Processing: {hotel_name}")

        # 1) GDS chain code
        chain_code = await gemini_chain_code_only(hotel_name)
        print(f"   ✅ Chain code: {chain_code}")

        # 2) Booking vendor fingerprint
        finding = await fingerprint_booking_vendor(hotel_name)
        all_booking_findings.append(asdict(finding))
        print(f"   ✅ Booking vendor: {finding.vendor} ({finding.confidence})")

        output_rows.append({
            "hotel_name": hotel_name,
            "zoominfo_category": prop.category or "",
            "zoominfo_score": prop.score if prop.score is not None else "",
            "gds_chain_code": chain_code,
            "booking_vendor": finding.vendor,
            "vendor_evidence_url": finding.vendor_evidence_url,
            "confidence": finding.confidence,
            "notes": finding.notes,
        })

        # Update run status continuously so you always get something
        write_text("RUN_STATUS.txt", f"processed {idx}/{len(properties)}\n")

    write_json("BOOKING_EVIDENCE.json", all_booking_findings)
    write_excel("HOTEL_OUTPUT.xlsx", output_rows)

    write_text("RUN_STATUS.txt", "done\n")
    print("\n✅ Done. Saved: screenshots/HOTEL_OUTPUT.xlsx")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        write_text("CRASH.txt", f"Script crashed:\n{repr(e)}\n")
        raise



