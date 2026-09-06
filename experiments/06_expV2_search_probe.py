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
print("🔬 PROBE CARIAN & BROWSE SUBSCENE V2 (06_expV2_search_probe.py)")
print("   Auto-Discovery Form + Probing Multi-Endpoint + Ekstrak SRT")
print("=" * 80)

BASE_URL = "https://sub-scene.com"
OUTPUT_DIR = Path(__file__).resolve().parent / "extracted_subs"
OUTPUT_DIR.mkdir(exist_ok=True)
REPORT_PATH = Path(__file__).resolve().parent / "search_probe_v2_report.json"

REPORT = {
    "timestamp": time.time(),
    "probed_endpoints": [],
    "discovered_form": None,
    "search_results": [],
    "selected_movie": None,
    "subtitles_found": 0,
    "success": False
}

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,id;q=0.8,ms;q=0.7",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1"
}

# ------------------------------------------------------------------
# SEMAKAN BLOK & ENJIN FETCH FLEKSIBEL (GET & POST)
# ------------------------------------------------------------------
def is_blocked(html: str, status_code: int) -> bool:
    if status_code in [403, 503]:
        return True
    html_lower = html.lower()
    return "just a moment..." in html_lower or "attention required!" in html_lower or "enable javascript" in html_lower

def execute_request(url: str, method: str = "GET", data: dict = None, referer: str = None) -> tuple[int, str, bytes, str]:
    """
    Melaksanakan request dengan curl-cffi terlebih dahulu, diikuti fallback selamat.
    Mengembalikan: (status_code, html_text, raw_bytes, final_url)
    """
    headers = BROWSER_HEADERS.copy()
    headers["Referer"] = referer if referer else f"{BASE_URL}/"

    # 1. curl-cffi
    try:
        from curl_cffi import requests
        session = requests.Session(impersonate="chrome120")
        if method.upper() == "POST":
            resp = session.post(url, data=data, headers=headers, allow_redirects=True, timeout=15)
        else:
            resp = session.get(url, headers=headers, allow_redirects=True, timeout=15)
            
        return resp.status_code, resp.text, resp.content, str(resp.url)
    except Exception as e:
        print(f"    [curl-cffi] Ralat: {e}")

    # 2. DrissionPage Fallback (dengan pengurusan ralat WSL selamat)
    try:
        from DrissionPage import ChromiumPage, ChromiumOptions
        co = ChromiumOptions()
        co.set_argument('--headless=new')
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-dev-shm-usage')
        co.set_argument('--disable-gpu')
        
        dp = ChromiumPage(co)
        try:
            dp.get(url, retry=1, interval=1)
            time.sleep(2)
            content = dp.html
            f_url = dp.url
            return 200, content, content.encode("utf-8"), f_url
        finally:
            dp.quit()
    except Exception as e:
        print(f"    [DrissionPage] Ralat: {e}")

    return 0, "", b"", url

