#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO SUBTITLE CATALOG - PRODUCTION SYNC ENGINE V2 (V16 LOGIC)
# LOKASI: /home/braderdin/stremio-sub-addon/ondemand_title_packs/ondemand_engine/01_sync_imdb_malay_catalog_v2.py
# ==============================================================================

import os
import re
import sys
import json
import sqlite3
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import dotenv_values
from guessit import guessit
from rapidfuzz import fuzz
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

console = Console()

# ==============================================================================
# KONFIGURASI DIREKTORI & PARAMETER PRODUKSI
# ==============================================================================
PROJECT_DIR = Path("/home/braderdin/stremio-sub-addon/ondemand_title_packs")
DATA_DIR = PROJECT_DIR / "data"
TEMP_DIR = PROJECT_DIR / "temp"
ENV_PATH = Path("/home/braderdin/stremio-sub-addon/.env.local")

SOURCE_MS_DBS = [
    DATA_DIR / "archive_detailed_map_ms_part_01.db",
    DATA_DIR / "archive_detailed_map_ms_part_02.db"
]

CACHE_DB_PATH = TEMP_DIR / "malay_slug_imdb_cache_v2.db"
OUTPUT_PREFIX = "malay_subtitles_catalog"

MAX_ROWS_PER_DB = 45000  # Batas aman partisi GitHub (~20MB - 25MB)
WORKER_THREADS = 12
MAX_ALLOWED_YEAR = 2025  # Menghalau entri placeholder masa depan

# Pembacaan Kredensial TMDB dari .env.local
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

JUNK_PATTERNS = [
    r"\blive with\b", r"\baftershow\b", r"\bbehind the scenes\b", 
    r"\bpodcast\b", r"\baudiobook\b", r"\bsoundtrack\b", r"\bmaking of\b"
]

# ==============================================================================
# PEMBERSIHAN TOKEN SUBSCENE, NOISE & EKSTRAKSI TANGGAL
# ==============================================================================
def sanitize_general_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"[?¿!#@$^_+\\\/\[\]{}~`]", " ", text)
    text = re.sub(r"([a-zA-Z])(20\d\d|19\d\d)", r"\1 \2", text)
    text = text.replace("-", " ").replace("_", " ").replace(".", " ")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def strip_noise_tokens(filename: str) -> str:
    if not filename:
        return ""
    t = filename
    t = re.sub(r"\[.*?\]|\(.*?\)", " ", t)
    t = re.sub(r"(?i)\b(?:www\.\w+\.\w+|cilokmovie|mkvking|pahe|yts|psa|katmoviehd)\b", " ", t)
    t = re.sub(r"(?i)\b(?:kuning|biru|hijau|putih|merah|ungu|warna|subverse)\b", " ", t)
    t = re.sub(r"(?i)_track\d+_\[\w+\]|track\d+", " ", t)
    t = re.sub(r"(?i)\b(?:may|msa?|mly|malay|bahasa\s+malaysia|sarikata|ind|indo|indonesian|eng|english|sub|subs|subtitle)\b", " ", t)
    t = re.sub(r"(?i)\b(?:2160p?|1080p?|720p?|480p?|576p?|360p?)\b", " ", t)
    t = re.sub(r"(?i)\b(?:x264|x265|h264|h265|hevc|web-?dl|webrip|bluray|brrip|hdtv|hdrip|dvdrip|remux|aac|ac3|dts)\b", " ", t)
    t = re.sub(r"(?i)\.(?:srt|ass|ssa|vtt|sub|smi)$", "", t)
    return sanitize_general_text(t)

