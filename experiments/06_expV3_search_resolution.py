import sys
import os
import re
import io
import time
import json
import zipfile
import asyncio
from pathlib import Path
from urllib.parse import urljoin, quote_plus, unquote
from bs4 import BeautifulSoup
from curl_cffi import requests

print("=" * 80)
print("🚀 UJIAN CARIAN & RESOLUSI KALIS-GAGAL V3 (06_expV3_search_resolution.py)")
print("   Cinemeta -> Multi-Strategy Search -> Strict Matching -> SRT Extract")
print("=" * 80)

BASE_URL = "https://sub-scene.com"
OUTPUT_DIR = Path(__file__).resolve().parent / "extracted_subs"
OUTPUT_DIR.mkdir(exist_ok=True)
REPORT_PATH = Path(__file__).resolve().parent / "search_resolution_v3_report.json"

REPORT = {
    "timestamp": time.time(),
    "target_imdb": "tt0145487",
    "resolved_meta": None,
    "strategy_used": None,
    "found_movie": None,
    "subtitles_count": 0,
    "download_success": False
}

# ------------------------------------------------------------------
# FASA 1: RESOLVE CINEMETA IMDB -> TAJUK FILEM
# ------------------------------------------------------------------
def resolve_cinemeta(imdb_id: str) -> tuple[str, str]:
    print(f"\n[Fasa 1] Resolusi IMDb ID via Cinemeta: {imdb_id}")
    url = f"https://v3-cinemeta.strem.io/meta/movie/{imdb_id}.json"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            meta = r.json().get("meta", {})
            name = meta.get("name", "")
            year = str(meta.get("year", ""))
            print(f"  └ ✅ Cinemeta Berjaya: '{name}' ({year})")
            REPORT["resolved_meta"] = {"name": name, "year": year}
            return name, year
    except Exception as e:
        print(f"  └ ⚠️ Ralat Cinemeta: {e}")
    return "", ""

# ------------------------------------------------------------------
# UTILITI: PENAPIS PADANAN TAJUK KETAT (MENGELAK FALSE POSITIVE)
# ------------------------------------------------------------------
def is_title_matched(query: str, candidate_title: str) -> bool:
    """
    Memastikan calon filem benar-benar mengandungi kata kunci asal.
    Mengelakkan laman menyerap filem rawak dari senarai terkini.
    """
    clean_q = re.sub(r"[^a-zA-Z0-9\s]", "", query.lower()).split()
    clean_c = re.sub(r"[^a-zA-Z0-9\s]", "", candidate_title.lower()).split()
    
    if not clean_q:
        return False

    matches = [word for word in clean_q if word in clean_c]
    # Sekurang-kurangnya 60% perkataan carian mesti wujud dalam tajuk
    ratio = len(matches) / len(clean_q)
    return ratio >= 0.5

def is_cloudflare_blocked(html: str, status_code: int) -> bool:
    if status_code in [403, 503]:
        return True
    lower = html.lower()
    return "just a moment..." in lower or "attention required!" in lower

# ------------------------------------------------------------------
# STRATEGI 1: PERSISTENT CURL-CFFI SESSION
# ------------------------------------------------------------------
def search_via_persistent_curl(query: str, session: requests.Session) -> list[dict]:
    print(f"\n[Strategi 1] Mencuba Persistent curl-cffi Session...")
    results = []
    search_url = f"{BASE_URL}/search?query={quote_plus(query)}"
    
    try:
        resp = session.get(search_url, headers={"Referer": f"{BASE_URL}/"}, allow_redirects=True, timeout=12)
        print(f"  └ Status Kod: {resp.status_code} | URL: {resp.url}")

        if resp.status_code == 200 and not is_cloudflare_blocked(resp.text, resp.status_code):
            # Jika berlaku auto-redirect terus ke laman filem
            if re.search(r"/subscene/\d+$", str(resp.url)):
                results.append({"title": query, "url": str(resp.url), "source": "redirect"})
                return results

            soup = BeautifulSoup(resp.text, "lxml")
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                title = a.get_text(strip=True)
                if re.match(r"^/subscene/\d+$", href) and title:
                    if is_title_matched(query, title):
                        results.append({"title": title, "url": urljoin(BASE_URL, href), "source": "native_search"})
    except Exception as e:
        print(f"  └ ⚠️ Ralat Strategi 1: {e}")

    return results

