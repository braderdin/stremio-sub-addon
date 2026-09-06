import sys
import os
import re
import io
import time
import json
import zipfile
import asyncio
from pathlib import Path
from urllib.parse import urljoin, quote_plus
from bs4 import BeautifulSoup
from curl_cffi import requests

print("=" * 80)
print("🚀 UJIAN BRIDGE CARIAN KALIS-GAGAL V5 (06_expV5_camoufox_bridge.py)")
print("   Camoufox Turnstile Resolver -> Cookie Handover -> curl-cffi Downloader")
print("=" * 80)

BASE_URL = "https://sub-scene.com"
OUTPUT_DIR = Path(__file__).resolve().parent / "extracted_subs"
OUTPUT_DIR.mkdir(exist_ok=True)
REPORT_PATH = Path(__file__).resolve().parent / "search_bridge_v5_report.json"

REPORT = {
    "target_query": "Spider-Man",
    "search_engine": "Camoufox Safe-Wait",
    "movies_found": [],
    "selected_movie": None,
    "subtitles_count": 0,
    "download_success": False
}

def is_title_matched(query: str, candidate_title: str) -> bool:
    clean_q = re.sub(r"[^a-zA-Z0-9\s]", "", query.lower()).split()
    clean_c = re.sub(r"[^a-zA-Z0-9\s]", "", candidate_title.lower()).split()
    if not clean_q:
        return False
    matches = [word for word in clean_q if word in clean_c]
    return (len(matches) / len(clean_q)) >= 0.5

# ------------------------------------------------------------------
# FASA 1: CAMOUFOX SAFE SEARCH (PENGENDALIAN NAVIGASI SELAMAT)
# ------------------------------------------------------------------
async def resolve_search_with_camoufox(query: str) -> tuple[list[dict], dict, str]:
    from camoufox.async_api import AsyncCamoufox
    
    search_url = f"{BASE_URL}/search?query={quote_plus(query)}"
    print(f"\n[Fasa 1] Membuka Laman Carian Camoufox: {search_url}")
    
    results = []
    extracted_cookies = {}
    user_agent = ""

    async with AsyncCamoufox(headless=True) as browser:
        context = await browser.new_context()
        page = await context.new_page()

        # Buka laman carian dan tunggu hingga DOM asas sedia
        await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
        user_agent = await page.evaluate("navigator.userAgent")
        print("  ├─ Menunggu penyelesaian Cloudflare Turnstile...")

        # Gelung menunggu selamat: Menghalang ralat jika berlaku page reload/redirect
        final_html = ""
        for attempt in range(25):
            await page.wait_for_timeout(1000)
            try:
                # Periksa jika terdapat checkbox Turnstile interaktif yang perlu diklik
                for frame in page.frames:
                    if "challenges.cloudflare.com" in frame.url or "turnstile" in frame.url:
                        checkbox = await frame.query_selector("input[type=checkbox], .ctp-checkbox-label, #challenge-stage")
                        if checkbox:
                            await checkbox.click()
                            await page.wait_for_timeout(1500)

                current_html = await page.content()
                lower_html = current_html.lower()

                # Jika halaman sudah tidak memaparkan amaran Cloudflare dan mengandungi struktur subscene
                if "just a moment" not in lower_html and "attention required" not in lower_html:
                    if "/subscene/" in current_html or "no results" in lower_html:
                        print(f"  ├─ ✅ Sesi carian berjaya dilepaskan pada saat ke-{attempt + 1}!")
                        final_html = current_html
                        break
            except Exception:
                # Abaikan ralat 'page is navigating' semasa Cloudflare memuat semula dokumen
                continue

        if not final_html:
            try:
                final_html = await page.content()
            except Exception:
                final_html = ""

        # Kutip kuki sesi yang sah dari context
        raw_cookies = await context.cookies()
        for ck in raw_cookies:
            extracted_cookies[ck["name"]] = ck["value"]

        # Parse hasil carian filem
        if final_html:
            soup = BeautifulSoup(final_html, "lxml")
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                title = a.get_text(strip=True)
                if re.match(r"^/subscene/\d+$", href) and title:
                    if is_title_matched(query, title):
                        full_u = urljoin(BASE_URL, href)
                        if full_u not in [r["url"] for r in results]:
                            results.append({"title": title, "url": full_u})

        await context.close()

    return results, extracted_cookies, user_agent

# ------------------------------------------------------------------
# FASA 2: EKSTRAK SARIKATA & MUAT TURUN VIA CURL-CFFI DENGAN KUKI
# ------------------------------------------------------------------
def fetch_movie_subtitles(movie_url: str, session: requests.Session) -> list[dict]:
    print(f"\n[Fasa 2] Membaca Sarikata di: {movie_url}")
    subtitles = []
    resp = session.get(movie_url, headers={"Referer": f"{BASE_URL}/"}, timeout=15)
    if resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    for tr in soup.find_all("tr"):
        row_text = tr.get_text(" ", strip=True).lower()
        if "malayalam" in row_text:
            continue

        lang = None
        if any(w in row_text for w in ["bahasa melayu", "melayu", "malay"]):
            lang = "ms"
        elif any(w in row_text for w in ["bahasa indonesia", "indonesia", "indonesian"]):
            lang = "id"

        if lang:
            a_tag = tr.find("a", href=True)
            if a_tag and re.match(r"^/subtitle/\d+$", a_tag["href"]):
                subtitles.append({
                    "lang": lang,
                    "release": a_tag.get_text(strip=True),
                    "detail_url": urljoin(BASE_URL, a_tag["href"])
                })
    return subtitles

