import re
import sys
import os
import time
import asyncio
import importlib
from typing import List, Dict, Tuple, Optional, Any
from urllib.parse import urljoin
from bs4 import BeautifulSoup

# Import modul pengekstrak ZIP
_zip = importlib.import_module("03_zip_extractor")
extract_srt_from_zip = _zip.extract_srt_from_zip

BASE_URL = "https://sub-scene.com"

# Pengepala rasmi pelayar Chrome Desktop
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,id;q=0.8,ms;q=0.7",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1"
}

# ------------------------------------------------------------------
# KESERASIAN BELAKANG (BACKWARD COMPATIBILITY)
# ------------------------------------------------------------------
def create_stealth_session() -> Tuple[Optional[Any], bool]:
    """
    Fungsi sokongan untuk skrip pemanggil sedia ada (06_ondemand_runner.py).
    """
    return None, True

# ------------------------------------------------------------------
# SEMAKAN SEKATAN CLOUDFLARE
# ------------------------------------------------------------------
def is_blocked(html: str, status_code: int) -> bool:
    if status_code in [403, 503]:
        return True
    html_lower = html.lower()
    return "just a moment..." in html_lower or "attention required!" in html_lower

# ------------------------------------------------------------------
# ENJIN FALLBACK: HTML FETCHING
# ------------------------------------------------------------------
def fetch_html_with_fallback(url: str, referer: Optional[str] = None) -> Tuple[bool, str]:
    headers = BROWSER_HEADERS.copy()
    if referer:
        headers["Referer"] = referer

    # 1. Enjin curl-cffi
    try:
        from curl_cffi import requests
        session = requests.Session(impersonate="chrome")
        resp = session.get(url, headers=headers, allow_redirects=True, timeout=12)
        if resp.status_code == 200 and not is_blocked(resp.text, resp.status_code):
            return True, resp.text
    except Exception:
        pass

    # 2. Enjin Camoufox
    try:
        from camoufox.async_api import AsyncCamoufox
        async def _run_camoufox():
            async with AsyncCamoufox(headless=True) as browser:
                page = await browser.new_page()
                if referer:
                    await page.set_extra_http_headers({"Referer": referer})
                res = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(1500)
                content = await page.content()
                st = res.status if res else 0
                return st, content

        st, content = asyncio.run(_run_camoufox())
        if st == 200 and not is_blocked(content, st):
            return True, content
    except Exception:
        pass

    # 3. Enjin DrissionPage
    try:
        from DrissionPage import ChromiumPage, ChromiumOptions
        co = ChromiumOptions()
        co.set_argument('--headless=new')
        co.set_argument('--no-sandbox')
        co.set_argument('--disable-dev-shm-usage')
        
        dp = ChromiumPage(co)
        dp.get(url, retry=1, interval=1)
        time.sleep(1.5)
        content = dp.html
        dp.quit()
        if not is_blocked(content, 200):
            return True, content
    except Exception:
        pass

    return False, ""

# ------------------------------------------------------------------
# ENJIN FALLBACK: BINARY FETCHING (FOR ZIP/SRT DOWNLOAD)
# ------------------------------------------------------------------
def fetch_bytes_with_fallback(url: str, referer: Optional[str] = None) -> Tuple[bool, bytes]:
    headers = BROWSER_HEADERS.copy()
    if referer:
        headers["Referer"] = referer

    # 1. Enjin curl-cffi
    try:
        from curl_cffi import requests
        session = requests.Session(impersonate="chrome")
        resp = session.get(url, headers=headers, allow_redirects=True, timeout=15)
        if resp.status_code == 200 and len(resp.content) > 100:
            return True, resp.content
    except Exception:
        pass

    # 2. Enjin Camoufox
    try:
        from camoufox.async_api import AsyncCamoufox
        async def _run_camoufox_bytes():
            async with AsyncCamoufox(headless=True) as browser:
                page = await browser.new_page()
                if referer:
                    await page.set_extra_http_headers({"Referer": referer})
                res = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                body = await res.body()
                return res.status, body

        st, body = asyncio.run(_run_camoufox_bytes())
        if st == 200 and len(body) > 100:
            return True, body
    except Exception:
        pass

    return False, b""

