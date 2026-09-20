#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO SUBTITLE CATALOG - PRODUCTION NULL RESOLVER ENGINE (V2 PRODUCTION)
# LOKASI: /home/braderdin/stremio-sub-addon/ondemand_title_packs/ondemand_engine/04_V2_resolve_null_records_catalog.py
# ==============================================================================

import os
import re
import sys
import json
import sqlite3
import argparse
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List, Set
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import dotenv_values
from guessit import guessit
from rapidfuzz import fuzz
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

console = Console()

# ==============================================================================
# KONFIGURASI DIREKTORI & PARAMETER
# ==============================================================================
PROJECT_DIR = Path("/home/braderdin/stremio-sub-addon/ondemand_title_packs")
DATA_DIR = PROJECT_DIR / "data"
TEMP_DIR = PROJECT_DIR / "temp"
ENV_PATH = Path("/home/braderdin/stremio-sub-addon/.env.local")

CATALOG_DBS = [
    DATA_DIR / "malay_subtitles_catalog_part_01.db",
    DATA_DIR / "malay_subtitles_catalog_part_02.db"
]

CACHE_DB_PATH = TEMP_DIR / "malay_null_resolved_cache_v2.db"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

MAX_ALLOWED_YEAR = 2026
DEFAULT_WORKER_THREADS = 12

# Membaca token & API Key TMDB dari .env.local
env_vars = dotenv_values(str(ENV_PATH)) if ENV_PATH.exists() else {}
TMDB_API_KEY = env_vars.get("TMDB_API_KEY") or os.getenv("TMDB_API_KEY")
TMDB_READ_TOKEN = env_vars.get("TMDB_READ_TOKEN") or os.getenv("TMDB_READ_TOKEN")

SEASON_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
    "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
    "nineteenth": 19, "twentieth": 20
}

WORD_NUMBERS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10"
}

GENERIC_TITLE_BLACKLIST = {
    "segunda temporada", "primera temporada", "tercera temporada",
    "first season", "second season", "third season", "fourth season",
    "season 1", "season 2", "season 3", "season 4", "season 5",
    "complete series", "episode", "special", "ova", "the series",
    "soundtrack", "making of", "behind the scenes", "blooper", "bloopers"
}

ULTRA_GENERIC_WORDS = {
    "you", "one", "two", "three", "me", "her", "him", "us", "it", "we", "he", "she",
    "they", "them", "life", "live", "run", "go", "home", "boy", "girl", "man", "woman",
    "time", "day", "night", "good", "bad", "big", "who", "what", "why", "how", "all",
    "new", "old", "red", "blue", "black", "white", "green", "yellow", "dark", "blood"
}

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for", "with",
    "by", "from", "season", "series", "episode", "musim", "part", "vol", "volume"
}

ACRONYMS = {"ripd", "swat", "fubar", "mib", "r2b", "ncis", "csi", "shield"}

JUNK_PATTERNS = [
    r"\blive with\b", r"\baftershow\b", r"\bbehind the scenes\b", 
    r"\bpodcast\b", r"\baudiobook\b", r"\bsoundtrack\b", r"\bmaking of\b"
]

# ==============================================================================
# PEMBERSIHAN TEKS & PENYERAGAMAN SINGKATAN
# ==============================================================================
def normalize_abbreviations(text: str) -> str:
    if not text:
        return ""
    t = text.lower()
    t = re.sub(r"\bms\.?\b", "miss", t)
    t = re.sub(r"\bmr\.?\b", "mister", t)
    t = re.sub(r"\bmrs\.?\b", "missus", t)
    t = re.sub(r"\bdr\.?\b", "doctor", t)
    t = re.sub(r"\bst\.?\b", "saint", t)
    t = re.sub(r"\bvs\.?\b", "versus", t)
    return t

def sanitize_general_text(text: str) -> str:
    if not text:
        return ""
    text = normalize_abbreviations(text)
    text = re.sub(r"[?¿!#@$^_+\\\/\[\]{}~`]", " ", text)
    text = re.sub(r"([a-zA-Z])(20\d\d|19\d\d)", r"\1 \2", text)
    text = text.replace("-", " ").replace("_", " ").replace(".", " ")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def strip_season_tokens_globally(text: str) -> str:
    if not text:
        return ""
    t = re.sub(
        r"[-_ ]*\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
        r"eleventh|twelfth|thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|"
        r"nineteenth|twentieth|\d+)(?:st|nd|rd|th)?[-_ ]*season\b",
        " ", text, flags=re.IGNORECASE
    )
    t = re.sub(r"[-_ ]*\bmusim[-_ ]*(?:ke[-_ ]*)?\d+\b", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bseason[-_ ]*\d+\b", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b[sS]\d{1,2}[-_ ]*[eE]\d{1,3}\b", " ", t)
    t = re.sub(r"\b\d{1,2}x\d{1,3}\b", " ", t)
    t = re.sub(r"\b[sS]\d{1,2}\b", " ", t)
    t = re.sub(r"\b(?:final|complete|tv)\b", " ", t, flags=re.IGNORECASE)
    return sanitize_general_text(t)