def download_and_extract_srt(detail_url: str, session: requests.Session) -> bool:
    print(f"\n[Fasa 3] Membuka Laman Detail Sarikata: {detail_url}")
    d_resp = session.get(detail_url, headers={"Referer": f"{BASE_URL}/"}, timeout=12)
    if d_resp.status_code != 200:
        return False

    soup = BeautifulSoup(d_resp.text, "lxml")
    dl_link = None
    for a in soup.find_all("a", href=True):
        if re.match(r"^/download/\d+$", a["href"]):
            dl_link = urljoin(BASE_URL, a["href"])
            break

    if not dl_link:
        print("  └ ❌ Pautan /download/XXXXXX tidak dijumpai.")
        return False

    print(f"  └ ✅ Pautan muat turun ditemui: {dl_link}")
    bin_resp = session.get(dl_link, headers={"Referer": detail_url}, timeout=15)
    raw_bytes = bin_resp.content

    if len(raw_bytes) < 100:
        print("  └ ❌ Muat turun kosong atau disekat.")
        return False

    print(f"  └ ✅ Muat turun fail binary siap: {len(raw_bytes)} bytes")

    extracted = []
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            for name in z.namelist():
                if name.endswith((".srt", ".vtt")):
                    target_p = OUTPUT_DIR / Path(name).name
                    with open(target_p, "wb") as f:
                        f.write(z.read(name))
                    extracted.append(str(target_p))
    except zipfile.BadZipFile:
        target_p = OUTPUT_DIR / "direct_sub.srt"
        with open(target_p, "wb") as f:
            f.write(raw_bytes)
        extracted.append(str(target_p))

    if extracted:
        print(f"  └ 🎉 BERJAYA EKSTRAK {len(extracted)} FAIL SRT:")
        for p in extracted:
            print(f"      ├─ Path: {p}")
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                preview = [f.readline().strip() for _ in range(5)]
            print(f"      └─ Kandungan Awal: {' | '.join(preview)}")
        return True

    return False

# ------------------------------------------------------------------
# ALUR KERJA UTAMA
# ------------------------------------------------------------------
if __name__ == "__main__":
    search_query = "Spider-Man"

    # 1. Jalankan Carian Selamat Melalui Camoufox
    movies, cookies, user_agent = asyncio.run(resolve_search_with_camoufox(search_query))
    REPORT["movies_found"] = movies

    if not movies:
        print("\n❌ Carian gagal atau tiada tajuk filem yang sepadan dijumpai.")
        with open(REPORT_PATH, "w") as f:
            json.dump(REPORT, f, indent=2)
        sys.exit(1)

    print(f"\n🎯 HASIL CARIAN TEPAT DIJUMPAI ({len(movies)} Filem):")
    for idx, m in enumerate(movies[:5]):
        print(f"  [{idx + 1}] {m['title']} -> {m['url']}")

    selected_movie = movies[0]
    REPORT["selected_movie"] = selected_movie

    # 2. Pindahkan Kuki ke curl-cffi untuk Muat Turun Pantas
    print(f"\n[Handover] Memindahkan Kuki Sesi ke Enjin curl-cffi...")
    fast_session = requests.Session(impersonate="chrome120")
    for k, v in cookies.items():
        fast_session.cookies.set(k, v, domain="sub-scene.com")

    if user_agent:
        fast_session.headers.update({"User-Agent": user_agent})

    # 3. Ambil Sarikata BM/ID
    subtitles = fetch_movie_subtitles(selected_movie["url"], fast_session)
    REPORT["subtitles_count"] = len(subtitles)
    print(f"  └ ✅ Ditemui {len(subtitles)} sarikata Bahasa Melayu / Indonesia!")

    if not subtitles:
        print("⚠️ Tiada sarikata BM/ID dijumpai untuk tajuk ini.")
        with open(REPORT_PATH, "w") as f:
            json.dump(REPORT, f, indent=2)
        sys.exit(0)

    # 4. Uji Muat Turun & Ekstrak Sarikata Pertama
    download_ok = download_and_extract_srt(subtitles[0]["detail_url"], fast_session)
    REPORT["download_success"] = download_ok

    with open(REPORT_PATH, "w") as f:
        json.dump(REPORT, f, indent=2)

    print("\n" + "=" * 80)
    print(f"📄 Laporan Penuh Selesai: {REPORT_PATH}")
    print("=" * 80)