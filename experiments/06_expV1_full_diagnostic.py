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

print("=" * 80)
print("🔬 DIAGNOSTIK LENGKAP END-TO-END V2 (06_expV1_full_diagnostic.py)")
print("   Menguji Stremio Payload -> Cinemeta -> Subscene WAF -> Redirect -> SRT")
print("=" * 80)

BASE_URL = "https://sub-scene.com"
OUTPUT_DIR = Path(__file__).resolve().parent / "extracted_subs"
OUTPUT_DIR.mkdir(exist_ok=True)
REPORT_PATH = Path(__file__).resolve().parent / "diagnostic_v1_report.json"

REPORT = {
    "timestamp": time.time(),
    "test_imdb_id": "tt0145487",
    "cinemeta_resolved_title": None,
    "stages": {},
    "errors": [],
    "success": False
}

def log_stage(stage_name: str, status: str, details: dict):
    REPORT["stages"][stage_name] = {"status": status, "details": details}

# ------------------------------------------------------------------
# FASA 1: RESOLVE IMDB ID KE TAJUK FILEM (CINEMETA)
# ------------------------------------------------------------------
def resolve_imdb_via_cinemeta(imdb_id: str) -> tuple[str, str]:
    print(f"\n[Fasa 1] Resolusi IMDb ID via Cinemeta API: {imdb_id}")
    url = f"https://v3-cinemeta.strem.io/meta/movie/{imdb_id}.json"
    
    try:
        from curl_cffi import requests
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            data = res.json().get("meta", {})
            name = data.get("name", "")
            year = str(data.get("year", ""))
            print(f"  └ ✅ Berjaya! Tajuk: '{name}', Tahun: '{year}'")
            log_stage("cinemeta_resolve", "SUCCESS", {"name": name, "year": year})
            return name, year
    except Exception as e:
        print(f"  └ ❌ Ralat Cinemeta API: {e}")
        REPORT["errors"].append(f"Cinemeta Error: {e}")
        
    log_stage("cinemeta_resolve", "FAILED", {})
    return "", ""

# ------------------------------------------------------------------
# FASA 2: WARM-UP SESI & PENYEMAKAN CLOUDFLARE BLOCK
# ------------------------------------------------------------------
def is_blocked(html: str, status_code: int) -> bool:
    if status_code in [403, 503]:
        return True
    html_lower = html.lower()
    return "just a moment..." in html_lower or "attention required!" in html_lower or "enable javascript" in html_lower