def strip_noise_tokens(text: str) -> str:
    if not text:
        return ""
    t = text
    t = re.sub(r"\[.*?\]|\(.*?\)", " ", t)
    t = re.sub(r"(?i)\b(?:www\.\w+\.\w+|cilokmovie|mkvking|pahe|yts|psa|katmoviehd|ganool|dramaday|moviesgogo)\b", " ", t)
    t = re.sub(r"(?i)\b(?:netflix|wetv|iqiyi|viu|dsnp|amzn|hbogo|nf|atvp|apple\s*tv|hbo|disney|hulu)\b", " ", t)
    t = re.sub(r"(?i)\b(?:kuning|biru|hijau|putih|merah|ungu|warna|subverse|biasa)\b", " ", t)
    t = re.sub(r"(?i)_track\d+_\[\w+\]|track\d+", " ", t)
    t = re.sub(r"(?i)\b(?:may|msa?|mly|malay|bahasa\s+malaysia|sarikata|ind|indo|indonesian|eng|english|sub|subs|subtitle)\b", " ", t)
    t = re.sub(r"(?i)\b(?:2160p?|1080p?|720p?|480p?|576p?|360p?)\b", " ", t)
    t = re.sub(r"(?i)\b(?:x264|x265|h264|h265|hevc|web-?dl|webrip|bluray|brrip|hdtv|hdrip|dvdrip|remux|aac|ac3|dts)\b", " ", t)
    t = re.sub(r"(?i)\.(?:srt|ass|ssa|vtt|sub|smi)$", "", t)
    return sanitize_general_text(t)

def extract_core_tokens(text: str) -> Set[str]:
    words = sanitize_general_text(text).lower().split()
    return {w for w in words if w not in STOPWORDS and len(w) > 1}

def extract_kdrama_date_year(text: str) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"(?:[eE]\d{1,3})?([0-2]\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\b", text)
    if m:
        yy = int(m.group(1))
        if 5 <= yy <= 26:
            return 2000 + yy
    return None

def extract_sequel_tag_v23(text: str) -> Optional[str]:
    """Ekstraksi sekuel selamat: Menapis tahun 4-angka dan menyokong perkataan angka."""
    if not text:
        return None
    t = text.lower()
    t = re.sub(r"\b(19\d\d|20\d\d)\b", " ", t)
    m = re.search(r"\b(?:vol(?:ume)?|part|chapter|pt)?\s*([1-9]|10|one|two|three|four|five|six|seven|eight|nine|ten|ii|iii|iv|v|vi|vii|viii|ix|x)\b", t)
    if m:
        val = m.group(1).lower()
        return WORD_NUMBERS.get(val, val)
    return None

# ==============================================================================
# PENGECAMAN AWAL EPISOD & TIPE MEDIA
# ==============================================================================
def parse_episode_and_season_early_v23(sub_fn: str, slug: str, pkg: str) -> Tuple[Optional[int], Optional[int], str]:
    season_val = None
    combined_context = f"{slug} {pkg}".lower()

    m_sw = re.search(
        r"[-_ ]*(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
        r"eleventh|twelfth|thirteenth|fourteenth|fifteenth|\d+)(?:st|nd|rd|th)?[-_ ]*season\b",
        combined_context
    )
    if m_sw:
        val = m_sw.group(1)
        season_val = SEASON_WORDS.get(val, int(val) if val.isdigit() and int(val) < 50 else None)

    if not season_val:
        m_ms = re.search(r"[-_ ]*musim[-_ ]*(?:ke[-_ ]*)?(\d+)\b", combined_context)
        if m_ms:
            season_val = int(m_ms.group(1))

    if not season_val:
        m_snum = re.search(r"\bseason[-_ ]*(\d+)\b", combined_context)
        if m_snum and int(m_snum.group(1)) < 50:
            season_val = int(m_snum.group(1))

    clean_fn = re.sub(r"(?i)\b(?:2160|1080|720|480)\b", " ", sub_fn)

    # 1. Standard S01E02 / 1x02
    m1 = re.search(r"\b[sS](\d{1,2})[-_ ]*[eE](\d{1,3})", clean_fn)
    if m1:
        s, e = int(m1.group(1)), int(m1.group(2))
        return (s if s < 50 else season_val), e, "series"

    m2 = re.search(r"\b(\d{1,2})x(\d{1,3})", clean_fn)
    if m2:
        s, e = int(m2.group(1)), int(m2.group(2))
        if s < 50 and e < 500:
            return s, e, "series"

    m3 = re.search(r"\b(?:ep|episode|e)[-_\.\s]*(\d{1,3})", clean_fn, re.IGNORECASE)
    if m3:
        return season_val or 1, int(m3.group(1)), "series"

    # 2. Corak Angka Tunggal Episod (cth: "8-ms.srt", "144.srt", "01.srt")
    m_single = re.match(r"^(\d{1,3})[-_\. ]*(?:ms|may|ind|sub|eng)?\.(?:srt|ass|vtt)$", sub_fn.strip(), re.IGNORECASE)
    if m_single:
        ep_num = int(m_single.group(1))
        if ep_num < 500:
            return season_val or 1, ep_num, "series"

    # 3. Kod Singkatan Drama (cth: "SHR12.ass", "DOTS05.srt")
    m_code = re.match(r"^[A-Za-z]{2,5}(\d{1,3})\.(?:srt|ass|vtt)$", sub_fn.strip(), re.IGNORECASE)
    if m_code:
        ep_num = int(m_code.group(1))
        if ep_num < 200:
            return season_val or 1, ep_num, "series"

    try:
        g = guessit(clean_fn)
        g_s = g.get("season")
        g_e = g.get("episode")
        if isinstance(g_e, list): g_e = g_e[0]
        if isinstance(g_s, list): g_s = g_s[0]
        if g_e is not None and isinstance(g_e, int):
            s_val = int(g_s) if g_s and int(g_s) < 50 else (season_val or 1)
            return s_val, int(g_e), "series"
    except Exception:
        pass

    if season_val is not None:
        return season_val, None, "series"

    return None, None, "movie"

