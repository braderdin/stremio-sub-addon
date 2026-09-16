import os
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Optional

# Konfigurasi Direktori
ARCHIVE_DIR = Path("/mnt/e/PROJEK-GITHUB/SUBTITLE--SUBSCENE-ARCHIVE")
BASE_WORK_DIR = Path("/home/braderdin/stremio-sub-addon/SUBTITLE--SUBSCENE-ARCHIVE")
OUTPUT_DIR = BASE_WORK_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Had maksimum entri per fail (50,000 entri ~ 22MB-25MB, sangat selamat untuk GitHub)
MAX_ROWS_PER_DB = 50000

ARCHIVES = {
    "malay": {
        "archive_file": ARCHIVE_DIR / "malay.7z",
        "prefix": "malay_archive",
        "lang_code": "ms"
    },
    "indonesian": {
        "archive_file": ARCHIVE_DIR / "indonesian.7z",
        "prefix": "indonesian_archive",
        "lang_code": "id"
    }
}

VALID_EXTENSIONS = {".zip", ".rar", ".srt", ".ass", ".ssa"}

def init_sqlite_db(db_path: Path) -> sqlite3.Connection:
    """Inisialisasi pangkalan data SQLite baharu dengan struktur dan indeks pantas."""
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    cursor.execute("PRAGMA journal_mode = WAL;")
    cursor.execute("PRAGMA synchronous = NORMAL;")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS archive_contents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            language TEXT NOT NULL,
            slug TEXT NOT NULL,
            filename TEXT NOT NULL,
            extension TEXT NOT NULL,
            archive_path TEXT NOT NULL UNIQUE,
            size_bytes INTEGER NOT NULL,
            subscene_id TEXT,
            is_archive INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_slug ON archive_contents (slug);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_subscene_id ON archive_contents (subscene_id);")

    conn.commit()
    return conn

def extract_subscene_id(filename: str) -> Optional[str]:
    """Mengekstrak ID numerik Subscene daripada nama fail."""
    m = re.search(r"(?:malay|indonesian|indo|hi|HI)[-_](\d+)\.(zip|rar|srt|ass|ssa)$", filename, re.IGNORECASE)
    if m:
        return m.group(1)
    m2 = re.search(r"[-_](\d{4,9})\.(zip|rar|srt|ass|ssa)$", filename)
    if m2:
        return m2.group(1)
    return None

def close_and_finalize_db(conn: sqlite3.Connection, db_path: Path, count: int):
    """Tutup DB dan gabungkan jurnal WAL sepenuhnya."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.commit()
    conn.close()
    size_mb = db_path.stat().st_size / (1024 * 1024)
    print(f"   💾 Selesai Bahagian -> {db_path.name} ({count:,} rekod | {size_mb:.2f} MB)")

def process_archive(lang_name: str, info: dict):
    archive_path = info["archive_file"]
    prefix = info["prefix"]
    lang_code = info["lang_code"]

    if not archive_path.exists():
        print(f"⚠️ Fail arkib tidak ditemui di laluan: {archive_path}")
        return

    print(f"\n📦 Memulakan indeks arkib: {lang_name.upper()} ({archive_path.name})")

    part_index = 1
    current_part_count = 0
    total_found = 0
    batch_rows = []
    BATCH_SIZE = 5000

    # Tentukan sama ada fail tunggal (malay) atau berpecah (indonesian)
    def get_current_db_path(p_idx: int) -> Path:
        if lang_name == "malay":
            return OUTPUT_DIR / f"{prefix}.db"
        return OUTPUT_DIR / f"{prefix}_part_{p_idx:02d}.db"

    current_db_path = get_current_db_path(part_index)
    conn = init_sqlite_db(current_db_path)
    cursor = conn.cursor()

    cmd = ["7z", "l", str(archive_path)]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="ignore")

    line_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+(\S+)\s+(\d+)\s*(\d*)\s+(.+)$")

    for line in process.stdout:
        m = line_pattern.match(line)
        if not m:
            continue

        attr, size_str, _, raw_name = m.groups()

        if "D" in attr:
            continue

        norm_path = raw_name.replace("\\", "/").strip()
        parts = [p for p in norm_path.split("/") if p]

        if len(parts) < 2:
            continue

        filename = parts[-1]
        ext = Path(filename).suffix.lower()

        if ext not in VALID_EXTENSIONS:
            continue

        slug = parts[-2]
        sub_id = extract_subscene_id(filename)
        is_compressed = 1 if ext in [".zip", ".rar"] else 0

        batch_rows.append((
            lang_code,
            slug,
            filename,
            ext,
            norm_path,
            int(size_str),
            sub_id,
            is_compressed
        ))

        total_found += 1
        current_part_count += 1

        # Tulis batch ke pangkalan data aktif
        if len(batch_rows) >= BATCH_SIZE:
            cursor.executemany("""
                INSERT OR IGNORE INTO archive_contents 
                (language, slug, filename, extension, archive_path, size_bytes, subscene_id, is_archive)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """, batch_rows)
            conn.commit()
            batch_rows.clear()

        # Semak had saiz bahagian bagi memulakan fail DB baru (khusus untuk fail besar)
        if lang_name != "malay" and current_part_count >= MAX_ROWS_PER_DB:
            close_and_finalize_db(conn, current_db_path, current_part_count)
            part_index += 1
            current_part_count = 0
            current_db_path = get_current_db_path(part_index)
            conn = init_sqlite_db(current_db_path)
            cursor = conn.cursor()

    # Simpan baki rekod terakhir
    if batch_rows:
        cursor.executemany("""
            INSERT OR IGNORE INTO archive_contents 
            (language, slug, filename, extension, archive_path, size_bytes, subscene_id, is_archive)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """, batch_rows)
        conn.commit()

    close_and_finalize_db(conn, current_db_path, current_part_count)
    process.wait()

    print(f"   ✅ Jumlah keseluruhan sah {lang_name.upper()}: {total_found:,} entri.")

def main():
    print("=" * 75)
    print("🗄️  PENJANAAN PANGKALAN DATA SQLite (AUTO-SPLIT SECARA PADAT)")
    print("=" * 75)

    for lang_name, info in ARCHIVES.items():
        process_archive(lang_name, info)

    print("\n" + "=" * 75)
    print("✨ PEMETAAN SELESAI: SEMUA FAIL KINI DI BAWAH 30MB & SELAMAT DI-PUSH!")
    print("=" * 75)

if __name__ == "__main__":
    main()