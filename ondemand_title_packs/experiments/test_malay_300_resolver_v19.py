#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO SUBTITLE CATALOG - RESOLVER TEST ENGINE V19 (ZERO-FALSE-POSITIVE)
# LOKASI: /home/braderdin/stremio-sub-addon/ondemand_title_packs/experiments/test_malay_300_resolver_v19.py
# ==============================================================================

import os
import re
import sys
import json
import sqlite3
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
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

console = Console()

# ==============================================================================
# KONFIGURASI DIREKTORI & PARAMETER
# ==============================================================================
BASE_DIR = Path("/home/braderdin/stremio-sub-addon/ondemand_title_packs")
DATA_DIR = BASE_DIR / "data"
TEMP_DIR = BASE_DIR / "temp"
ENV_PATH = Path("/home/braderdin/stremio-sub-addon/.env.local")

CATALOG_DB_PART_1 = DATA_DIR / "malay_subtitles_catalog_part_01.db"
CATALOG_DB_PART_2 = DATA_DIR / "malay_subtitles_catalog_part_02.db"

OUTPUT_JSON_PATH = TEMP_DIR / "sample_malay_v19_300_results.json"
OUTPUT_DB_PATH = TEMP_DIR / "sample_malay_v19_300_results.db"

TEMP_DIR.mkdir(parents=True, exist_ok=True)

SAMPLE_SIZE = 300
WORKER_THREADS = 10
MAX_ALLOWED_YEAR = 2026

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

ROMAN_NUMERALS = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6"}

# Senarai sekatan tajuk palsu / placeholder bahasa asing
GENERIC_TITLE_BLACKLIST = {
    "segunda temporada", "primera temporada", "tercera temporada",
    "first season", "second season", "third season", "fourth season",
    "season 1", "season 2", "season 3", "season 4", "season 5",
    "complete series", "episode", "special", "ova", "the series",
    "soundtrack", "making of", "behind the scenes", "blooper", "bloopers"
}

# Perkataan ultra-lazim yang wajib mempunyai padanan tahun ketat
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
# PEMBERSIHAN SEJAGAT & EKSTRAKSI TOKEN INTI
# ==============================================================================
def sanitize_general_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"[?¿!#@$^_+\\\/\[\]{}~`]", " ", text)
    text = re.sub(r"([a-zA-Z])(20\d\d|19\d\d)", r"\1 \2", text)
    text = text.replace("-", " ").replace("_", " ").replace(".", " ")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def strip_season_tokens_globally(text: str) -> str:
    """Pembersihan musim sejagat merentasi semua peringkat hierarki."""
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
    t = re.sub(r"(?i)\b(?:kuning|biru|hijau|putih|merah|ungu|warna|subverse|biasa)\b", " ", t)
    t = re.sub(r"(?i)_track\d+_\[\w+\]|track\d+", " ", t)
    t = re.sub(r"(?i)\b(?:may|msa?|mly|malay|bahasa\s+malaysia|sarikata|ind|indo|indonesian|eng|english|sub|subs|subtitle)\b", " ", t)
    t = re.sub(r"(?i)\b(?:2160p?|1080p?|720p?|480p?|576p?|360p?)\b", " ", t)
    t = re.sub(r"(?i)\b(?:x264|x265|h264|h265|hevc|web-?dl|webrip|bluray|brrip|hdtv|hdrip|dvdrip|remux|aac|ac3|dts)\b", " ", t)
    t = re.sub(r"(?i)\.(?:srt|ass|ssa|vtt|sub|smi)$", "", t)
    return sanitize_general_text(t)

def extract_core_tokens(text: str) -> Set[str]:
    """Mengekstrak perkataan teras penting (bukan kata henti)."""
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

def extract_sequel_tag(text: str) -> Optional[str]:
    t = text.lower()
    m = re.search(r"\b(?:vol|volume|part|chapter|pt)?\s*(\d+|ii|iii|iv|v|vi)\b", t)
    if m:
        val = m.group(1)
        return ROMAN_NUMERALS.get(val, val)
    return None

