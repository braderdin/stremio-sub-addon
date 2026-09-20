#!/usr/bin/env python3
# ==============================================================================
# PROJEK: PEMBERSIH SURGICAL 10 SHARD REDIS (DRY-RUN & AUDIT CACHE ENGINE)
# LOKASI: /home/braderdin/stremio-sub-addon/live_engine/13_sweep_clean_redis.py
# ==============================================================================

import os
import sys
import json
import sqlite3
import argparse
import importlib
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Any

from curl_cffi import requests
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

console = Console()

# ==============================================================================
# LALUAN DIREKTORI & MODUL LIVE ENGINE
# ==============================================================================
LIVE_ENGINE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = LIVE_ENGINE_DIR.parent
TEMP_DIR = LIVE_ENGINE_DIR / "temp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

AUDIT_DB_PATH = TEMP_DIR / "redis_sweep_audit.db"
JSON_REPORT_PATH = TEMP_DIR / "redis_dirty_records_report.json"

if str(LIVE_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(LIVE_ENGINE_DIR))

try:
    _config = importlib.import_module("00_config")
    _redis = importlib.import_module("01_redis_db")
except ImportError as e:
    console.print(f"[bold red]❌ Ralat mengimport modul live_engine: {e}[/bold red]")
    sys.exit(1)

# ==============================================================================
# INISIALISASI PANGKALAN DATA AUDIT TEMPATAN (SQLite)
# ==============================================================================
def init_audit_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS redis_sweep_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shard_index INTEGER NOT NULL,
            redis_key TEXT NOT NULL,
            imdb_id TEXT,
            total_subs_before INTEGER,
            total_subs_valid INTEGER,
            total_subs_dirty INTEGER,
            action_planned TEXT,
            dirty_records_json TEXT,
            mode TEXT,
            scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_shard ON redis_sweep_audit (shard_index);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_key ON redis_sweep_audit (redis_key);")
    conn.commit()
    return conn

# ==============================================================================
# IMBASAN KEKUNCI REDIS VIA SCAN REST API
# ==============================================================================
def scan_all_keys_in_account(acc: dict, pattern: str = "sub:*") -> List[str]:
    """Mengambil semua senarai kunci 'sub:*' menggunakan kursor SCAN bukan-penyekat."""
    url = acc.get("url", "").rstrip("/")
    token = acc.get("token", "")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    all_keys = []
    cursor = "0"

    while True:
        payload = ["SCAN", cursor, "MATCH", pattern, "COUNT", "1000"]
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=20)
            if resp.status_code != 200:
                break
            data = resp.json()
            result = data.get("result", ["0", []])
            cursor = str(result[0])
            keys = result[1]
            all_keys.extend(keys)

            if cursor == "0":
                break
        except Exception as e:
            console.print(f"[yellow]⚠️ Ralat scan kekunci akaun #{acc['index']}: {e}[/yellow]")
            break

    return sorted(list(set(all_keys)))

