import re
import sys
import os
import time
import io
import zipfile
import asyncio
import importlib
from typing import List, Dict, Tuple, Optional, Any, Set
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

# Corak pengesanan sekuel untuk mengelak salah padan filem sekuel
SEQUEL_PATTERNS = [
    r"\b2\b", r"\b3\b", r"\b4\b", r"\b5\b", r"\b6\b", r"\b7\b", r"\b8\b", r"\b9\b",
    r"\bii\b", r"\biii\b", r"\biv\b", r"\bvi\b", r"\bvii\b", r"\bviii\b", r"\bix\b",
    r"\bpart\s*2\b", r"\bpart\s*3\b", r"\bpart\s*4\b", r"\bpart\s*5\b",
    r"\bchapter\s*2\b", r"\bchapter\s*3\b", r"\bchapter\s*4\b", r"\bchapter\s*5\b",
    r"\bvolume\s*2\b", r"\bvolume\s*3\b", r"\bvol\s*2\b", r"\bvol\s*3\b"
]

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

def calculate_match_score(
    candidate_title: str, 
    target_title: str, 
    target_year: str = "",
    is_imdb_match: bool = False
) -> int:
    """
    Mengira markah kejituan tajuk calon berbanding tajuk sasaran dan tahun.
    Menyokong pengecualian pintar bagi carian IMDb sah dan sub-tajuk francais.
    """
    c_lower = candidate_title.lower().strip()
    t_lower = target_title.lower().strip()

    # Normalisasi simbol '&' ke 'and' untuk kedua-dua tajuk
    c_lower = c_lower.replace("&", " and ")
    t_lower = t_lower.replace("&", " and ")

    # 1. Semakan Tahun Terbitan (Year Filter)
    c_year = extract_year(c_lower)
    t_year = None
    if target_year and str(target_year).strip().isdigit():
        t_year = int(str(target_year).strip())

    if c_year and t_year:
        year_diff = abs(c_year - t_year)
        max_diff = 2 if is_imdb_match else 1
        if year_diff > max_diff:
            return -999
        elif year_diff == 0:
            score = 50
        else:
            score = 35
    elif t_year and not c_year:
        score = 20
    else:
        score = 10

    # 2. Penalti Filem Parodi / Siri / Animasi Tidak Berkenaan
    unwanted_keywords = ["lego", "xxx", "porn", "parody", "season", "complete series", "animated series"]
    for bad in unwanted_keywords:
        if bad in c_lower and bad not in t_lower:
            return -999

    # Bersihkan teks tajuk calon (buang tahun 4-digit)
    c_text_only = re.sub(r"\(?\b(19\d\d|20\d\d)\)?", "", c_lower).strip()
    t_text_only = re.sub(r"\(?\b(19\d\d|20\d\d)\)?", "", t_lower).strip()

    # ==============================================================
    # PENGECUALIAN PERINGKAT 1: HASIL CARIAN IMDB ID SAH
    # ==============================================================
    if is_imdb_match:
        # Jika hasil datang daripada carian IMDb ID tepat di Subscene,
        # luluskan terus tanpa penolakan nombor sekuel komuniti.
        return score + 50

    # Bersihkan simbol tanda baca
    c_clean_words = set(re.sub(r"[^a-zA-Z0-9\s]", " ", c_text_only).split())
    t_words = [w for w in re.sub(r"[^a-zA-Z0-9\s]", " ", t_text_only).split() if w]
    t_content_words = [w for w in t_words if w not in STOP_WORDS and len(w) > 1]
    shared_content_words = [w for w in t_content_words if w in c_clean_words]

    # Semak jika tajuk sasaran mempunyai sub-tajuk (cth: 'Resident Evil: Retribution')
    has_target_subtitle = bool(re.search(r"[:\-_/]", target_title))
    has_shared_subtitle_word = any(len(w) >= 4 for w in shared_content_words[1:]) if len(shared_content_words) > 1 else False

    # 3. Penapis Pintar Nombor Sekuel (Sequel Mismatch Guard)
    for pat in SEQUEL_PATTERNS:
        in_candidate = bool(re.search(pat, c_text_only))
        in_target = bool(re.search(pat, t_text_only))
        if in_candidate != in_target:
            # Pengecualian Pintar: Calon ada nombor francais tambahan (cth: 'Resident Evil 5')
            # tetapi berkongsi sub-tajuk unik (cth: 'Retribution') dan tahun terbitan sepadan.
            if in_candidate and not in_target and has_target_subtitle and has_shared_subtitle_word:
                if c_year and t_year and abs(c_year - t_year) <= 1:
                    score += 10
                    continue
            return -999

    # 4. Semakan Liputan Kata Utama (Content Words Coverage)
    if t_content_words:
        if len(t_content_words) == 1:
            if t_content_words[0] not in c_clean_words:
                return -999
            c_content_count = len([w for w in re.sub(r"[^a-zA-Z0-9\s]", " ", c_text_only).split() if w not in STOP_WORDS and len(w) > 1])
            if c_content_count > 3 and not any(sub in c_lower for sub in ["the movie", "part", "vol", "1"]):
                return -999

        if t_content_words[0] not in c_clean_words:
            score -= 40

        content_ratio = len(shared_content_words) / len(t_content_words)

        if len(t_content_words) == 1 and content_ratio == 0:
            return -999

        if len(t_content_words) >= 2 and content_ratio < 0.5:
            return -999

        score += int(content_ratio * 40)
    else:
        intersect = set(t_words).intersection(c_clean_words)
        if not intersect:
            return -999
        score += int((len(intersect) / len(t_words)) * 30)

    # 5. Bonus Padanan Tepat & Awalan Tajuk
    clean_t_str = " ".join(t_words)
    clean_c_str = " ".join(re.sub(r"[^a-zA-Z0-9\s]", " ", c_text_only).split())
    clean_c_no_one = re.sub(r"\b1\b", "", clean_c_str).strip()
    clean_c_no_one = re.sub(r"\s+", " ", clean_c_no_one)

    if clean_t_str == clean_c_str or clean_t_str == clean_c_no_one:
        score += 30
    elif clean_c_str.startswith(clean_t_str) or clean_c_no_one.startswith(clean_t_str):
        score += 15

    return score

