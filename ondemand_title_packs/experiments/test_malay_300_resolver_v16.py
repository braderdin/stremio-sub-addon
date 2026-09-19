#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO SUBTITLE CATALOG - RESOLVER TEST ENGINE V16 (SERIES-HARDENED)
# LOKASI: /home/braderdin/stremio-sub-addon/ondemand_title_packs/experiments/test_malay_300_resolver_v16.py
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
# KONFIGURASI DIREKTORI & PARAMETER
# ==============================================================================
BASE_DIR = Path("/home/braderdin/stremio-sub-addon/ondemand_title_packs")
DATA_DIR = BASE_DIR / "data"
TEMP_DIR = BASE_DIR / "temp"
ENV_PATH = Path("/home/braderdin/stremio-sub-addon/.env.local")

DB_PART_1 = DATA_DIR / "archive_detailed_map_ms_part_01.db"
DB_PART_2 = DATA_DIR / "archive_detailed_map_ms_part_02.db"

OUTPUT_JSON_PATH = TEMP_DIR / "sample_malay_v16_300_results.json"
OUTPUT_DB_PATH = TEMP_DIR / "sample_malay_v16_300_results.db"

TEMP_DIR.mkdir(parents=True, exist_ok=True)

SAMPLE_SIZE = 300
WORKER_THREADS = 10
MAX_ALLOWED_YEAR = 2025  # Menyekat entri 'placeholder' palsu masa hadapan

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
# PEMBERSIHAN PENANDA SUBSCENE & EKSTRAKSI TARIKH MELEKAT
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
    """Mengekstrak tarikh siaran Korea (YYMMDD), termasuk yang melekat di belakang kod episod."""
    if not text:
        return None
    # Menangani E18180822 (Episod 18, Tarikh 180822) atau 200725
    m = re.search(r"(?:[eE]\d{1,3})?([0-2]\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\b", text)
    if m:
        yy = int(m.group(1))
        # Julat siaran digital K-drama moden: 2005 hingga 2025
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
# PENGECAMAN AWAL EPISOD & TIPE MEDIA
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

    # Corak episod (termasuk kes melekat seperti E18180822)
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
# PEMBINAAN QUERY KANONIKAL
# ==============================================================================
def build_clean_query_candidates(slug: str, pkg: str, sub_fn: str, m_type: str) -> Tuple[List[str], Optional[int]]:
    detected_year = extract_kdrama_date_year(sub_fn)

    if not detected_year:
        y_matches = re.findall(r"\b(19\d\d|20\d\d)\b", f"{slug} {pkg} {sub_fn}")
        if y_matches:
            valid_y = [int(y) for y in y_matches if 1930 <= int(y) <= MAX_ALLOWED_YEAR]
            if valid_y:
                detected_year = valid_y[-1]

    # Bersihkan slug
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

    # Tajuk bersih nama fail
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
# ENJIN SKOR KETAT V16 (SERIES-HARDENED RULES)
# ==============================================================================
def verify_candidate_integrity_v16(query: str, cand_title: str, query_year: Optional[int],
                                   cand_year: Optional[int], media_type: str, cand_type: str,
                                   season_num: Optional[int], query_sequel: Optional[str]) -> Tuple[bool, float]:
    if not cand_title:
        return False, 0.0

    # 1. Padanan Kategori Media Wajib Sepadan
    if cand_type:
        c_type = "series" if cand_type in ["tv", "series"] else "movie"
        if media_type != c_type:
            return False, 0.0

    # 2. Had Tahun Masa Depan (Hapuskan Placeholder)
    if cand_year and cand_year > MAX_ALLOWED_YEAR:
        return False, 0.0

    # 3. Penapisan Tahun Siri & Filem
    if media_type == "movie":
        if query_year and cand_year:
            if abs(cand_year - query_year) > 1:
                return False, 0.0
        elif not query_year and cand_year:
            if cand_year < 1960:
                return False, 0.0
    else:
        # Peraturan Keras Siri:
        if query_year and cand_year:
            # Tahun mula siri tidak boleh berada di masa depan berbanding sarikata
            if cand_year > query_year + 1:
                return False, 0.0
            # Had jurang dinamik: siri awal (S1/S2) tidak boleh terpisah lebih 2 tahun
            s_num = season_num or 1
            max_allowed_gap = 2 if s_num <= 2 else (s_num + 3)
            if (query_year - cand_year) > max_allowed_gap:
                return False, 0.0
        elif not query_year:
            # Jika tiada rujukan tahun langsung, elak siri klasik < 2000 melainkan query sangat unik
            if cand_year and cand_year < 2000:
                return False, 0.0

    # 4. Kawalan Tag Sekuel
    cand_sequel = extract_sequel_tag(cand_title)
    if query_sequel and cand_sequel != query_sequel:
        return False, 0.0
    if not query_sequel and cand_sequel in ["2", "3", "4", "5", "6"]:
        return False, 0.0

    # 5. Penilaian Keserupaan RapidFuzz & Perlindungan Tajuk 1-2 Perkataan
    q_clean = query.lower()
    c_clean = cand_title.lower()
    words_count = len(q_clean.split())

    if words_count <= 2:
        # Tajuk generik pendek (cth: The Heirs, Voice, Your Honor, Ghost)
        sim = fuzz.ratio(q_clean, c_clean) / 100.0
        if sim < 0.88:
            return False, 0.0
        # Wajib ada tahun dan perbezaan maksimum 1 tahun
        if not query_year or not cand_year or abs(cand_year - query_year) > 1:
            return False, 0.0
        return True, sim

    sim = fuzz.token_sort_ratio(q_clean, c_clean) / 100.0
    min_threshold = 0.72 if (query_year and cand_year and abs(cand_year - query_year) <= 1) else 0.82

    return (sim >= min_threshold), sim