# ==============================================================================
# PENGECAMAN AWAL EPISOD & TIPE MEDIA
# ==============================================================================
def parse_episode_and_season_early(sub_fn: str, slug: str, pkg: str) -> Tuple[Optional[int], Optional[int], str]:
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
        m_ms = re.search(r"[-_ ]*musim[-_ ]*(?:ke[-_ ]*)?\d+\b", combined_context)
        if m_ms:
            season_val = int(m_ms.group(1))

    if not season_val:
        m_snum = re.search(r"\bseason[-_ ]*(\d+)\b", combined_context)
        if m_snum and int(m_snum.group(1)) < 50:
            season_val = int(m_snum.group(1))

    clean_fn = re.sub(r"(?i)\b(?:2160|1080|720|480)\b", " ", sub_fn)

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

    try:
        g = guessit(clean_fn)
        g_s = g.get("season")
        g_e = g.get("episode")
        if isinstance(g_e, list):
            g_e = g_e[0]
        if isinstance(g_s, list):
            g_s = g_s[0]
        if g_e is not None:
            s_val = int(g_s) if g_s and int(g_s) < 50 else (season_val or 1)
            return s_val, int(g_e), "series"
    except Exception:
        pass

    if season_val is not None:
        return season_val, None, "series"

    return None, None, "movie"

# ==============================================================================
# PEMBINAAN QUERY HIERARKI V19 (UNIVERSAL STRIP & EPISODE TRAP DEFENSE)
# ==============================================================================
def build_hierarchical_queries_v19(slug: str, pkg: str, sub_fn: str, int_p: str, media_type: str) -> Tuple[List[Tuple[str, str]], Optional[int]]:
    detected_year = extract_kdrama_date_year(sub_fn)
    if not detected_year:
        y_matches = re.findall(r"\b(19\d\d|20\d\d)\b", f"{slug} {pkg} {sub_fn}")
        if y_matches:
            valid_y = [int(y) for y in y_matches if 1930 <= int(y) <= MAX_ALLOWED_YEAR]
            if valid_y:
                detected_year = valid_y[-1]

    queries: List[Tuple[str, str]] = []
    seen_texts = set()

    def add_q(q_text: str, tier: str):
        clean_q = sanitize_general_text(q_text)
        if clean_q and len(clean_q) >= 2 and clean_q.lower() not in seen_texts:
            seen_texts.add(clean_q.lower())
            queries.append((clean_q, tier))

    # Ekstrak token teras daripada slug sebagai perisai penentu
    slug_stripped = strip_season_tokens_globally(slug)
    slug_core_tokens = extract_core_tokens(slug_stripped)

    # -------------------------------------------------------------------------
    # TIER 1: SLUG CHUNKS (Keutamaan Tertinggi & Pemecahan Alias Asia)
    # -------------------------------------------------------------------------
    slug_raw = re.sub(r"(?<=\d{4})[-_]\d+$", "", slug)
    slug_raw = re.sub(r"[-_]tv$", "", slug_raw, flags=re.IGNORECASE)
    slug_raw = strip_season_tokens_globally(slug_raw)

    alias_parts = re.split(r"--|[-_]aka[-_]|\s+aka\s+", slug_raw, flags=re.IGNORECASE)
    for part in alias_parts:
        part_clean = re.sub(r"\b(19\d\d|20\d\d)\b", " ", part)
        part_text = sanitize_general_text(part_clean)
        if part_text:
            add_q(part_text, "1_SLUG_ALIAS")
            words = part_text.split()
            # Pemecahan Pintar Alias Judul Asia
            if len(words) >= 4:
                add_q(" ".join(words[:2]), "1_SLUG_SHORT_2")
                add_q(" ".join(words[:3]), "1_SLUG_SHORT_3")
            elif len(words) == 3:
                add_q(" ".join(words[:2]), "1_SLUG_SHORT_2")
            elif len(words) == 2:
                # Menyelamatkan judul seperti 'Vincenzo Binsenjo' -> 'Vincenzo'
                add_q(words[0], "1_SLUG_SINGLE_WORD")

    # -------------------------------------------------------------------------
    # TIER 2: PACKAGE_NAME (Dibersihkan daripada Musim)
    # -------------------------------------------------------------------------
    if pkg:
        pkg_clean = re.sub(r"_(?:HI_)?malay-\d+\.(?:zip|rar)$", "", pkg, flags=re.IGNORECASE)
        pkg_clean = re.sub(r"\b(19\d\d|20\d\d)\b", " ", pkg_clean)
        pkg_clean = strip_season_tokens_globally(pkg_clean)
        pkg_text = sanitize_general_text(pkg_clean)
        if pkg_text:
            add_q(pkg_text, "2_PACKAGE_NAME")

    # -------------------------------------------------------------------------
    # TIER 3: SUB_FILENAME (Perisai Perangkap Tajuk Episod Diaktifkan)
    # -------------------------------------------------------------------------
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
                    # PERISAI EPISOD: Untuk siri, tolak nama fail jika 0 perkataan bertindih dengan slug
                    if media_type == "series" and slug_core_tokens:
                        if not fn_core.intersection(slug_core_tokens):
                            continue
                    add_q(candidate_fn, "3_SUB_FILENAME")

    # -------------------------------------------------------------------------
    # TIER 4: INTERNAL_SUB_PATH (Sandaran)
    # -------------------------------------------------------------------------
    if int_p and "/" in int_p:
        folder_candidate = Path(int_p).parent.name
        folder_clean = strip_noise_tokens(folder_candidate)
        folder_clean = strip_season_tokens_globally(folder_clean)
        if folder_clean:
            add_q(folder_clean, "4_INTERNAL_PATH")

    return queries, detected_year