def fetch_url_with_session(url: str, referer: str = None, session=None) -> tuple[bool, str, str, bytes, str, any]:
    """
    Sistem penarik URL bertingkat dengan header navigasi penuh dan rotasi impersonate.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,id;q=0.8,ms;q=0.7",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Referer": referer if referer else f"{BASE_URL}/"
    }

    # 1. Enjin curl-cffi (Sesi Utama + Rotasi Impersonate)
    try:
        from curl_cffi import requests
        impersonate_targets = ["chrome120", "safari15_5", "chrome119", "edge101"]
        
        s = session
        for imp in impersonate_targets:
            if not s:
                s = requests.Session(impersonate=imp)
            
            resp = s.get(url, headers=headers, allow_redirects=True, timeout=15)
            final_url = str(resp.url)
            
            if resp.status_code == 200 and not is_blocked(resp.text, resp.status_code):
                return True, resp.text, f"curl-cffi ({imp})", resp.content, final_url, s
            
            # Reset sesi jika ditolak untuk mencuba impersonate baru
            s = None
        
        print(f"  ⚠️ [curl-cffi] Ditolak pada semua profil impersonate. Memulakan fallback pelayar...")
    except Exception as e:
        print(f"  ⚠️ [curl-cffi] Ralat Rangkaian: {e}")

    # 2. Enjin Camoufox (Stealth Browser)
    try:
        from camoufox.async_api import AsyncCamoufox
        async def _run_camoufox():
            async with AsyncCamoufox(headless=True) as browser:
                page = await browser.new_page()
                if referer:
                    await page.set_extra_http_headers({"Referer": referer})
                res = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(2000)
                content = await page.content()
                st = res.status if res else 0
                f_url = page.url
                return st, content, f_url

        st, content, f_url = asyncio.run(_run_camoufox())
        if st == 200 and not is_blocked(content, st):
            return True, content, "Camoufox", content.encode("utf-8"), f_url, None
    except Exception as e:
        print(f"  ⚠️ [Camoufox] Ralat: {e}")

    # 3. Enjin DrissionPage (Pengendalian Ralat WSL2)
    try:
        from DrissionPage import ChromiumPage, ChromiumOptions
        co = ChromiumOptions()
        co.set_argument('--headless=new')
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-dev-shm-usage')
        co.set_argument('--disable-gpu')
        
        dp = ChromiumPage(co)
        dp.get(url, retry=1, interval=1)
        time.sleep(2)
        content = dp.html
        f_url = dp.url
        dp.quit()
        if not is_blocked(content, 200):
            return True, content, "DrissionPage", content.encode("utf-8"), f_url, None
    except Exception as e:
        print(f"  ⚠️ [DrissionPage] Ralat: {e}")

    return False, "", "None", b"", url, None

# ------------------------------------------------------------------
# FASA 3: PENGECAMAN CARIAN & REDIRECT HANDLING
# ------------------------------------------------------------------
def parse_movie_links(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    movies = []
    
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        title = a.get_text().strip()
        if re.match(r"^/subscene/\d+$", href) and title:
            full_url = urljoin(BASE_URL, href)
            if full_url not in [m["url"] for m in movies]:
                movies.append({"title": title, "url": full_url})
    return movies

def parse_subtitles_from_movie_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    subtitles = []
    
    for tr in soup.find_all("tr"):
        row_text = tr.get_text(" ", strip=True).lower()
        
        # Elak Malayalam
        if "malayalam" in row_text:
            continue
            
        lang = None
        if "bahasa melayu" in row_text or "melayu" in row_text or "malay" in row_text:
            lang = "ms"
        elif "bahasa indonesia" in row_text or "indonesian" in row_text or "indonesia" in row_text:
            lang = "id"

        if lang:
            a_tag = tr.find("a", href=True)
            if a_tag and re.match(r"^/subtitle/\d+$", a_tag["href"]):
                subtitles.append({
                    "lang": lang,
                    "release": a_tag.get_text(strip=True),
                    "url": urljoin(BASE_URL, a_tag["href"])
                })
    return subtitles

# ------------------------------------------------------------------
# SKRIP UTAMA DIAGNOSTIK
# ------------------------------------------------------------------
if __name__ == "__main__":
    imdb_id = "tt0145487" # Spider-Man (2002)
    
    # 1. Warm-Up Sesi Utama untuk Dapatkan Cookies
    print(f"\n[Fasa 2] Memulakan Warm-Up Sesi Utama di: {BASE_URL}")
    w_success, _, w_engine, _, _, active_session = fetch_url_with_session(BASE_URL)
    if not w_success:
        print("❌ CRITICAL: Gagal melepasi Warm-up laman utama Subscene!")
        REPORT["errors"].append("Homepage Warmup Failed")
    else:
        print(f"  └ ✅ Sesi utama diwujudkan menggunakan enjin [{w_engine}]")

    # 2. Resolve IMDb ID
    movie_title, movie_year = resolve_imdb_via_cinemeta(imdb_id)
    REPORT["cinemeta_resolved_title"] = f"{movie_title} ({movie_year})"

    # 3. Strategi Carian Dual-Mode (IMDb ID -> Title Fallback)
    target_movie_url = None
    search_queries = [imdb_id, f"{movie_title} {movie_year}".strip(), movie_title]
    
    print("\n[Fasa 3] Memulakan Pengikisan Carian Subscene...")
    for q in search_queries:
        if not q: continue
        search_url = f"{BASE_URL}/search?query={quote_plus(q)}"
        print(f"  ├─ Mencuba Query: '{q}' -> URL: {search_url}")
        
        s_success, s_html, s_engine, _, final_url, active_session = fetch_url_with_session(
            search_url, referer=BASE_URL, session=active_session
        )

        if not s_success:
            print(f"  │   └ ❌ Carian terhalang atau gagal (Enjin: {s_engine})")
            continue

        # Semak jika Auto-Redirect ke Laman Filem (/subscene/XXXXX)
        if re.search(r"/subscene/\d+$", final_url):
            print(f"  │   └ 🎯 DETECTED AUTO-REDIRECT! Terus ke laman filem: {final_url}")
            target_movie_url = final_url
            log_stage("search_subscene", "SUCCESS_REDIRECT", {"query": q, "final_url": final_url})
            break

        # Jika Laman Hasil Carian Biasa
        movies = parse_movie_links(s_html)
        if movies:
            print(f"  │   └ 🎯 Jumpa {len(movies)} padanan filem dalam senarai!")
            target_movie_url = movies[0]["url"]
            log_stage("search_subscene", "SUCCESS_PARSED", {"query": q, "movie": movies[0]})
            break
        else:
            print("  │   └ ⚠️ Tiada tajuk dijumpai dalam HTML carian.")

    if not target_movie_url:
        print("\n❌ CRITICAL: Keseluruhan cubaan carian gagal ditemui!")
        log_stage("search_subscene", "FAILED", {})
        with open(REPORT_PATH, "w") as f: json.dump(REPORT, f, indent=2)
        sys.exit(1)

    # 4. Ekstrak Laman Filem & Sarikata
    print(f"\n[Fasa 4] Membuka Laman Filem Sasaran: {target_movie_url}")
    m_success, m_html, m_engine, _, _, active_session = fetch_url_with_session(
        target_movie_url, referer=BASE_URL, session=active_session
    )
    
    if not m_success:
        print("❌ Gagal membaca HTML laman filem.")
        log_stage("fetch_movie_page", "FAILED", {"url": target_movie_url})
        sys.exit(1)

    subtitles = parse_subtitles_from_movie_page(m_html)
    print(f"  └ ✅ Ditemui {len(subtitles)} sarikata BM/ID!")
    log_stage("fetch_movie_page", "SUCCESS", {"count": len(subtitles)})

    if not subtitles:
        print("❌ Tiada sarikata BM/ID ditemui dalam jadual filem.")
        sys.exit(1)

    # 5. Ujian Muat Turun & Pengekstrak Zip
    sample_sub = subtitles[0]
    print(f"\n[Fasa 5] Membuka Detail Sarikata: {sample_sub['release']} ({sample_sub['lang']})")
    print(f"  └ URL: {sample_sub['url']}")

    d_success, d_html, d_engine, _, _, active_session = fetch_url_with_session(
        sample_sub["url"], referer=target_movie_url, session=active_session
    )

    soup = BeautifulSoup(d_html, "lxml")
    dl_link = None
    for a in soup.find_all("a", href=True):
        if re.match(r"^/download/\d+$", a["href"]):
            dl_link = urljoin(BASE_URL, a["href"])
            break

    if not dl_link:
        print("❌ Pautan /download/XXXXXX tidak ditemui di laman detail.")
        log_stage("download_sub", "FAILED_NO_LINK", {})
        sys.exit(1)

    print(f"  └ ✅ Jumpa pautan muat turun: {dl_link}")

    # Muat Turun Fail Binary
    bin_success, _, bin_engine, bin_bytes, _, _ = fetch_url_with_session(
        dl_link, referer=sample_sub["url"], session=active_session
    )

    if not bin_success or len(bin_bytes) < 100:
        print("❌ Gagal memuat turun fail binary sarikata.")
        log_stage("download_sub", "FAILED_BINARY", {})
        sys.exit(1)

    print(f"  └ ✅ Muat turun berjaya! Saiz: {len(bin_bytes)} bytes (Enjin: {bin_engine})")

    # Ekstrak SRT
    print("\n[Fasa 6] Pengekstrak Fail SRT...")
    extracted_files = []
    try:
        with zipfile.ZipFile(io.BytesIO(bin_bytes)) as z:
            for file_name in z.namelist():
                if file_name.endswith(".srt"):
                    out_p = OUTPUT_DIR / file_name
                    with open(out_p, "wb") as f: f.write(z.read(file_name))
                    extracted_files.append(str(out_p))
    except zipfile.BadZipFile:
        out_p = OUTPUT_DIR / "subtitle.srt"
        with open(out_p, "wb") as f: f.write(bin_bytes)
        extracted_files.append(str(out_p))

    if extracted_files:
        print(f"  └ 🎉 UJIAN BERJAYA! {len(extracted_files)} fail .srt diekstrak.")
        REPORT["success"] = True
        log_stage("extraction", "SUCCESS", {"files": extracted_files})
    else:
        print("❌ Tiada fail .srt dihasilkan selepas ekstrak.")
        log_stage("extraction", "FAILED", {})

    # Laporan Akhir
    with open(REPORT_PATH, "w") as f:
        json.dump(REPORT, f, indent=2)

    print("\n" + "=" * 80)
    print(f"📄 Laporan Diagnostik disimpan di: {REPORT_PATH}")
    print("=" * 80)