# ------------------------------------------------------------------
# FASA 1: AUTO-DISCOVERY STRUKTUR SEARCH FORM
# ------------------------------------------------------------------
def discover_search_form(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    forms = soup.find_all("form")
    for form in forms:
        action = form.get("action", "")
        method = form.get("method", "get").upper()
        inputs = form.find_all("input")
        input_names = [inp.get("name") for inp in inputs if inp.get("name")]
        
        # Cari input yang berkaitan carian
        query_field = next((name for name in input_names if name in ["query", "q", "search", "keyword", "title"]), None)
        if query_field or "search" in action.lower() or "subtitle" in action.lower():
            resolved_action = urljoin(BASE_URL, action) if action else BASE_URL
            return {
                "action": resolved_action,
                "method": method,
                "query_field": query_field if query_field else "query",
                "all_fields": input_names
            }
    return {}

# ------------------------------------------------------------------
# FASA 2: PARSER HASIL CARIAN & BROWSE
# ------------------------------------------------------------------
def parse_movie_results(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    movies = []
    
    # 1. Pautan terus filem /subscene/NUMERIC
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        title = a.get_text(strip=True)
        if re.match(r"^/subscene/\d+$", href) and title:
            full_url = urljoin(BASE_URL, href)
            if full_url not in [m["url"] for m in movies]:
                movies.append({"title": title, "url": full_url, "type": "exact_subscene"})

    # 2. Struktur senarai tajuk (format standard subscene: div.title atau table)
    for td in soup.find_all(["td", "div"], class_=re.compile(r"(title|item|name)", re.I)):
        a = td.find("a", href=True)
        if a:
            href = a["href"].strip()
            title = a.get_text(strip=True)
            if ("/subscene/" in href or "/subtitles/" in href) and title:
                full_url = urljoin(BASE_URL, href)
                if full_url not in [m["url"] for m in movies]:
                    movies.append({"title": title, "url": full_url, "type": "listed_match"})

    return movies

def parse_subtitles_table(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    subtitles = []
    
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
                    "release": a_tag.get_text(strip=True) or "Sarikata",
                    "url": urljoin(BASE_URL, a_tag["href"])
                })
    return subtitles

# ------------------------------------------------------------------
# ALUR KERJA DIAGNOSTIK UTAMA
# ------------------------------------------------------------------
if __name__ == "__main__":
    search_term = "Spider-Man"
    
    # 1. Semak Laman Utama & Dapatkan Struktur Form Asal
    print(f"\n[Langkah 1] Membuka Laman Utama ({BASE_URL}) untuk Auto-Discovery Form...")
    status, home_html, _, _ = execute_request(BASE_URL)
    print(f"  └ Status Kod Laman Utama: {status}")
    
    if status != 200 or is_blocked(home_html, status):
        print("❌ Laman utama gagal dimuatkan atau disekat.")
        sys.exit(1)

    form_info = discover_search_form(home_html)
    if form_info:
        print(f"  └ 🎯 Form Carian Ditemui:")
        print(f"      ├─ Action: {form_info['action']}")
        print(f"      ├─ Method: {form_info['method']}")
        print(f"      └─ Field : {form_info['query_field']}")
        REPORT["discovered_form"] = form_info
    else:
        print("  └ ⚠️ Form carian khusus tidak ditemui dalam HTML. Menggunakan senarai probe lalai.")

    # 2. Membina Matriks Ujian Endpoint (Probe Matrix)
    probe_matrix = []

    # Masukkan form yang ditemui secara automatik
    if form_info:
        if form_info["method"] == "POST":
            probe_matrix.append({
                "label": "Auto-Discovered Form (POST)",
                "url": form_info["action"],
                "method": "POST",
                "data": {form_info["query_field"]: search_term}
            })
        else:
            probe_matrix.append({
                "label": "Auto-Discovered Form (GET)",
                "url": f"{form_info['action']}?{form_info['query_field']}={quote_plus(search_term)}",
                "method": "GET",
                "data": None
            })

    # Tambah endpoint lazim Subscene
    probe_matrix.extend([
        {
            "label": "Subscene Klasik (POST /subtitles/searchbytitle)",
            "url": f"{BASE_URL}/subtitles/searchbytitle",
            "method": "POST",
            "data": {"query": search_term}
        },
        {
            "label": "Subscene Klasik (GET /subtitles/searchbytitle)",
            "url": f"{BASE_URL}/subtitles/searchbytitle?query={quote_plus(search_term)}",
            "method": "GET",
            "data": None
        },
        {
            "label": "Carian Alternatif (/search?q=)",
            "url": f"{BASE_URL}/search?q={quote_plus(search_term)}",
            "method": "GET",
            "data": None
        },
        {
            "label": "Laman Semakan Browse (/browse)",
            "url": f"{BASE_URL}/browse",
            "method": "GET",
            "data": None
        }
    ])

    # 3. Uji Setiap Endpoint
    print("\n[Langkah 2] Memulakan Probing Endpoint...")
    found_movies = []
    working_probe = None

    for probe in probe_matrix:
        print(f"\n  ├─ Ujian: {probe['label']}")
        print(f"  │  URL: {probe['url']} [{probe['method']}]")
        
        p_status, p_html, _, final_url = execute_request(
            probe["url"], method=probe["method"], data=probe["data"], referer=BASE_URL
        )
        print(f"  │  └ Status: {p_status} | Final URL: {final_url}")
        
        REPORT["probed_endpoints"].append({
            "label": probe["label"],
            "url": probe["url"],
            "status": p_status,
            "final_url": final_url
        })

        if p_status != 200:
            print("  │     └ ❌ Bukan 200 OK, abaikan.")
            continue

        if is_blocked(p_html, p_status):
            print("  │     └ ⚠️ Dikesan cabaran Cloudflare WAF.")
            continue

        # Semak jika berlaku auto-redirect terus ke filem
        if re.search(r"/subscene/\d+$", final_url):
            print(f"  │     └ 🎯 Direct Redirect ke Laman Filem: {final_url}")
            found_movies.append({"title": search_term, "url": final_url, "type": "redirect"})
            working_probe = probe
            break

        # Semak padanan pautan dalam HTML
        matches = parse_movie_results(p_html)
        if matches:
            print(f"  │     └ 🎉 BERJAYA! Ditemui {len(matches)} entri filem.")
            for m in matches[:3]:
                print(f"  │        ├─ {m['title']} -> {m['url']}")
            found_movies = matches
            working_probe = probe
            break
        else:
            print("  │     └ ⚠️ Tiada format pautan filem dikenali.")

    if not found_movies:
        print("\n❌ Gagal menemukan filem melalui mana-mana endpoint probe.")
        with open(REPORT_PATH, "w") as f:
            json.dump(REPORT, f, indent=2)
        sys.exit(1)

    target_movie = found_movies[0]
    REPORT["selected_movie"] = target_movie
    REPORT["search_results"] = found_movies

    # 4. Ambil Jadual Sarikata Filem
    print(f"\n[Langkah 3] Membuka Laman Filem: {target_movie['title']}")
    print(f"  └ URL: {target_movie['url']}")

    m_status, m_html, _, _ = execute_request(target_movie["url"], referer=BASE_URL)
    if m_status != 200 or not m_html:
        print("❌ Gagal memuat turun laman filem sasaran.")
        sys.exit(1)

    subs = parse_subtitles_table(m_html)
    print(f"  └ ✅ Ditemui {len(subs)} sarikata Malay/Indonesian!")
    REPORT["subtitles_found"] = len(subs)

    if not subs:
        print("❌ Tiada sarikata BM/ID pada laman filem ini.")
        sys.exit(1)

    sample_sub = subs[0]
    print(f"\n[Langkah 4] Membuka Detail Sarikata: {sample_sub['release']} ({sample_sub['lang']})")
    print(f"  └ URL: {sample_sub['url']}")

    d_status, d_html, _, _ = execute_request(sample_sub["url"], referer=target_movie["url"])
    soup = BeautifulSoup(d_html, "lxml")
    dl_url = None
    for a in soup.find_all("a", href=True):
        if re.match(r"^/download/\d+$", a["href"]):
            dl_url = urljoin(BASE_URL, a["href"])
            break

    if not dl_url:
        print("❌ Pautan /download/XXXXXX tidak dijumpai.")
        sys.exit(1)

    print(f"  └ ✅ Pautan muat turun: {dl_url}")

    # 5. Muat Turun & Ekstrak Fail SRT
    print(f"\n[Langkah 5] Memuat turun fail binary sarikata...")
    _, _, bin_data, _ = execute_request(dl_url, referer=sample_sub["url"])

    if len(bin_data) < 100:
        print("❌ Kandungan binary terlalu kecil atau kosong.")
        sys.exit(1)

    print(f"  └ ✅ Muat turun siap: {len(bin_data)} bytes")

    extracted = []
    try:
        with zipfile.ZipFile(io.BytesIO(bin_data)) as z:
            for fname in z.namelist():
                if fname.endswith(".srt"):
                    target_p = OUTPUT_DIR / fname
                    with open(target_p, "wb") as f:
                        f.write(z.read(fname))
                    extracted.append(str(target_p))
    except zipfile.BadZipFile:
        target_p = OUTPUT_DIR / "sample.srt"
        with open(target_p, "wb") as f:
            f.write(bin_data)
        extracted.append(str(target_p))

    if extracted:
        print(f"  └ 🎉 BERJAYA EKSTRAK FAIL SRT:")
        for p in extracted:
            print(f"      ├─ {p}")
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                snippet = [f.readline().strip() for _ in range(5)]
            print(f"      └─ Pratonton: {' // '.join(snippet)}")
        REPORT["success"] = True
    else:
        print("❌ Gagal mengekstrak fail .srt.")

    with open(REPORT_PATH, "w") as f:
        json.dump(REPORT, f, indent=2)

    print("\n" + "=" * 80)
    print(f"📄 Ujian selesai. Laporan penuh disimpan di: {REPORT_PATH}")
    print("=" * 80)