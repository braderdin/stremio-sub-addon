import os
import re
import sys
import shutil
import zipfile
import sqlite3
import subprocess
from pathlib import Path
from typing import Optional, Tuple, List, Dict
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
TEMP_STAGING_DIR = TEMP_DIR / "staging_inspect"

for p in [DATA_DIR, TEMP_DIR, TEMP_STAGING_DIR]:
    p.mkdir(parents=True, exist_ok=True)

# Laluan arkib asal di pemacu Windows
ARCHIVE_BASE_DIR = Path("/mnt/e/PROJEK-GITHUB/SUBTITLE--SUBSCENE-ARCHIVE")

ARCHIVES_CONFIG = [
    {
        "lang_code": "ms",
        "lang_name": "malay",
        "archive_path": ARCHIVE_BASE_DIR / "malay.7z",
        "db_prefix": "archive_detailed_map_ms"
    },
    {
        "lang_code": "id",
        "lang_name": "indonesian",
        "archive_path": ARCHIVE_BASE_DIR / "indonesian.7z",
        "db_prefix": "archive_detailed_map_id"
    }
]

# Had entri per fail DB (~20MB hingga 25MB)
MAX_ROWS_PER_DB = 45000
BATCH_SIZE = 5000

VALID_SUB_EXTS = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".smi"}
VALID_PKG_EXTS = {".zip", ".rar", ".7z"}

