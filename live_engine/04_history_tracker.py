import json
import time
import sqlite3
import importlib
from pathlib import Path
from typing import Dict, List, Optional, Any

# Import dinamik modul 00_config
_config = importlib.import_module("00_config")
DATA_DIR = _config.DATA_DIR

# [PERUBAHAN FUNGSI]: Menghalakan laluan data ke SQLite stremio_cache.db
DB_FILE = DATA_DIR / "stremio_cache.db"
LEGACY_HISTORY_FILE = DATA_DIR / "scraped_history.json"

def _get_connection() -> sqlite3.Connection:
    """
    [PERUBAHAN FUNGSI]: Membuka sambungan SQLite dengan mod WAL dan timeout selamat
    bagi mengelakkan ralat 'database locked' semasa proses selari.
    """
    conn = sqlite3.connect(str(DB_FILE), timeout=15)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn

def _ensure_table():
    """Memastikan jadual scraped_history wujud sekiranya pangkalan data baru dimulakan."""
    try:
        with _get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scraped_history (
                    imdb_id TEXT PRIMARY KEY,
                    updated_at INTEGER,
                    sub_count INTEGER,
                    subtitles_json TEXT
                );
            """)
    except Exception as e:
        print(f"⚠️ Ralat inisialisasi jadual SQLite: {e}")

# Pastikan jadual tersedia
_ensure_table()

def _fallback_is_imdb_processed_json(imdb_id: str) -> bool:
    """Sandaran kecemasan: Menyemak fail JSON lama sekiranya berlaku isu SQLite."""
    if LEGACY_HISTORY_FILE.exists():
        try:
            with open(LEGACY_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                items = data.get("processed_ids", data) if isinstance(data, dict) else {}
                return imdb_id in items
        except Exception:
            pass
    return False

def is_imdb_processed(imdb_id: str) -> bool:
    """
    [PERUBAHAN FUNGSI]: Semakan pantas O(1) status IMDb ID terus melalui indeks SQLite.
    Jauh lebih laju dan tidak memuatkan keseluruhan data ke RAM berbanding JSON lama.
    """
    clean_id = str(imdb_id or "").strip()
    if not clean_id:
        return False

    try:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM scraped_history WHERE imdb_id = ? LIMIT 1;", (clean_id,))
            row = cursor.fetchone()
            return row is not None
    except Exception as e:
        print(f"⚠️ Ralat semakan SQLite is_imdb_processed ({clean_id}): {e}. Beralih ke JSON...")
        return _fallback_is_imdb_processed_json(clean_id)

def add_processed_imdb(imdb_id: str, sub_records: list) -> bool:
    """
    [PERUBAHAN FUNGSI]: Menyimpan atau mengemas kini rekod sejarah IMDb ID ke SQLite
    menggunakan transaksi 'INSERT OR REPLACE' tanpa perlu menulis semula fail besar.
    """
    clean_id = str(imdb_id or "").strip()
    if not clean_id:
        return False

    now_ts = int(time.time())
    sub_count = len(sub_records) if isinstance(sub_records, list) else 0
    subtitles_json_str = json.dumps(sub_records if sub_records else [], ensure_ascii=False)

    try:
        with _get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO scraped_history (imdb_id, updated_at, sub_count, subtitles_json)
                VALUES (?, ?, ?, ?);
            """, (clean_id, now_ts, sub_count, subtitles_json_str))
        return True
    except Exception as e:
        print(f"❌ Ralat menyimpan SQLite add_processed_imdb ({clean_id}): {e}")
        return False

def load_history() -> dict:
    """
    [PERUBAHAN FUNGSI]: Membaca rekod sejarah daripada SQLite dan mengembalikan format kamus asal
    {'processed_ids': ..., 'total_items': ...} demi menjamin keserasian belakang penuh modul lain.
    """
    try:
        with _get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT imdb_id, updated_at, sub_count, subtitles_json FROM scraped_history;")
            rows = cursor.fetchall()

            processed = {}
            for row in rows:
                iid, updated_at, sub_count, subs_raw = row
                try:
                    subs = json.loads(subs_raw) if subs_raw else []
                except Exception:
                    subs = []
                processed[iid] = {
                    "updated_at": updated_at,
                    "sub_count": sub_count,
                    "subtitles": subs
                }
            return {
                "processed_ids": processed,
                "total_items": len(processed)
            }
    except Exception as e:
        print(f"⚠️ Ralat membaca SQLite load_history: {e}. Menggunakan fallback JSON...")
        if LEGACY_HISTORY_FILE.exists():
            try:
                with open(LEGACY_HISTORY_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"processed_ids": {}, "total_items": 0}

def save_history(data: dict) -> bool:
    """
    [PERUBAHAN FUNGSI]: Menyimpan senarai kelompok sejarah ke SQLite (Kekal disokong untuk keserasian).
    """
    items = data.get("processed_ids", data) if isinstance(data, dict) else {}
    if not isinstance(items, dict):
        return False

    records = []
    for iid, item in items.items():
        if isinstance(item, dict):
            updated_at = int(item.get("updated_at", time.time()))
            subs = item.get("subtitles", [])
            sub_count = int(item.get("sub_count", len(subs)))
            subs_json = json.dumps(subs, ensure_ascii=False)
            records.append((str(iid).strip(), updated_at, sub_count, subs_json))

    if not records:
        return True

    try:
        with _get_connection() as conn:
            conn.executemany("""
                INSERT OR REPLACE INTO scraped_history (imdb_id, updated_at, sub_count, subtitles_json)
                VALUES (?, ?, ?, ?);
            """, records)
        return True
    except Exception as e:
        print(f"❌ Ralat menyimpan SQLite save_history: {e}")
        return False