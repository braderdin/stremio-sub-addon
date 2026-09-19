import os
import re
import sys
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Optional, Tuple
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

# ==============================================================================
# KONFIGURASI DIREKTORI & PARAMETER
# ==============================================================================
# Laluan asas projek di WSL
PROJECT_DIR = Path("/home/braderdin/stremio-sub-addon/ondemand_title_packs")
DATA_DIR = PROJECT_DIR / "data"
TEMP_DIR = PROJECT_DIR / "temp"
OUTPUT_SUB_DIR = PROJECT_DIR / "subtitles_by_title"

# Sediakan folder jika belum wujud
for p in [DATA_DIR, TEMP_DIR, OUTPUT_SUB_DIR]:
    p.mkdir(parents=True, exist_ok=True)

# Laluan Arkib Asal (Windows Mount di WSL)
ARCHIVE_BASE_DIR = Path("/mnt/e/PROJEK-GITHUB/SUBTITLE--SUBSCENE-ARCHIVE")

ARCHIVES_CONFIG = [
    {
        "lang_code": "ms",
        "lang_name": "malay",
        "archive_path": ARCHIVE_BASE_DIR / "malay.7z",
        "db_prefix": "archive_map_ms"
    },
    {
        "lang_code": "id",
        "lang_name": "indonesian",
        "archive_path": ARCHIVE_BASE_DIR / "indonesian.7z",
        "db_prefix": "archive_map_id"
    }
]

# Had rekod setiap fail .db (~20MB hingga 25MB setiap fail, sangat selamat untuk GitHub)
MAX_ROWS_PER_DB = 45000
BATCH_SIZE = 5000

VALID_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".zip", ".rar", ".7z"}

