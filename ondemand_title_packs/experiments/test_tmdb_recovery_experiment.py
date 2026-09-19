import os
import re
import sys
import json
import sqlite3
import urllib.request
import urllib.parse
from pathlib import Path
from difflib import SequenceMatcher
from typing import Optional, Dict, Any, Tuple, List
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import dotenv_values
from guessit import guessit
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

CACHE_DB_PATH = TEMP_DIR / "malay_slug_imdb_cache.db"
DETAILED_MS_DB = DATA_DIR / "archive_detailed_map_ms_part_01.db"
OUTPUT_JSON_PATH = TEMP_DIR / "sample_tmdb_recovery_results.json"

SAMPLE_FAIL_COUNT = 500
WORKER_THREADS = 10
MAX_ALLOWED_YEAR = 2024

env_vars = dotenv_values(str(ENV_PATH)) if ENV_PATH.exists() else {}
TMDB_API_KEY = env_vars.get("TMDB_API_KEY") or os.getenv("TMDB_API_KEY")
TMDB_READ_TOKEN = env_vars.get("TMDB_READ_TOKEN") or os.getenv("TMDB_READ_TOKEN")

if not TMDB_API_KEY and not TMDB_READ_TOKEN:
    console.print(f"[bold red]❌ Ralat: Kunci TMDB tidak dijumpai di {ENV_PATH}![/bold red]")
    sys.exit(1)

SEASON_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10
}

STOP_WORDS = {
    "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for", 
    "with", "by", "tv", "season", "series"
}

JUNK_PATTERNS = [
    r"\blive with\b", r"\baftershow\b", r"\bbehind the scenes\b", 
    r"\bpodcast\b", r"\baudiobook\b", r"\bsoundtrack\b", r"\bmaking of\b"
]