# ------------------------------------------------------------------
# PENAPIS BAHASA KETAT
# ------------------------------------------------------------------
def parse_and_filter_language(text: str) -> Optional[str]:
    clean = text.lower().strip()

    if "malayalam" in clean:
        return None

    if "bahasa melayu" in clean or "melayu" in clean or re.search(r'\bmalay\b', clean) or re.search(r'\bmay\b', clean):
        return "ms"

    if "bahasa indonesia" in clean or "indonesian" in clean or re.search(r'\bindonesia\b', clean) or re.search(r'\bind\b', clean):
        return "id"

    return None

# ------------------------------------------------------------------
# ALUR KERJA UTAMA SCRAPER
# ------------------------------------------------------------------
def search_subscene(session=None, query: str = "") -> List[Dict[str, str]]:
    """
    Mencari filem mengikut kata kunci/IMDb ID. Jika /search terhalang, automatik fallback ke /browse.
    """
    results = []

    # Cubaan 1: Carian terus
    search_url = f"{BASE_URL}/search?query={query.replace(' ', '+')}"
    success, html = fetch_html_with_fallback(search_url, referer=f"{BASE_URL}/")

    # Cubaan 2: Modus Browse (Fallback jika /search gagal)
    if not success or not html:
        browse_url = f"{BASE_URL}/browse"
        success, html = fetch_html_with_fallback(browse_url, referer=f"{BASE_URL}/")

    if success and html:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = str(a["href"]).strip()
            title = a.get_text(strip=True)

            # Padanan persis URL laluan filem /subscene/ID
            if re.match(r"^/subscene/\d+$", href) and title:
                full_url = urljoin(BASE_URL, href)
                sub_id = href.split("/")[-1]
                results.append({
                    "id": str(sub_id),
                    "title": str(title),
                    "url": full_url
                })

    unique = {r["url"]: r for r in results}.values()
    return list(unique)

def get_movie_subtitles(session=None, movie_url: str = "") -> List[Dict[str, str]]:
    """
    Mengekstrak senarai sarikata bahasa Melayu ('ms') dan Indonesia ('id') daripada laman filem.
    """
    subtitles = []
    success, html = fetch_html_with_fallback(movie_url, referer=f"{BASE_URL}/")

    if not success or not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    for tr in soup.find_all("tr"):
        row_text = tr.get_text(" ", strip=True)
        lang_code = parse_and_filter_language(row_text)

        if not lang_code:
            continue

        a_tag = tr.find("a", href=True)
        if a_tag and re.match(r"^/subtitle/\d+$", a_tag["href"]):
            detail_url = urljoin(BASE_URL, str(a_tag["href"]))
            release_title = a_tag.get_text(strip=True) or "Unknown Release"
            sub_id = detail_url.split("/")[-1]

            subtitles.append({
                "sub_id": str(sub_id),
                "lang": lang_code,
                "release": str(release_title),
                "detail_url": str(detail_url)
            })

    return subtitles

def download_and_extract_subtitles(session=None, detail_url: str = "") -> List[Dict[str, str]]:
    """
    Memuat turun fail sarikata dari laman detail dan mengekstrak kandungan .srt.
    """
    # 1. Dapatkan pautan /download/XXXXXX dari laman detail sarikata
    success, html = fetch_html_with_fallback(detail_url, referer=f"{BASE_URL}/")
    if not success or not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    dl_url = None
    for a in soup.find_all("a", href=True):
        if re.match(r"^/download/\d+$", a["href"]):
            dl_url = urljoin(BASE_URL, a["href"])
            break

    if not dl_url:
        return []

    # 2. Muat turun fail binary
    b_success, binary_content = fetch_bytes_with_fallback(dl_url, referer=detail_url)
    if not b_success or len(binary_content) == 0:
        return []

    # 3. Ekstrak fail .srt melalui modul 03_zip_extractor
    try:
        return extract_srt_from_zip(binary_content)
    except Exception:
        return [{"filename": "subtitle.srt", "content": binary_content.decode("utf-8", errors="ignore")}]