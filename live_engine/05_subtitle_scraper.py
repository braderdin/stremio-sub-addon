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

# Senarai kata henti umum yang tidak mewakili identiti unik filem
STOP_WORDS = {
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for", 
    "with", "by", "from", "part", "movie", "film"
}

# ------------------------------------------------------------------
# KESERASIAN BELAKANG (BACKWARD COMPATIBILITY)
# ------------------------------------------------------------------
def create_stealth_session() -> Tuple[Optional[Any], bool]:
    return None, True

# ------------------------------------------------------------------
# ALGORITMA SMART MATCHING & STRICT YEAR SCORER
# ------------------------------------------------------------------
def extract_year(text: str) -> Optional[int]:
    """Mengekstrak 4-digit tahun (1900-2099) daripada teks tajuk."""
    m = re.search(r"\b(19\d\d|20\d\d)\b", text)
    return int(m.group(1)) if m else None

def calculate_match_score(candidate_title: str, target_title: str, target_year: str = "") -> int:
    """
    Mengira markah kejituan tajuk calon berbanding tajuk sasaran dan tahun.
    Menolak sekeras-kerasnya padanan jika jurang tahun > 1 tahun (Year Discrepancy Reject),
    atau jika perkataan utama (content words) tajuk sasaran tiada dalam calon.
    """
    c_lower = candidate_title.lower().strip()
    t_lower = target_title.lower().strip()

    # 1. Semakan Tegas Tahun Terbitan (Strict Year Filter)
    c_year = extract_year(c_lower)
    t_year = None
    if target_year and str(target_year).strip().isdigit():
        t_year = int(str(target_year).strip())

    if c_year and t_year:
        year_diff = abs(c_year - t_year)
        if year_diff > 1:
            # Jurang melebihi 1 tahun (cth: 2012 vs 1998, 2026 vs 2006) -> Tolak serta-merta!
            return -999
        elif year_diff == 0:
            score = 50
        else:
            # Toleransi selisih 1 tahun dibenarkan (cth: 1997 vs 1998 bagi Good Will Hunting)
            score = 35
    elif t_year and not c_year:
        # Calon di Subscene belum diletakkan tahun
        score = 20
    else:
        score = 10

    # 2. Penalti Filem Parodi / Siri / Animasi Tidak Berkenaan
    unwanted_keywords = ["lego", "xxx", "porn", "parody", "season", "complete series", "animated series"]
    for bad in unwanted_keywords:
        if bad in c_lower and bad not in t_lower:
            return -999

    # Bersihkan teks tajuk calon (buang tahun dan simbol) untuk perbandingan teks tulen
    c_text_only = re.sub(r"\(?\b(19\d\d|20\d\d)\)?", "", c_lower)
    c_clean_words = set(re.sub(r"[^a-zA-Z0-9\s]", " ", c_text_only).split())
    t_words = re.sub(r"[^a-zA-Z0-9\s]", " ", t_lower).split()

    # 3. Semakan Perkataan Utama (Content Words Coverage)
    t_content_words = [w for w in t_words if w not in STOP_WORDS and len(w) > 1]
    
    if t_content_words:
        # Semak perkataan teras pertama (cth: "Terminator", "Avengers", "Hangover")
        if t_content_words[0] not in c_clean_words:
            score -= 40

        matched_content = [w for w in t_content_words if w in c_clean_words]
        content_ratio = len(matched_content) / len(t_content_words)

        # Jika tajuk sasaran cuma ada 1 perkataan utama dan calon tiada kata itu -> Tolak!
        if len(t_content_words) == 1 and content_ratio == 0:
            return -999

        # Jika sasaran ada >=2 perkataan utama dan nisbah sepadan < 60% -> Tolak!
        if len(t_content_words) >= 2 and content_ratio < 0.6:
            return -999

        score += int(content_ratio * 40)
    else:
        intersect = set(t_words).intersection(c_clean_words)
        if not intersect:
            return -999
        score += int((len(intersect) / len(t_words)) * 30)

    # 4. Bonus Padanan Tepat & Awalan Tajuk
    clean_t_str = " ".join(t_words)
    clean_c_str = " ".join(re.sub(r"[^a-zA-Z0-9\s]", " ", c_text_only).split())

    if clean_t_str == clean_c_str:
        score += 30
    elif clean_c_str.startswith(clean_t_str):
        score += 15

    return score

MIN_ACCEPTABLE_SCORE = 45

