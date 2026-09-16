import os
import sys
import time
import shutil
import hashlib
import sqlite3
import subprocess
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

console = Console()

# Direktori Tempatan WSL (Laju Maksimum Ext4)
BASE_DIR = Path("/home/braderdin/stremio-sub-addon/SUBTITLE--SUBSCENE-ARCHIVE")
DATA_DIR = BASE_DIR / "data"
MANIFEST_DB = DATA_DIR / "split_manifest.db"
SPLIT_OUTPUT_DIR = BASE_DIR / "archive-split"
TEMP_STAGING_DIR = BASE_DIR / "temp_staging"

# Direktori Sumber Arkib Fizikal (Windows Mount)
ARCHIVE_SOURCE_DIR = Path("/mnt/e/PROJEK-GITHUB/SUBTITLE--SUBSCENE-ARCHIVE")
OLD_WINDOWS_SPLIT_DIR = ARCHIVE_SOURCE_DIR / "archive-split"

TARGET_CHUNK_BYTES = 50 * 1024 * 1024  # Had 50MB setiap pek kendiri

TARGET_ARCHIVES = [
    {"name": "malay", "file": ARCHIVE_SOURCE_DIR / "malay.7z"},
    {"name": "indonesian", "file": ARCHIVE_SOURCE_DIR / "indonesian.7z"}
]

def init_manifest_db(db_path: Path):
    """Sediakan pangkalan data SQLite manifes arkib kendiri."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode = WAL;")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS archive_split_manifest (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            archive_name TEXT NOT NULL,
            part_number INTEGER NOT NULL,
            part_filename TEXT UNIQUE NOT NULL,
            part_path TEXT NOT NULL,
            file_count INTEGER NOT NULL,
            size_bytes INTEGER NOT NULL,
            size_mb REAL NOT NULL,
            sha256_hash TEXT NOT NULL,
            status TEXT DEFAULT 'pending_upload',
            tg_message_id INTEGER DEFAULT NULL,
            tg_file_id TEXT DEFAULT NULL,
            uploaded_at TIMESTAMP DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS archive_part_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            part_filename TEXT NOT NULL,
            internal_path TEXT NOT NULL,
            size_bytes INTEGER,
            FOREIGN KEY (part_filename) REFERENCES archive_split_manifest(part_filename)
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_part_filename ON archive_split_manifest (part_filename)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_internal_path ON archive_part_files (internal_path)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_part_mapping ON archive_part_files (part_filename)")
    conn.commit()
    conn.close()

def calculate_sha256(filepath: Path) -> str:
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(2 * 1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()

def is_part_already_done(part_filename: str) -> bool:
    conn = sqlite3.connect(MANIFEST_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM archive_split_manifest WHERE part_filename = ?", (part_filename,))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def sync_existing_malay_parts():
    """Salin bahagian arkib Malay yang telah berjaya dibuat dari pemacu E: ke WSL jika ada."""
    if not OLD_WINDOWS_SPLIT_DIR.exists():
        return

    malay_files = sorted(list(OLD_WINDOWS_SPLIT_DIR.glob("malay_part_*.7z")))
    if not malay_files:
        return

    console.print(f"[cyan]📦 Mengesan {len(malay_files)} bahagian malay yang telah siap di pemacu E:...[/cyan]")
    for mf in malay_files:
        dest_file = SPLIT_OUTPUT_DIR / mf.name
        if not dest_file.exists():
            shutil.copy2(mf, dest_file)

def process_archive(archive_name: str, source_path: Path):
    if not source_path.exists():
        console.print(f"[bold red]❌ Ralat: Fail {source_path} tidak ditemui![/bold red]")
        return

    SPLIT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    extract_folder = TEMP_STAGING_DIR / archive_name

    # 1. Ekstrak arkib sekali jalan ke native disk WSL
    if not extract_folder.exists() or not any(extract_folder.iterdir()):
        console.print(Panel.fit(
            f"[bold cyan]⚡ FASA 1: Mengekstrak Keseluruhan Fail ({archive_name.upper()}) Sekali Jalan[/bold cyan]\n"
            f"[white]Mengekstrak daripada:[/white] [yellow]{source_path}[/yellow]\n"
            f"[white]Destinasi Staging:[/white] [green]{extract_folder}[/green]",
            border_style="cyan"
        ))

        extract_folder.mkdir(parents=True, exist_ok=True)
        start_ext = time.time()

        cmd_extract = [
            "7z", "x", "-y",
            f"-o{extract_folder}",
            str(source_path),
            "-bso1", "-bsp1"
        ]
        proc = subprocess.run(cmd_extract)
        if proc.returncode != 0:
            console.print(f"[bold red]❌ Ralat ketika mengekstrak {source_path.name}![/bold red]")
            sys.exit(1)

        console.print(f"[bold green]✔ Selesai mengekstrak dalam masa {round(time.time() - start_ext, 1)}s![/bold green]\n")
    else:
        console.print(f"[green]✔ Menggunakan fail staging sedia ada di {extract_folder}[/green]")

    # 2. Imbas semua fail yang telah diekstrak
    console.print(f"[cyan]🔍 Mengindeks senarai fail dari disk tempatan...[/cyan]")
    all_files = []
    for root, _, files in os.walk(extract_folder):
        for f in files:
            full_p = Path(root) / f
            rel_p = full_p.relative_to(extract_folder)
            size = full_p.stat().st_size
            all_files.append((str(rel_p), full_p, size))

    all_files.sort(key=lambda x: x[0])
    total_files = len(all_files)
    console.print(f"   [green]✔ Dijumpai {total_files:,} fail fizikal.[/green]")

    # 3. Kumpulkan kepada kelompok 50MB
    chunks = []
    current_chunk = []
    current_size = 0

    for rel_path, full_path, size in all_files:
        current_chunk.append((rel_path, full_path, size))
        current_size += size

        if current_size >= TARGET_CHUNK_BYTES:
            chunks.append(current_chunk)
            current_chunk = []
            current_size = 0

    if current_chunk:
        chunks.append(current_chunk)

    total_parts = len(chunks)
    console.print(f"   [yellow]📦 Jumlah sasaran: {total_parts} pek bebas kendiri (50MB/pek)[/yellow]\n")

    # 4. Mampatkan kelompok 50MB ke fail 7z kendiri
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[bold yellow]{task.completed}/{task.total} Pek"),
        TimeElapsedColumn(),
        console=console
    ) as progress:
        task = progress.add_task(f"Membina {archive_name}", total=total_parts)

        for idx, chunk in enumerate(chunks, 1):
            part_filename = f"{archive_name}_part_{idx:03d}.7z"
            part_dest_path = SPLIT_OUTPUT_DIR / part_filename

            if part_dest_path.exists() and is_part_already_done(part_filename):
                progress.update(task, advance=1, description=f"[dim]Langkau {part_filename} (Sedia Ada)[/dim]")
                continue

            progress.update(task, description=f"[bold cyan]Memampat {part_filename}...[/bold cyan]")

            # Senarai fail untuk 7z
            list_txt = TEMP_STAGING_DIR / f"list_{archive_name}_{idx}.txt"
            with open(list_txt, "w", encoding="utf-8") as lf:
                for rel_p, _, _ in chunk:
                    lf.write(f"{rel_p}\n")

            # Mampatkan fail terus dari folder ekstrak
            cmd_compress = [
                "7z", "a", "-t7z", "-mx=1",
                str(part_dest_path),
                f"@{list_txt}",
                "-bso0", "-bsp0"
            ]
            subprocess.run(cmd_compress, cwd=str(extract_folder), check=True)

            if list_txt.exists():
                list_txt.unlink()

            # Kira saiz dan SHA-256
            sha256_val = calculate_sha256(part_dest_path)
            f_size_bytes = part_dest_path.stat().st_size
            f_size_mb = round(f_size_bytes / (1024 * 1024), 2)

            # Rekod ke split_manifest.db
            conn = sqlite3.connect(MANIFEST_DB)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO archive_split_manifest (
                    archive_name, part_number, part_filename, part_path,
                    file_count, size_bytes, size_mb, sha256_hash, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending_upload')
                ON CONFLICT(part_filename) DO UPDATE SET
                    size_bytes = excluded.size_bytes,
                    size_mb = excluded.size_mb,
                    sha256_hash = excluded.sha256_hash,
                    file_count = excluded.file_count
            """, (
                archive_name, idx, part_filename, str(part_dest_path),
                len(chunk), f_size_bytes, f_size_mb, sha256_val
            ))

            file_rows = [(part_filename, rel_p, sz) for rel_p, _, sz in chunk]
            cursor.executemany("""
                INSERT INTO archive_part_files (part_filename, internal_path, size_bytes)
                VALUES (?, ?, ?)
            """, file_rows)

            conn.commit()
            conn.close()

            progress.update(task, advance=1)

    # Bersihkan folder staging selepas selesai satu arkib penuh untuk jimat ruang
    if extract_folder.exists():
        console.print(f"[dim]Membersihkan cache ekstrak sementara {archive_name}...[/dim]")
        shutil.rmtree(extract_folder, ignore_errors=True)

