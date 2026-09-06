import re
import sys
import os
import time
import io
import zipfile
import asyncio
import importlib
from typing import List, Dict, Tuple, Optional, Any
from urllib.parse import urljoin, quote_plus
from bs4 import BeautifulSoup
from curl_cffi import requests

# Import modul pengekstrak ZIP sedia ada
_zip = importlib.import_module("03_zip_extractor")
extract_srt_from_zip = _zip.extract_srt_from_zip

BASE_URL = "https://sub-scene.com"

# ------------------------------------------------------------------
# KESERASIAN BELAKANG (BACKWARD COMPATIBILITY)
# ------------------------------------------------------------------
def create_stealth_session() -> Tuple[Optional[Any], bool]:
    """
    Kekal disokong untuk mengelakkan ralat pada pemanggil lama.
    """
    return None, True

# ------------------------------------------------------------------
# ALGORITMA SMART MATCHING & YEAR SCORER
# ------------------------------------------------------------------
def calculate_match_score(candidate_title: str, target_title: str, target_year: str = "") -> int:
    """
    Mengira markah kejituan tajuk calon berbanding tajuk sasaran dan tahun.
    Menapis filem parodi, kartun Lego, atau siri TV yang tidak berkenaan.
    """
    score = 0
    c_lower = candidate_title.lower()
    t_lower = target_title.lower()

    # 1. Padanan Tahun (Pemberat Utama)
    if target_year and target_year in c_lower:
        score += 50
    elif target_year:
        score -= 25

    # 2. Penalti Filem Parodi / Animasi Tidak Berkenaan
    unwanted_keywords = ["lego", "xxx", "porn", "parody", "season", "complete series", "animated series"]
    for bad in unwanted_keywords:
        if bad in c_lower and bad not in t_lower:
            score -= 60

    # 3. Nisbah Perkataan Sepadan
    clean_target = set(re.sub(r"[^a-zA-Z0-9\s]", "", t_lower).split())
    clean_candidate = set(re.sub(r"[^a-zA-Z0-9\s]", "", c_lower).split())
    
    if clean_target:
        intersect = clean_target.intersection(clean_candidate)
        score += int((len(intersect) / len(clean_target)) * 40)

    # 4. Bonus Permulaan Tajuk
    if c_lower.startswith(t_lower):
        score += 15

    return score

def pick_best_movie_matches(movies: List[Dict[str, str]], target_title: str, target_year: str = "", top_k: int = 1) -> List[Dict[str, str]]:
    """
    Menyusun calon filem mengikut skor tertinggi dan memulangkan pilihan paling tepat.
    """
    scored = []
    for m in movies:
        sc = calculate_match_score(m["title"], target_title, target_year)
        if sc > 0:  # Hanya terima calon dengan skor positif
            scored.append((sc, m))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored[:top_k]]

# ------------------------------------------------------------------
# CAMOUFOX TURNSTILE RESOLVER & CARIAN SELAMAT
# ------------------------------------------------------------------
async def _async_camoufox_search(query: str) -> Tuple[List[Dict[str, str]], Dict[str, str], str]:
    from camoufox.async_api import AsyncCamoufox
    
    search_url = f"{BASE_URL}/search?query={quote_plus(query)}"
    results = []
    cookies = {}
    user_agent = ""

    async with AsyncCamoufox(headless=True) as browser:
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
        user_agent = await page.evaluate("navigator.userAgent")

        final_html = ""
        for _ in range(25):
            await page.wait_for_timeout(1000)
            try:
                # Cuba klik checkbox Turnstile sekiranya wujud dalam iframe
                for frame in page.frames:
                    if "challenges.cloudflare.com" in frame.url or "turnstile" in frame.url:
                        chk = await frame.query_selector("input[type=checkbox], .ctp-checkbox-label, #challenge-stage")
                        if chk:
                            await chk.click()
                            await page.wait_for_timeout(1500)

                content = await page.content()
                lower = content.lower()
                if "just a moment" not in lower and "attention required" not in lower:
                    if "/subscene/" in content or "no results" in lower:
                        final_html = content
                        break
            except Exception:
                continue

        if not final_html:
            try:
                final_html = await page.content()
            except Exception:
                final_html = ""

        raw_cookies = await context.cookies()
        for ck in raw_cookies:
            cookies[ck["name"]] = ck["value"]

        if final_html:
            soup = BeautifulSoup(final_html, "lxml")
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                title = a.get_text(strip=True)
                if re.match(r"^/subscene/\d+$", href) and title:
                    results.append({"title": title, "url": urljoin(BASE_URL, href)})

        await context.close()

    return results, cookies, user_agent