def extract_kdrama_date_year(text: str) -> Optional[int]:
    """Mendeteksi format tanggal siaran (YYMMDD) termasuk yang menempel pada nomor episode (E18180822)."""
    if not text:
        return None
    m = re.search(r"(?:[eE]\d{1,3})?([0-2]\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\b", text)
    if m:
        yy = int(m.group(1))
        if 5 <= yy <= 25:
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
# PARSING AWAL EPISODE & PENENTUAN TIPE MEDIA
# ==============================================================================
def parse_episode_and_season_early(sub_fn: str, slug: str) -> Tuple[Optional[int], Optional[int], str]:
    season_val = None
    slug_lower = slug.lower()

    m_sw = re.search(
        r"[-_ ]*(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
        r"eleventh|twelfth|thirteenth|fourteenth|fifteenth|\d+)(?:st|nd|rd|th)?[-_ ]*season\b",
        slug_lower
    )
    if m_sw:
        val = m_sw.group(1)
        season_val = SEASON_WORDS.get(val, int(val) if val.isdigit() and int(val) < 50 else None)

    if not season_val:
        m_ms = re.search(r"[-_ ]*musim[-_ ]*(?:ke[-_ ]*)?(\d+)\b", slug_lower)
        if m_ms:
            season_val = int(m_ms.group(1))

    if not season_val:
        m_snum = re.search(r"\bseason[-_ ]*(\d+)\b", slug_lower)
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
# PEMBINAAN QUERY PENCARIAN
# ==============================================================================
def build_clean_query_candidates(slug: str, pkg: str, sub_fn: str) -> Tuple[List[str], Optional[int]]:
    detected_year = extract_kdrama_date_year(sub_fn)

    if not detected_year:
        y_matches = re.findall(r"\b(19\d\d|20\d\d)\b", f"{slug} {pkg} {sub_fn}")
        if y_matches:
            valid_y = [int(y) for y in y_matches if 1930 <= int(y) <= MAX_ALLOWED_YEAR]
            if valid_y:
                detected_year = valid_y[-1]

    primary = slug.split("--")[0]
    primary = re.split(r"[-_]aka[-_]|\s+aka\s+", primary, flags=re.IGNORECASE)[0]
    primary = re.sub(r"(?<=\d{4})[-_]\d+$", "", primary)
    primary = re.sub(r"[-_]tv$", "", primary, flags=re.IGNORECASE)

    primary = re.sub(
        r"[-_ ]*(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
        r"eleventh|twelfth|thirteenth|fourteenth|fifteenth|\d+)(?:st|nd|rd|th)?[-_ ]*season\b",
        " ", primary, flags=re.IGNORECASE
    )
    primary = re.sub(r"[-_ ]*musim[-_ ]*(?:ke[-_ ]*)?\d+\b", " ", primary, flags=re.IGNORECASE)
    primary = re.sub(r"\bseason[-_ ]*\d+\b", " ", primary, flags=re.IGNORECASE)
    primary = re.sub(r"\b(19\d\d|20\d\d)\b", " ", primary)
    slug_title = sanitize_general_text(primary)

    fn_title = None
    clean_fn = strip_noise_tokens(sub_fn)
    if clean_fn:
        words_fn = clean_fn.split()
        if any(not w.isdigit() for w in words_fn):
            try:
                g = guessit(clean_fn)
                t = g.get("title")
                if t and len(str(t)) >= 2:
                    fn_title = sanitize_general_text(str(t))
            except Exception:
                pass
            if not fn_title:
                fn_title = clean_fn

    queries = []
    if fn_title and len(fn_title) > 2 and fn_title not in queries:
        queries.append(fn_title)

    if slug_title and slug_title not in queries:
        queries.append(slug_title)

    words = slug_title.split()
    if len(words) > 4:
        short_q = " ".join(words[:4])
        if short_q not in queries:
            queries.append(short_q)

    return queries, detected_year