# ------------------------------------------------------------------
# STRATEGI 2: CAMOUFOX STEALTH BROWSER (WSL SAFE)
# ------------------------------------------------------------------
def search_via_camoufox(query: str) -> list[dict]:
    print(f"\n[Strategi 2] Mencuba Camoufox Stealth Engine...")
    results = []
    search_url = f"{BASE_URL}/search?query={quote_plus(query)}"

    try:
        from camoufox.async_api import AsyncCamoufox
        async def _scrape():
            async with AsyncCamoufox(headless=True) as browser:
                page = await browser.new_page()
                res = await page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
                await page.wait_for_timeout(2500)
                html = await page.content()
                f_url = page.url
                st = res.status if res else 0
                return st, html, f_url

        st, html, f_url = asyncio.run(_scrape())
        print(f"  └ Camoufox Status: {st} | Final URL: {f_url}")

        if st == 200 and not is_cloudflare_blocked(html, st):
            if re.search(r"/subscene/\d+$", f_url):
                return [{"title": query, "url": f_url, "source": "camoufox_redirect"}]

            soup = BeautifulSoup(html, "lxml")
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                title = a.get_text(strip=True)
                if re.match(r"^/subscene/\d+$", href) and title:
                    if is_title_matched(query, title):
                        results.append({"title": title, "url": urljoin(BASE_URL, href), "source": "camoufox_search"})
    except Exception as e:
        print(f"  └ ⚠️ Camoufox tidak tersedia atau ralat: {e}")

    return results

# ------------------------------------------------------------------
# STRATEGI 3: EXTERNAL SEARCH FALLBACK (DUCKDUCKGO HTML)
# ------------------------------------------------------------------
def search_via_external_ddg(query: str, session: requests.Session) -> list[dict]:
    print(f"\n[Strategi 3] Mencuba External Search Index (DuckDuckGo HTML)...")
    results = []
    # Cari terus entri subscene bagi tajuk spesifik
    ddg_query = f"site:sub-scene.com {query}"
    ddg_url = f"https://html.duckduckgo.com/html/?q={quote_plus(ddg_query)}"
    
    try:
        resp = session.get(ddg_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}, timeout=12)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "lxml")
            for a in soup.find_all("a", class_="result__url", href=True):
                raw_href = a["href"]
                # Ekstrak pautan sebenar dari wrapper redirect DDG
                match = re.search(r"uddg=([^&]+)", raw_href)
                target = unquote(match.group(1)) if match else raw_href
                
                # Terima pautan laman filem /subscene/ID
                m = re.search(r"https?://sub-scene\.com/subscene/(\d+)", target)
                if m:
                    full_url = f"{BASE_URL}/subscene/{m.group(1)}"
                    if full_url not in [r["url"] for r in results]:
                        results.append({"title": query, "url": full_url, "source": "ddg_index"})
    except Exception as e:
        print(f"  └ ⚠️ Ralat Strategi 3: {e}")

    return results

# ------------------------------------------------------------------
# FASA 3 & 4: EKSTRAK SARIKATA BM/ID & MUAT TURUN
# ------------------------------------------------------------------
def get_malay_indo_subtitles(movie_url: str, session: requests.Session) -> list[dict]:
    print(f"\n[Fasa 3] Membaca Jadual Sarikata di: {movie_url}")
    subtitles = []
    try:
        resp = session.get(movie_url, headers={"Referer": f"{BASE_URL}/"}, timeout=15)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        for tr in soup.find_all("tr"):
            row_text = tr.get_text(" ", strip=True).lower()
            if "malayalam" in row_text:
                continue

            lang = None
            if "bahasa melayu" in row_text or "melayu" in row_text or "malay" in row_text:
                lang = "ms"
            elif "bahasa indonesia" in row_text or "indonesia" in row_text or "indonesian" in row_text:
                lang = "id"

            if lang:
                a_tag = tr.find("a", href=True)
                if a_tag and re.match(r"^/subtitle/\d+$", a_tag["href"]):
                    subtitles.append({
                        "lang": lang,
                        "release": a_tag.get_text(strip=True),
                        "detail_url": urljoin(BASE_URL, a_tag["href"])
                    })
    except Exception as e:
        print(f"  └ ⚠️ Ralat membaca sarikata: {e}")

    return subtitles