# ------------------------------------------------------------------
# ENJIN CARIAN UTAMA (BRIDGE KE CURL-CFFI)
# ------------------------------------------------------------------
def search_subscene(query: str, year: str = "", top_k: int = 1) -> Tuple[List[Dict[str, str]], Optional[requests.Session]]:
    """
    Melaksanakan carian melalui Camoufox, meleraikan Cloudflare Turnstile,
    menapis hasil menggunakan Smart Scorer, dan memulangkan sesi curl-cffi aktif.
    """
    try:
        raw_movies, cookies, ua = asyncio.run(_async_camoufox_search(query))
    except Exception as e:
        print(f"❌ Ralat semasa carian Camoufox: {e}")
        return [], None

    if not raw_movies:
        return [], None

    # Tapis dan pilih filem yang tepat berdasarkan tajuk dan tahun
    best_movies = pick_best_movie_matches(raw_movies, target_title=query, target_year=year, top_k=top_k)
    if not best_movies:
        return [], None

    # Handover kuki sesi Turnstile ke curl-cffi untuk muat turun laju
    session = requests.Session(impersonate="chrome120")
    for k, v in cookies.items():
        session.cookies.set(k, v, domain="sub-scene.com")
    if ua:
        session.headers.update({"User-Agent": ua})

    return best_movies, session

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
# EKSTRAK SENARAI SARIKATA FILEM
# ------------------------------------------------------------------
def get_movie_subtitles(session: requests.Session, movie_url: str) -> List[Dict[str, str]]:
    """
    Mengekstrak senarai sarikata Bahasa Melayu ('ms') dan Indonesia ('id') daripada laman filem.
    """
    subtitles = []
    try:
        resp = session.get(movie_url, headers={"Referer": f"{BASE_URL}/"}, timeout=15)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "lxml")
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
    except Exception as e:
        print(f"⚠️ Ralat mengambil senarai sarikata: {e}")

    return subtitles

# ------------------------------------------------------------------
# MUAT TURUN & EKSTRAK FAIL SRT
# ------------------------------------------------------------------
def download_and_extract_subtitles(session: requests.Session, detail_url: str) -> List[Dict[str, str]]:
    """
    Memuat turun fail zip sarikata dari laman detail dan mengekstrak teks .srt ke dalam memori.
    """
    try:
        # 1. Buka laman detail untuk dapatkan pautan /download/XXXXXX
        d_resp = session.get(detail_url, headers={"Referer": f"{BASE_URL}/"}, timeout=12)
        if d_resp.status_code != 200:
            return []

        soup = BeautifulSoup(d_resp.text, "lxml")
        dl_url = None
        for a in soup.find_all("a", href=True):
            if re.match(r"^/download/\d+$", a["href"]):
                dl_url = urljoin(BASE_URL, a["href"])
                break

        if not dl_url:
            return []

        # 2. Muat turun binary ZIP
        bin_resp = session.get(dl_url, headers={"Referer": detail_url}, timeout=15)
        binary_content = bin_resp.content

        if len(binary_content) < 100:
            return []

        # 3. Nyahmampat fail ZIP
        return extract_srt_from_zip(binary_content)

    except Exception as e:
        print(f"⚠️ Ralat semasa memuat turun sarikata: {e}")
        return []