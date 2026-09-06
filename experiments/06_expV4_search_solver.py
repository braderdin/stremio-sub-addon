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
print("🚀 UJIAN RESOLUSI CARIAN & BYPASS WAF V4 (06_expV4_search_solver.py)")
print("   Menguji: 1. Camoufox Turnstile Solver | 2. Bing Index | 3. SubDL API")
print("=" * 80)

BASE_URL = "https://sub-scene.com"
OUTPUT_DIR = Path(__file__).resolve().parent / "extracted_subs"
OUTPUT_DIR.mkdir(exist_ok=True)
REPORT_PATH = Path(__file__).resolve().parent / "search_solver_v4_report.json"

REPORT = {
    "imdb_id": "tt0145487",
    "movie_title": "Spider-Man (2002)",
    "methods_tested": {},
    "successful_method": None,
    "download_verified": False
}

# ------------------------------------------------------------------
# KAEDAH 1: CAMOUFOX TURNSTILE SOLVER (TUNGGU SELESAI CABARAN CLOUDFLARE)
# ------------------------------------------------------------------
def solve_subscene_search_camoufox(query: str) -> list[dict]:
    print("\n[Kaedah 1] Menguji Camoufox dengan Turnstile Wait...")
    search_url = f"{BASE_URL}/search?query={quote_plus(query)}"
    results = []

    try:
        from camoufox.async_api import AsyncCamoufox
        async def _run():
            async with AsyncCamoufox(headless=True) as browser:
                page = await browser.new_page()
                print(f"  ├─ Membuka: {search_url}")
                await page.goto(search_url, wait_until="commit", timeout=30000)

                # Beri masa pelayar meleraikan cabaran Turnstile (sehingga 12 saat)
                print("  ├─ Menunggu penyelesaian Cloudflare Turnstile...")
                for sec in range(12):
                    await page.wait_for_timeout(1000)
                    content = await page.content()
                    # Jika perkataan cabaran sudah hilang dan ada pautan /subscene/
                    if "/subscene/" in content and "just a moment" not in content.lower():
                        print(f"  ├─ ✅ Turnstile selesai dalam ~{sec + 1} saat!")
                        break

                html = await page.content()
                f_url = page.url
                cookies = await page.context.cookies()
                return html, f_url, cookies

        html, final_url, cookies = asyncio.run(_run())
        
        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            title = a.get_text(strip=True)
            if re.match(r"^/subscene/\d+$", href) and title:
                full_u = urljoin(BASE_URL, href)
                if full_u not in [r["url"] for r in results]:
                    results.append({"title": title, "url": full_u, "source": "camoufox_turnstile"})

        if results:
            print(f"  └ ✅ Kaedah 1 Berjaya! Menemui {len(results)} entri.")
        else:
            print("  └ ⚠️ Kaedah 1: Tiada entri ditemui selepas cabaran.")
    except Exception as e:
        print(f"  └ ❌ Ralat Kaedah 1: {e}")

    REPORT["methods_tested"]["camoufox_turnstile"] = len(results) > 0
    return results

# ------------------------------------------------------------------
# KAEDAH 2: BING SEARCH INDEX LOOKUP (PINTAS TERUS KE /subscene/ID)
# ------------------------------------------------------------------
def solve_via_bing_index(query: str, session: requests.Session) -> list[dict]:
    print("\n[Kaedah 2] Menguji Bing Index Lookup (Pintas /search)...")
    results = []
    bing_query = f"site:sub-scene.com/subscene {query}"
    bing_url = f"https://www.bing.com/search?q={quote_plus(bing_query)}"

    try:
        resp = session.get(bing_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }, timeout=12)

        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "lxml")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                m = re.search(r"https?://sub-scene\.com/subscene/(\d+)", href)
                if m:
                    full_u = f"{BASE_URL}/subscene/{m.group(1)}"
                    title = a.get_text(strip=True) or query
                    if full_u not in [r["url"] for r in results]:
                        results.append({"title": title, "url": full_u, "source": "bing_index"})

        if results:
            print(f"  └ ✅ Kaedah 2 Berjaya! Menemui pautan terus: {results[0]['url']}")
        else:
            print("  └ ⚠️ Kaedah 2: Tiada pautan sub-scene dijumpai dalam indeks Bing.")
    except Exception as e:
        print(f"  └ ❌ Ralat Kaedah 2: {e}")

    REPORT["methods_tested"]["bing_index"] = len(results) > 0
    return results