# ==============================================================================
# AUDIT & PEMBERSIHAN SATU SHARD
# ==============================================================================
def process_shard(acc: dict, dry_run: bool, progress: Progress, task_id: Any) -> Dict[str, Any]:
    acc_idx = acc["index"]
    url = acc.get("url", "").rstrip("/")
    token = acc.get("token", "")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    keys = scan_all_keys_in_account(acc, "sub:*")
    total_keys = len(keys)
    progress.update(task_id, total=total_keys if total_keys > 0 else 1, completed=0)

    clean_keys_count = 0
    repaired_keys_count = 0
    deleted_keys_count = 0
    total_dirty_subs = 0
    total_valid_subs = 0

    audit_records_db = []
    detailed_dirty_reports = []

    mode_label = "DRY_RUN" if dry_run else "LIVE_EXECUTION"

    for idx, key in enumerate(keys, 1):
        imdb_id = key.replace("sub:", "").strip()
        try:
            resp = requests.post(url, headers=headers, json=["GET", key], timeout=10)
            if resp.status_code != 200:
                progress.update(task_id, advance=1)
                continue
            raw_val = resp.json().get("result")
            if not raw_val:
                progress.update(task_id, advance=1)
                continue

            records = json.loads(raw_val) if isinstance(raw_val, str) else raw_val
            if not isinstance(records, list):
                progress.update(task_id, advance=1)
                continue
        except Exception:
            progress.update(task_id, advance=1)
            continue

        clean_records = []
        dirty_records = []

        for r in records:
            if not isinstance(r, dict):
                dirty_records.append({"item": r, "reason": "Bukan objek kamus JSON sah"})
                continue

            sub_url = str(r.get("url", "")).strip()

            # Tapisan Pintar: Ralat ruang kosong atau format URL terputus
            if " " in sub_url:
                dirty_records.append({
                    "id": r.get("id"),
                    "url": sub_url,
                    "release": r.get("release"),
                    "reason": "URL mengandungi ruang kosong (Broken Link)"
                })
            elif not sub_url.startswith("http"):
                dirty_records.append({
                    "id": r.get("id"),
                    "url": sub_url,
                    "release": r.get("release"),
                    "reason": "URL tidak mempunyai protokol HTTP/HTTPS sah"
                })
            else:
                clean_records.append(r)

        dirty_count = len(dirty_records)
        valid_count = len(clean_records)
        total_before = len(records)

        total_dirty_subs += dirty_count
        total_valid_subs += valid_count

        if dirty_count == 0:
            clean_keys_count += 1
        else:
            action_type = "DELETE_KEY" if valid_count == 0 else "TRIM_RECORDS"

            if action_type == "DELETE_KEY":
                deleted_keys_count += 1
                if not dry_run:
                    requests.post(url, headers=headers, json=["DEL", key], timeout=10)
            else:
                repaired_keys_count += 1
                if not dry_run:
                    requests.post(
                        url,
                        headers=headers,
                        json=["SET", key, json.dumps(clean_records, ensure_ascii=False)],
                        timeout=10
                    )

            # Sediakan data untuk cache audit SQLite
            dirty_json_str = json.dumps(dirty_records, ensure_ascii=False)
            audit_records_db.append((
                acc_idx, key, imdb_id, total_before, valid_count, dirty_count,
                action_type, dirty_json_str, mode_label
            ))

            detailed_dirty_reports.append({
                "shard_index": acc_idx,
                "redis_key": key,
                "imdb_id": imdb_id,
                "action": action_type,
                "total_before": total_before,
                "valid_retained": valid_count,
                "dirty_removed": dirty_count,
                "dirty_samples": dirty_records
            })

        progress.update(task_id, advance=1)

    return {
        "shard_index": acc_idx,
        "total_keys": total_keys,
        "clean_keys": clean_keys_count,
        "repaired_keys": repaired_keys_count,
        "deleted_keys": deleted_keys_count,
        "total_dirty_subs": total_dirty_subs,
        "total_valid_subs": total_valid_subs,
        "db_rows": audit_records_db,
        "json_reports": detailed_dirty_reports
    }