# ==============================================================================
# ATURAN INTEGRITAS KETAT V16 (ZERO-FALSE-POSITIVE)
# ==============================================================================
def verify_candidate_integrity_v16(query: str, cand_title: str, query_year: Optional[int],
                                   cand_year: Optional[int], media_type: str, cand_type: str,
                                   season_num: Optional[int], query_sequel: Optional[str]) -> Tuple[bool, float]:
    if not cand_title:
        return False, 0.0

    if cand_type:
        c_type = "series" if cand_type in ["tv", "series"] else "movie"
        if media_type != c_type:
            return False, 0.0

    if cand_year and cand_year > MAX_ALLOWED_YEAR:
        return False, 0.0

    if media_type == "movie":
        if query_year and cand_year:
            if abs(cand_year - query_year) > 1:
                return False, 0.0
        elif not query_year and cand_year:
            if cand_year < 1960:
                return False, 0.0
    else:
        if query_year and cand_year:
            if cand_year > query_year + 1:
                return False, 0.0
            s_num = season_num or 1
            max_allowed_gap = 2 if s_num <= 2 else (s_num + 3)
            if (query_year - cand_year) > max_allowed_gap:
                return False, 0.0
        elif not query_year:
            if cand_year and cand_year < 2000:
                return False, 0.0

    cand_sequel = extract_sequel_tag(cand_title)
    if query_sequel and cand_sequel != query_sequel:
        return False, 0.0
    if not query_sequel and cand_sequel in ["2", "3", "4", "5", "6"]:
        return False, 0.0

    q_clean = query.lower()
    c_clean = cand_title.lower()
    words_count = len(q_clean.split())

    if words_count <= 2:
        sim = fuzz.ratio(q_clean, c_clean) / 100.0
        if sim < 0.88:
            return False, 0.0
        if not query_year or not cand_year or abs(cand_year - query_year) > 1:
            return False, 0.0
        return True, sim

    sim = fuzz.token_sort_ratio(q_clean, c_clean) / 100.0
    min_threshold = 0.72 if (query_year and cand_year and abs(cand_year - query_year) <= 1) else 0.82

    return (sim >= min_threshold), sim