def display_summary():
    """Papar ringkasan pangkalan data manifes."""
    conn = sqlite3.connect(MANIFEST_DB)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT archive_name, COUNT(*), SUM(file_count), SUM(size_mb)
        FROM archive_split_manifest
        GROUP BY archive_name
    """)
    rows = cursor.fetchall()
    conn.close()

    table = Table(title="📊 Ringkasan Pangkalan Data Arkib Bebas (split_manifest.db)", border_style="green")
    table.add_column("Arkib", style="cyan")
    table.add_column("Jumlah Pakej (.7z)", justify="right", style="yellow")
    table.add_column("Jumlah Fail Sarikata", justify="right", style="magenta")
    table.add_column("Jumlah Saiz Output", justify="right", style="green")

    for arch, parts, files, size_mb in rows:
        table.add_row(arch.upper(), f"{parts} pek", f"{files:,} fail", f"{size_mb:.2f} MB")

    console.print(table)
    console.print(f"[bold green]✔ Manifes SQLite sedia untuk GitHub: [/bold green]{MANIFEST_DB}\n")

if __name__ == "__main__":
    init_manifest_db(MANIFEST_DB)
    sync_existing_malay_parts()

    start_total = time.time()
    for item in TARGET_ARCHIVES:
        process_archive(item["name"], item["file"])

    if TEMP_STAGING_DIR.exists():
        shutil.rmtree(TEMP_STAGING_DIR, ignore_errors=True)

    display_summary()
    console.print(f"[cyan]⏱ Pemprosesan siap sepenuhnya dalam: {round(time.time() - start_total, 2)} saat[/cyan]")