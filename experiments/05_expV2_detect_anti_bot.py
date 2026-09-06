import sys
import os
import time
import re
import asyncio
from pathlib import Path

print("=" * 75)
print("🔬 DIAGNOSTIK ANTI-BOT & FALLBACK ENGINE V2 (05_expV2_detect_anti_bot.py)")
print("=" * 75)

TARGET_URLS = [
    "https://sub-scene.com/",
    "https://sub-scene.com/subtitles/searchbytitle?query=tt0241527"
]

def analyze_response(html: str, status_code: int, headers: dict) -> tuple[bool, list]:
    html_lower = html.lower()
    headers_str = str(headers).lower()
    detected = []

    if "cf-ray" in headers_str or "cloudflare" in html_lower or "turnstile" in html_lower:
        detected.append("Cloudflare CDN")
    if "akamai" in headers_str or "ak_bmsc" in html_lower:
        detected.append("Akamai")
    if "anubis" in html_lower:
        detected.append("Anubis")

    is_blocked = False
    if status_code in [403, 503]:
        is_blocked = True
    elif "just a moment..." in html_lower or "attention required!" in html_lower:
        is_blocked = True
        detected.append("Cloudflare Challenge Page")

    if not detected:
        detected = ["Akses Lutsinar"]

    return is_blocked, detected

def extract_title(html: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else "Tajuk Tidak Ditemui"

# ------------------------------------------------------------------
# ENGINE 1: curl-cffi
# ------------------------------------------------------------------
def try_curl_cffi(url: str) -> tuple[bool, str]:
    print("\n[Engine 1] Menguji curl-cffi (Chrome Impersonation)...")
    try:
        from curl_cffi import requests
        start = time.time()
        resp = requests.get(url, impersonate="chrome", allow_redirects=True, timeout=12)
        elapsed = round(time.time() - start, 2)

        is_blocked, anti_bots = analyze_response(resp.text, resp.status_code, resp.headers)
        title = extract_title(resp.text)

        print(f"  ├ Status HTTP : {resp.status_code} (Masa: {elapsed}s)")
        print(f"  ├ Tajuk Laman : {title}")
        print(f"  └ Pengesanan  : {', '.join(anti_bots)}")

        return (resp.status_code == 200 and not is_blocked), resp.text
    except Exception as e:
        print(f"  └ ❌ Gagal (curl-cffi Error): {e}")
        return False, ""

# ------------------------------------------------------------------
# ENGINE 2: Camoufox
# ------------------------------------------------------------------
async def _run_camoufox_async(url: str):
    from camoufox.async_api import AsyncCamoufox
    async with AsyncCamoufox(headless=True) as browser:
        page = await browser.new_page()
        start = time.time()
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        elapsed = round(time.time() - start, 2)

        content = await page.content()
        status = resp.status if resp else 0
        title = await page.title()

        return status, title, content, elapsed

def try_camoufox(url: str) -> tuple[bool, str]:
    print("\n[Engine 2] Menguji Camoufox[geoip] (Stealth Firefox Browser)...")
    try:
        status, title, content, elapsed = asyncio.run(_run_camoufox_async(url))
        is_blocked, anti_bots = analyze_response(content, status, {})

        print(f"  ├ Status HTTP : {status} (Masa: {elapsed}s)")
        print(f"  ├ Tajuk Laman : {title}")
        print(f"  └ Pengesanan  : {', '.join(anti_bots)}")

        return (status == 200 and not is_blocked), content
    except Exception as e:
        print(f"  └ ❌ Gagal (Camoufox Error): {e}")
        return False, ""

# ------------------------------------------------------------------
# ENGINE 3: DrissionPage
# ------------------------------------------------------------------
def try_drissionpage(url: str) -> tuple[bool, str]:
    print("\n[Engine 3] Menguji DrissionPage (Chromium WSL Headless)...")
    try:
        from DrissionPage import ChromiumPage, ChromiumOptions

        co = ChromiumOptions()
        co.set_argument('--headless=new')
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-dev-shm-usage')
        co.set_argument('--disable-gpu')

        page = ChromiumPage(co)
        start = time.time()
        page.get(url, retry=1, interval=1)
        elapsed = round(time.time() - start, 2)

        content = page.html
        title = page.title
        is_blocked, anti_bots = analyze_response(content, 200, {})

        print(f"  ├ Masa Diambil: {elapsed}s")
        print(f"  ├ Tajuk Laman : {title}")
        print(f"  └ Pengesanan  : {', '.join(anti_bots)}")

        page.quit()
        return (not is_blocked), content
    except Exception as e:
        print(f"  └ ❌ Gagal (DrissionPage Error): {e}")
        return False, ""

# ------------------------------------------------------------------
# ALUR KERJA UTAMA
# ------------------------------------------------------------------
if __name__ == "__main__":
    for target in TARGET_URLS:
        print(f"\n🎯 MENGUJI SASARAN: {target}")
        print("-" * 65)

        success, html = try_curl_cffi(target)
        if not success:
            print("  ⚠️ Beralih ke Engine 2 (Camoufox)...")
            success, html = try_camoufox(target)

        if not success:
            print("  ⚠️ Beralih ke Engine 3 (DrissionPage)...")
            success, html = try_drissionpage(target)

        if success:
            print(f"\n✅ BERJAYA: Laman {target} berjaya dicapai!")
            print(f"   Cuplikan HTML: {html[:150].strip()}...")
        else:
            print(f"\n❌ GAGAL: Kesemua enjin gagal pada {target}.")

    print("\n" + "=" * 75)
    print("✨ DIAGNOSTIK KANVAS SELESAI")
    print("=" * 75)