# ==============================================================================
# PEMANGGILAN HTTP: CINEMETA & TMDB
# ==============================================================================
def query_cinemeta(query_title: str, media_type: str) -> List[Dict[str, Any]]:
    if not query_title or len(query_title) < 2:
        return []
    encoded = urllib.parse.quote(query_title)
    url = f"https://v3-cinemeta.strem.io/catalog/{media_type}/top/search={encoded}.json"
    headers = {"User-Agent": "Mozilla/5.0 (StremioMalayProductionEngine/V16)"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=4.5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("metas", [])
    except Exception:
        pass
    return []

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
# RESOLUSI SLUG TINGKAT WORKER
# ==============================================================================
def resolve_slug_worker(slug_data: Dict[str, Any]) -> Dict[str, Any]:
    slug = slug_data["slug"]
    pkg = slug_data.get("rep_pkg", "")
    sub_fn = slug_data.get("rep_sub_fn", "")

    season, episode, media_type = parse_episode_and_season_early(sub_fn, slug)
    queries, year = build_clean_query_candidates(slug, pkg, sub_fn)
    sequel = extract_sequel_tag(slug)

    best_match = None
    best_score = 0.0

    # 1. Cinemeta
    for q in queries:
        metas = query_cinemeta(q, media_type)
        for cand in metas[:6]:
            cand_name = cand.get("name", "")
            if any(re.search(p, cand_name, re.IGNORECASE) for p in JUNK_PATTERNS):
                continue

            cand_year_raw = cand.get("releaseInfo") or cand.get("year")
            cand_year = int(str(cand_year_raw)[:4]) if cand_year_raw and str(cand_year_raw)[:4].isdigit() else None
            cand_type = cand.get("type") or media_type

            valid, score = verify_candidate_integrity_v16(
                q, cand_name, year, cand_year, media_type, cand_type, season, sequel
            )
            if valid and score > best_score:
                best_score = score
                best_match = {
                    "imdb_id": cand.get("imdb_id"),
                    "canonical_title": cand_name,
                    "canonical_year": str(cand_year) if cand_year else "",
                    "media_type": cand_type
                }

        if best_match:
            break

    # 2. TMDB Fallback
    if not best_match and (TMDB_READ_TOKEN or TMDB_API_KEY):
        for q in queries:
            results = query_tmdb_search(q, media_type, year)
            for res in results[:6]:
                res_name = res.get("title") or res.get("name") or ""
                date_str = res.get("release_date") or res.get("first_air_date") or ""
                cand_year = int(date_str[:4]) if len(date_str) >= 4 and date_str[:4].isdigit() else None
                cand_type = "series" if media_type == "series" else "movie"

                valid, score = verify_candidate_integrity_v16(
                    q, res_name, year, cand_year, media_type, cand_type, season, sequel
                )
                if valid and score > best_score:
                    ext_imdb = fetch_tmdb_external_imdb(media_type, res["id"])
                    if ext_imdb:
                        best_score = score
                        best_match = {
                            "imdb_id": ext_imdb,
                            "canonical_title": res_name,
                            "canonical_year": str(cand_year) if cand_year else "",
                            "media_type": media_type
                        }

            if best_match:
                break

    if best_match and best_match.get("imdb_id"):
        return {
            "slug": slug,
            "imdb_id": best_match["imdb_id"],
            "canonical_title": best_match["canonical_title"],
            "canonical_year": best_match["canonical_year"],
            "media_type": best_match["media_type"]
        }

    return {
        "slug": slug,
        "imdb_id": None,
        "canonical_title": None,
        "canonical_year": None,
        "media_type": media_type
    }

# ==============================================================================
# CACHE DAN INISIALISASI DATABASE KATALOG
# ==============================================================================
def init_slug_cache() -> sqlite3.Connection:
    conn = sqlite3.connect(str(CACHE_DB_PATH))
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS malay_slug_cache (
            slug TEXT PRIMARY KEY,
            imdb_id TEXT,
            canonical_title TEXT,
            canonical_year TEXT,
            media_type TEXT,
            is_resolved INTEGER DEFAULT 0
        );
    """)
    conn.commit()
    return conn

def init_catalog_db(db_path: Path) -> sqlite3.Connection:
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS subtitle_catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            imdb_id TEXT,
            stremio_id TEXT,
            canonical_title TEXT,
            release_year TEXT,
            media_type TEXT NOT NULL,
            season INTEGER,
            episode INTEGER,
            language_code TEXT NOT NULL,
            slug TEXT NOT NULL,
            package_name TEXT,
            sub_filename TEXT NOT NULL,
            sub_extension TEXT NOT NULL,
            internal_sub_path TEXT NOT NULL,
            sub_size_bytes INTEGER NOT NULL,
            subscene_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cat_stremio ON subtitle_catalog (stremio_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cat_imdb ON subtitle_catalog (imdb_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cat_slug ON subtitle_catalog (slug);")
    conn.commit()
    return conn

def finalize_db(conn: sqlite3.Connection, db_path: Path, count: int):
    cur = conn.cursor()
    cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.commit()
    conn.close()
    sz_mb = db_path.stat().st_size / (1024 * 1024)
    console.print(f"   [bold green]💾 Berhasil Mengunci Bagian[/bold green] -> [yellow]{db_path.name}[/yellow] "
                  f"([cyan]{count:,}[/cyan] baris | [magenta]{sz_mb:.2f} MB[/magenta])")

# ==============================================================================
# ALUR KERJA UTAMA
# ==============================================================================
def main():
    console.print("\n" + "=" * 80)
    console.print("🚀 [bold green]PRODUKSI: SINKRONISASI KATALOG SUBTITEL (ENGINE V16 HARDENED)[/bold green]")
    console.print("=" * 80 + "\n")

    for db_p in SOURCE_MS_DBS:
        if not db_p.exists():
            console.print(f"[bold red]❌ Error: File database sumber tidak ada: {db_p}[/bold red]")
            sys.exit(1)

    # 1. Pindai seluruh slug unik dan ambil sampel nama file terbaik
    console.print("🔍 [cyan]Mengumpulkan seluruh slug unik dari database arsip sumber...[/cyan]")
    slug_rep_map: Dict[str, Dict[str, str]] = {}

    for db_p in SOURCE_MS_DBS:
        conn = sqlite3.connect(str(db_p))
        cur = conn.cursor()
        cur.execute("SELECT slug, package_name, sub_filename FROM archive_subtitles_v2;")
        for slug, pkg, sub_fn in cur.fetchall():
            if slug not in slug_rep_map:
                slug_rep_map[slug] = {"rep_pkg": pkg or "", "rep_sub_fn": sub_fn or ""}
            else:
                if len(sub_fn or "") > len(slug_rep_map[slug]["rep_sub_fn"]):
                    slug_rep_map[slug] = {"rep_pkg": pkg or "", "rep_sub_fn": sub_fn or ""}
        conn.close()

    total_slugs = len(slug_rep_map)
    console.print(f"   [green]✔ Ditemukan [yellow]{total_slugs:,}[/yellow] judul (slug) unik.[/green]\n")

    # 2. Resolusi Cache Slug Persisten
    cache_conn = init_slug_cache()
    cache_cur = cache_conn.cursor()

    cache_cur.execute("SELECT slug, imdb_id, canonical_title, canonical_year, media_type FROM malay_slug_cache WHERE is_resolved = 1;")
    resolved_cache = {
        r[0]: {"imdb_id": r[1], "canonical_title": r[2], "canonical_year": r[3], "media_type": r[4]}
        for r in cache_cur.fetchall()
    }

    slugs_to_resolve = [
        {"slug": s, "rep_pkg": d["rep_pkg"], "rep_sub_fn": d["rep_sub_fn"]}
        for s, d in slug_rep_map.items() if s not in resolved_cache
    ]

    console.print(f"⚡ [cyan]Status Verifikasi Metadata Slug ({WORKER_THREADS} Threads):[/cyan]")
    console.print(f"   ├─ Terverifikasi di Cache V2: [green]{len(resolved_cache):,}[/green] judul")
    console.print(f"   └─ Perlu diverifikasi        : [yellow]{len(slugs_to_resolve):,}[/yellow] judul\n")

    if slugs_to_resolve:
        batch_cache = []
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[bold yellow]{task.completed}/{task.total} Judul"),
            TimeElapsedColumn(),
            console=console
        ) as progress:
            task = progress.add_task("Memverifikasi IMDb V16...", total=len(slugs_to_resolve))

            with ThreadPoolExecutor(max_workers=WORKER_THREADS) as executor:
                future_to_slug = {executor.submit(resolve_slug_worker, item): item for item in slugs_to_resolve}
                for future in as_completed(future_to_slug):
                    res = future.result()
                    s = res["slug"]
                    resolved_cache[s] = res
                    batch_cache.append((res["imdb_id"], res["canonical_title"], res["canonical_year"], res["media_type"], s))

                    if len(batch_cache) >= 150:
                        cache_cur.executemany("""
                            INSERT INTO malay_slug_cache (imdb_id, canonical_title, canonical_year, media_type, slug, is_resolved)
                            VALUES (?, ?, ?, ?, ?, 1)
                            ON CONFLICT(slug) DO UPDATE SET
                                imdb_id = excluded.imdb_id,
                                canonical_title = excluded.canonical_title,
                                canonical_year = excluded.canonical_year,
                                media_type = excluded.media_type,
                                is_resolved = 1;
                        """, batch_cache)
                        cache_conn.commit()
                        batch_cache.clear()

                    progress.update(task, advance=1)

            if batch_cache:
                cache_cur.executemany("""
                    INSERT INTO malay_slug_cache (imdb_id, canonical_title, canonical_year, media_type, slug, is_resolved)
                    VALUES (?, ?, ?, ?, ?, 1)
                    ON CONFLICT(slug) DO UPDATE SET
                        imdb_id = excluded.imdb_id,
                        canonical_title = excluded.canonical_title,
                        canonical_year = excluded.canonical_year,
                        media_type = excluded.media_type,
                        is_resolved = 1;
                """, batch_cache)
                cache_conn.commit()

    cache_conn.close()
    console.print("   [bold green]✔ Seluruh slug unik selesai divalidasi ke dalam cache persisten V2![/bold green]\n")

    # 3. Pemetaan Cepat O(1) ke File Database Katalog Baru (< 25MB)
    console.print("📦 [cyan]Memetakan seluruh baris subtitel fisik ke katalog database produksi (< 25MB)...[/cyan]")

    part_idx = 1
    current_part_count = 0
    total_saved = 0
    batch_records = []

    def get_catalog_path(idx: int) -> Path:
        return DATA_DIR / f"{OUTPUT_PREFIX}_part_{idx:02d}.db"

    curr_path = get_catalog_path(part_idx)
    target_conn = init_catalog_db(curr_path)
    target_cur = target_conn.cursor()

    summary_parts = []

    for src_db in SOURCE_MS_DBS:
        console.print(f"   ├─ Memproses: [yellow]{src_db.name}[/yellow]...")
        conn = sqlite3.connect(str(src_db))
        cur = conn.cursor()
        cur.execute("""
            SELECT language_code, slug, package_name, sub_filename, sub_extension,
                   internal_sub_path, sub_size_bytes, subscene_id
            FROM archive_subtitles_v2;
        """)

        for row in cur:
            lang, slug, pkg_fn, sub_fn, sub_ext, int_path, sz_bytes, sub_id = row
            info = resolved_cache.get(slug, {})

            imdb_id = info.get("imdb_id")
            canonical_title = info.get("canonical_title") or slug.replace("-", " ").title()
            canonical_year = info.get("canonical_year")
            
            # Parsing episode spesifik per file
            season, episode, sub_type = parse_episode_and_season_early(sub_fn, slug)
            media_type = info.get("media_type") or sub_type

            if season or episode:
                media_type = "series"

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

            batch_records.append((
                imdb_id,
                stremio_id,
                canonical_title,
                canonical_year,
                media_type,
                season,
                episode,
                lang,
                slug,
                pkg_fn,
                sub_fn,
                sub_ext,
                int_path,
                sz_bytes,
                sub_id
            ))

            total_saved += 1
            current_part_count += 1

            if len(batch_records) >= 5000:
                target_cur.executemany("""
                    INSERT INTO subtitle_catalog 
                    (imdb_id, stremio_id, canonical_title, release_year, media_type, season, episode,
                     language_code, slug, package_name, sub_filename, sub_extension, internal_sub_path,
                     sub_size_bytes, subscene_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, batch_records)
                target_conn.commit()
                batch_records.clear()

            # Partisi database saat mencapai batas baris
            if current_part_count >= MAX_ROWS_PER_DB:
                finalize_db(target_conn, curr_path, current_part_count)
                summary_parts.append({
                    "file": curr_path.name,
                    "rows": current_part_count,
                    "size_mb": curr_path.stat().st_size / (1024 * 1024)
                })

                part_idx += 1
                current_part_count = 0
                curr_path = get_catalog_path(part_idx)
                target_conn = init_catalog_db(curr_path)
                target_cur = target_conn.cursor()

        conn.close()

    if batch_records:
        target_cur.executemany("""
            INSERT INTO subtitle_catalog 
            (imdb_id, stremio_id, canonical_title, release_year, media_type, season, episode,
             language_code, slug, package_name, sub_filename, sub_extension, internal_sub_path,
             sub_size_bytes, subscene_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, batch_records)
        target_conn.commit()
        batch_records.clear()

    finalize_db(target_conn, curr_path, current_part_count)
    summary_parts.append({
        "file": curr_path.name,
        "rows": current_part_count,
        "size_mb": curr_path.stat().st_size / (1024 * 1024)
    })

    # Tampilkan Ringkasan Partisi Database
    table = Table(title="📋 Ringkasan File Database Katalog Akhir (Sedia GitHub)", border_style="cyan")
    table.add_column("Nama File Database", style="yellow")
    table.add_column("Jumlah Baris", justify="right", style="green")
    table.add_column("Ukuran File", justify="right", style="magenta")

    for p in summary_parts:
        table.add_row(p["file"], f"{p['rows']:,}", f"{p['size_mb']:.2f} MB")

    console.print("\n", table)
    console.print(f"[bold green]✨ SELESAI! Sebanyak {total_saved:,} subtitel berhasil dipetakan secara presisi di {DATA_DIR}![/bold green]\n")

if __name__ == "__main__":
    main()