# ==============================================================================
# INISIALISASI PANGKALAN DATA SQLite
# ==============================================================================
def init_sqlite_db_v2(db_path: Path) -> sqlite3.Connection:
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute("PRAGMA journal_mode = WAL;")
    cur.execute("PRAGMA synchronous = NORMAL;")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS archive_subtitles_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            language_code TEXT NOT NULL,
            archive_source TEXT NOT NULL,
            slug TEXT NOT NULL,
            folder_path TEXT NOT NULL,
            package_name TEXT,
            package_full_path TEXT,
            sub_filename TEXT NOT NULL,
            sub_extension TEXT NOT NULL,
            internal_sub_path TEXT NOT NULL,
            sub_size_bytes INTEGER NOT NULL,
            package_size_bytes INTEGER,
            subscene_id TEXT,
            is_package INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_v2_slug ON archive_subtitles_v2 (slug);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v2_sub_filename ON archive_subtitles_v2 (sub_filename);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v2_sub_id ON archive_subtitles_v2 (subscene_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v2_lang ON archive_subtitles_v2 (language_code);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_v2_ext ON archive_subtitles_v2 (sub_extension);")

    conn.commit()
    return conn

def finalize_db(conn: sqlite3.Connection, db_path: Path, count: int):
    cur = conn.cursor()
    cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.commit()
    conn.close()
    size_mb = db_path.stat().st_size / (1024 * 1024)
    console.print(f"   [bold green]💾 Selesai Bahagian[/bold green] -> [yellow]{db_path.name}[/yellow] "
                  f"([cyan]{count:,}[/cyan] rekod | [magenta]{size_mb:.2f} MB[/magenta])")

# ==============================================================================
# PENGECAMAN ID & PARSING
# ==============================================================================
def extract_subscene_id(text: str) -> Optional[str]:
    m = re.search(r"(?:malay|indonesian|indo|hi|HI)[-_](\d+)\.(?:zip|rar|7z|srt|ass|ssa|vtt)$", text, re.IGNORECASE)
    if m:
        return m.group(1)
    m2 = re.search(r"[-_](\d{4,9})\.(?:zip|rar|7z|srt|ass|ssa|vtt)$", text)
    if m2:
        return m2.group(1)
    return None

def inspect_package_contents(file_path: Path, ext: str) -> List[Dict[str, Any]]:
    """Membaca senarai fail sarikata di dalam pakej .zip atau .rar tanpa ekstrak ke disk."""
    items = []

    if ext == ".zip":
        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                for zinfo in zf.infolist():
                    if zinfo.is_dir():
                        continue
                    clean_name = zinfo.filename.replace("\\", "/").strip()
                    sub_fn = Path(clean_name).name
                    sub_ext = Path(sub_fn).suffix.lower()

                    if sub_ext in VALID_SUB_EXTS:
                        items.append({
                            "sub_filename": sub_fn,
                            "sub_extension": sub_ext,
                            "internal_sub_path": clean_name,
                            "sub_size_bytes": zinfo.file_size
                        })
        except Exception:
            pass

    elif ext == ".rar":
        try:
            import rarfile
            with rarfile.RarFile(file_path, "r") as rf:
                for rinfo in rf.infolist():
                    if rinfo.isdir():
                        continue
                    clean_name = rinfo.filename.replace("\\", "/").strip()
                    sub_fn = Path(clean_name).name
                    sub_ext = Path(sub_fn).suffix.lower()

                    if sub_ext in VALID_SUB_EXTS:
                        items.append({
                            "sub_filename": sub_fn,
                            "sub_extension": sub_ext,
                            "internal_sub_path": clean_name,
                            "sub_size_bytes": rinfo.file_size
                        })
        except Exception:
            pass

    return items

# ==============================================================================
# ALUR PEMPROSESAN UTAMA
# ==============================================================================
def process_archive(cfg: dict, summary_list: list):
    lang_code = cfg["lang_code"]
    lang_name = cfg["lang_name"]
    archive_path = cfg["archive_path"]
    db_prefix = cfg["db_prefix"]

    if not archive_path.exists():
        console.print(f"[bold red]❌ Ralat: Fail arkib tidak wujud di:[/bold red] {archive_path}")
        return

    console.print(Panel.fit(
        f"[bold cyan]⚡ FASA 1: Mengekstrak Arkib Fizikal ({lang_name.upper()}) ke Staging Tempatan WSL[/bold cyan]\n"
        f"[white]Fail Sumber:[/white] [yellow]{archive_path}[/yellow]\n"
        f"[white]Destinasi Staging:[/white] [green]{TEMP_STAGING_DIR / lang_name}[/green]",
        border_style="cyan"
    ))

    extract_folder = TEMP_STAGING_DIR / lang_name
    if not extract_folder.exists() or not any(extract_folder.iterdir()):
        extract_folder.mkdir(parents=True, exist_ok=True)
        cmd_extract = [
            "7z", "x", "-y",
            f"-o{extract_folder}",
            str(archive_path),
            "-bso1", "-bsp1"
        ]
        proc = subprocess.run(cmd_extract)
        if proc.returncode != 0:
            console.print(f"[bold red]❌ Gagal mengekstrak arkib {archive_path.name}![/bold red]")
            return

    console.print(f"[bold green]✔ Selesai ekstrak ke staging. Memulakan pemetaan ke pangkalan data...[/bold green]\n")

    part_idx = 1
    current_part_count = 0
    total_sub_records = 0
    batch_records = []

    def get_part_db_path(idx: int) -> Path:
        return DATA_DIR / f"{db_prefix}_part_{idx:02d}.db"

    curr_db_path = get_part_db_path(part_idx)
    conn = init_sqlite_db_v2(curr_db_path)
    cur = conn.cursor()

    # Kumpulkan semua fail fizikal di staging
    all_files = []
    for root, _, files in os.walk(extract_folder):
        for f in files:
            full_p = Path(root) / f
            all_files.append(full_p)

    all_files.sort()
    console.print(f"🔍 Ditemui [yellow]{len(all_files):,}[/yellow] fail pakej di folder staging.")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[bold yellow]{task.completed}/{task.total} Fail"),
        TimeElapsedColumn(),
        console=console
    ) as progress:
        task = progress.add_task(f"Memetakan sarikata {lang_name.upper()}...", total=len(all_files))

        for full_p in all_files:
            rel_p = full_p.relative_to(extract_folder)
            norm_path = str(rel_p).replace("\\", "/")
            parts = [p for p in norm_path.split("/") if p]

            if len(parts) >= 2:
                slug = parts[-2]
                folder_path = "/".join(parts[:-1])
            else:
                slug = Path(parts[-1]).stem
                folder_path = ""

            pkg_filename = parts[-1]
            pkg_ext = full_p.suffix.lower()
            pkg_size = full_p.stat().st_size
            sub_id = extract_subscene_id(pkg_filename)

            # Jika pakej mampatan (.zip, .rar, .7z)
            if pkg_ext in VALID_PKG_EXTS:
                inner_subs = inspect_package_contents(full_p, pkg_ext)

                if inner_subs:
                    for sub in inner_subs:
                        final_sub_id = extract_subscene_id(sub["sub_filename"]) or sub_id

                        batch_records.append((
                            lang_code,
                            archive_path.name,
                            slug,
                            folder_path,
                            pkg_filename,
                            norm_path,
                            sub["sub_filename"],
                            sub["sub_extension"],
                            sub["internal_sub_path"],
                            sub["sub_size_bytes"],
                            pkg_size,
                            final_sub_id,
                            1
                        ))
                        total_sub_records += 1
                        current_part_count += 1
                else:
                    # Sandaran jika arkib kosong atau gagal dibuka
                    batch_records.append((
                        lang_code,
                        archive_path.name,
                        slug,
                        folder_path,
                        pkg_filename,
                        norm_path,
                        pkg_filename,
                        pkg_ext,
                        norm_path,
                        pkg_size,
                        pkg_size,
                        sub_id,
                        1
                    ))
                    total_sub_records += 1
                    current_part_count += 1

            # Jika fail sarikata berdiri sendiri (.srt, .ass terus tanpa zip)
            elif pkg_ext in VALID_SUB_EXTS:
                batch_records.append((
                    lang_code,
                    archive_path.name,
                    slug,
                    folder_path,
                    None,
                    norm_path,
                    pkg_filename,
                    pkg_ext,
                    norm_path,
                    pkg_size,
                    pkg_size,
                    sub_id,
                    0
                ))
                total_sub_records += 1
                current_part_count += 1

            # Tulis berkumpulan ke SQLite
            if len(batch_records) >= BATCH_SIZE:
                cur.executemany("""
                    INSERT INTO archive_subtitles_v2 
                    (language_code, archive_source, slug, folder_path, package_name,
                     package_full_path, sub_filename, sub_extension, internal_sub_path,
                     sub_size_bytes, package_size_bytes, subscene_id, is_package)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, batch_records)
                conn.commit()
                batch_records.clear()

            # Split fail DB jika melebihi had 45,000 baris
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
                conn = init_sqlite_db_v2(curr_db_path)
                cur = conn.cursor()

            progress.update(task, advance=1)

    # Simpan baki rekod terakhir
    if batch_records:
        cur.executemany("""
            INSERT INTO archive_subtitles_v2 
            (language_code, archive_source, slug, folder_path, package_name,
             package_full_path, sub_filename, sub_extension, internal_sub_path,
             sub_size_bytes, package_size_bytes, subscene_id, is_package)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
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

    # Bersihkan staging selepas selesai untuk jimat ruang storan
    if extract_folder.exists():
        console.print(f"[dim]Membersihkan folder staging {lang_name}...[/dim]")
        shutil.rmtree(extract_folder, ignore_errors=True)

    console.print(f"[bold green]✔ Selesai memproses {lang_name.upper()} (Jumlah fail sarikata sebenar: {total_sub_records:,}).[/bold green]\n")

# ==============================================================================
# FUNGSI UTAMA
# ==============================================================================
def main():
    console.print("\n" + "=" * 80)
    console.print("🗄️  [bold yellow]PEMETAAN TERPERINCI KANDUNGAN SARIKATA SUBSCENE (V2 - LEVEL .SRT)[/bold yellow]")
    console.print("=" * 80 + "\n")

    summary_list = []

    # Boleh proses Malay dahulu dengan mengulas Indonesian jika mahu menjimatkan masa
    for cfg in ARCHIVES_CONFIG:
        # Jika mahu proses Malay sahaja dahulu secara pantas, semak syarat di sini
        process_archive(cfg, summary_list)

    table = Table(title="📋 Senarai Fail SQLite Terperinci V2 (Sedia untuk GitHub)", border_style="cyan")
    table.add_column("Bahasa", style="cyan", justify="center")
    table.add_column("Nama Fail Database", style="yellow")
    table.add_column("Jumlah Rekod Sarikata", justify="right", style="green")
    table.add_column("Saiz Fail", justify="right", style="magenta")

    total_rows = 0
    total_size = 0.0

    for item in summary_list:
        table.add_row(item["lang"], item["file"], f"{item['rows']:,}", f"{item['size_mb']:.2f} MB")
        total_rows += item["rows"]
        total_size += item["size_mb"]

    table.add_section()
    table.add_row("JUMLAH", f"{len(summary_list)} fail .db", f"{total_rows:,}", f"{total_size:.2f} MB")

    console.print(table)
    console.print(f"\n[bold green]✨ Data terperinci sedia di: {DATA_DIR}[/bold green]\n")

if __name__ == "__main__":
    main()