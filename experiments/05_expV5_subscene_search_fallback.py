import sys
import os
import time
import re
import asyncio
from bs4 import BeautifulSoup

print("=" * 75)
print("🧪 UJIAN ALUR KERJA PENUH SUB-SCENE (05_expV5_subscene_search_fallback.py)")
print("=" * 75)

BASE_URL = "https://sub-scene.com"
TEST_QUERY = "rambo"

# ------------------------------------------------------------------
# SEMAKAN KANDUNGAN
# ------------------------------------------------------------------
def is_blocked(html: str, status_code: int) -> bool:
    if status_code in [403, 503]:
        return True
    html_lower = html.lower()
    if "just a moment..." in html_lower or "attention required!" in html_lower:
        return True
    return False

# ------------------------------------------------------------------
# SISTEM FALLBACK 3 ENJIN
# ------------------------------------------------------------------
def fetch_url(url: str) -> tuple[bool, str, str]:
    """
    Mencuba mengambil HTML menggunakan fallback chain:
    curl-cffi -> Camoufox -> DrissionPage
    """
    # ENJIN 1: curl-cffi
    try:
        from curl_cffi import requests
        resp = requests.get(url, impersonate="chrome", allow_redirects=True, timeout=12)
        if resp.status_code == 200 and not is_blocked(resp.text, resp.status_code):
            return True, resp.text, "curl-cffi"
    except Exception:
        pass

    # ENJIN 2: Camoufox
    try:
        from camoufox.async_api import AsyncCamoufox
        async def _run_camoufox():
            async with AsyncCamoufox(headless=True) as browser:
                page = await browser.new_page()
                res = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                content = await page.content()
                st = res.status if res else 0
                return st, content

        st, content = asyncio.run(_run_camoufox())
        if st == 200 and not is_blocked(content, st):
            return True, content, "Camoufox"
    except Exception:
        pass

    # ENJIN 3: DrissionPage
    try:
        from DrissionPage import ChromiumPage, ChromiumOptions
        co = ChromiumOptions()
        co.set_argument('--headless=new')
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-dev-shm-usage')
        co.set_argument('--disable-gpu')
        
        dp = ChromiumPage(co)
        dp.get(url, retry=1, interval=1)
        content = dp.html
        dp.quit()
        if not is_blocked(content, 200):
            return True, content, "DrissionPage"
    except Exception:
        pass

    return False, "", "None"

# ------------------------------------------------------------------
# PARSER UTAMA
# ------------------------------------------------------------------
def parse_search_or_browse(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    results = []
    
    # Imbas semua pautan yang menuju ke /subscene/
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/subscene/" in href:
            full_url = BASE_URL + href if href.startswith("/") else href
            title = a.get_text().strip()
            if title and full_url not in [r["url"] for r in results]:
                results.append({"title": title, "url": full_url})
    return results

def parse_movie_subtitles(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    subs = []
    
    for tr in soup.find_all("tr"):
        text = tr.get_text().lower()
        if "malay" in text or "indonesian" in text:
            a_tag = tr.find("a", href=True)
            if a_tag and "/subtitle/" in a_tag["href"]:
                full_url = BASE_URL + a_tag["href"] if a_tag["href"].startswith("/") else a_tag["href"]
                lang = "Malay" if "malay" in text else "Indonesian"
                subs.append({
                    "lang": lang,
                    "title": a_tag.get_text().strip(),
                    "url": full_url
                })
    return subs

def parse_download_link(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        if "/download/" in a["href"]:
            href = a["href"]
            return BASE_URL + href if href.startswith("/") else href
    return ""

# ------------------------------------------------------------------
# ALUR KERJA UJIAN
# ------------------------------------------------------------------
if __name__ == "__main__":
    search_url = f"{BASE_URL}/search?query={TEST_QUERY}"
    print(f"\n[Fasa 1] Mencuba carian utama: {search_url}")
    
    success, html, engine_used = fetch_url(search_url)
    movie_list = []

    if success:
        print(f"  └ ✅ Berjaya melepasi sekatan guna enjin: [{engine_used}]")
        movie_list = parse_search_or_browse(html)
        print(f"  └ Tajuk filem dijumpai: {len(movie_list)}")
    else:
        print("  └ ❌ Carian terus /search gagal pada semua enjin.")
        print("\n[Fasa 1 Alternative] Beralih ke modus Browse: https://sub-scene.com/browse")
        
        browse_url = f"{BASE_URL}/browse"
        success, html, engine_used = fetch_url(browse_url)
        if success:
            print(f"  └ ✅ Browse berjaya dibuka guna enjin: [{engine_used}]")
            movie_list = parse_search_or_browse(html)
            print(f"  └ Tajuk filem dari browse dijumpai: {len(movie_list)}")

    # FASA 2 & 3: Pengambilan Sarikata & Pautan Muat Turun
    if movie_list:
        target_movie = movie_list[0]
        print(f"\n[Fasa 2] Membuka laman filem: {target_movie['title']}")
        print(f"  └ URL: {target_movie['url']}")
        
        s_success, s_html, s_engine = fetch_url(target_movie['url'])
        if s_success:
            subs = parse_movie_subtitles(s_html)
            print(f"  └ ✅ Berjaya! Jumpa {len(subs)} sarikata (Malay/Indonesian)")
            
            if subs:
                target_sub = subs[0]
                print(f"\n[Fasa 3] Membuka laman muat turun sarikata: {target_sub['title']}")
                print(f"  └ Bahasa: {target_sub['lang']}")
                print(f"  └ URL Sub: {target_sub['url']}")
                
                dl_success, dl_html, dl_engine = fetch_url(target_sub['url'])
                if dl_success:
                    download_link = parse_download_link(dl_html)
                    print(f"  └ 🚀 PAUTAN MUAT TURUN AKHIR (.zip/.srt): {download_link}")
                else:
                    print("  └ ❌ Gagal membuka laman detail sarikata.")
        else:
            print("  └ ❌ Gagal membuka laman filem.")
    else:
        print("\n❌ TIADA HASIL DIJUMPAI SAMA ADA DARI SEARCH ATAU BROWSE.")

    print("\n" + "=" * 75)
    print("✨ UJIAN SELESAI")
    print("=" * 75)