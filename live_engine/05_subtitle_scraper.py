import re
import importlib
from typing import List, Dict, Tuple, Optional
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from curl_cffi import requests

# Import modul pengekstrak ZIP
_zip = importlib.import_module("03_zip_extractor")
extract_srt_from_zip = _zip.extract_srt_from_zip

BASE_URL = "https://sub-scene.com"

# Pengepala rasmi pelayar Chrome Desktop melepasi tapisan Cloudflare WAF
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,id;q=0.8,ms;q=0.7",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1"
}

def create_stealth_session() -> Tuple[requests.Session, bool]:
    """
    Membina sesi curl-cffi impersonate pelayar Chrome 124.
    """
    s = requests.Session(impersonate="chrome124")
    s.headers.update(BROWSER_HEADERS)
    try:
        resp = s.get(BASE_URL, timeout=12)
        return s, (resp.status_code == 200)
    except Exception:
        return s, False

def parse_and_filter_language(text: str) -> Optional[str]:
    """
    Penapis Bahasa Ketat:
    1. Sekatan mutlak perkataan 'malayalam'.
    2. Kod bahasa standard Stremio: 'ms' (Melayu) dan 'id' (Indonesia).
    """
    clean = text.lower().strip()

    if "malayalam" in clean:
        return None

    if "bahasa melayu" in clean or "melayu" in clean or re.search(r'\bmalay\b', clean) or re.search(r'\bmay\b', clean):
        return "ms"

    if "bahasa indonesia" in clean or "indonesian" in clean or re.search(r'\bindonesia\b', clean) or re.search(r'\bind\b', clean):
        return "id"

    return None

def search_subscene(session: requests.Session, query: str) -> List[Dict[str, str]]:
    """
    Mencari tajuk filem/siri di Subscene mengikut kata kunci atau IMDb ID.
    """
    search_url = f"{BASE_URL}/search?query={query.replace(' ', '+')}"
    headers = {"Referer": f"{BASE_URL}/", "Sec-Fetch-Site": "same-origin"}
    results = []

    try:
        resp = session.get(search_url, headers=headers, timeout=15)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for link in soup.select("div.search-result ul li a, table tr a, a[href*='/subscene/'], a[href*='/subtitles/']"):
                raw_href = link.get("href")
                if not raw_href or not isinstance(raw_href, str):
                    continue

                href = str(raw_href).strip()
                title = link.get_text(strip=True)

                if href and title and not href.startswith("#"):
                    match_id = re.search(r"/(?:subscene|subtitles)/([^/]+)", href)
                    sub_id = match_id.group(1) if match_id else href.split("/")[-1]
                    results.append({
                        "id": str(sub_id),
                        "title": str(title),
                        "url": urljoin(BASE_URL, href)
                    })
    except Exception:
        pass

    unique = {r["url"]: r for r in results}.values()
    return list(unique)

def get_movie_subtitles(session: requests.Session, movie_url: str) -> List[Dict[str, str]]:
    """
    Mengekstrak pautan detail subtitle bagi bahasa 'ms' dan 'id' sahaja.
    """
    headers = {"Referer": f"{BASE_URL}/search", "Sec-Fetch-Site": "same-origin"}
    subtitles = []

    try:
        resp = session.get(movie_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")

        for row in soup.select("table tr, div.subtitle-entry"):
            lang_code = parse_and_filter_language(row.get_text(" ", strip=True))
            if not lang_code:
                continue

            link_tag = row.find("a", href=True)
            if not link_tag:
                continue

            raw_href = link_tag.get("href")
            if not raw_href or not isinstance(raw_href, str):
                continue

            detail_url = urljoin(BASE_URL, str(raw_href))
            release_title = link_tag.get_text(strip=True) or "Unknown Release"

            sub_match = re.search(r"/(\d+)$", detail_url)
            sub_id = sub_match.group(1) if sub_match else detail_url.split("/")[-1]

            subtitles.append({
                "sub_id": str(sub_id),
                "lang": lang_code,
                "release": str(release_title),
                "detail_url": str(detail_url)
            })
    except Exception:
        pass

    return subtitles

def download_and_extract_subtitles(session: requests.Session, detail_url: str) -> List[Dict[str, str]]:
    """
    Memuat turun arkib ZIP dan mengekstrak fail .srt melalui modul 03_zip_extractor.
    """
    headers = {"Referer": detail_url, "Sec-Fetch-Site": "same-origin"}
    try:
        resp = session.get(detail_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        dl_btn = soup.select_one("a#downloadButton, a.download, a[href*='/download/']")
        if not dl_btn or not dl_btn.get("href"):
            return []

        dl_url = urljoin(BASE_URL, str(dl_btn.get("href")))
        zip_resp = session.get(dl_url, headers=headers, timeout=20)

        if zip_resp.status_code == 200 and len(zip_resp.content) > 0:
            return extract_srt_from_zip(zip_resp.content)

    except Exception:
        pass

    return []