# ==============================================================================
# PENGURUSAN PANGKALAN DATA (SQLITE)
# ==============================================================================
def init_sqlite_db(db_path: Path) -> sqlite3.Connection:
    """Inisialisasi fail SQLite3 baharu dengan skema terperinci dan indeks pantas."""
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute("PRAGMA journal_mode = WAL;")
    cur.execute("PRAGMA synchronous = NORMAL;")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS archive_subtitles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            language_code TEXT NOT NULL,
            archive_source TEXT NOT NULL,
            slug TEXT NOT NULL,
            folder_path TEXT NOT NULL,
            filename TEXT NOT NULL,
            extension TEXT NOT NULL,
            full_archive_path TEXT NOT NULL UNIQUE,
            size_bytes INTEGER NOT NULL,
            subscene_id TEXT,
            is_package INTEGER NOT NULL DEFAULT 0,
            date_archived TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Indeks carian pantas
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sub_slug ON archive_subtitles (slug);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sub_id ON archive_subtitles (subscene_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sub_lang ON archive_subtitles (language_code);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sub_ext ON archive_subtitles (extension);")

    conn.commit()
    return conn

def finalize_db(conn: sqlite3.Connection, db_path: Path, count: int):
    """Tutup DB dan gabungkan WAL ke fail utama secara penuh."""
    cur = conn.cursor()
    cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.commit()
    conn.close()
    size_mb = db_path.stat().st_size / (1024 * 1024)
    console.print(f"   [bold green]💾 Selesai Bahagian[/bold green] -> [yellow]{db_path.name}[/yellow] "
                  f"([cyan]{count:,}[/cyan] rekod | [magenta]{size_mb:.2f} MB[/magenta])")

# ==============================================================================
# FUNGSI PEMPROSESAN STRING & ID
# ==============================================================================
def extract_subscene_id(filename: str) -> Optional[str]:
    """Mengekstrak Subscene ID numerik daripada corak nama fail."""
    # Corak biasa: malay-123456.zip, indonesian_123456.rar, hi-123456.srt
    m = re.search(r"(?:malay|indonesian|indo|hi|HI)[-_](\d+)\.(zip|rar|7z|srt|ass|ssa|vtt)$", filename, re.IGNORECASE)
    if m:
        return m.group(1)
    
    # Corak nombor di hujung nama sebelum sambungan fail (contoh: movie_title_1234567.zip)
    m2 = re.search(r"[-_](\d{4,9})\.(zip|rar|7z|srt|ass|ssa|vtt)$", filename)
    if m2:
        return m2.group(1)

    return None

def parse_archive_path(norm_path: str) -> Tuple[str, str, str, str]:
    """
    Memecahkan laluan fail di dalam arkib kepada:
    (slug, folder_path, filename, extension)
    """
    parts = [p for p in norm_path.split("/") if p]
    filename = parts[-1]
    ext = Path(filename).suffix.lower()

    if len(parts) >= 2:
        # Folder tepat di atas fail adalah slug judul (standard Subscene)
        slug = parts[-2]
        folder_path = "/".join(parts[:-1])
    else:
        # Fail berada di root arkib
        slug = Path(filename).stem
        folder_path = ""

    return slug, folder_path, filename, ext

# ==============================================================================
# ALUR PEMPROSESAN ARKIB UTAMA
# ==============================================================================
def process_archive(cfg: dict, summary_list: list):
    lang_code = cfg["lang_code"]
    lang_name = cfg["lang_name"]
    archive_path = cfg["archive_path"]
    db_prefix = cfg["db_prefix"]

    if not archive_path.exists():
        console.print(f"[bold red]❌ Ralat: Fail arkib tidak dijumpai di:[/bold red] {archive_path}")
        return

    console.print(Panel.fit(
        f"[bold cyan]🔍 MEMULAKAN IMBASAN ARKIB: {lang_name.upper()}[/bold cyan]\n"
        f"[white]Fail Sumber:[/white] [yellow]{archive_path}[/yellow]\n"
        f"[white]Had Rekod per Fail DB:[/white] [green]{MAX_ROWS_PER_DB:,} rekod (~25MB)[/green]",
        border_style="cyan"
    ))

    # Pastikan command 7z ada
    if not shutil.which("7z"):
        console.print("[bold red]❌ Pakej 'p7zip-full' belum dipasang! Sila jalankan: sudo apt install p7zip-full[/bold red]")
        sys.exit(1)

    part_idx = 1
    current_part_count = 0
    total_valid_entries = 0
    batch_records = []

    def get_part_db_path(idx: int) -> Path:
        return DATA_DIR / f"{db_prefix}_part_{idx:02d}.db"

    curr_db_path = get_part_db_path(part_idx)
    conn = init_sqlite_db(curr_db_path)
    cur = conn.cursor()

    cmd = ["7z", "l", str(archive_path)]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="ignore")

    # Regex membaca format senarai 7z: Date Time Attr Size Compressed Name
    line_regex = re.compile(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+(\S+)\s+(\d+)\s*(\d*)\s+(.+)$")

    for line in process.stdout:
        match = line_regex.match(line)
        if not match:
            continue

        date_str, attr, size_str, _, raw_name = match.groups()

        # Langkau jika ini adalah entri direktori
        if "D" in attr:
            continue

        norm_path = raw_name.replace("\\", "/").strip()
        slug, folder_path, filename, ext = parse_archive_path(norm_path)

        if ext not in VALID_EXTENSIONS:
            continue

        size_bytes = int(size_str)
        sub_id = extract_subscene_id(filename)
        is_package = 1 if ext in [".zip", ".rar", ".7z"] else 0

        batch_records.append((
            lang_code,
            archive_path.name,
            slug,
            folder_path,
            filename,
            ext,
            norm_path,
            size_bytes,
            sub_id,
            is_package,
            date_str
        ))

        total_valid_entries += 1
        current_part_count += 1

        # Tulis ke SQLite secara kelompok
        if len(batch_records) >= BATCH_SIZE:
            cur.executemany("""
                INSERT OR IGNORE INTO archive_subtitles 
                (language_code, archive_source, slug, folder_path, filename, extension,
                 full_archive_path, size_bytes, subscene_id, is_package, date_archived)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, batch_records)
            conn.commit()
            batch_records.clear()

        # Split fail DB baharu jika melebihi had
        if current_part_count >= MAX_ROWS_PER_DB:
            finalize_db(conn, curr_db_path, current_part_count)
            summary_list.append({
                "lang": lang_code.upper(),
                "file": curr_db_path.name,
                "rows": current_part_count,
                "size_mb": curr_db_path.stat().st_size / (1024 * 1024)
            })

            part_idx += 1
            current_part_count = 0
            curr_db_path = get_part_db_path(part_idx)
            conn = init_sqlite_db(curr_db_path)
            cur = conn.cursor()

    # Simpan baki rekod terakhir
    if batch_records:
        cur.executemany("""
            INSERT OR IGNORE INTO archive_subtitles 
            (language_code, archive_source, slug, folder_path, filename, extension,
             full_archive_path, size_bytes, subscene_id, is_package, date_archived)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, batch_records)
        conn.commit()
        batch_records.clear()

    finalize_db(conn, curr_db_path, current_part_count)
    summary_list.append({
        "lang": lang_code.upper(),
        "file": curr_db_path.name,
        "rows": current_part_count,
        "size_mb": curr_db_path.stat().st_size / (1024 * 1024)
    })

    process.wait()
    console.print(f"[bold green]✔ Selesai memproses {lang_name.upper()} (Jumlah: {total_valid_entries:,} sarikata sah).[/bold green]\n")

# ==============================================================================
# FUNGSI UTAMA
# ==============================================================================
def main():
    console.print("\n" + "=" * 78)
    console.print("🗄️  [bold yellow]PEMETAAN ARKIB SUBSCENE KE SQLITE (.DB AUTO-SPLIT 25MB)[/bold yellow]")
    console.print("=" * 78 + "\n")

    summary_list = []

    for cfg in ARCHIVES_CONFIG:
        process_archive(cfg, summary_list)

    # Papar jadual ringkasan fail output
    table = Table(title="📋 Senarai Fail SQLite Terjana (Sedia untuk GitHub)", border_style="cyan")
    table.add_column("Bahasa", style="cyan", justify="center")
    table.add_column("Nama Fail Database", style="yellow")
    table.add_column("Jumlah Rekod", justify="right", style="green")
    table.add_column("Saiz Fail", justify="right", style="magenta")

    total_all_rows = 0
    total_all_size = 0.0

    for item in summary_list:
        table.add_row(
            item["lang"],
            item["file"],
            f"{item['rows']:,}",
            f"{item['size_mb']:.2f} MB"
        )
        total_all_rows += item["rows"]
        total_all_size += item["size_mb"]

    table.add_section()
    table.add_row("JUMLAH", f"{len(summary_list)} fail .db", f"{total_all_rows:,}", f"{total_all_size:.2f} MB")

    console.print(table)
    console.print(f"\n[bold green]✨ Semua data berjaya dipetakan ke dalam: {DATA_DIR}[/bold green]\n")

if __name__ == "__main__":
    main()