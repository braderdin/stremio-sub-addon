import os
import re
import time
import json
import sqlite3
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Optional, Tuple, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==============================================================================
# KONFIGURASI DIREKTORI & PARAMETER
# ==============================================================================
BASE_WORK_DIR = Path("/home/braderdin/stremio-sub-addon/SUBTITLE--SUBSCENE-ARCHIVE")
OUTPUT_DIR = BASE_WORK_DIR / "output"
DATA_DIR = BASE_WORK_DIR / "data"
CACHE_DB_PATH = DATA_DIR / "slug_imdb_cache.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)

MAX_ROWS_PER_DB = 50000  # ~20MB - 30MB setiap fail .db
WORKER_THREADS = 25      # Kelajuan carian serentak pantas

SEASON_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10
}

# ==============================================================================
# PENGURUSAN CACHE SLUG & IMDB (SQLITE PERSISTENT)
# ==============================================================================
def init_slug_cache_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(CACHE_DB_PATH))
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS slug_cache (
            slug TEXT PRIMARY KEY,
            clean_title TEXT,
            year INTEGER,
            media_type TEXT,
            imdb_id TEXT,
            canonical_title TEXT,
            is_resolved INTEGER DEFAULT 0
        );
    """)
    conn.commit()
    return conn

def clean_slug(slug: str) -> Tuple[str, Optional[int], Optional[int], str]:
    clean = slug.replace("_", " ").strip()
    
    # Kesan tahun 4 digit
    year_match = re.search(r"\b(19\d\d|20\d\d)\b", clean)
    detected_year = int(year_match.group(1)) if year_match else None
    
    # Kesan musim (Series)
    season_match = re.search(r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+)(?:st|nd|rd|th)?[- ]season\b", clean, re.IGNORECASE)
    detected_season = None
    if season_match:
        val = season_match.group(1).lower()
        detected_season = SEASON_WORDS.get(val, int(val) if val.isdigit() else None)

    media_type = "series" if detected_season else "movie"

    # Bersihkan nama untuk query Cinemeta
    query_title = re.sub(r"[-_]+", " ", slug)
    query_title = re.sub(r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+)(?:st|nd|rd|th)?[- ]season\b", "", query_title, flags=re.IGNORECASE)
    query_title = re.sub(r"\b(19\d\d|20\d\d)\b", "", query_title)
    query_title = re.sub(r"\s+", " ", query_title).strip()

    return query_title, detected_year, detected_season, media_type

# ==============================================================================
# CARIAN CINEMETA DENGAN MULTITHREADING
# ==============================================================================
def fetch_cinemeta_worker(item: dict) -> dict:
    slug = item["slug"]
    q_title = item["clean_title"]
    year = item["year"]
    m_type = item["media_type"]

    if not q_title or len(q_title) < 2:
        return {"slug": slug, "imdb_id": None, "canonical_title": None}

    encoded = urllib.parse.quote(q_title)
    url = f"https://v3-cinemeta.strem.io/catalog/{m_type}/top/search={encoded}.json"
    headers = {"User-Agent": "Mozilla/5.0 (StremioSubArchiveFast/2.0)"}

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                metas = data.get("metas", [])
                
                # Cuba movie jika carian series kosong
                if not metas and m_type == "series":
                    url_m = f"https://v3-cinemeta.strem.io/catalog/movie/top/search={encoded}.json"
                    req_m = urllib.request.Request(url_m, headers=headers)
                    with urllib.request.urlopen(req_m, timeout=3.5) as resp_m:
                        if resp_m.status == 200:
                            metas = json.loads(resp_m.read().decode("utf-8")).get("metas", [])

                for m in metas:
                    item_year = m.get("releaseInfo") or m.get("year")
                    if year and item_year:
                        try:
                            if abs(int(str(item_year)[:4]) - year) <= 1:
                                return {"slug": slug, "imdb_id": m.get("imdb_id"), "canonical_title": m.get("name")}
                        except Exception:
                            pass

                if metas:
                    return {"slug": slug, "imdb_id": metas[0].get("imdb_id"), "canonical_title": metas[0].get("name")}
    except Exception:
        pass

    return {"slug": slug, "imdb_id": None, "canonical_title": None}

# ==============================================================================
# PANGKALAN DATA SASARAN (METADATA AUTO-SPLIT)
# ==============================================================================
def init_metadata_part_db(db_path: Path) -> sqlite3.Connection:
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS subtitle_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            imdb_id TEXT,
            canonical_title TEXT,
            slug TEXT NOT NULL,
            clean_title TEXT NOT NULL,
            media_type TEXT NOT NULL,
            year INTEGER,
            season INTEGER,
            episode INTEGER,
            language TEXT NOT NULL,
            subscene_id TEXT,
            filename TEXT NOT NULL,
            extension TEXT NOT NULL,
            archive_path TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            is_archive INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_imdb ON subtitle_metadata (imdb_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_slug ON subtitle_metadata (slug);")
    conn.commit()
    return conn

# ==============================================================================
# ALUR KERJA UTAMA
# ==============================================================================
def main():
    print("=" * 75)
    print("🚀 PENJANAAN PANTAS METADATA SUBTITLE (MULTITHREADING + SQLITE SPLIT)")
    print("=" * 75)

    source_dbs = sorted(list(OUTPUT_DIR.glob("*.db")))
    if not source_dbs:
        print(f"❌ Tiada fail pangkalan data sumber di {OUTPUT_DIR}.")
        return

    cache_conn = init_slug_cache_db()
    cache_cur = cache_conn.cursor()

    # 1. Kumpulkan slug unik daripada 6 fail .db
    print("\n🔍 FASA 1: Mengumpul semua tajuk (slug) unik daripada arkib...")
    all_unique_slugs = set()
    for s_db in source_dbs:
        conn = sqlite3.connect(str(s_db))
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT slug FROM archive_contents;")
        rows = [r[0] for r in cur.fetchall() if r[0]]
        all_unique_slugs.update(rows)
        conn.close()

    print(f"   🎯 Ditemui: {len(all_unique_slugs):,} tajuk (slug) unik bagi 274,000+ fail sarikata.")

    # 2. Masukkan slug baharu ke dalam slug_cache
    for slug in all_unique_slugs:
        q_title, year, season, m_type = clean_slug(slug)
        cache_cur.execute("""
            INSERT OR IGNORE INTO slug_cache (slug, clean_title, year, media_type, is_resolved)
            VALUES (?, ?, ?, ?, 0);
        """, (slug, q_title, year, m_type))
    cache_conn.commit()

    # Dapatkan senarai slug yang belum dicari
    cache_cur.execute("SELECT slug, clean_title, year, media_type FROM slug_cache WHERE is_resolved = 0;")
    unresolved_rows = [
        {"slug": r[0], "clean_title": r[1], "year": r[2], "media_type": r[3]}
        for r in cache_cur.fetchall()
    ]

    print(f"\n⚡ FASA 2: Carian IMDb serentak di Cinemeta ({WORKER_THREADS} Threads)...")
    print(f"   ├─ Jumlah tajuk perlu disemak: {len(unresolved_rows):,}")

    if unresolved_rows:
        done_count = 0
        batch_updates = []
        with ThreadPoolExecutor(max_workers=WORKER_THREADS) as executor:
            future_to_item = {executor.submit(fetch_cinemeta_worker, item): item for item in unresolved_rows}
            for future in as_completed(future_to_item):
                res = future.result()
                batch_updates.append((res["imdb_id"], res["canonical_title"], res["slug"]))
                done_count += 1

                if len(batch_updates) >= 500:
                    cache_cur.executemany("""
                        UPDATE slug_cache 
                        SET imdb_id = ?, canonical_title = ?, is_resolved = 1 
                        WHERE slug = ?;
                    """, batch_updates)
                    cache_conn.commit()
                    batch_updates.clear()
                    print(f"   ├─ Kemajuan carian: {done_count:,} / {len(unresolved_rows):,} tajuk selesai...")

        if batch_updates:
            cache_cur.executemany("""
                UPDATE slug_cache 
                SET imdb_id = ?, canonical_title = ?, is_resolved = 1 
                WHERE slug = ?;
            """, batch_updates)
            cache_conn.commit()

    print("   ✅ Semua carian IMDb unik telah siap disimpan ke pangkalan data cache!")

    # Muatkan semua slug_cache ke RAM untuk pemetaan sepantas kilat O(1)
    cache_cur.execute("SELECT slug, clean_title, year, media_type, imdb_id, canonical_title FROM slug_cache;")
    slug_lookup = {
        r[0]: {"clean_title": r[1], "year": r[2], "media_type": r[3], "imdb_id": r[4], "canonical_title": r[5]}
        for r in cache_cur.fetchall()
    }
    cache_conn.close()

    # 3. Fasa Pemetaan & Penjanaan Fail Part .db
    print("\n📦 FASA 3: Memetakan 274,000+ fail sarikata ke fail .db berasingan (20MB-30MB)...")
    
    part_idx = 1
    current_count = 0
    total_saved = 0
    batch_records = []
    
    def get_part_path(idx: int) -> Path:
        return DATA_DIR / f"subtitles_metadata_part_{idx:02d}.db"

    curr_path = get_part_path(part_idx)
    target_conn = init_metadata_part_db(curr_path)
    target_cur = target_conn.cursor()

    for s_db in source_dbs:
        print(f"   ├─ Memproses arkib: {s_db.name}...")
        conn = sqlite3.connect(str(s_db))
        cur = conn.cursor()
        cur.execute("""
            SELECT language, slug, filename, extension, archive_path, size_bytes, subscene_id, is_archive
            FROM archive_contents;
        """)

        for row in cur:
            lang, slug, fn, ext, a_path, sz, sub_id, is_arch = row
            info = slug_lookup.get(slug, {})
            
            # Pengesanan episod ringkas daripada nama fail
            ep_match = re.search(r"(?:[sS](\d+))?[eE][pP]?\s*(\d+)|episode\s*(\d+)", fn, re.I)
            season = int(ep_match.group(1)) if (ep_match and ep_match.group(1)) else None
            episode = int(ep_match.group(2) or ep_match.group(3)) if ep_match else None

            batch_records.append((
                info.get("imdb_id"),
                info.get("canonical_title"),
                slug,
                info.get("clean_title", slug),
                info.get("media_type", "movie"),
                info.get("year"),
                season,
                episode,
                lang,
                sub_id,
                fn,
                ext,
                a_path,
                sz,
                is_arch
            ))

            total_saved += 1
            current_count += 1

            if len(batch_records) >= 5000:
                target_cur.executemany("""
                    INSERT INTO subtitle_metadata 
                    (imdb_id, canonical_title, slug, clean_title, media_type, year, season, episode,
                     language, subscene_id, filename, extension, archive_path, size_bytes, is_archive)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, batch_records)
                target_conn.commit()
                batch_records.clear()

            if current_count >= MAX_ROWS_PER_DB:
                target_cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                target_conn.close()
                print(f"   💾 Selesai Bahagian -> {curr_path.name} ({current_count:,} rekod | {curr_path.stat().st_size / (1024*1024):.2f} MB)")
                part_idx += 1
                current_count = 0
                curr_path = get_part_path(part_idx)
                target_conn = init_metadata_part_db(curr_path)
                target_cur = target_conn.cursor()

        conn.close()

    if batch_records:
        target_cur.executemany("""
            INSERT INTO subtitle_metadata 
            (imdb_id, canonical_title, slug, clean_title, media_type, year, season, episode,
             language, subscene_id, filename, extension, archive_path, size_bytes, is_archive)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, batch_records)
        target_conn.commit()

    target_cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    target_conn.close()
    print(f"   💾 Selesai Bahagian -> {curr_path.name} ({current_count:,} rekod | {curr_path.stat().st_size / (1024*1024):.2f} MB)")

    print("\n" + "=" * 75)
    print(f"✨ SELESAI PENUH! Sebanyak {total_saved:,} rekod berjaya dipetakan sepenuhnya!")
    print("=" * 75)

if __name__ == "__main__":
    main()