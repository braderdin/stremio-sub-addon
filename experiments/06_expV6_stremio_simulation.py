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
print("🎬 SIMULASI END-TO-END STREMIO PAYLOAD V6 (06_expV6_stremio_simulation.py)")
print("   IMDb ID -> Cinemeta -> Camoufox Search -> Smart Match -> Stremio JSON")
print("=" * 80)

BASE_URL = "https://sub-scene.com"
OUTPUT_DIR = Path(__file__).resolve().parent / "extracted_subs"
OUTPUT_DIR.mkdir(exist_ok=True)
REPORT_PATH = Path(__file__).resolve().parent / "stremio_simulation_v6_report.json"

REPORT = {
    "test_imdb_id": "tt0145487",
    "cinemeta_meta": {},
    "smart_matched_movie": None,
    "stremio_subtitles_payload": [],
    "success": False
}

# ------------------------------------------------------------------
# FASA 1: RESOLVE METADATA CINEMETA (STREMIO STANDARD)
# ------------------------------------------------------------------
def fetch_cinemeta_meta(imdb_id: str) -> tuple[str, str]:
    print(f"\n[Fasa 1] Menyemak IMDb ID melalui Cinemeta API: {imdb_id}")
    url = f"https://v3-cinemeta.strem.io/meta/movie/{imdb_id}.json"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            meta = res.json().get("meta", {})
            name = meta.get("name", "")
            year = str(meta.get("year", ""))
            print(f"  └ ✅ Sah: '{name}' ({year})")
            REPORT["cinemeta_meta"] = {"title": name, "year": year}
            return name, year
    except Exception as e:
        print(f"  └ ❌ Ralat Cinemeta: {e}")
    return "", ""

# ------------------------------------------------------------------
# FASA 2: ALGORITMA SMART MATCHING & YEAR SCORER
# ------------------------------------------------------------------
def calculate_match_score(candidate_title: str, target_title: str, target_year: str) -> int:
    score = 0
    c_lower = candidate_title.lower()
    t_lower = target_title.lower()

    # 1. Padanan Tahun Tepat (Pemberat Tertinggi)
    if target_year and target_year in c_lower:
        score += 50
    elif target_year:
        # Penalti jika tahun langsung tidak sepadan (cth: 2019 vs 2002)
        score -= 20

    # 2. Penalti Filem Animasi Lego, Siri TV, atau Parodi Lucah
    unwanted_keywords = ["lego", "xxx", "porn", "parody", "season", "complete series", "animated series"]
    for bad in unwanted_keywords:
        if bad in c_lower and bad not in t_lower:
            score -= 60

    # 3. Nisbah Padanan Perkataan Tajuk
    clean_target = set(re.sub(r"[^a-zA-Z0-9\s]", "", t_lower).split())
    clean_candidate = set(re.sub(r"[^a-zA-Z0-9\s]", "", c_lower).split())
    
    intersect = clean_target.intersection(clean_candidate)
    score += int((len(intersect) / len(clean_target)) * 40)

    # 4. Bonus jika permulaan tajuk tepat
    if c_lower.startswith(t_lower):
        score += 15

    return score

def pick_best_movie_match(movies: list[dict], target_title: str, target_year: str) -> dict:
    scored_movies = []
    for m in movies:
        sc = calculate_match_score(m["title"], target_title, target_year)
        scored_movies.append((sc, m))

    # Susun ikut markah tertinggi
    scored_movies.sort(key=lambda x: x[0], reverse=True)
    
    print("\n  📊 Skor Padanan Calon Filem:")
    for sc, m in scored_movies[:5]:
        print(f"      ├─ [{sc} pts] {m['title']}")

    best_match = scored_movies[0][1]
    return best_match

# ------------------------------------------------------------------
# FASA 3: CARIAN CAMOUFOX & RESOLUSI KUKI TURNSTILE
# ------------------------------------------------------------------
async def search_camoufox(query: str) -> tuple[list[dict], dict, str]:
    from camoufox.async_api import AsyncCamoufox
    
    search_url = f"{BASE_URL}/search?query={quote_plus(query)}"
    print(f"\n[Fasa 2] Melancarkan Camoufox ke Endpoint Carian: {search_url}")
    
    results = []
    extracted_cookies = {}
    user_agent = ""

    async with AsyncCamoufox(headless=True) as browser:
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
        user_agent = await page.evaluate("navigator.userAgent")

        final_html = ""
        for _ in range(25):
            await page.wait_for_timeout(1000)
            try:
                content = await page.content()
                lower = content.lower()
                if "just a moment" not in lower and "attention required" not in lower:
                    if "/subscene/" in content or "no results" in lower:
                        final_html = content
                        break
            except Exception:
                continue

        if not final_html:
            try: final_html = await page.content()
            except Exception: final_html = ""

        raw_cookies = await ctx.cookies()
        for ck in raw_cookies:
            extracted_cookies[ck["name"]] = ck["value"]

        if final_html:
            soup = BeautifulSoup(final_html, "lxml")
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                title = a.get_text(strip=True)
                if re.match(r"^/subscene/\d+$", href) and title:
                    results.append({"title": title, "url": urljoin(BASE_URL, href)})

        await ctx.close()

    return results, extracted_cookies, user_agent