# ==============================================================================
# ENJIN HTTP
# ==============================================================================
def query_cinemeta(query_title: str, media_type: str) -> List[Dict[str, Any]]:
    if not query_title or len(query_title) < 2:
        return []
    encoded = urllib.parse.quote(query_title)
    url = f"https://v3-cinemeta.strem.io/catalog/{media_type}/top/search={encoded}.json"
    headers = {"User-Agent": "Mozilla/5.0 (StremioMalayEngine/V16)"}
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
# ALUR RESOLVER UTAMA
# ==============================================================================
def resolve_sample_item_v16(item: Dict[str, Any]) -> Dict[str, Any]:
    slug = item.get("slug", "")
    pkg = item.get("package_name", "") or ""
    sub_fn = item.get("sub_filename", "") or ""

    season, episode, media_type = parse_episode_and_season_early(sub_fn, slug)
    queries, year = build_clean_query_candidates(slug, pkg, sub_fn, media_type)
    sequel = extract_sequel_tag(slug)

    best_match = None
    best_score = 0.0
    engine_used = None
    used_query = None

    # TAHAP 1: Cinemeta
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
                    "title": cand_name,
                    "year": str(cand_year) if cand_year else "",
                    "media_type": cand_type
                }
                engine_used = "CINEMETA_CATALOG"
                used_query = q

        if best_match:
            break

    # TAHAP 2: TMDB Fallback
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
                            "title": res_name,
                            "year": str(cand_year) if cand_year else "",
                            "media_type": media_type
                        }
                        engine_used = "TMDB_FALLBACK"
                        used_query = q

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
        "slug": slug,
        "sub_filename": sub_fn,
        "engine": engine_used,
        "used_query": used_query,
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
# PENGAMBILAN 300 SAMPEL & SIMPAN
# ==============================================================================
def collect_random_samples() -> List[Dict[str, Any]]:
    samples = []
    half_size = SAMPLE_SIZE // 2

    for db_path, count in [(DB_PART_1, half_size), (DB_PART_2, SAMPLE_SIZE - half_size)]:
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT slug, package_name, sub_filename, internal_sub_path 
                FROM archive_subtitles_v2 
                ORDER BY RANDOM() LIMIT ?;
            """, (count,))
            for r in cur.fetchall():
                samples.append(dict(r))
            conn.close()
    return samples

def save_results_to_sqlite(results: List[Dict[str, Any]]):
    if OUTPUT_DB_PATH.exists():
        OUTPUT_DB_PATH.unlink()

    conn = sqlite3.connect(str(OUTPUT_DB_PATH))
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE sample_v16_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT,
            sub_filename TEXT,
            engine TEXT,
            used_query TEXT,
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
            r["slug"], r["sub_filename"], r["engine"], r["used_query"],
            1 if r["recovered"] else 0, r["imdb_id"], r["canonical_title"],
            r["release_year"], r["media_type"], r["season"], r["episode"], r["stremio_id"]
        )
        for r in results
    ]
    cur.executemany("""
        INSERT INTO sample_v16_results 
        (slug, sub_filename, engine, used_query, recovered, imdb_id, canonical_title,
         release_year, media_type, season, episode, stremio_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, records)
    conn.commit()
    conn.close()