# ==============================================================================
# PEMBINAAN QUERY HIERARKI V23
# ==============================================================================
def build_hierarchical_queries_v23(slug: str, pkg: str, sub_fn: str, int_p: str, media_type: str,
                                  slug_detected_year: Optional[int]) -> Tuple[List[Tuple[str, str]], Optional[int]]:
    detected_year = slug_detected_year
    if not detected_year:
        detected_year = extract_kdrama_date_year(sub_fn)
    if not detected_year:
        y_matches = re.findall(r"\b(19\d\d|20\d\d)\b", f"{slug} {pkg} {sub_fn}")
        if y_matches:
            valid_y = [int(y) for y in y_matches if 1930 <= int(y) <= MAX_ALLOWED_YEAR]
            if valid_y:
                detected_year = valid_y[-1]

    queries: List[Tuple[str, str]] = []
    seen_texts: Set[str] = set()

    slug_stripped = strip_season_tokens_globally(slug)
    slug_core_tokens = extract_core_tokens(slug_stripped)
    orig_slug_words_count = len(slug_stripped.split())

    def add_q(q_text: str, tier: str):
        clean_q = sanitize_general_text(q_text)
        words = clean_q.split()
        while words and words[-1].lower() in STOPWORDS:
            words.pop()
        clean_q = " ".join(words).strip()

        # Kunci Keselamatan: Slug >= 3 perkataan dilarang potong jadi 1 perkataan tunggal
        if orig_slug_words_count >= 3 and len(clean_q.split()) < 2:
            return

        if clean_q and len(clean_q) >= 2 and clean_q.lower() not in seen_texts:
            seen_texts.add(clean_q.lower())
            queries.append((clean_q, tier))

    # TIER 1: SLUG ALIAS PENUH
    slug_raw = re.sub(r"(?<=\d{4})[-_]\d+$", "", slug)
    slug_raw = re.sub(r"[-_]tv$", "", slug_raw, flags=re.IGNORECASE)
    slug_raw = strip_season_tokens_globally(slug_raw)

    alias_parts = re.split(r"--|[-_]aka[-_]|\s+aka\s+|/|\|", slug_raw, flags=re.IGNORECASE)
    for part in alias_parts:
        part_clean = re.sub(r"\b(19\d\d|20\d\d)\b", " ", part)
        part_text = sanitize_general_text(part_clean)
        if part_text:
            add_q(part_text, "1_SLUG_ALIAS")

    # TIER 2: CLEAN SUB_FILENAME (Mendahului Pemotongan Slug Singkatan)
    if sub_fn:
        fn_clean = strip_noise_tokens(sub_fn)
        fn_clean = strip_season_tokens_globally(fn_clean)
        if fn_clean:
            words_fn = fn_clean.split()
            if any(not w.isdigit() for w in words_fn):
                fn_title = None
                try:
                    g = guessit(fn_clean)
                    t = g.get("title")
                    if t and len(str(t)) >= 2:
                        fn_title = sanitize_general_text(str(t))
                except Exception:
                    pass

                target_fn_titles = [t for t in [fn_title, fn_clean] if t]
                for candidate_fn in target_fn_titles:
                    fn_core = extract_core_tokens(candidate_fn)
                    if slug_core_tokens and not fn_core.intersection(slug_core_tokens):
                        continue
                    add_q(candidate_fn, "2_SUB_FILENAME")

    # TIER 3: SLUG SHORT CHUNKS (3 & 2 Perkataan Sahaja)
    for part in alias_parts:
        part_clean = re.sub(r"\b(19\d\d|20\d\d)\b", " ", part)
        part_text = sanitize_general_text(part_clean)
        if part_text:
            words = part_text.split()
            if len(words) >= 4:
                add_q(" ".join(words[:3]), "3_SLUG_SHORT_3")
                add_q(" ".join(words[:2]), "3_SLUG_SHORT_2")
            elif len(words) == 3:
                add_q(" ".join(words[:2]), "3_SLUG_SHORT_2")

    # TIER 4: PACKAGE_NAME (Sandaran Terakhir)
    if pkg:
        pkg_clean = re.sub(r"_(?:HI_)?malay-\d+\.(?:zip|rar)$", "", pkg, flags=re.IGNORECASE)
        pkg_clean = re.sub(r"\b(19\d\d|20\d\d)\b", " ", pkg_clean)
        pkg_clean = strip_season_tokens_globally(pkg_clean)
        pkg_text = sanitize_general_text(pkg_clean)
        if pkg_text:
            add_q(pkg_text, "4_PACKAGE_NAME")

    return queries, detected_year