MIN_ACCEPTABLE_SCORE = 45

def pick_best_movie_matches(
    movies: List[Dict[str, str]], 
    target_title: str, 
    target_year: str = "", 
    top_k: int = 1,
    exclude_urls: Optional[List[str]] = None,
    is_imdb_match: bool = False
) -> List[Dict[str, str]]:
    """
    Menyusun calon filem mengikut skor tertinggi dan menolak calon di bawah ambang skor minimum.
    """
    excluded_set: Set[str] = {u.rstrip("/").lower() for u in (exclude_urls or [])}
    scored = []
    
    for m in movies:
        m_url_clean = m.get("url", "").rstrip("/").lower()
        if m_url_clean in excluded_set:
            continue

        sc = calculate_match_score(
            candidate_title=m["title"], 
            target_title=target_title, 
            target_year=target_year,
            is_imdb_match=is_imdb_match
        )
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
# ENJIN CARIAN PINTAR 2-PERINGKAT (VERIFIKASI IMDB ID & TEXT FALLBACK)
# ------------------------------------------------------------------
def search_subscene(
    query: str, 
    year: str = "", 
    imdb_id: str = "", 
    top_k: int = 1,
    exclude_urls: Optional[List[str]] = None,
    force_text: bool = False
) -> Tuple[List[Dict[str, str]], Optional[requests.Session]]:
    """
    Carian Pintar Subscene:
    - Peringkat 1: Carian terus IMDb ID dengan pengesahan longgar bagi nombor sekuel.
    - Peringkat 2: Carian Sandaran Teks Bersih (jika Peringkat 1 tiada hasil / dikecualikan).
    """
    target_imdb = imdb_id.strip() if imdb_id else ""
    if not target_imdb and re.match(r"^tt\d+$", query.strip()):
        target_imdb = query.strip()

    clean_query = query.replace("&", " and ")
    clean_query = re.sub(r"[:\-_/]", " ", clean_query).strip()
    clean_query = re.sub(r"\s+", " ", clean_query)

    best_movies = []
    cookies = {}
    ua = ""

    # ==============================================================
    # PERINGKAT 1: CARIAN TERUS IMDB ID
    # ==============================================================
    if target_imdb and not force_text:
        print(f"🔎 [Peringkat 1] Mencuba carian terus IMDb ID di Subscene: '{target_imdb}'...")
        try:
            raw_movies, cookies, ua = asyncio.run(_async_camoufox_search(target_imdb))
            if raw_movies:
                verified = pick_best_movie_matches(
                    raw_movies, 
                    target_title=clean_query, 
                    target_year=year, 
                    top_k=top_k, 
                    exclude_urls=exclude_urls,
                    is_imdb_match=True
                )
                if verified:
                    best_movies = verified
                    print(f"   🎯 Padanan Sah IMDb ID Ditemui: {best_movies[0]['title']} -> {best_movies[0]['url']}")
                else:
                    print(f"   ⚠️ Calon IMDb ID '{target_imdb}' ditolak (tidak sepadan / dalam senarai exclude). Beralih ke carian teks...")
            else:
                print(f"   ℹ️ Subscene tidak memulangkan hasil untuk IMDb ID '{target_imdb}'.")
        except Exception as e:
            print(f"   ⚠️ Ralat semasa carian IMDb ID: {e}")

    # ==============================================================
    # PERINGKAT 2: CARIAN SANDARAN TEKS BERSIH
    # ==============================================================
    if not best_movies:
        if target_imdb and not force_text:
            print(f"🔄 Mengaktifkan mod sandaran: Carian tajuk teks...")

        # Hanya sertakan tahun jika tajuk sangat pendek (<=3 huruf cth: 'It', 'Up', 'Us')
        if len(clean_query) <= 3 and year:
            search_text = f"{clean_query} {year}".strip()
        else:
            search_text = clean_query

        print(f"🔎 [Peringkat 2] Carian Sandaran Teks: '{search_text}' (Tahun: {year})")
        try:
            raw_movies, cookies_txt, ua_txt = asyncio.run(_async_camoufox_search(search_text))
            if raw_movies:
                best_movies = pick_best_movie_matches(
                    raw_movies, 
                    target_title=clean_query, 
                    target_year=year, 
                    top_k=top_k, 
                    exclude_urls=exclude_urls,
                    is_imdb_match=False
                )
                if best_movies:
                    cookies = cookies_txt
                    ua = ua_txt
                    print(f"   🎯 Padanan Teks Ditemui: {best_movies[0]['title']} -> {best_movies[0]['url']}")
        except Exception as e:
            print(f"   ❌ Ralat semasa carian teks: {e}")

        # Carian Sandaran Tanpa Tahun jika carian tajuk pendek dengan tahun memulangkan 0 hasil
        if not best_movies and search_text != clean_query:
            print(f"🔎 [Peringkat 2 - Sandaran Kata] Mencuba tanpa tahun: '{clean_query}' (Tahun: {year})")
            try:
                raw_movies_no_yr, cookies_ny, ua_ny = asyncio.run(_async_camoufox_search(clean_query))
                if raw_movies_no_yr:
                    best_movies = pick_best_movie_matches(
                        raw_movies_no_yr, 
                        target_title=clean_query, 
                        target_year=year, 
                        top_k=top_k, 
                        exclude_urls=exclude_urls,
                        is_imdb_match=False
                    )
                    if best_movies:
                        cookies = cookies_ny
                        ua = ua_ny
                        print(f"   🎯 Padanan Teks Ditemui: {best_movies[0]['title']} -> {best_movies[0]['url']}")
            except Exception as e:
                print(f"   ❌ Ralat semasa carian teks tanpa tahun: {e}")

        # Variasi sandaran: Jika bermula dengan awalan 'The '
        if not best_movies and clean_query.lower().startswith("the ") and len(clean_query) > 6:
            alt_query = clean_query[4:].strip()
            print(f"🔎 [Peringkat 2 - Variasi] Mencuba tanpa awalan 'The': '{alt_query}' (Tahun: {year})")
            try:
                raw_movies_alt, cookies_alt, ua_alt = asyncio.run(_async_camoufox_search(alt_query))
                if raw_movies_alt:
                    best_movies = pick_best_movie_matches(
                        raw_movies_alt, 
                        target_title=clean_query, 
                        target_year=year, 
                        top_k=top_k, 
                        exclude_urls=exclude_urls,
                        is_imdb_match=False
                    )
                    if best_movies:
                        cookies = cookies_alt
                        ua = ua_alt
                        print(f"   🎯 Padanan Teks Variasi Ditemui: {best_movies[0]['title']} -> {best_movies[0]['url']}")
            except Exception as e:
                print(f"   ❌ Ralat semasa carian variasi teks: {e}")

    if not best_movies:
        return [], None

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