def main():
    console.print("\n" + "=" * 80)
    console.print("🛡️  [bold green]UJIAN PENYELESAIAN 300 SAMPEL V16 (SERIES-HARDENED & ZERO-FALSE-POSITIVE)[/bold green]")
    console.print("=" * 80 + "\n")

    samples = collect_random_samples()
    if not samples:
        console.print("[bold red]❌ Tiada sampel berjaya dimuat turun dari database arkib.[/bold red]")
        sys.exit(1)

    console.print(f"📥 Memproses [cyan]{len(samples)}[/cyan] sampel rawak (Part 01 + Part 02).")
    console.print(f"⚙️  Pekerja serentak: [yellow]{WORKER_THREADS} Threads[/yellow]\n")

    results = []
    success_count = 0
    engine_stats = {"CINEMETA_CATALOG": 0, "TMDB_FALLBACK": 0}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[bold yellow]{task.completed}/{task.total} Judul"),
        TimeElapsedColumn(),
        console=console
    ) as progress:
        task = progress.add_task("Mengesahkan IMDb V16...", total=len(samples))

        with ThreadPoolExecutor(max_workers=WORKER_THREADS) as executor:
            future_to_item = {executor.submit(resolve_sample_item_v16, s): s for s in samples}
            for future in as_completed(future_to_item):
                res = future.result()
                results.append(res)
                if res["recovered"]:
                    success_count += 1
                    eng = res.get("engine")
                    if eng in engine_stats:
                        engine_stats[eng] += 1
                progress.update(task, advance=1)

    with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    save_results_to_sqlite(results)

    json_kb = OUTPUT_JSON_PATH.stat().st_size / 1024
    console.print(f"\n[bold green]💾 Fail JSON disimpan:[/bold green] [yellow]{OUTPUT_JSON_PATH}[/yellow] ({json_kb:.2f} KB)")

    acc_rate = (success_count / len(samples)) * 100
    table = Table(title="📊 Analisis Ketepatan Enjin V16", border_style="cyan")
    table.add_column("Status / Enjin", style="yellow")
    table.add_column("Jumlah Sampel", justify="right", style="green")
    table.add_column("Peratusan", justify="right", style="magenta")

    table.add_row("BERJAYA DIKESAN (IMDb Sah)", f"{success_count:,}", f"{acc_rate:.1f}%")
    table.add_row("  ├─ Cinemeta Catalog (Primary)", f"{engine_stats['CINEMETA_CATALOG']:,}", f"{(engine_stats['CINEMETA_CATALOG']/len(samples))*100:.1f}%")
    table.add_row("  └─ TMDB Fallback", f"{engine_stats['TMDB_FALLBACK']:,}", f"{(engine_stats['TMDB_FALLBACK']/len(samples))*100:.1f}%")
    table.add_row("GAGAL (NOT_FOUND)", f"{len(samples) - success_count:,}", f"{100 - acc_rate:.1f}%")
    console.print(table)

if __name__ == "__main__":
    main()