# ==============================================================================
# ENJIN SKOR KETAT V19 (CORE TOKEN CONTAINMENT & GENERAL LOCKDOWN)
# ==============================================================================
def verify_candidate_integrity_v19(query: str, cand_title: str, query_year: Optional[int],
                                   cand_year: Optional[int], media_type: str, cand_type: str,
                                   season_num: Optional[int], query_sequel: Optional[str]) -> Tuple[bool, float]:
    if not cand_title:
        return False, 0.0

    cand_title_clean = cand_title.lower().strip()

    # 1. Penolakan Senarai Sekatan Tajuk Generik
    if cand_title_clean in GENERIC_TITLE_BLACKLIST:
        return False, 0.0

    # 2. Padanan Wajib Kategori Media
    if cand_type:
        c_type = "series" if cand_type in ["tv", "series"] else "movie"
        if media_type != c_type:
            return False, 0.0

    # 3. Had Tahun Maksimum
    if cand_year and cand_year > MAX_ALLOWED_YEAR:
        return False, 0.0

    # 4. FORMULA PENAHANAN TOKEN INTI (CORE TOKEN CONTAINMENT)
    q_core = extract_core_tokens(query)
    c_core = extract_core_tokens(cand_title)

    if not q_core:
        return False, 0.0

    containment = len(q_core.intersection(c_core)) / len(q_core)

    if len(q_core) <= 2:
        # Untuk 1-2 perkataan teras, SEMUA perkataan query wajib ada dalam tajuk calon
        if containment < 1.0:
            return False, 0.0
    else:
        # Untuk > 2 perkataan, minimum 60% perkataan teras mesti ada
        if containment < 0.60:
            return False, 0.0

    # 5. KAWALAN PERKATAAN ULTRA-GENERIC
    is_ultra_generic = any(w in ULTRA_GENERIC_WORDS for w in q_core) and len(q_core) <= 2
    if is_ultra_generic:
        # Kata ultra-lazim (cth: 'You', 'One') WAJIB mempunyai padanan tahun rasmi (diff <= 1)
        if not query_year or not cand_year or abs(cand_year - query_year) > 1:
            return False, 0.0

    # 6. Kawalan Akronim Mandatori (Contoh: R.I.P.D., SWAT, FUBAR)
    q_words = query.lower().split()
    for w in q_words:
        w_clean = re.sub(r"[^\w\d]", "", w)
        if w_clean in ACRONYMS:
            cand_flat = re.sub(r"[^\w\d]", "", cand_title.lower())
            if w_clean not in cand_flat:
                return False, 0.0

    # 7. Penapisan Tahun Filem & Siri
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
            if cand_year and (cand_year < 1995 or cand_year >= 2025):
                return False, 0.0

    # 8. Kawalan Tag Sekuel
    cand_sequel = extract_sequel_tag(cand_title)
    if query_sequel and cand_sequel != query_sequel:
        return False, 0.0
    if not query_sequel and cand_sequel in ["2", "3", "4", "5", "6"]:
        return False, 0.0

    # 9. Penilaian Keserupaan RapidFuzz
    q_clean = query.lower()
    c_clean = cand_title.lower()

    if len(q_core) <= 2:
        sim = fuzz.ratio(q_clean, c_clean) / 100.0
        min_threshold = 0.85 if not is_ultra_generic else 0.95
        return (sim >= min_threshold), sim

    sim = fuzz.token_sort_ratio(q_clean, c_clean) / 100.0
    min_threshold = 0.72 if (query_year and cand_year and abs(cand_year - query_year) <= 1) else 0.80

    return (sim >= min_threshold), sim