# ------------------------------------------------------------------
# KAEDAH 3: SUBDL API INTEGRATION (ARKIB ASAL SUBSCENE DENGAN IMDB DIRECT)
# ------------------------------------------------------------------
def solve_via_subdl_api(imdb_id: str) -> list[dict]:
    print(f"\n[Kaedah 3] Menguji Arkib SubDL API untuk IMDb: {imdb_id}...")
    subtitles = []
    # Endpoint awam SubDL v1 (menyokong carian tepat mengikut IMDb ID)
    api_url = f"https://api.subdl.com/api/v1/subtitles?imdb_id={imdb_id}&languages=ms,id"

    try:
        resp = requests.get(api_url, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") and data.get("subtitles"):
                for item in data["subtitles"]:
                    lang = item.get("language", "").lower()
                    lang_code = "ms" if "malay" in lang else "id"
                    dl_path = item.get("url", "")
                    if dl_path:
                        subtitles.append({
                            "release": item.get("release_name") or item.get("name", "Unknown"),
                            "lang": lang_code,
                            "download_url": f"https://dl.subdl.com{dl_path}",
                            "source": "subdl_archive"
                        })

        if subtitles:
            print(f"  └ ✅ Kaedah 3 Berjaya! Menemui {len(subtitles)} sarikata BM/ID dari arkib SubDL.")
        else:
            print("  └ ⚠️ Kaedah 3: Respons API tiada entri sarikata BM/ID.")
    except Exception as e:
        print(f"  └ ❌ Ralat Kaedah 3: {e}")

    REPORT["methods_tested"]["subdl_api"] = len(subtitles) > 0
    return subtitles

# ------------------------------------------------------------------
# UTILITI: EKSTRAK SARIKATA BM/ID DARI LAMAN FILEM SUBSCENE
# ------------------------------------------------------------------
def fetch_subscene_subtitles(movie_url: str, session: requests.Session) -> list[dict]:
    print(f"\n[Ekstrak Subscene] Membuka: {movie_url}")
    subtitles = []
    resp = session.get(movie_url, headers={"Referer": f"{BASE_URL}/"}, timeout=12)
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

# ------------------------------------------------------------------
# UTILITI: MUAT TURUN DAN SAHKAN KANDUNGAN SRT
# ------------------------------------------------------------------
def verify_and_download_srt(target_url: str, session: requests.Session, is_subscene: bool = True) -> bool:
    print(f"\n[Pengesahan SRT] Muat turun dari: {target_url}")
    try:
        final_download_url = target_url
        # Jika pautan perincian Subscene, ambil pautan muat turun (/download/ID) dahulu
        if is_subscene:
            d_resp = session.get(target_url, headers={"Referer": f"{BASE_URL}/"}, timeout=12)
            soup = BeautifulSoup(d_resp.text, "lxml")
            for a in soup.find_all("a", href=True):
                if re.match(r"^/download/\d+$", a["href"]):
                    final_download_url = urljoin(BASE_URL, a["href"])
                    break

        print(f"  ├─ Direct Download URL: {final_download_url}")
        bin_resp = session.get(final_download_url, timeout=15)
        raw_bytes = bin_resp.content

        if len(raw_bytes) < 100:
            print("  └ ❌ Saiz bait terlalu kecil atau ralat.")
            return False

        extracted_any = False
        try:
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
                for name in z.namelist():
                    if name.endswith((".srt", ".vtt")):
                        out = OUTPUT_DIR / Path(name).name
                        with open(out, "wb") as f:
                            f.write(z.read(name))
                        print(f"  └ 🎉 BERJAYA EKSTRAK: {out.name} ({len(raw_bytes)} bytes)")
                        extracted_any = True
                        break
        except zipfile.BadZipFile:
            out = OUTPUT_DIR / "direct_download.srt"
            with open(out, "wb") as f:
                f.write(raw_bytes)
            print(f"  └ 🎉 BERJAYA SIMPAN FAIL SRT TERUS: {out.name}")
            extracted_any = True

        return extracted_any
    except Exception as e:
        print(f"  └ ❌ Ralat semasa muat turun sarikata: {e}")
        return False

# ------------------------------------------------------------------
# ALUR KERJA UTAMA
# ------------------------------------------------------------------
if __name__ == "__main__":
    imdb_id = "tt0145487"
    title_query = "Spider-Man 2002"
    session = requests.Session(impersonate="chrome120")

    # 1. Uji Kaedah 1 (Camoufox Turnstile)
    m1_results = solve_subscene_search_camoufox("Spider-Man")
    if m1_results:
        REPORT["successful_method"] = "Camoufox Turnstile"
        subs = fetch_subscene_subtitles(m1_results[0]["url"], session)
        if subs:
            ok = verify_and_download_srt(subs[0]["detail_url"], session, is_subscene=True)
            REPORT["download_verified"] = ok

    # 2. Uji Kaedah 2 jika Kaedah 1 belum memadai (Bing Index)
    if not REPORT.get("download_verified"):
        m2_results = solve_via_bing_index(title_query, session)
        if m2_results:
            REPORT["successful_method"] = "Bing Index Lookup"
            subs = fetch_subscene_subtitles(m2_results[0]["url"], session)
            if subs:
                ok = verify_and_download_srt(subs[0]["detail_url"], session, is_subscene=True)
                REPORT["download_verified"] = ok

    # 3. Uji Kaedah 3 (SubDL Direct IMDb Archive)
    if not REPORT.get("download_verified"):
        m3_subs = solve_via_subdl_api(imdb_id)
        if m3_subs:
            REPORT["successful_method"] = "SubDL API (IMDb Direct)"
            ok = verify_and_download_srt(m3_subs[0]["download_url"], session, is_subscene=False)
            REPORT["download_verified"] = ok

    with open(REPORT_PATH, "w") as f:
        json.dump(REPORT, f, indent=2)

    print("\n" + "=" * 80)
    print(f"📄 UJIAN SELESAI. Kaedah paling berkesan: [{REPORT['successful_method']}]")
    print(f"   Status Muat Turun Sah: {REPORT['download_verified']}")
    print("=" * 80)