# ==============================================================================
# ALIRAN UTAMA
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="Enjin Pembersihan Surgical 10 Shard Upstash Redis")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Laksanakan pembersihan dan pemadaman SEBENAR di Redis. (Jika tidak ditaip, mod DRY-RUN aktif secara lalai)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Paksa mod simulasi audit tanpa sebarang perubahan fizikal di Redis."
    )
    args = parser.parse_args()

    # Mod selamat: Dry-run melainkan pengguna menetapkan --execute secara eksplisit
    is_dry_run = not args.execute

    console.print()
    if is_dry_run:
        banner = Panel.fit(
            "[bold yellow]🛡️ MOD SIMULASI: DRY-RUN AKTIF (TIADA PERUBAHAN DITULIS)[/bold yellow]\n"
            "├─ Objektif : Mengaudit URL sarikata rosak/berspasi merentasi 10 Shard Redis\n"
            "├─ Output   : Rekod dicache ke SQLite & eksport terperinci ke JSON\n"
            "└─ Nota     : Untuk laksanakan pemadaman sebenar kelak, gunakan flag [bold cyan]--execute[/bold cyan]",
            title="[bold cyan]Redis Surgical Audit[/bold cyan]",
            border_style="yellow"
        )
    else:
        banner = Panel.fit(
            "[bold red]⚡ MOD SEBENAR: LIVE EXECUTION (PENGUBAHAN AKTIF)[/bold red]\n"
            "├─ Objektif : Memadam sarikata rosak dan mengemaskini kekunci di 10 Redis Utama\n"
            "└─ Perhatian: Perubahan ini adalah kekal pada Upstash Redis!",
            title="[bold red]Redis Surgical Cleaner[/bold red]",
            border_style="red"
        )
    console.print(banner)

    redis_accounts = getattr(_config, "REDIS_ACCOUNTS", [])
    if not redis_accounts:
        console.print("[bold red]❌ Tiada akaun Redis ditemui dalam 00_config.py![/bold red]")
        sys.exit(1)

    # Kosongkan cache audit SQLite larian sebelumnya untuk data segar
    conn = init_audit_db(AUDIT_DB_PATH)
    conn.execute("DELETE FROM redis_sweep_audit;")
    conn.commit()

    all_shard_results = []
    master_dirty_reports = []

    # Paparan Progress Bar Kustom Rich
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}[/bold cyan]"),
        BarColumn(bar_width=40),
        TextColumn("[bold yellow]{task.completed}/{task.total} kunci[/bold yellow]"),
        TimeElapsedColumn(),
        console=console
    ) as progress:

        for acc in redis_accounts:
            t_id = progress.add_task(f"Mengaudit Shard #{acc['index']}...", total=100)
            res = process_shard(acc, dry_run=is_dry_run, progress=progress, task_id=t_id)
            all_shard_results.append(res)

            # Simpan rekod audit ke SQLite
            if res["db_rows"]:
                conn.executemany("""
                    INSERT INTO redis_sweep_audit 
                    (shard_index, redis_key, imdb_id, total_subs_before, total_subs_valid, total_subs_dirty, action_planned, dirty_records_json, mode)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, res["db_rows"])
                conn.commit()

            # Kumpulkan sampel JSON
            master_dirty_reports.extend(res["json_reports"])

    # Jalankan checkpoint WAL untuk pastikan fail SQLite bersih & padat
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.close()

    # Eksport laporan terperinci ke JSON
    summary_report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "DRY_RUN" if is_dry_run else "LIVE_EXECUTION",
        "total_shards": len(redis_accounts),
        "total_keys_scanned": sum(r["total_keys"] for r in all_shard_results),
        "total_clean_keys": sum(r["clean_keys"] for r in all_shard_results),
        "total_keys_to_repair": sum(r["repaired_keys"] for r in all_shard_results),
        "total_keys_to_delete": sum(r["deleted_keys"] for r in all_shard_results),
        "total_dirty_subs_found": sum(r["total_dirty_subs"] for r in all_shard_results),
        "total_valid_subs_retained": sum(r["total_valid_subs"] for r in all_shard_results),
        "affected_entries": master_dirty_reports
    }

    with open(JSON_REPORT_PATH, "w", encoding="utf-8") as jf:
        json.dump(summary_report, jf, indent=2, ensure_ascii=False)

    # ==============================================================================
    # JADUAL RINGKASAN TERMINAL KEMAS & PROFESIONAL
    # ==============================================================================
    table = Table(
        title=f"📊 Status 10 Shard Upstash Redis ({'DRY-RUN' if is_dry_run else 'LIVE'})",
        border_style="cyan",
        header_style="bold magenta"
    )
    table.add_column("Akaun Shard", style="yellow", justify="center")
    table.add_column("Kunci Diimbas", justify="right", style="white")
    table.add_column("Kunci Sempurna", justify="right", style="green")
    table.add_column("Kunci Dipangkas", justify="right", style="cyan")
    table.add_column("Kunci Dihapus", justify="right", style="red")
    table.add_column("Sarikata Rosak", justify="right", style="bold red")
    table.add_column("Sarikata Sah", justify="right", style="bold green")

    tot_scanned = 0
    tot_clean = 0
    tot_repaired = 0
    tot_deleted = 0
    tot_dirty = 0
    tot_valid = 0

    for r in all_shard_results:
        tot_scanned += r["total_keys"]
        tot_clean += r["clean_keys"]
        tot_repaired += r["repaired_keys"]
        tot_deleted += r["deleted_keys"]
        tot_dirty += r["total_dirty_subs"]
        tot_valid += r["total_valid_subs"]

        table.add_row(
            f"Shard #{r['shard_index']}",
            f"{r['total_keys']:,}",
            f"{r['clean_keys']:,}",
            f"{r['repaired_keys']:,}" if r['repaired_keys'] > 0 else "[dim]0[/dim]",
            f"{r['deleted_keys']:,}" if r['deleted_keys'] > 0 else "[dim]0[/dim]",
            f"[bold red]{r['total_dirty_subs']:,}[/bold red]" if r['total_dirty_subs'] > 0 else "[dim]0[/dim]",
            f"[bold green]{r['total_valid_subs']:,}[/bold green]"
        )

    table.add_section()
    table.add_row(
        "[bold white]JUMLAH[/bold white]",
        f"[bold white]{tot_scanned:,}[/bold white]",
        f"[bold green]{tot_clean:,}[/bold green]",
        f"[bold cyan]{tot_repaired:,}[/bold cyan]",
        f"[bold red]{tot_deleted:,}[/bold red]",
        f"[bold red]{tot_dirty:,}[/bold red]",
        f"[bold green]{tot_valid:,}[/bold green]"
    )

    console.print("\n", table, "\n")

    # ==============================================================================
    # PANEL KEPUTUSAN & PETUNJUK FAIL
    # ==============================================================================
    status_title = "✨ LAPORAN SIMULASI SELESAI (DRY-RUN)" if is_dry_run else "✅ PEMBERSIHAN SEBENAR SELESAI"
    theme_color = "yellow" if is_dry_run else "green"

    console.print(Panel.fit(
        f"[bold {theme_color}]{status_title}[/bold {theme_color}]\n"
        f"├─ Kunci Diimbas Sepenuhnya     : [white]{tot_scanned:,}[/white] kunci\n"
        f"├─ Kunci Selamat & Bersih       : [green]{tot_clean:,}[/green] kunci\n"
        f"├─ Kunci Dibaiki (Pangkas Rosak): [cyan]{tot_repaired:,}[/cyan] kunci\n"
        f"├─ Kunci Kosong (Dihapus Habis) : [magenta]{tot_deleted:,}[/magenta] kunci\n"
        f"├─ Bilangan URL Rosak Dikesan   : [bold red]{tot_dirty:,}[/bold red] sarikata\n"
        f"├─ Sarikata Sah Dikekalkan      : [bold green]{tot_valid:,}[/bold green] fail\n"
        f"├─ Cache SQLite Audit (.db)     : [cyan]{AUDIT_DB_PATH}[/cyan]\n"
        f"└─ Laporan Terperinci (.json)   : [cyan]{JSON_REPORT_PATH}[/cyan]\n\n"
        f"[dim white]"
        f"{'👉 Tiada data diubah. Jika berpuas hati, jalankan: python live_engine/13_sweep_clean_redis.py --execute' if is_dry_run else '👉 Semua perubahan telah selamat dikemaskini ke 10 Shard Redis!'}"
        f"[/dim white]",
        border_style=theme_color
    ))

if __name__ == "__main__":
    main()