# ==============================================================================
# ENJIN HTTP: CINEMETA CATALOG, CINEMETA META & TMDB
# ==============================================================================
def query_cinemeta_catalog(query_title: str, media_type: str) -> List[Dict[str, Any]]:
    if not query_title or len(query_title) < 2:
        return []
    encoded = urllib.parse.quote(query_title)
    url = f"https://v3-cinemeta.strem.io/catalog/{media_type}/top/search={encoded}.json"
    headers = {"User-Agent": "Mozilla/5.0 (StremioMalayResolverV19/1.0)"}
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
    headers = {"User-Agent": "Mozilla/5.0 (StremioMalayResolverV19/1.0)"}
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
# ALUR RESOLVER UTAMA V19
# ==============================================================================
def resolve_sample_item_v19(item: Dict[str, Any]) -> Dict[str, Any]:
    catalog_id = item.get("id")
    slug = item.get("slug", "") or ""
    pkg = item.get("package_name", "") or ""
    sub_fn = item.get("sub_filename", "") or ""
    int_p = item.get("internal_sub_path", "") or ""

    season, episode, media_type = parse_episode_and_season_early(sub_fn, slug, pkg)
    queries_with_tiers, year = build_hierarchical_queries_v19(slug, pkg, sub_fn, int_p, media_type)
    sequel = extract_sequel_tag(slug)

    best_match = None
    best_score = 0.0
    engine_used = None
    used_query = None
    used_tier = None

    # TAHAP 1: Cinemeta Catalog Search
    for q_text, tier in queries_with_tiers:
        metas = query_cinemeta_catalog(q_text, media_type)
        for cand in metas[:6]:
            cand_name = cand.get("name", "")
            if any(re.search(p, cand_name, re.IGNORECASE) for p in JUNK_PATTERNS):
                continue

            cand_year_raw = cand.get("releaseInfo") or cand.get("year")
            cand_year = int(str(cand_year_raw)[:4]) if cand_year_raw and str(cand_year_raw)[:4].isdigit() else None
            cand_type = cand.get("type") or media_type

            valid, score = verify_candidate_integrity_v19(
                q_text, cand_name, year, cand_year, media_type, cand_type, season, sequel
            )
            if valid and score > best_score:
                cand_imdb = cand.get("imdb_id")
                meta_details = fetch_cinemeta_meta(media_type, cand_imdb)
                final_title = meta_details.get("name", cand_name) if meta_details else cand_name
                final_year = str(cand_year) if cand_year else ""

                best_score = score
                best_match = {
                    "imdb_id": cand_imdb,
                    "title": final_title,
                    "year": final_year,
                    "media_type": cand_type
                }
                engine_used = f"CINEMETA_CATALOG+META [{tier}]"
                used_query = q_text
                used_tier = tier

        if best_match:
            break

    # TAHAP 2: TMDB Fallback + Pengesahan Cinemeta Meta
    if not best_match and (TMDB_READ_TOKEN or TMDB_API_KEY):
        for q_text, tier in queries_with_tiers:
            results = query_tmdb_search(q_text, media_type, year)
            for res in results[:6]:
                res_name = res.get("title") or res.get("name") or ""
                date_str = res.get("release_date") or res.get("first_air_date") or ""
                cand_year = int(date_str[:4]) if len(date_str) >= 4 and date_str[:4].isdigit() else None
                cand_type = "series" if media_type == "series" else "movie"

                valid, score = verify_candidate_integrity_v19(
                    q_text, res_name, year, cand_year, media_type, cand_type, season, sequel
                )
                if valid and score > best_score:
                    ext_imdb = fetch_tmdb_external_imdb(media_type, res["id"])
                    if ext_imdb:
                        meta_details = fetch_cinemeta_meta(media_type, ext_imdb)
                        confirmed_title = meta_details.get("name", res_name) if meta_details else res_name
                        best_score = score
                        best_match = {
                            "imdb_id": ext_imdb,
                            "title": confirmed_title,
                            "year": str(cand_year) if cand_year else "",
                            "media_type": media_type
                        }
                        engine_used = f"TMDB_CONFIRMED [{tier}]"
                        used_query = q_text
                        used_tier = tier

            if best_match:
                break

    imdb_id = best_match["imdb_id"] if best_match else None
    stremio_id = None
    if imdb_id:
        if media_type == "series":
            if season and episode:
                stremio_id = f"{imdb_id}:{season}:{episode}"
            elif season:
                stremio_id = f"{imdb_id}:{season}"
            else:
                stremio_id = imdb_id
        else:
            stremio_id = imdb_id

    return {
        "catalog_db_id": catalog_id,
        "source_db": item.get("source_db", ""),
        "slug": slug,
        "package_name": pkg,
        "sub_filename": sub_fn,
        "internal_sub_path": int_p,
        "engine": engine_used,
        "used_query": used_query,
        "used_tier": used_tier,
        "recovered": bool(imdb_id),
        "imdb_id": imdb_id,
        "canonical_title": best_match["title"] if best_match else None,
        "release_year": best_match["year"] if best_match else None,
        "media_type": media_type,
        "season": season,
        "episode": episode,
        "stremio_id": stremio_id
    }

