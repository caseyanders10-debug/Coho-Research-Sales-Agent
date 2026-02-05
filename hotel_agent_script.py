import asyncio
import os
import json
from typing import Optional, Dict, Any

from google import genai
from playwright.async_api import async_playwright

# Raw input from workflow: can be a full email body OR a short hotel name
EMAIL_INPUT = os.environ.get("EMAIL_INPUT", "").strip()

# Gemini client
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))


def _strip_code_fences(text: str) -> str:
    """Remove common markdown code fences Gemini might add."""
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
    """Call Gemini and parse a JSON object from the response, with retries."""
    for attempt in range(1, retries + 1):
        try:
            print(f"🤖 Gemini request (attempt {attempt}/{retries})...")
            resp = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt
            )
            raw = _strip_code_fences(getattr(resp, "text", "") or "")
            return json.loads(raw)
        except Exception as e:
            print(f"⏳ Gemini attempt {attempt} failed: {e}")
            await asyncio.sleep(base_delay_s * attempt)
    return None


async def extract_hotel_name(raw_email_or_name: str) -> str:
    """
    If the input looks like a short name, return it.
    If it looks like an email body, ask Gemini to extract the property name.
    """
    # If it's already short and single-line, assume it's a name.
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

    # Fallback if extraction fails
    return "UNKNOWN_PROPERTY"


async def lookup_property_details(hotel_name: str) -> Optional[Dict[str, Any]]:
    """
    Ask Gemini for GDS codes + phone + official site URL.
    Keep this strictly structured to reduce hallucinations.
    """
    prompt = (
        f"Provide GDS codes, official phone, and official website URL for '{hotel_name}'.\n"
        "Return ONLY JSON with keys exactly:\n"
        "chain, sabre, amadeus, apollo, worldspan, phone, url\n"
        "Use null for unknown values.\n"
        "Example:\n"
        "{\"chain\":\"PW\",\"sabre\":\"192496\",\"amadeus\":\"WWDRSH\",\"apollo\":\"44708\","
        "\"worldspan\":\"ACYRS\",\"phone\":\"609-368-0100\",\"url\":\"https://reedsatshelterhaven.com/\"}"
    )
    return await gemini_json(prompt)


def looks_like_bot_block(html: str) -> bool:
    """Basic detection for common verification / bot-check pages."""
    if not html:
        return False
    s = html.lower()
    patterns = [
        "are you a human",
        "verify you are human",
        "verification required",
        "captcha",
        "access denied",
        "unusual traffic",
        "press and hold",
        "bot detection",
        "cloudflare",
        "checking your browser",
    ]
    return any(p in s for p in patterns)


def write_report(path: str, hotel_name: str, data: Optional[Dict[str, Any]]) -> None:
    """Always write a report so artifacts upload doesn't fail."""
    if data and isinstance(data, dict):
        c = data.get("chain") or ""
        report = (
            f"--- GDS PROPERTY SNAPSHOT ---\n"
            f"PROPERTY:  {hotel_name}\n"
            f"PHONE:     {data.get('phone')}\n"
            f"URL:       {data.get('url')}\n"
            f"CHAIN:     {c}\n"
            f"-----------------------------\n"
            f"SABRE:     {c}{data.get('sabre')}\n"
            f"AMADEUS:   {c}{data.get('amadeus')}\n"
            f"APOLLO:    {c}{data.get('apollo')}\n"
            f"WORLDSPAN: {c}{data.get('worldspan')}\n"
            f"-----------------------------\n"
        )
    else:
        report = f"Failed to retrieve data for {hotel_name}\n"

    with open(path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"✅ Report saved: {path}")


async def capture_site_proof(url: str) -> None:
    """
    Visit the official URL directly. If blocked, save BLOCKED.png + BLOCKED.html.
    Otherwise save a normal proof screenshot.
    """
    print(f"🌐 Visiting: {url}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)

            html = await page.content()
            if looks_like_bot_block(html):
                print("🧱 Bot/verification page detected. Capturing evidence and continuing.")
                await page.screenshot(path="screenshots/BLOCKED.png", full_page=True)
                with open("screenshots/BLOCKED.html", "w", encoding="utf-8") as f:
                    f.write(html)
            else:
                await page.screenshot(path="screenshots/Booking_Engine_Proof.png", full_page=False)
                print("📸 Booking proof captured.")

        except Exception as e:
            print(f"⚠️ Direct visit failed: {e}")
            try:
                await page.screenshot(path="screenshots/visit_error.png", full_page=True)
            except Exception:
                pass

        finally:
            await browser.close()


async def main():
    os.makedirs("screenshots", exist_ok=True)

    if not os.environ.get("GEMINI_API_KEY"):
        print("❌ GEMINI_API_KEY is missing. Add it to GitHub Secrets and workflow env.")
        # Still create a report so artifacts exist
        write_report("screenshots/GDS_REPORT.txt", "UNKNOWN_PROPERTY", None)
        return

    # 1) Extract hotel name from email (or accept short name)
    hotel_name = await extract_hotel_name(EMAIL_INPUT)
    print(f"🏨 Property name: {hotel_name}")

    # 2) Lookup details
    data = await lookup_property_details(hotel_name)

    # 3) Always write report
    write_report("screenshots/GDS_REPORT.txt", hotel_name, data)

    # 4) Visit official URL (best effort)
    url = None
    if data and isinstance(data, dict):
        url = (data.get("url") or "").strip()

    if url:
        await capture_site_proof(url)
    else:
        print("ℹ️ No URL returned from Gemini; skipping website visit.")


if __name__ == "__main__":
    asyncio.run(main())