# ==============================================================================
# PEMBERSIHAN KATA KUNCI & PENAPIS BEBAS RALAT
# ==============================================================================
def sanitize_chars(text: str) -> str:
    text = text.replace("-", " ").replace("_", " ").replace(".", " ")
    text = re.sub(r"[^\w\s\.]", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def extract_sequel_tag(text: str) -> Optional[str]:
    t = text.lower()
    m = re.search(r"\b(?:vol|volume|part|chapter|pt)?\s*(\d+|ii|iii|iv|v)\b", t)
    if m:
        val = m.group(1)
        roman_map = {"ii": "2", "iii": "3", "iv": "4", "v": "5"}
        return roman_map.get(val, val)
    return None

def normalize_tokens(text: str) -> List[str]:
    clean = re.sub(r"[^\w\s]", " ", text.lower())
    return [w for w in clean.split() if w and w not in STOP_WORDS]

def calculate_strict_similarity(query: str, target: str) -> float:
    q_tokens = normalize_tokens(query)
    t_tokens = normalize_tokens(target)
    if not q_tokens or not t_tokens:
        return 0.0

    q_set, t_set = set(q_tokens), set(t_tokens)
    intersection = q_set.intersection(t_set)
    union = q_set.union(t_set)

    jaccard = len(intersection) / len(union)
    seq_ratio = SequenceMatcher(None, query.lower(), target.lower()).ratio()
    coverage = len(intersection) / len(q_set)
    return round((jaccard * 0.4) + (seq_ratio * 0.3) + (coverage * 0.3), 2)

def build_safe_queries(slug: str, sample_sub_fn: str) -> Tuple[List[str], Optional[int], str, Optional[str]]:
    """Membina query yang selamat tanpa memotong tajuk menjadi terlalu pendek."""
    # 1. Kesan Tahun dari nama fail atau slug
    detected_year = None
    sub_clean = sample_sub_fn.replace(".", " ").replace("_", " ").replace("-", " ")
    y_sub = re.findall(r"\b(19\d\d|20\d\d)\b", sub_clean)
    if y_sub:
        valid_y = [int(y) for y in y_sub if 1920 <= int(y) <= MAX_ALLOWED_YEAR]
        if valid_y:
            detected_year = valid_y[-1]

    if not detected_year:
        y_slug = re.search(r"\b(19\d\d|20\d\d)\b", slug.replace("_", " ").replace("-", " "))
        if y_slug:
            detected_year = int(y_slug.group(1))

    # 2. Kesan Jenis Media
    slug_lower = slug.lower()
    is_series = bool(re.search(r"[-_ ]*(?:first|second|third|fourth|fifth|\d+)[-_ ]*season\b", slug_lower))
    if not is_series and re.search(r"\b[sS]\d+[-_ ]*[eE]\d+\b", sample_sub_fn):
        is_series = True
    media_type = "series" if is_series else "movie"

    # 3. Bentuk Query Bersih
    queries = []

    # Query A: Dari Guessit (Hanya ambil jika nama fail bukan format nombor pendek)
    if sample_sub_fn and not re.match(r"^(?:ep?\d+|\d+)\.(?:srt|ass)$", sample_sub_fn, re.I):
        try:
            g = guessit(sample_sub_fn)
            t = g.get("title")
            if t and len(str(t)) >= 3:
                clean_t = sanitize_chars(str(t))
                # Jangan masukkan query jika hanya perkataan umum
                if clean_t.lower() not in ["skin", "recap", "episode", "part"]:
                    queries.append(clean_t)
        except Exception:
            pass

    # Query B: Dari Slug Asal
    primary = slug.split("--")[0]
    primary = re.split(r"[-_]aka[-_]", primary, flags=re.IGNORECASE)[0]
    primary = re.sub(r"(?<=\d{4})[-_]\d+$", "", primary)
    primary = re.sub(r"[-_]tv$", "", primary, flags=re.IGNORECASE)
    primary = re.sub(r"[-_ ]*(?:first|second|third|fourth|fifth|\d+)[-_ ]*season\b", " ", primary, flags=re.IGNORECASE)
    primary = re.sub(r"\b(19\d\d|20\d\d)\b", " ", primary)
    clean_slug = sanitize_chars(primary)

    if clean_slug and clean_slug not in queries:
        queries.append(clean_slug)

    # Query C: Potongan Terkawal (Hanya potong jika lebih daripada 5 perkataan dan kekalkan min 4 perkataan)
    words = clean_slug.split()
    if len(words) > 5:
        safe_short = " ".join(words[:4])
        if safe_short not in queries:
            queries.append(safe_short)

    sequel_tag = extract_sequel_tag(clean_slug)
    return queries, detected_year, media_type, sequel_tag

# ==============================================================================
# FUNGSI PANGGILAN API (TMDB + CINEMETA)
# ==============================================================================
def query_tmdb_multi(query: str) -> List[Dict[str, Any]]:
    if not query or len(query) < 2:
        return []
    encoded = urllib.parse.quote(query)
    url = f"https://api.themoviedb.org/3/search/multi?query={encoded}&include_adult=false&language=en-US&page=1"
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
    target_type = "tv" if media_type == "tv" else "movie"
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

def query_cinemeta_fallback(query: str, media_type: str) -> List[Dict[str, Any]]:
    if not query or len(query) < 2:
        return []
    target_type = "series" if media_type == "series" else "movie"
    encoded = urllib.parse.quote(query)
    url = f"https://v3-cinemeta.strem.io/catalog/{target_type}/top/search={encoded}.json"
    headers = {"User-Agent": "Mozilla/5.0 (StremioRecoveryV5/1.0)"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("metas", [])
    except Exception:
        pass
    return []

# ==============================================================================
# ALUR KERJA PENYELESAIAN (TMDB -> CINEMETA)
# ==============================================================================
def resolve_sample_item(item: Dict[str, Any]) -> Dict[str, Any]:
    slug = item["slug"]
    sub_fn = item.get("sub_filename") or ""

    queries, year, m_type, sequel_tag = build_safe_queries(slug, sub_fn)

    best_match = None
    engine_used = None
    used_query = None
    best_score = 0.0

    # 1. CUBA TMDB TERLEBIH DAHULU
    for q in queries:
        tmdb_results = query_tmdb_multi(q)
        for res in tmdb_results[:6]:
            res_type = res.get("media_type")
            if res_type not in ["movie", "tv"]:
                continue

            name = res.get("title") or res.get("name") or ""
            date_str = res.get("release_date") or res.get("first_air_date") or ""
            cand_year = int(date_str[:4]) if len(date_str) >= 4 and date_str[:4].isdigit() else None

            # KEKANGAN KERAS TAHUN
            if year and cand_year and abs(cand_year - year) > 1:
                continue
            if not year and cand_year and cand_year > MAX_ALLOWED_YEAR:
                continue

            # KEKANGAN SEKUEL
            cand_seq = extract_sequel_tag(name)
            if sequel_tag and cand_seq != sequel_tag:
                continue
            if not sequel_tag and cand_seq in ["2", "3", "4", "5"]:
                continue

            sim = calculate_strict_similarity(q, name)
            if year and cand_year and abs(cand_year - year) <= 1:
                if sim >= 0.70 and sim > best_score:
                    best_match = res
                    best_score = sim
                    engine_used = "TMDB"
                    used_query = q
            elif not year:
                if sim >= 0.85 and sim > best_score:
                    best_match = res
                    best_score = sim
                    engine_used = "TMDB"
                    used_query = q

        if best_match:
            break

    # 2. JIKA TMDB TIADA, CUBA CINEMETA FALLBACK
    if not best_match:
        for q in queries:
            cine_results = query_cinemeta_fallback(q, m_type)
            for cand in cine_results[:6]:
                name = cand.get("name", "")
                if any(re.search(p, name, re.IGNORECASE) for p in JUNK_PATTERNS):
                    continue

                cand_year_raw = cand.get("releaseInfo") or cand.get("year")
                cand_year = int(str(cand_year_raw)[:4]) if cand_year_raw and str(cand_year_raw)[:4].isdigit() else None

                if year and cand_year and abs(cand_year - year) > 1:
                    continue
                if not year and cand_year and cand_year > MAX_ALLOWED_YEAR:
                    continue

                cand_seq = extract_sequel_tag(name)
                if sequel_tag and cand_seq != sequel_tag:
                    continue
                if not sequel_tag and cand_seq in ["2", "3", "4", "5"]:
                    continue

                sim = calculate_strict_similarity(q, name)
                if year and cand_year and abs(cand_year - year) <= 1:
                    if sim >= 0.70 and sim > best_score:
                        best_match = cand
                        best_score = sim
                        engine_used = "CINEMETA"
                        used_query = q
                elif not year:
                    if sim >= 0.85 and sim > best_score:
                        best_match = cand
                        best_score = sim
                        engine_used = "CINEMETA"
                        used_query = q

            if best_match:
                break

    # 3. EKSTRAK MAKLUMAT AKHIR
    imdb_id = None
    canonical_title = None
    release_year = None
    final_type = m_type

    if best_match:
        if engine_used == "TMDB":
            canonical_title = best_match.get("title") or best_match.get("name")
            date_str = best_match.get("release_date") or best_match.get("first_air_date") or ""
            release_year = date_str[:4] if len(date_str) >= 4 else None
            final_type = "series" if best_match.get("media_type") == "tv" else "movie"
            imdb_id = fetch_tmdb_external_imdb(best_match.get("media_type", "movie"), best_match["id"])
        else:
            canonical_title = best_match.get("name")
            release_year = str(best_match.get("releaseInfo") or best_match.get("year") or "")
            final_type = best_match.get("type", m_type)
            imdb_id = best_match.get("imdb_id")

    return {
        "slug": slug,
        "sub_filename": sub_fn,
        "queries_used": queries,
        "matched_query": used_query,
        "engine": engine_used,
        "similarity_score": best_score,
        "recovered": bool(imdb_id),
        "imdb_id": imdb_id,
        "canonical_title": canonical_title,
        "release_year": release_year,
        "media_type": final_type
    }

# ==============================================================================
# PENGAMBILAN 500 SAMPEL RAWAK DARI DATA GAGAL
# ==============================================================================
def collect_random_null_samples() -> List[Dict[str, Any]]:
    if not CACHE_DB_PATH.exists():
        console.print(f"[bold red]❌ Fail cache tidak wujud di: {CACHE_DB_PATH}[/bold red]")
        sys.exit(1)

    conn_c = sqlite3.connect(str(CACHE_DB_PATH))
    cur_c = conn_c.cursor()
    cur_c.execute("SELECT slug FROM malay_slug_cache WHERE imdb_id IS NULL ORDER BY RANDOM() LIMIT ?;", (SAMPLE_FAIL_COUNT * 2,))
    all_null_slugs = [r[0] for r in cur_c.fetchall()]
    conn_c.close()

    conn_d = sqlite3.connect(str(DETAILED_MS_DB))
    cur_d = conn_d.cursor()
    
    samples = []
    for slug in all_null_slugs:
        cur_d.execute("SELECT sub_filename FROM archive_subtitles_v2 WHERE slug = ? LIMIT 1;", (slug,))
        row = cur_d.fetchone()
        sub_fn = row[0] if row else ""
        samples.append({"slug": slug, "sub_filename": sub_fn})
        if len(samples) >= SAMPLE_FAIL_COUNT:
            break

    conn_d.close()
    return samples

# ==============================================================================
# ALUR KERJA UTAMA
# ==============================================================================
def main():
    console.print("\n" + "=" * 80)
    console.print("🧪 [bold green]UJIKAJI PEMULIHAN 500 SAMPEL (TMDB + CINEMETA FALLBACK KETAT)[/bold green]")
    console.print("=" * 80 + "\n")

    console.print(f"📥 Mengambil {SAMPLE_FAIL_COUNT} sampel slug NULL secara rawak...")
    samples = collect_random_null_samples()
    console.print(f"   [green]✔ {len(samples)} sampel gagal sedia untuk diuji.[/green]\n")

    results = []
    rec_count = 0
    engine_stats = {"TMDB": 0, "CINEMETA": 0}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[bold yellow]{task.completed}/{task.total} Judul"),
        TimeElapsedColumn(),
        console=console
    ) as progress:
        task = progress.add_task("Menyelaras TMDB & Cinemeta...", total=len(samples))

        with ThreadPoolExecutor(max_workers=WORKER_THREADS) as executor:
            future_to_item = {executor.submit(resolve_sample_item, s): s for s in samples}
            for future in as_completed(future_to_item):
                res = future.result()
                results.append(res)
                if res["recovered"]:
                    rec_count += 1
                    eng = res.get("engine")
                    if eng in engine_stats:
                        engine_stats[eng] += 1
                progress.update(task, advance=1)

    with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    file_size_kb = OUTPUT_JSON_PATH.stat().st_size / 1024
    console.print(f"\n[bold green]💾 Hasil ujian disimpan ke:[/bold green] [yellow]{OUTPUT_JSON_PATH}[/yellow] ([cyan]{file_size_kb:.2f} KB[/cyan])")

    rec_pct = (rec_count / len(samples)) * 100 if samples else 0
    table = Table(title="📊 Statistik Pemulihan 500 Sampel Gagal", border_style="cyan")
    table.add_column("Kategori", style="yellow")
    table.add_column("Jumlah Rekod", justify="right", style="green")
    table.add_column("Peratusan", justify="right", style="magenta")

    table.add_row("Berjaya Dipulihkan (Dapat IMDb ID)", f"{rec_count:,}", f"{rec_pct:.1f}%")
    table.add_row("  ├─ Diselamatkan oleh TMDB", f"{engine_stats['TMDB']:,}", f"{(engine_stats['TMDB']/len(samples))*100:.1f}%")
    table.add_row("  └─ Diselamatkan oleh Cinemeta", f"{engine_stats['CINEMETA']:,}", f"{(engine_stats['CINEMETA']/len(samples))*100:.1f}%")
    table.add_row("Kekal Tidak Ditemui (NOT_FOUND)", f"{len(samples) - rec_count:,}", f"{100 - rec_pct:.1f}%")
    table.add_section()
    table.add_row("JUMLAH SAMPEL DIUJI", f"{len(samples):,}", "100.0%")
    console.print(table)

    preview = Table(title="🔍 Contoh 12 Judul Gagal yang Berjaya Dipulihkan", border_style="green")
    preview.add_column("Slug Asal", style="dim", max_width=28, overflow="fold")
    preview.add_column("Hasil Tajuk", style="cyan")
    preview.add_column("Enjin", justify="center", style="bold yellow")
    preview.add_column("IMDb ID", style="bold green")
    preview.add_column("Tahun", justify="center", style="magenta")

    for r in [x for x in results if x["recovered"]][:12]:
        preview.add_row(
            r["slug"],
            r["canonical_title"],
            r["engine"],
            r["imdb_id"],
            str(r["release_year"] or "-")
        )

    console.print(preview)
    console.print("\n[bold yellow]👉 Jalankan skrip ini dan kongsikan fail JSON untuk kita semak ketiadaan ralat sebelum bina skrip v2 penuh.[/bold yellow]\n")

if __name__ == "__main__":
    main()