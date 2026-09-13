import os
import sys
import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

# Laluan Folder dan Fail Data
LIVE_ENGINE_DIR = Path(__file__).resolve().parent
DATA_DIR = LIVE_ENGINE_DIR / "data"
DB_FILE = DATA_DIR / "stremio_cache.db"

NO_SUBS_JSON = DATA_DIR / "no_subs_history.json"
SCRAPED_JSON = DATA_DIR / "scraped_history.json"
MOVIE_POOL_JSON = DATA_DIR / "movie_pool.json"

def init_database(conn: sqlite3.Connection):
    """
    Membina skema jadual dan indeks SQLite untuk prestasi carian pantas O(1).
    """
    cursor = conn.cursor()
    
    # 1. Jadual Senarai Disahkan Tiada Sarikata
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS no_subs_history (
            imdb_id TEXT PRIMARY KEY,
            title TEXT,
            year TEXT,
            checked_at TEXT
        );
    """)

    # 2. Jadual Sejarah Sarikata Yang Telah Siap Dikikis
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scraped_history (
            imdb_id TEXT PRIMARY KEY,
            updated_at INTEGER,
            sub_count INTEGER,
            subtitles_json TEXT
        );
    """)

    # 3. Jadual Kolam Tajuk Cinemeta Aktif
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS movie_pool (
            imdb_id TEXT PRIMARY KEY,
            title TEXT,
            year TEXT,
            media_type TEXT,
            series_id TEXT,
            episode_title TEXT,
            season INTEGER,
            episode INTEGER,
            source TEXT,
            added_at TEXT
        );
    """)

    # Indeks Tambahan untuk Menapis Mengikut Jenis Media & Siri
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pool_type ON movie_pool(media_type);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pool_series ON movie_pool(series_id);")

    conn.commit()

def migrate_no_subs(cursor: sqlite3.Cursor) -> int:
    """Memindahkan rekod dari no_subs_history.json ke jadual no_subs_history."""
    if not NO_SUBS_JSON.exists():
        return 0

    try:
        with open(NO_SUBS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        console.print(f"[bold red]⚠️ Ralat membaca {NO_SUBS_JSON.name}: {e}[/bold red]")
        return 0

    if not isinstance(data, dict):
        return 0

    records = []
    for imdb_id, item in data.items():
        if isinstance(item, dict):
            records.append((
                str(imdb_id).strip(),
                str(item.get("title", "")),
                str(item.get("year", "")),
                str(item.get("checked_at", ""))
            ))

    cursor.executemany("""
        INSERT OR REPLACE INTO no_subs_history (imdb_id, title, year, checked_at)
        VALUES (?, ?, ?, ?);
    """, records)

    return len(records)

def migrate_scraped_history(cursor: sqlite3.Cursor) -> int:
    """Memindahkan rekod dari scraped_history.json ke jadual scraped_history."""
    if not SCRAPED_JSON.exists():
        return 0

    try:
        with open(SCRAPED_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        console.print(f"[bold red]⚠️ Ralat membaca {SCRAPED_JSON.name}: {e}[/bold red]")
        return 0

    # Menyokong format bersarang 'processed_ids' mahupun format kamus rata
    items_dict: Dict[str, Any] = data.get("processed_ids", data) if isinstance(data, dict) else {}

    records = []
    for imdb_id, item in items_dict.items():
        if isinstance(item, dict):
            updated_at = int(item.get("updated_at", time.time()))
            subs = item.get("subtitles", [])
            sub_count = int(item.get("sub_count", len(subs)))
            subtitles_json_str = json.dumps(subs, ensure_ascii=False)

            records.append((
                str(imdb_id).strip(),
                updated_at,
                sub_count,
                subtitles_json_str
            ))

    cursor.executemany("""
        INSERT OR REPLACE INTO scraped_history (imdb_id, updated_at, sub_count, subtitles_json)
        VALUES (?, ?, ?, ?);
    """, records)

    return len(records)

def migrate_movie_pool(cursor: sqlite3.Cursor) -> int:
    """Memindahkan rekod dari movie_pool.json ke jadual movie_pool."""
    if not MOVIE_POOL_JSON.exists():
        return 0

    try:
        with open(MOVIE_POOL_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        console.print(f"[bold red]⚠️ Ralat membaca {MOVIE_POOL_JSON.name}: {e}[/bold red]")
        return 0

    if not isinstance(data, dict):
        return 0

    records = []
    for key_id, item in data.items():
        if isinstance(item, dict):
            imdb_id = str(item.get("imdb_id") or key_id).strip()
            title = str(item.get("title", ""))
            year = str(item.get("year", ""))
            media_type = str(item.get("type", "movie"))
            series_id = str(item.get("series_id", ""))
            episode_title = str(item.get("episode_title", ""))
            season = item.get("season")
            episode = item.get("episode")
            source = str(item.get("source", "cinemeta"))
            added_at = str(item.get("added_at", ""))

            records.append((
                imdb_id,
                title,
                year,
                media_type,
                series_id,
                episode_title,
                int(season) if season is not None else None,
                int(episode) if episode is not None else None,
                source,
                added_at
            ))

    cursor.executemany("""
        INSERT OR REPLACE INTO movie_pool (
            imdb_id, title, year, media_type, series_id, episode_title, season, episode, source, added_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, records)

    return len(records)

def main():
    console.print(Panel.fit(
        "[bold cyan]📦 MIGRASI DATA JSON KE SQLITE (STREMIO_CACHE.DB)[/bold cyan]\n"
        "[yellow]Menyalin no_subs_history, scraped_history & movie_pool ke fail SQLite tunggal[/yellow]",
        title="Database Migration Engine", border_style="cyan"
    ))

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    start_time = time.perf_counter()
    conn = sqlite3.connect(DB_FILE)
    
    # Pengoptimuman prestasi tulis SQLite
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")

    try:
        init_database(conn)
        cursor = conn.cursor()

        console.print("[cyan]Memulakan pemindahan data...[/cyan]")
        count_no_subs = migrate_no_subs(cursor)
        count_scraped = migrate_scraped_history(cursor)
        count_pool = migrate_movie_pool(cursor)

        conn.commit()
    finally:
        conn.close()

    elapsed = time.perf_counter() - start_time
    db_size_mb = DB_FILE.stat().st_size / (1024 * 1024) if DB_FILE.exists() else 0.0

    # Jadual Ringkasan Pemindahan
    table = Table(title="✨ Ringkasan Hasil Migrasi SQLite", border_style="green")
    table.add_column("Sumber Asal (JSON)", style="cyan")
    table.add_column("Jadual SQLite Sasaran", style="yellow")
    table.add_column("Jumlah Rekod Berjaya", justify="right", style="bold green")

    table.add_row("no_subs_history.json", "no_subs_history", f"{count_no_subs:,}")
    table.add_row("scraped_history.json", "scraped_history", f"{count_scraped:,}")
    table.add_row("movie_pool.json", "movie_pool", f"{count_pool:,}")

    console.print(table)

    summary_panel = Panel(
        f"[bold white]Lokasi Database :[/bold white] [green]{DB_FILE}[/green]\n"
        f"[bold white]Saiz Fail .db   :[/bold white] [yellow]{db_size_mb:.2f} MB[/yellow]\n"
        f"[bold white]Masa Diambil    :[/bold white] [cyan]{elapsed:.3f} saat[/cyan]\n"
        f"[bold white]Status Asal     :[/bold white] [white]Ketiga-tiga fail .json asal kekal selamat tanpa diusik.[/white]",
        title="Status Penyiapan", border_style="green"
    )
    console.print(summary_panel)

if __name__ == "__main__":
    main()