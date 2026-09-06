import sys
import os
import re
import io
import time
import zipfile
import asyncio
from pathlib import Path
from bs4 import BeautifulSoup
from curl_cffi import requests

print("=" * 75)
print("🧪 UJIAN END-TO-END: BROWSE, DOWNLOAD & EXTRACT SRT (05_expV6_end_to_end.py)")
print("=" * 75)

BASE_URL = "https://sub-scene.com"
OUTPUT_DIR = Path(__file__).resolve().parent / "extracted_subs"
OUTPUT_DIR.mkdir(exist_ok=True)

# ------------------------------------------------------------------
# SEMAKAN CLOUDFLARE BLOCK
# ------------------------------------------------------------------
def is_blocked(html: str, status_code: int) -> bool:
    if status_code in [403, 503]:
        return True
    html_lower = html.lower()
    return "just a moment..." in html_lower or "attention required!" in html_lower

# ------------------------------------------------------------------
# SISTEM FALLBACK FETCH
# ------------------------------------------------------------------
def fetch_url(url: str, referer: str = None) -> tuple[bool, str, str, bytes]:
    headers = {"Referer": referer} if referer else {}
    
    # 1. curl-cffi
    try:
        session = requests.Session(impersonate="chrome")
        resp = session.get(url, headers=headers, allow_redirects=True, timeout=15)
        if resp.status_code == 200 and not is_blocked(resp.text, resp.status_code):
            return True, resp.text, "curl-cffi", resp.content
    except Exception:
        pass

    # 2. Camoufox
    try:
        from camoufox.async_api import AsyncCamoufox
        async def _run_camoufox():
            async with AsyncCamoufox(headless=True) as browser:
                page = await browser.new_page()
                if referer:
                    await page.set_extra_http_headers({"Referer": referer})
                res = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                await page.wait_for_timeout(2000)
                content = await page.content()
                st = res.status if res else 0
                return st, content
        st, content = asyncio.run(_run_camoufox())
        if st == 200 and not is_blocked(content, st):
            return True, content, "Camoufox", content.encode("utf-8")
    except Exception:
        pass

    # 3. DrissionPage
    try:
        from DrissionPage import ChromiumPage, ChromiumOptions
        co = ChromiumOptions()
        co.set_argument('--headless=new')
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-dev-shm-usage')
        
        dp = ChromiumPage(co)
        dp.get(url, retry=2, interval=2)
        time.sleep(2)
        content = dp.html
        dp.quit()
        if not is_blocked(content, 200):
            return True, content, "DrissionPage", content.encode("utf-8")
    except Exception:
        pass

    return False, "", "None", b""

# ------------------------------------------------------------------
# PARSER PERSIS (REGULAR EXPRESSION)
# ------------------------------------------------------------------
def parse_movie_links(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    movies = []
    
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Hanya terima pautan tepat /subscene/NUMERIC (elak /subscene/NUMERIC/English)
        if re.match(r"^/subscene/\d+$", href):
            full_url = BASE_URL + href
            title = a.get_text().strip()
            if title and full_url not in [m["url"] for m in movies]:
                movies.append({"title": title, "url": full_url})
    return movies

def parse_subtitle_links(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    subs = []
    
    for tr in soup.find_all("tr"):
        text = tr.get_text().lower()
        if "malay" in text or "indonesian" in text:
            a_tag = tr.find("a", href=True)
            if a_tag and re.match(r"^/subtitle/\d+$", a_tag["href"]):
                full_url = BASE_URL + a_tag["href"]
                lang = "Malay" if "malay" in text else "Indonesian"
                subs.append({
                    "lang": lang,
                    "title": a_tag.get_text().strip(),
                    "url": full_url
                })
    return subs

def parse_download_url(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        if re.match(r"^/download/\d+$", a["href"]):
            return BASE_URL + a["href"]
    return ""

# ------------------------------------------------------------------
# PROSES UTAMA
# ------------------------------------------------------------------
if __name__ == "__main__":
    # Gunakan pautan filem sasaran Rambo IV yang disahkan wujud sarikata BM
    target_movie_url = "https://sub-scene.com/subscene/42661"
    
    print(f"\n[Fasa 1] Membuka Laman Filem: {target_movie_url}")
    success, html, engine, _ = fetch_url(target_movie_url)
    
    if not success:
        print("❌ Gagal melepasi sekatan laman filem.")
        sys.exit(1)
        
    print(f"  └ ✅ Berjaya buka laman filem menggunakan enjin [{engine}]")
    
    # 2. Cari sarikata BM/ID
    subs = parse_subtitle_links(html)
    print(f"  └ Jumpa {len(subs)} sarikata Malay/Indonesian")
    
    if not subs:
        print("❌ Tiada sarikata BM/ID dijumpai dalam jadual.")
        sys.exit(1)

    target_sub = subs[0]
    print(f"\n[Fasa 2] Membuka Laman Detail Sarikata: {target_sub['title']}")
    print(f"  └ Bahasa: {target_sub['lang']}")
    print(f"  └ URL: {target_sub['url']}")

    s_success, s_html, s_engine, _ = fetch_url(target_sub['url'])
    if not s_success:
        print("❌ Gagal membuka laman detail sarikata.")
        sys.exit(1)

    download_url = parse_download_url(s_html)
    if not download_url:
        print("❌ Pautan /download/XXXXXX tidak dijumpai.")
        sys.exit(1)

    print(f"  └ ✅ Jumpa pautan muat turun: {download_url}")

    # 3. Muat Turun Fail Sarikata (.zip / .srt)
    print(f"\n[Fasa 3] Memuat turun fail dari: {download_url}")
    d_success, _, d_engine, binary_content = fetch_url(download_url, referer=target_sub['url'])
    
    if not d_success or len(binary_content) < 100:
        print("❌ Gagal memuat turun kandungan binary sarikata.")
        sys.exit(1)

    print(f"  └ ✅ Muat turun berjaya! Saiz: {len(binary_content)} bytes (Enjin: {d_engine})")

    # 4. Penyyahmampatan (Extraction)
    print(f"\n[Fasa 4] Menguji Penyyahmampatan (Extraction)...")
    extracted_files = []

    try:
        # Cuba sebagai fail ZIP
        with zipfile.ZipFile(io.BytesIO(binary_content)) as z:
            for file_name in z.namelist():
                if file_name.endswith(".srt"):
                    extracted_path = OUTPUT_DIR / file_name
                    with open(extracted_path, "wb") as f:
                        f.write(z.read(file_name))
                    extracted_files.append(extracted_path)
    except zipfile.BadZipFile:
        # Jika terus fail raw .srt
        extracted_path = OUTPUT_DIR / "subtitle.srt"
        with open(extracted_path, "wb") as f:
            f.write(binary_content)
        extracted_files.append(extracted_path)

    if extracted_files:
        print(f"  └ 🎉 BERJAYA EXTRACT {len(extracted_files)} FAIL SRT:")
        for file_path in extracted_files:
            print(f"      ├─ Path: {file_path}")
            # Baca 5 baris pertama sarikata
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = [f.readline().strip() for _ in range(8)]
            print("      └─ Cuplikan Teks SRT:")
            for line in lines:
                print(f"          | {line}")
    else:
        print("❌ Tiada fail .srt dijumpai selepas ekstrak.")

    print("\n" + "=" * 75)
    print("✨ UJIAN END-TO-END SELESAI TANPA SEBARANG MASALAH!")
    print("=" * 75)