# ==============================================================================
# PENGAMBILAN 300 SAMPEL RAWAK NULL
# ==============================================================================
def collect_null_samples() -> List[Dict[str, Any]]:
    samples = []
    half_size = SAMPLE_SIZE // 2

    for db_path, count in [(CATALOG_DB_PART_1, half_size), (CATALOG_DB_PART_2, SAMPLE_SIZE - half_size)]:
        if not db_path.exists():
            console.print(f"[bold red]❌ Pangkalan data tidak ditemui: {db_path}[/bold red]")
            continue

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT id, slug, package_name, sub_filename, sub_extension, internal_sub_path, subscene_id
            FROM subtitle_catalog 
            WHERE (imdb_id IS NULL OR imdb_id = '') 
               OR (stremio_id IS NULL OR stremio_id = '')
            ORDER BY RANDOM() LIMIT ?;
        """, (count,))
        
        for r in cur.fetchall():
            row_dict = dict(r)
            row_dict["source_db"] = db_path.name
            samples.append(row_dict)
        conn.close()

    return samples

def save_results_to_sqlite(results: List[Dict[str, Any]]):
    if OUTPUT_DB_PATH.exists():
        OUTPUT_DB_PATH.unlink()

    conn = sqlite3.connect(str(OUTPUT_DB_PATH))
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE sample_v19_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            catalog_db_id INTEGER,
            source_db TEXT,
            slug TEXT,
            package_name TEXT,
            sub_filename TEXT,
            internal_sub_path TEXT,
            engine TEXT,
            used_query TEXT,
            used_tier TEXT,
            recovered INTEGER,
            imdb_id TEXT,
            canonical_title TEXT,
            release_year TEXT,
            media_type TEXT,
            season INTEGER,
            episode INTEGER,
            stremio_id TEXT
        );
    """)
    records = [
        (
            r["catalog_db_id"], r["source_db"], r["slug"], r["package_name"], r["sub_filename"],
            r["internal_sub_path"], r["engine"], r["used_query"], r["used_tier"],
            1 if r["recovered"] else 0, r["imdb_id"], r["canonical_title"], r["release_year"],
            r["media_type"], r["season"], r["episode"], r["stremio_id"]
        )
        for r in results
    ]
    cur.executemany("""
        INSERT INTO sample_v19_results 
        (catalog_db_id, source_db, slug, package_name, sub_filename, internal_sub_path,
         engine, used_query, used_tier, recovered, imdb_id, canonical_title, release_year,
         media_type, season, episode, stremio_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, records)
    conn.commit()
    conn.close()

# ==============================================================================
# ENTRY POINT UTAMA
# ==============================================================================
def main():
    console.print("\n" + "=" * 80)
    console.print("🛡️  [bold green]UJIAN PENYELESAIAN 300 SAMPEL NULL V19 (ZERO-FALSE-POSITIVE LOCKDOWN)[/bold green]")
    console.print("=" * 80 + "\n")

    samples = collect_null_samples()
    if not samples:
        console.print("[bold red]❌ Tiada sampel rekod NULL ditemui daripada fail katalog![/bold red]")
        sys.exit(1)

    console.print(f"📥 Mengambil [cyan]{len(samples)}[/cyan] sampel rawak NULL (Part 01 + Part 02).")
    console.print(f"⚙️  Pekerja Serentak: [yellow]{WORKER_THREADS} Threads[/yellow]")
    console.print(f"🔒 Benteng Keselamatan: [green]Core Token Containment + Universal Season Stripper + Blacklist[/green]\n")

    results = []
    success_count = 0
    tier_stats = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[bold yellow]{task.completed}/{task.total} Judul"),
        TimeElapsedColumn(),
        console=console
    ) as progress:
        task = progress.add_task("Mengesahkan IMDb V19...", total=len(samples))

        with ThreadPoolExecutor(max_workers=WORKER_THREADS) as executor:
            future_to_item = {executor.submit(resolve_sample_item_v19, s): s for s in samples}
            for future in as_completed(future_to_item):
                res = future.result()
                results.append(res)
                if res["recovered"]:
                    success_count += 1
                    t = res.get("used_tier") or "UNKNOWN"
                    tier_stats[t] = tier_stats.get(t, 0) + 1
                progress.update(task, advance=1)

    with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    save_results_to_sqlite(results)

    json_kb = OUTPUT_JSON_PATH.stat().st_size / 1024
    console.print(f"\n[bold green]💾 Fail JSON disimpan:[/bold green] [yellow]{OUTPUT_JSON_PATH}[/yellow] ({json_kb:.2f} KB)")
    console.print(f"[bold green]💾 Fail SQLite disimpan:[/bold green] [yellow]{OUTPUT_DB_PATH}[/yellow]")

    acc_rate = (success_count / len(samples)) * 100
    table = Table(title="📊 Analisis Ketepatan Padanan Sampel NULL V19", border_style="cyan")
    table.add_column("Status / Peringkat Hierarki", style="yellow")
    table.add_column("Jumlah Ditemui", justify="right", style="green")
    table.add_column("Peratusan", justify="right", style="magenta")

    table.add_row("BERJAYA DIKESAN (IMDb Sah)", f"{success_count:,}", f"{acc_rate:.1f}%")
    for tier_name, count_val in sorted(tier_stats.items()):
        table.add_row(f"  ├─ {tier_name}", f"{count_val:,}", f"{(count_val/len(samples))*100:.1f}%")
    table.add_row("KEKAL NULL (Selamat & Bersih)", f"{len(samples) - success_count:,}", f"{100 - acc_rate:.1f}%")
    console.print(table)

if __name__ == "__main__":
    main()