def download_and_extract_srt(detail_url: str, session: requests.Session) -> bool:
    print(f"\n[Fasa 4] Menguji Muat Turun Sarikata dari: {detail_url}")
    try:
        # 1. Buka laman detail untuk cari pautan /download/XXXXXX
        d_resp = session.get(detail_url, headers={"Referer": f"{BASE_URL}/"}, timeout=12)
        if d_resp.status_code != 200:
            return False

        soup = BeautifulSoup(d_resp.text, "lxml")
        dl_href = None
        for a in soup.find_all("a", href=True):
            if re.match(r"^/download/\d+$", a["href"]):
                dl_href = urljoin(BASE_URL, a["href"])
                break

        if not dl_href:
            print("  └ ❌ Pautan /download/XXXXXX tidak dijumpai.")
            return False

        print(f"  └ ✅ Jumpa pautan muat turun: {dl_href}")

        # 2. Muat turun binary ZIP/SRT
        bin_resp = session.get(dl_href, headers={"Referer": detail_url}, timeout=15)
        raw_bytes = bin_resp.content

        if len(raw_bytes) < 100:
            print("  └ ❌ Fail muat turun rosak atau kosong.")
            return False

        print(f"  └ ✅ Saiz fail: {len(raw_bytes)} bytes")

        # 3. Ekstrak
        extracted_files = []
        try:
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
                for name in z.namelist():
                    if name.endswith((".srt", ".vtt")):
                        p = OUTPUT_DIR / name
                        with open(p, "wb") as f:
                            f.write(z.read(name))
                        extracted_files.append(str(p))
        except zipfile.BadZipFile:
            p = OUTPUT_DIR / "direct_subtitle.srt"
            with open(p, "wb") as f:
                f.write(raw_bytes)
            extracted_files.append(str(p))

        if extracted_files:
            print(f"  └ 🎉 BERJAYA EXTRACT {len(extracted_files)} FAIL SRT!")
            for fpath in extracted_files:
                print(f"      ├─ Fail: {fpath}")
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    preview = [f.readline().strip() for _ in range(6)]
                print(f"      └─ Pratonton Kandungan: {' | '.join(preview)}")
            return True

    except Exception as e:
        print(f"  └ ❌ Ralat proses muat turun: {e}")

    return False

# ------------------------------------------------------------------
# PROSES UTAMA
# ------------------------------------------------------------------
if __name__ == "__main__":
    test_imdb = "tt0145487" # Spider-Man (2002)
    title, year = resolve_cinemeta(test_imdb)

    if not title:
        print("❌ Gagal mendapatkan metadata dari Cinemeta.")
        sys.exit(1)

    # Inisialisasi Sesi Tunggal curl-cffi
    main_session = requests.Session(impersonate="chrome120")

    # Warm-up Laman Utama untuk kutip kuki Cloudflare
    print(f"\n[Warm-up] Membuka Laman Utama ({BASE_URL})...")
    w_resp = main_session.get(BASE_URL, timeout=12)
    print(f"  └ Kuki Sesi Diterima: {list(main_session.cookies.keys())}")

    search_query = f"{title} {year}".strip()
    matched_movies = []
    used_strategy = None

    # Cuba Strategi 1
    matched_movies = search_via_persistent_curl(search_query, main_session)
    if matched_movies:
        used_strategy = "Persistent curl-cffi"
    else:
        # Cuba variasi nama ringkas
        matched_movies = search_via_persistent_curl(title, main_session)
        if matched_movies:
            used_strategy = "Persistent curl-cffi (Short Title)"

    # Cuba Strategi 2 (Jika Strategi 1 disekat)
    if not matched_movies:
        matched_movies = search_via_camoufox(search_query)
        if matched_movies:
            used_strategy = "Camoufox Stealth"

    # Cuba Strategi 3 (Jika Strategi 1 & 2 tiada hasil)
    if not matched_movies:
        matched_movies = search_via_external_ddg(f"{title} {year}", main_session)
        if matched_movies:
            used_strategy = "DuckDuckGo External Index"

    if not matched_movies:
        print(f"\n❌ CRITICAL: Tiada hasil carian yang sah bagi tajuk '{title}'.")
        with open(REPORT_PATH, "w") as f:
            json.dump(REPORT, f, indent=2)
        sys.exit(1)

    chosen_movie = matched_movies[0]
    REPORT["strategy_used"] = used_strategy
    REPORT["found_movie"] = chosen_movie
    print(f"\n🎯 SASARAN TEPAT DIJUMPAI!")
    print(f"  ├─ Enjin Digunakan: {used_strategy}")
    print(f"  ├─ Tajuk Filem     : {chosen_movie['title']}")
    print(f"  └─ URL Sasaran     : {chosen_movie['url']}")

    # Ambil Sarikata
    subs = get_malay_indo_subtitles(chosen_movie["url"], main_session)
    REPORT["subtitles_count"] = len(subs)
    print(f"  └ ✅ Menemui {len(subs)} sarikata Bahasa Melayu / Indonesia.")

    if not subs:
        print("⚠️ Filem ini tiada rekod sarikata BM/ID di Subscene.")
        with open(REPORT_PATH, "w") as f:
            json.dump(REPORT, f, indent=2)
        sys.exit(0)

    # Uji muat turun sarikata pertama
    dl_ok = download_and_extract_srt(subs[0]["detail_url"], main_session)
    REPORT["download_success"] = dl_ok

    with open(REPORT_PATH, "w") as f:
        json.dump(REPORT, f, indent=2)

    print("\n" + "=" * 80)
    print(f"📄 Laporan Penuh Disimpan: {REPORT_PATH}")
    print("=" * 80)