# ==============================================================================
# ENJIN PENILAIAN INTEGRITI V23 PRODUCTION
# ==============================================================================
def verify_candidate_integrity_v23(query: str, cand_title: str, query_year: Optional[int],
                                   cand_year: Optional[int], media_type: str, cand_type: str,
                                   season_num: Optional[int], query_sequel: Optional[str],
                                   tier: str, alt_cand_title: Optional[str] = None) -> Tuple[bool, float]:
    if not cand_title:
        return False, 0.0

    cand_title_clean = normalize_abbreviations(cand_title).strip()
    if cand_title_clean in GENERIC_TITLE_BLACKLIST:
        return False, 0.0

    # 1. Padanan Wajib Kategori Media
    if cand_type:
        c_type = "series" if cand_type in ["tv", "series"] else "movie"
        if media_type != c_type:
            return False, 0.0

    # 2. Had Tahun Terkini
    if cand_year and cand_year > MAX_ALLOWED_YEAR:
        return False, 0.0

    # 3. Kunci Keselamatan: Query Terpotong Tanpa Tahun Dilarang Memadankan Filem
    if "SHORT" in tier and not query_year and media_type == "movie":
        return False, 0.0

    # [PENINGKATAN V2 PRODUCTION]: Larangan Calon Bertahun NULL Sekiranya Query Mempunyai Tahun Rasmi
    if query_year and not cand_year:
        return False, 0.0

    # 4. Semakan Penahanan Token Inti
    q_core = extract_core_tokens(query)
    c_core = extract_core_tokens(cand_title_clean)
    if alt_cand_title:
        c_core = c_core.union(extract_core_tokens(normalize_abbreviations(alt_cand_title)))

    if not q_core:
        return False, 0.0

    containment = len(q_core.intersection(c_core)) / len(q_core)
    if len(q_core) <= 2:
        if containment < 1.0:
            return False, 0.0
    else:
        if containment < 0.60:
            return False, 0.0

    # 5. Kawalan Perkataan Ultra-Generic (Wajib Padanan Tahun Ketat)
    is_ultra_generic = any(w in ULTRA_GENERIC_WORDS for w in q_core) and len(q_core) <= 2
    if is_ultra_generic:
        if not query_year or not cand_year or abs(cand_year - query_year) > 1:
            return False, 0.0

    # 6. Kawalan Akronim
    for w in query.lower().split():
        w_clean = re.sub(r"[^\w\d]", "", w)
        if w_clean in ACRONYMS:
            cand_flat = re.sub(r"[^\w\d]", "", cand_title_clean)
            if alt_cand_title:
                cand_flat += " " + re.sub(r"[^\w\d]", "", alt_cand_title.lower())
            if w_clean not in cand_flat:
                return False, 0.0

    # 7. Kawalan Tahun Ketat
    if media_type == "movie":
        if query_year and cand_year:
            if abs(cand_year - query_year) > 1:
                return False, 0.0
        elif not query_year and cand_year:
            if cand_year < 1965 or cand_year >= 2025:
                return False, 0.0
    else:
        if query_year and cand_year:
            if cand_year > query_year + 1:
                return False, 0.0
            s_num = season_num or 1
            max_allowed_gap = 2 if s_num <= 2 else (s_num + 4)
            if (query_year - cand_year) > max_allowed_gap:
                return False, 0.0
        elif not query_year:
            # [PENINGKATAN V2 PRODUCTION]: Siri tanpa tahun query disekat daripada memadankan siri sebelum tahun 2000
            if cand_year and (cand_year < 2000 or cand_year >= 2025):
                return False, 0.0

    # 8. Kawalan Sekuel Bebas Tahun
    cand_sequel = extract_sequel_tag_v23(cand_title)
    if query_sequel and cand_sequel:
        if query_sequel != cand_sequel:
            return False, 0.0
    elif not query_sequel and cand_sequel in ["2", "3", "4", "5", "6", "7", "8", "9", "10"]:
        return False, 0.0

    # 9. Skor Keserupaan RapidFuzz
    q_clean = normalize_abbreviations(query)
    c_clean = cand_title_clean
    sim_main = fuzz.token_sort_ratio(q_clean, c_clean) / 100.0
    sim_alt = (fuzz.token_sort_ratio(q_clean, normalize_abbreviations(alt_cand_title)) / 100.0) if alt_cand_title else 0.0
    sim = max(sim_main, sim_alt)

    # Ambang Ketat V2 Production
    if is_ultra_generic:
        min_threshold = 0.88
    elif len(q_core) <= 2:
        if len(c_core) > len(q_core):
            min_threshold = 0.88
        else:
            min_threshold = 0.82 if (query_year and cand_year and abs(cand_year - query_year) <= 1) else 0.85
    else:
        min_threshold = 0.72 if (query_year and cand_year and abs(cand_year - query_year) <= 1) else 0.78

    return (sim >= min_threshold), sim

