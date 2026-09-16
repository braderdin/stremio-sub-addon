import sqlite3
from pathlib import Path
from rich.console import Console
from rich.table import Table

console = Console()

BASE_DIR = Path("/home/braderdin/stremio-sub-addon/SUBTITLE--SUBSCENE-ARCHIVE")
DATA_DIR = BASE_DIR / "data"

SOURCE_DB = DATA_DIR / "split_manifest.db"
TARGET_DB = DATA_DIR / "cloud_parts_manifest.db"

def export_cloud_manifest():
    if not SOURCE_DB.exists():
        console.print(f"[bold red]❌ Fail sumber {SOURCE_DB} tidak dijumpai![/bold red]")
        return

    console.print(f"[cyan]📦 Menyalin jadual antrian awan dari {SOURCE_DB.name} ➔ {TARGET_DB.name}...[/cyan]")

    # 1. Buka pangkalan data sumber dalam mod Read-Only (Selamat tanpa ganggu fail asal)
    src_conn = sqlite3.connect(f"file:{SOURCE_DB}?mode=ro", uri=True)
    src_cur = src_conn.cursor()

    # Pastikan jadual archive_split_manifest wujud
    src_cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='archive_split_manifest';")
    create_table_row = src_cur.fetchone()
    if not create_table_row:
        console.print("[bold red]❌ Jadual 'archive_split_manifest' tidak ditemui![/bold red]")
        src_conn.close()
        return

    create_table_sql = create_table_row[0]

    # Ambil semua data dari jadual archive_split_manifest (209 baris)
    src_cur.execute("""
        SELECT id, archive_name, part_number, part_filename, part_path,
               file_count, size_bytes, size_mb, sha256_hash, status,
               tg_message_id, tg_file_id, uploaded_at, created_at
        FROM archive_split_manifest
        ORDER BY id ASC;
    """)
    rows = src_cur.fetchall()
    src_conn.close()

    # 2. Sediakan fail sasaran baharu (cloud_parts_manifest.db)
    if TARGET_DB.exists():
        TARGET_DB.unlink()

    dest_conn = sqlite3.connect(str(TARGET_DB))
    dest_cur = dest_conn.cursor()
    dest_cur.execute("PRAGMA journal_mode = WAL;")
    dest_cur.execute("PRAGMA synchronous = NORMAL;")

    # Cipta semula skema jadual
    dest_cur.execute(create_table_sql)
    dest_cur.execute("CREATE INDEX IF NOT EXISTS idx_part_filename ON archive_split_manifest (part_filename);")
    dest_cur.execute("CREATE INDEX IF NOT EXISTS idx_status ON archive_split_manifest (status);")

    # Masukkan data
    dest_cur.executemany("""
        INSERT INTO archive_split_manifest (
            id, archive_name, part_number, part_filename, part_path,
            file_count, size_bytes, size_mb, sha256_hash, status,
            tg_message_id, tg_file_id, uploaded_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, rows)

    dest_conn.commit()
    dest_cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    dest_conn.close()

    size_kb = TARGET_DB.stat().st_size / 1024

    table = Table(title="✨ Ringkasan Pengeksportan Manifes Awan", border_style="green")
    table.add_column("Perkara", style="cyan")
    table.add_column("Status / Maklumat", style="white")

    table.add_row("Fail Asal (Kekal Utuh)", f"{SOURCE_DB.name} (84.1 MB)")
    table.add_row("Fail Mini Awan Baharu", f"{TARGET_DB.name} ({size_kb:.2f} KB)")
    table.add_row("Jumlah Bahagian Disalin", f"{len(rows)} bahagian pek")

    console.print(table)
    console.print("[bold green]✔ Fail pangkalan data mini siap untuk kegunaan enjin awan dan GitHub Actions![/bold green]\n")

if __name__ == "__main__":
    export_cloud_manifest()