# ------------------------------------------------------------------
# FASA 4: EKSTRAKSI SARIKATA & PENJANAAN PAYLOAD STREMIO
# ------------------------------------------------------------------
def fetch_movie_subtitles(movie_url: str, session: requests.Session) -> list[dict]:
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
            lang = "msa"  # Kod ISO 639-2 untuk Stremio
        elif any(w in row_text for w in ["bahasa indonesia", "indonesia", "indonesian"]):
            lang = "ind"  # Kod ISO 639-2 untuk Stremio

        if lang:
            a_tag = tr.find("a", href=True)
            if a_tag and re.match(r"^/subtitle/\d+$", a_tag["href"]):
                subtitles.append({
                    "lang": lang,
                    "release": a_tag.get_text(strip=True),
                    "detail_url": urljoin(BASE_URL, a_tag["href"]),
                    "sub_id": a_tag["href"].split("/")[-1]
                })
    return subtitles

def download_and_extract_sample(detail_url: str, session: requests.Session) -> tuple[bool, str, str]:
    d_resp = session.get(detail_url, headers={"Referer": f"{BASE_URL}/"}, timeout=12)
    soup = BeautifulSoup(d_resp.text, "lxml")
    dl_link = None
    for a in soup.find_all("a", href=True):
        if re.match(r"^/download/\d+$", a["href"]):
            dl_link = urljoin(BASE_URL, a["href"])
            break

    if not dl_link:
        return False, "", ""

    bin_resp = session.get(dl_link, headers={"Referer": detail_url}, timeout=15)
    raw_bytes = bin_resp.content

    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
            for name in z.namelist():
                if name.endswith((".srt", ".vtt")):
                    content = z.read(name).decode("utf-8", errors="ignore")
                    return True, name, content
    except zipfile.BadZipFile:
        return True, "sample.srt", raw_bytes.decode("utf-8", errors="ignore")

    return False, "", ""

# ------------------------------------------------------------------
# PROSES UTAMA SIMULASI
# ------------------------------------------------------------------
if __name__ == "__main__":
    test_imdb = "tt0145487" # Spider-Man (2002)

    # 1. Resolusi Metadata
    target_title, target_year = fetch_cinemeta_meta(test_imdb)
    if not target_title:
        print("❌ Gagal mendapatkan metadata filem.")
        sys.exit(1)

    # 2. Carian Camoufox
    found_movies, cookies, ua = asyncio.run(search_camoufox(target_title))
    if not found_movies:
        print("❌ Tiada filem ditemui semasa carian.")
        sys.exit(1)

    # 3. Penapisan Pintar (Smart Matching)
    selected_movie = pick_best_movie_match(found_movies, target_title, target_year)
    REPORT["smart_matched_movie"] = selected_movie
    print(f"\n🎯 Filem Tepat Dipilih Secara Automatik:")
    print(f"  ├─ Tajuk : {selected_movie['title']}")
    print(f"  └─ URL   : {selected_movie['url']}")

    # 4. Handover Kuki ke curl-cffi
    session = requests.Session(impersonate="chrome120")
    for k, v in cookies.items():
        session.cookies.set(k, v, domain="sub-scene.com")
    if ua:
        session.headers.update({"User-Agent": ua})

    # 5. Ekstrak Sarikata
    print(f"\n[Fasa 3] Mengutip Senarai Sarikata...")
    sub_list = fetch_movie_subtitles(selected_movie["url"], session)
    print(f"  └ ✅ Ditemui {len(sub_list)} sarikata Bahasa Melayu/Indonesia.")

    if not sub_list:
        print("⚠️ Tiada sarikata BM/ID bagi filem ini.")
        sys.exit(0)

    # 6. Simulasi Muat Turun & Pembentukan Payload Stremio
    print(f"\n[Fasa 4] Menguji Muat Turun & Menjana Simulasi Payload Stremio...")
    sample_sub = sub_list[0]
    ok, filename, srt_content = download_and_extract_sample(sample_sub["detail_url"], session)

    if ok:
        print(f"  └ ✅ Berjaya ekstrak: {filename}")
        preview_lines = [l for l in srt_content.splitlines() if l.strip()][:5]
        print(f"  └ 📝 Pratonton Dialog: {' // '.join(preview_lines)}")

        # Bentuk JSON mengikut format rasmi Stremio Subtitles API
        stremio_payload = {
            "subtitles": [
                {
                    "id": f"{s['sub_id']}",
                    "url": f"https://stremio-b2-cdn.contoh.com/subs/{test_imdb}/{s['lang']}_{s['sub_id']}.srt",
                    "lang": s["lang"],
                    "release": s["release"]
                }
                for s in sub_list
            ]
        }
        REPORT["stremio_subtitles_payload"] = stremio_payload
        REPORT["success"] = True

        print("\n" + "=" * 80)
        print("🎉 SIMULASI PAYLOAD STREMIO BERJAYA DIJANA:")
        print(json.dumps(stremio_payload, indent=2)[:400] + "\n... (dipendekkan)")
        print("=" * 80)

    with open(REPORT_PATH, "w") as f:
        json.dump(REPORT, f, indent=2)

    print(f"\n📄 Laporan penuh disimpan di: {REPORT_PATH}")