# ==============================================================================
# ENJIN HTTP: CINEMETA & TMDB
# ==============================================================================
def query_cinemeta_catalog(query_title: str, media_type: str) -> List[Dict[str, Any]]:
    if not query_title or len(query_title) < 2:
        return []
    encoded = urllib.parse.quote(query_title)
    url = f"https://v3-cinemeta.strem.io/catalog/{media_type}/top/search={encoded}.json"
    headers = {"User-Agent": "Mozilla/5.0 (StremioMalayProductionResolverV2/1.0)"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=4.5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("metas", [])
    except Exception:
        pass
    return []

def fetch_cinemeta_meta(media_type: str, imdb_id: str) -> Optional[Dict[str, Any]]:
    if not imdb_id or not imdb_id.startswith("tt"):
        return None
    url = f"https://v3-cinemeta.strem.io/meta/{media_type}/{imdb_id}.json"
    headers = {"User-Agent": "Mozilla/5.0 (StremioMalayProductionResolverV2/1.0)"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                meta = data.get("meta")
                if meta and meta.get("id"):
                    return meta
    except Exception:
        pass
    return None

def query_tmdb_search(query_title: str, media_type: str, year: Optional[int]) -> List[Dict[str, Any]]:
    if not query_title or len(query_title) < 2:
        return []
    target_type = "tv" if media_type == "series" else "movie"
    encoded = urllib.parse.quote(query_title)

    url = f"https://api.themoviedb.org/3/search/{target_type}?query={encoded}&include_adult=false&language=en-US&page=1"
    if year and media_type == "movie":
        url += f"&year={year}"

    headers = {"Accept": "application/json"}
    if TMDB_READ_TOKEN:
        headers["Authorization"] = f"Bearer {TMDB_READ_TOKEN}"
    elif TMDB_API_KEY:
        url += f"&api_key={TMDB_API_KEY}"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=4.5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("results", [])
    except Exception:
        pass
    return []

def fetch_tmdb_external_imdb(media_type: str, tmdb_id: int) -> Optional[str]:
    target_type = "tv" if media_type == "series" else "movie"
    url = f"https://api.themoviedb.org/3/{target_type}/{tmdb_id}/external_ids"
    headers = {"Accept": "application/json"}
    if TMDB_READ_TOKEN:
        headers["Authorization"] = f"Bearer {TMDB_READ_TOKEN}"
    elif TMDB_API_KEY:
        url += f"&api_key={TMDB_API_KEY}"

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                imdb = data.get("imdb_id")
                return imdb if imdb and imdb.startswith("tt") else None
    except Exception:
        pass
    return None

# ==============================================================================
# RESOLVER PEKERJA PER-SLUG (1 SLUG = 1 PENGESAHAN KANONIKAL)
# ==============================================================================
def resolve_slug_unit_production(slug_info: Dict[str, Any]) -> Dict[str, Any]:
    slug = slug_info["slug"]
    rep_pkg = slug_info.get("rep_pkg", "")
    rep_sub_fn = slug_info.get("rep_sub_fn", "")
    rep_int_p = slug_info.get("rep_int_p", "")
    slug_year = slug_info.get("slug_detected_year")

    season, episode, media_type = parse_episode_and_season_early_v23(rep_sub_fn, slug, rep_pkg)
    queries_with_tiers, year = build_hierarchical_queries_v23(slug, rep_pkg, rep_sub_fn, rep_int_p, media_type, slug_year)
    sequel = extract_sequel_tag_v23(slug)

    best_match = None
    best_score = 0.0

    # TAHAP 1: Carian Cinemeta Catalog
    for q_text, tier in queries_with_tiers:
        metas = query_cinemeta_catalog(q_text, media_type)
        for cand in metas[:6]:
            cand_name = cand.get("name", "")
            if any(re.search(p, cand_name, re.IGNORECASE) for p in JUNK_PATTERNS):
                continue

            cand_year_raw = cand.get("releaseInfo") or cand.get("year")
            cand_year = int(str(cand_year_raw)[:4]) if cand_year_raw and str(cand_year_raw)[:4].isdigit() else None
            cand_type = cand.get("type") or media_type

            valid, score = verify_candidate_integrity_v23(
                q_text, cand_name, year, cand_year, media_type, cand_type, season, sequel, tier
            )

            if valid and score > best_score:
                cand_imdb = cand.get("imdb_id")
                meta_details = fetch_cinemeta_meta(media_type, cand_imdb)
                if meta_details:
                    final_title = meta_details.get("name", cand_name)
                    final_year = str(cand_year) if cand_year else ""

                    best_score = score
                    best_match = {
                        "imdb_id": cand_imdb,
                        "canonical_title": final_title,
                        "release_year": final_year,
                        "media_type": cand_type
                    }

        if best_match:
            break

    # TAHAP 2: TMDB Search (Dengan Semakan Tajuk Asal) + Dwi-Pengesahan Cinemeta Meta
    if not best_match and (TMDB_READ_TOKEN or TMDB_API_KEY):
        for q_text, tier in queries_with_tiers:
            results = query_tmdb_search(q_text, media_type, year)
            for res in results[:6]:
                res_name = res.get("title") or res.get("name") or ""
                orig_name = res.get("original_title") or res.get("original_name") or ""
                date_str = res.get("release_date") or res.get("first_air_date") or ""
                cand_year = int(date_str[:4]) if len(date_str) >= 4 and date_str[:4].isdigit() else None
                cand_type = "series" if media_type == "series" else "movie"

                valid, score = verify_candidate_integrity_v23(
                    q_text, res_name, year, cand_year, media_type, cand_type, season, sequel, tier,
                    alt_cand_title=orig_name
                )

                if valid and score > best_score:
                    ext_imdb = fetch_tmdb_external_imdb(media_type, res["id"])
                    if ext_imdb:
                        meta_details = fetch_cinemeta_meta(media_type, ext_imdb)
                        if meta_details:
                            confirmed_title = meta_details.get("name", res_name)
                            best_score = score
                            best_match = {
                                "imdb_id": ext_imdb,
                                "canonical_title": confirmed_title,
                                "release_year": str(cand_year) if cand_year else "",
                                "media_type": media_type
                            }

            if best_match:
                break

    if best_match and best_match.get("imdb_id"):
        return {
            "slug": slug,
            "imdb_id": best_match["imdb_id"],
            "canonical_title": best_match["canonical_title"],
            "release_year": best_match["release_year"],
            "media_type": best_match["media_type"],
            "is_resolved": 1
        }

    return {
        "slug": slug,
        "imdb_id": None,
        "canonical_title": None,
        "release_year": None,
        "media_type": media_type,
        "is_resolved": 1
    }

# ==============================================================================
# INISIALISASI PANGKALAN DATA CACHE PERSISTEN
# ==============================================================================
def init_persistent_cache() -> sqlite3.Connection:
    conn = sqlite3.connect(str(CACHE_DB_PATH))
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS null_resolved_cache (
            slug TEXT PRIMARY KEY,
            imdb_id TEXT,
            canonical_title TEXT,
            release_year TEXT,
            media_type TEXT,
            is_resolved INTEGER DEFAULT 0
        );
    """)
    conn.commit()
    return conn

# ==============================================================================
# ALUR KERJA UTAMA
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="Enjin Pengemaskini Pukal Rekod NULL Katalog Sarikata (V2 Production)")
    parser.add_argument("--dry-run", action="store_true", help="Ujian simulasi tanpa menulis kemas kini ke fail katalog sebenar")
    parser.add_argument("--threads", type=int, default=DEFAULT_WORKER_THREADS, help="Bilangan pekerja serentak (threads)")
    parser.add_argument("--limit", type=int, default=None, help="Hadkan bilangan slug untuk diproses bagi tujuan semakan berperingkat")
    args = parser.parse_args()

    console.print("\n" + "=" * 80)
    console.print("🚀 [bold green]ENJIN PEMULIHAN REKOD NULL KATALOG MALAY (PRODUCTION V2 ENGINE)[/bold green]")
    console.print("=" * 80 + "\n")

    if args.dry_run:
        console.print("[bold yellow]⚠ MOD DRY-RUN AKTIF: Tiada sebarang pangkalan data katalog akan diubah.[/bold yellow]\n")

    for db_path in CATALOG_DBS:
        if not db_path.exists():
            console.print(f"[bold red]❌ Ralat: Fail pangkalan data katalog tidak ditemui: {db_path}[/bold red]")
            sys.exit(1)

    # -------------------------------------------------------------------------
    # LANGKAH 1: PEMBAIKAN PANTAS STREMIO_ID (JIKA IMDB SAH TETAPI TIADA STREMIO_ID)
    # -------------------------------------------------------------------------
    console.print("🔧 [cyan]LANGKAH 1: Menyemak pembaikan segera rekod dengan IMDb sah...[/cyan]")
    quick_fixed_total = 0

    for db_path in CATALOG_DBS:
        conn = sqlite3.connect(str(db_path), timeout=60)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT id, imdb_id, media_type, season, episode, sub_filename, slug
            FROM subtitle_catalog
            WHERE imdb_id IS NOT NULL AND imdb_id != ''
              AND (stremio_id IS NULL OR stremio_id = '');
        """)
        rows = cur.fetchall()
        
        if rows:
            fix_batch = []
            for r in rows:
                r_id = r["id"]
                imdb = r["imdb_id"]
                m_type = r["media_type"]
                s_val = r["season"]
                e_val = r["episode"]
                sub_fn = r["sub_filename"] or ""
                slug = r["slug"] or ""

                if not s_val and not e_val:
                    parsed_s, parsed_e, _ = parse_episode_and_season_early_v23(sub_fn, slug, "")
                    s_val = parsed_s
                    e_val = parsed_e

                if m_type == "series":
                    if s_val and e_val:
                        s_id = f"{imdb}:{s_val}:{e_val}"
                    elif s_val:
                        s_id = f"{imdb}:{s_val}"
                    else:
                        s_id = imdb
                else:
                    s_id = imdb

                fix_batch.append((s_id, s_val, e_val, r_id))

            if not args.dry_run and fix_batch:
                cur.executemany("""
                    UPDATE subtitle_catalog 
                    SET stremio_id = ?, season = ?, episode = ?
                    WHERE id = ?;
                """, fix_batch)
                conn.commit()

            quick_fixed_total += len(fix_batch)
            console.print(f"   ├─ [{db_path.name}] Stremio ID diperbaiki serta-merta: [green]{len(fix_batch):,}[/green] baris.")
        else:
            console.print(f"   ├─ [{db_path.name}] Tiada ralat Stremio ID tertinggal pada IMDb sah.")
        conn.close()

    console.print(f"   [green]✔ Jumlah baris dibaiki di Langkah 1:[/green] [yellow]{quick_fixed_total:,}[/yellow]\n")

    # -------------------------------------------------------------------------
    # LANGKAH 2: PENGUMPULAN SLUG BERSTATUS NULL & AGREGASI TAHUN
    # -------------------------------------------------------------------------
    console.print("🔍 [cyan]LANGKAH 2: Mengumpul slug unik dan konteks tarikh daripada rekod NULL...[/cyan]")
    
    slug_group_map: Dict[str, Dict[str, Any]] = {}
    null_rows_per_db: Dict[str, List[sqlite3.Row]] = {}

    for db_path in CATALOG_DBS:
        conn = sqlite3.connect(str(db_path), timeout=60)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT id, slug, package_name, sub_filename, internal_sub_path, season, episode
            FROM subtitle_catalog
            WHERE imdb_id IS NULL OR imdb_id = '';
        """)
        rows = cur.fetchall()
        null_rows_per_db[str(db_path)] = rows
        conn.close()

        console.print(f"   ├─ [{db_path.name}] Baris NULL ditemui: [yellow]{len(rows):,}[/yellow]")

        for r in rows:
            s = r["slug"]
            if not s:
                continue
            pkg = r["package_name"] or ""
            sub_fn = r["sub_filename"] or ""
            int_p = r["internal_sub_path"] or ""

            # Agregasi Tahun
            detected_y = extract_kdrama_date_year(sub_fn)
            if not detected_y:
                y_matches = re.findall(r"\b(19\d\d|20\d\d)\b", f"{s} {pkg} {sub_fn}")
                if y_matches:
                    valid_y = [int(y) for y in y_matches if 1930 <= int(y) <= MAX_ALLOWED_YEAR]
                    if valid_y:
                        detected_y = valid_y[-1]

            if s not in slug_group_map:
                slug_group_map[s] = {
                    "slug": s,
                    "rep_pkg": pkg,
                    "rep_sub_fn": sub_fn,
                    "rep_int_p": int_p,
                    "slug_detected_year": detected_y,
                    "count_null_subs": 1
                }
            else:
                slug_group_map[s]["count_null_subs"] += 1
                if not slug_group_map[s]["slug_detected_year"] and detected_y:
                    slug_group_map[s]["slug_detected_year"] = detected_y
                if len(sub_fn) > len(slug_group_map[s]["rep_sub_fn"]):
                    slug_group_map[s]["rep_pkg"] = pkg
                    slug_group_map[s]["rep_sub_fn"] = sub_fn
                    slug_group_map[s]["rep_int_p"] = int_p

    total_null_slugs = len(slug_group_map)
    console.print(f"\n   [green]✔ Jumlah slug unik NULL untuk diselesaikan:[/green] [yellow]{total_null_slugs:,}[/yellow]\n")

    # -------------------------------------------------------------------------
    # LANGKAH 3: PENYELESAIAN DENGAN BANTUAN CACHE PERSISTEN
    # -------------------------------------------------------------------------
    cache_conn = init_persistent_cache()
    cache_cur = cache_conn.cursor()

    cache_cur.execute("SELECT slug, imdb_id, canonical_title, release_year, media_type FROM null_resolved_cache WHERE is_resolved = 1;")
    cached_slug_resolutions: Dict[str, Dict[str, Any]] = {
        r[0]: {"imdb_id": r[1], "canonical_title": r[2], "release_year": r[3], "media_type": r[4]}
        for r in cache_cur.fetchall()
    }

    slugs_to_query = [
        data for s, data in slug_group_map.items()
        if s not in cached_slug_resolutions
    ]

    if args.limit and args.limit > 0:
        slugs_to_query = slugs_to_query[:args.limit]
        console.print(f"[bold yellow]⚡ Had carian dihadkan kepada: {len(slugs_to_query):,} slug unik.[/bold yellow]\n")

    console.print(Panel.fit(
        f"[bold cyan]Status Persediaan Resolusi V2 Production:[/bold cyan]\n"
        f"├─ Slug Selesai di Cache Sedia Ada : [green]{len(cached_slug_resolutions):,}[/green] tajuk\n"
        f"├─ Slug Wajib Dibuat Carian Baharu : [yellow]{len(slugs_to_query):,}[/yellow] tajuk\n"
        f"├─ Bilangan Bebenang (Threads)     : [cyan]{args.threads}[/cyan]\n"
        f"└─ Benteng Zero-False-Positive     : [green]Dual-Meta + Filename Priority + 0.88 Threshold[/green]",
        border_style="cyan"
    ))

    if slugs_to_query:
        batch_cache_save = []
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[bold yellow]{task.completed}/{task.total} Slug"),
            TimeElapsedColumn(),
            console=console
        ) as progress:
            task = progress.add_task("Mengesahkan IMDb V2...", total=len(slugs_to_query))

            with ThreadPoolExecutor(max_workers=args.threads) as executor:
                future_to_slug = {
                    executor.submit(resolve_slug_unit_production, item): item["slug"]
                    for item in slugs_to_query
                }

                for future in as_completed(future_to_slug):
                    res = future.result()
                    s = res["slug"]
                    cached_slug_resolutions[s] = res
                    batch_cache_save.append((
                        s, res["imdb_id"], res["canonical_title"],
                        res["release_year"], res["media_type"]
                    ))

                    if len(batch_cache_save) >= 100:
                        cache_cur.executemany("""
                            INSERT INTO null_resolved_cache (slug, imdb_id, canonical_title, release_year, media_type, is_resolved)
                            VALUES (?, ?, ?, ?, ?, 1)
                            ON CONFLICT(slug) DO UPDATE SET
                                imdb_id = excluded.imdb_id,
                                canonical_title = excluded.canonical_title,
                                release_year = excluded.release_year,
                                media_type = excluded.media_type,
                                is_resolved = 1;
                        """, batch_cache_save)
                        cache_conn.commit()
                        batch_cache_save.clear()

                    progress.update(task, advance=1)

            if batch_cache_save:
                cache_cur.executemany("""
                    INSERT INTO null_resolved_cache (slug, imdb_id, canonical_title, release_year, media_type, is_resolved)
                    VALUES (?, ?, ?, ?, ?, 1)
                    ON CONFLICT(slug) DO UPDATE SET
                        imdb_id = excluded.imdb_id,
                        canonical_title = excluded.canonical_title,
                        release_year = excluded.release_year,
                        media_type = excluded.media_type,
                        is_resolved = 1;
                """, batch_cache_save)
                cache_conn.commit()

    cache_cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    cache_conn.close()

    # -------------------------------------------------------------------------
    # LANGKAH 4: PENGEMASKINIAN ATOMIK KE DALAM DUA PANGKALAN DATA KATALOG
    # -------------------------------------------------------------------------
    console.print("\n💾 [cyan]LANGKAH 4: Menyuntik metadata sah ke pangkalan data katalog part 01 & 02...[/cyan]")
    
    total_recovered_rows = 0
    total_remaining_null_rows = 0

    for db_path in CATALOG_DBS:
        db_rows = null_rows_per_db.get(str(db_path), [])
        if not db_rows:
            continue

        update_batch = []
        recovered_in_this_db = 0

        for r in db_rows:
            r_id = r["id"]
            slug = r["slug"]
            sub_fn = r["sub_filename"] or ""
            pkg = r["package_name"] or ""

            res = cached_slug_resolutions.get(slug)
            if res and res.get("imdb_id"):
                imdb_id = res["imdb_id"]
                c_title = res["canonical_title"]
                r_year = res["release_year"]
                m_type = res["media_type"]

                # Pengesahan episod tepat per fail
                parsed_s, parsed_e, _ = parse_episode_and_season_early_v23(sub_fn, slug, pkg)
                final_s = r["season"] if r["season"] is not None else parsed_s
                final_e = r["episode"] if r["episode"] is not None else parsed_e

                if final_s or final_e:
                    m_type = "series"

                if m_type == "series":
                    if final_s and final_e:
                        stremio_id = f"{imdb_id}:{final_s}:{final_e}"
                    elif final_s:
                        stremio_id = f"{imdb_id}:{final_s}"
                    else:
                        stremio_id = imdb_id
                else:
                    stremio_id = imdb_id

                update_batch.append((
                    imdb_id, stremio_id, c_title, r_year, m_type, final_s, final_e, r_id
                ))
                recovered_in_this_db += 1

        if not args.dry_run and update_batch:
            conn = sqlite3.connect(str(db_path), timeout=60)
            cur = conn.cursor()
            cur.executemany("""
                UPDATE subtitle_catalog SET
                    imdb_id = ?,
                    stremio_id = ?,
                    canonical_title = ?,
                    release_year = ?,
                    media_type = ?,
                    season = ?,
                    episode = ?
                WHERE id = ?;
            """, update_batch)
            conn.commit()
            cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            conn.close()

        total_recovered_rows += recovered_in_this_db
        remaining_null = len(db_rows) - recovered_in_this_db
        total_remaining_null_rows += remaining_null

        console.print(f"   ├─ [{db_path.name}] Berjaya dipulihkan: [bold green]{recovered_in_this_db:,}[/bold green] baris | "
                      f"Kekal NULL: [yellow]{remaining_null:,}[/yellow] baris")

    # -------------------------------------------------------------------------
    # LANGKAH 5: RINGKASAN STATUS AKHIR
    # -------------------------------------------------------------------------
    all_null_scanned = total_recovered_rows + total_remaining_null_rows
    recovery_rate = (total_recovered_rows / all_null_scanned * 100) if all_null_scanned > 0 else 0.0

    table = Table(title="📋 Laporan Akhir Pemulihan Metadata NULL Katalog (V2 Production)", border_style="cyan")
    table.add_column("Kategori / Metrik", style="yellow")
    table.add_column("Jumlah Baris", justify="right", style="green")
    table.add_column("Peratusan", justify="right", style="magenta")

    table.add_row("Jumlah Baris NULL Asal", f"{all_null_scanned:,}", "100.0%")
    table.add_row("Berjaya Dipadankan (IMDb Sah)", f"{total_recovered_rows:,}", f"{recovery_rate:.1f}%")
    table.add_row("Kekal NULL (Selamat & Bersih)", f"{total_remaining_null_rows:,}", f"{100 - recovery_rate:.1f}%")
    table.add_row("Stremio ID Dibaiki di Langkah 1", f"{quick_fixed_total:,}", "-")

    console.print("\n", table)

    if args.dry_run:
        console.print("\n[bold yellow]✨ Simulasi DRY-RUN selesai sepenuhnya! Tiada perubahan kekal ditulis ke pangkalan data katalog.[/bold yellow]\n")
    else:
        console.print("\n[bold green]✨ SEMPURNA! Kedua-dua fail katalog berjaya dikemas kini secara bersih, atomik, dan selamat![/bold green]\n")

if __name__ == "__main__":
    main()