def pick_best_movie_matches(movies: List[Dict[str, str]], target_title: str, target_year: str = "", top_k: int = 1) -> List[Dict[str, str]]:
    """
    Menyusun calon filem mengikut skor tertinggi dan menolak calon di bawah ambang skor minimum.
    """
    scored = []
    for m in movies:
        sc = calculate_match_score(m["title"], target_title, target_year)
        if sc >= MIN_ACCEPTABLE_SCORE:
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
# ENJIN CARIAN PINTAR 2-PERINGKAT (DENGAN VERIFIKASI IMDB ID & TEXT FALLBACK)
# ------------------------------------------------------------------
def search_subscene(
    query: str, 
    year: str = "", 
    imdb_id: str = "", 
    top_k: int = 1
) -> Tuple[List[Dict[str, str]], Optional[requests.Session]]:
    target_imdb = imdb_id.strip() if imdb_id else ""
    if not target_imdb and re.match(r"^tt\d+$", query.strip()):
        target_imdb = query.strip()

    clean_query = re.sub(r"[:\-_/]", " ", query).strip()
    clean_query = re.sub(r"\s+", " ", clean_query)

    best_movies = []
    cookies = {}
    ua = ""

    # ==============================================================
    # PERINGKAT 1: CARIAN TERUS IMDB ID (DENGAN PENGESAHAN TAJUK WAJIB)
    # ==============================================================
    if target_imdb:
        print(f"🔎 [Peringkat 1] Mencuba carian terus IMDb ID di Subscene: '{target_imdb}'...")
        try:
            raw_movies, cookies, ua = asyncio.run(_async_camoufox_search(target_imdb))
            if raw_movies:
                # Wajib sahkan calon IMDb ID terhadap tajuk/tahun sebenar bagi menepis metadata rosak (cth: Amigo TV)
                verified = pick_best_movie_matches(raw_movies, target_title=clean_query, target_year=year, top_k=top_k)
                if verified:
                    best_movies = verified
                    print(f"   🎯 Padanan Sah IMDb ID Ditemui: {best_movies[0]['title']} -> {best_movies[0]['url']}")
                else:
                    print(f"   ⚠️ Calon IMDb ID '{target_imdb}' ditolak kerana tidak sepadan tajuk/tahun ({raw_movies[0]['title']}). Beralih ke carian teks...")
            else:
                print(f"   ℹ️ Subscene tidak memulangkan hasil untuk IMDb ID '{target_imdb}'.")
        except Exception as e:
            print(f"   ⚠️ Ralat semasa carian IMDb ID: {e}")

    # ==============================================================
    # PERINGKAT 2: CARIAN SANDARAN TEKS BERSIH
    # ==============================================================
    if not best_movies:
        if target_imdb:
            print(f"🔄 Mengaktifkan mod sandaran: Carian tajuk teks...")

        # Jika tajuk pendek (<=3 huruf cth: 'It'), gabungkan tahun terus dalam kata carian
        search_text = f"{clean_query} {year}".strip() if len(clean_query) <= 3 and year else clean_query

        print(f"🔎 [Peringkat 2] Carian Sandaran Teks: '{search_text}' (Tahun: {year})")
        try:
            raw_movies, cookies, ua = asyncio.run(_async_camoufox_search(search_text))
            if raw_movies:
                best_movies = pick_best_movie_matches(raw_movies, target_title=clean_query, target_year=year, top_k=top_k)
                if best_movies:
                    print(f"   🎯 Padanan Teks Ditemui: {best_movies[0]['title']} -> {best_movies[0]['url']}")
        except Exception as e:
            print(f"   ❌ Ralat semasa carian teks: {e}")

        # Variasi sandaran: Jika bermula dengan perkataan 'The ' dan tiada padanan, cuba buang 'The ' (cth: 'The Hangover' -> 'Hangover')
        if not best_movies and clean_query.lower().startswith("the ") and len(clean_query) > 6:
            alt_query = clean_query[4:].strip()
            print(f"🔎 [Peringkat 2 - Variasi] Mencuba tanpa awalan 'The': '{alt_query}' (Tahun: {year})")
            try:
                raw_movies_alt, cookies_alt, ua_alt = asyncio.run(_async_camoufox_search(alt_query))
                if raw_movies_alt:
                    best_movies = pick_best_movie_matches(raw_movies_alt, target_title=clean_query, target_year=year, top_k=top_k)
                    if best_movies:
                        cookies = cookies_alt
                        ua = ua_alt
                        print(f"   🎯 Padanan Teks Variasi Ditemui: {best_movies[0]['title']} -> {best_movies[0]['url']}")
            except Exception as e:
                print(f"   ❌ Ralat semasa carian variasi teks: {e}")

    if not best_movies:
        return [], None

    # Sambungkan kuki Turnstile ke curl-cffi untuk muat turun sarikata
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
    try:
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

        bin_resp = session.get(dl_url, headers={"Referer": detail_url}, timeout=15)
        binary_content = bin_resp.content

        if len(binary_content) < 100:
            return []

        return extract_srt_from_zip(binary_content)

    except Exception as e:
        print(f"⚠️ Ralat semasa memuat turun sarikata: {e}")
        return []