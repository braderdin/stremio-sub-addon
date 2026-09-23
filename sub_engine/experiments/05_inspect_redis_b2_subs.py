#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO SUB ADDON - REDIS & B2 SUBTITLE INSPECTOR
# LOKASI: /home/braderdin/stremio-sub-addon/sub_engine/experiments/05_inspect_redis_b2_subs.py
# FUNGSI: Memeriksa rekod sarikata di Redis Shard & fail fizikal di B2
# ==============================================================================

import sys
import json
import argparse
import importlib
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

EXPERIMENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EXPERIMENT_DIR.parent.parent
LIVE_ENGINE_DIR = PROJECT_ROOT / "live_engine"

if str(LIVE_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(LIVE_ENGINE_DIR))

try:
    _redis = importlib.import_module("01_redis_db")
    _b2 = importlib.import_module("02_b2_storage")
    _config = importlib.import_module("00_config")
except ImportError as e:
    console.print(f"[bold red]❌ Ralat memuatkan modul live_engine: {e}[/bold red]")
    sys.exit(1)


def inspect_redis_and_b2(imdb_id: str):
    clean_id = imdb_id.strip()
    base_id = clean_id.split(":")[0]

    console.print(Panel.fit(
        f"[bold cyan]🔍 DIAGNOSTIK KANDUNGAN REDIS & B2 UNTUK SASARAN:[/bold cyan] [bold yellow]{clean_id}[/bold yellow]\n"
        f"Base IMDb ID : [white]{base_id}[/white]",
        border_style="cyan"
    ))

    # =========================================================================
    # 1. SEMAK KUNCI DI UPSTASH REDIS
    # =========================================================================
    keys_to_check = [f"subs:{clean_id}"]
    if ":" in clean_id:
        keys_to_check.append(f"subs:{base_id}")

    console.print("[bold yellow]1. Memeriksa Pangkalan Data Upstash Redis...[/bold yellow]")

    found_any_redis = False
    for k in keys_to_check:
        clean_key = k.replace("subs:", "")
        
        # Cuba dapatkan Shard Index
        shard_idx = 1
        if hasattr(_redis, "get_redis_shard_index"):
            shard_idx = _redis.get_redis_shard_index(clean_key)
        elif hasattr(_redis, "get_shard_index"):
            shard_idx = _redis.get_shard_index(clean_key)
            
        raw_val = _redis.get_subtitle_records(clean_key)

        if raw_val:
            found_any_redis = True
            table = Table(title=f"📋 Kunci Redis: [green]{k}[/green] (Shard #{shard_idx}) | Jumlah: {len(raw_val)} Sarikata", border_style="green")
            table.add_column("No", justify="center", style="cyan", width=4)
            table.add_column("Bahasa", justify="center", style="magenta", width=8)
            table.add_column("Punca", justify="center", style="yellow", width=14)
            table.add_column("Nama Versi / Release", style="white")
            table.add_column("Akaun B2", justify="center", style="green", width=10)
            table.add_column("URL Strim B2", style="dim")

            for idx, item in enumerate(raw_val, 1):
                table.add_row(
                    str(idx),
                    str(item.get("lang", "")).upper(),
                    str(item.get("source", "-")),
                    str(item.get("release", "-"))[:45],
                    f"Acc #{item.get('acc', 1)}",
                    str(item.get("url", ""))[:50] + "..."
                )
            console.print(table)
        else:
            console.print(f"[dim red]❌ Kunci '{k}' KOSONG atau TIADA rekod di Shard #{shard_idx}.[/dim red]")

    # =========================================================================
    # 2. SEMAK FAIL FIZIKAL DI BACKBLAZE B2
    # =========================================================================
    console.print(f"\n[bold yellow]2. Memeriksa Storan Fail Fizikal Backblaze B2 (subs/{base_id}/)...[/bold yellow]")
    b2_prefix = f"subs/{base_id}/"

    b2_files_found = []
    b2_accounts = getattr(_config, "B2_ACCOUNTS", [])

    for acc in b2_accounts:
        idx = acc.get("index", 1)
        b_name = acc.get("bucket_name", "")
        try:
            b2_api = _b2._get_b2_api(acc)
            bucket = b2_api.get_bucket_by_name(b_name)
            for file_version, _ in bucket.ls(folder_to_list=b2_prefix, recursive=True):
                b2_files_found.append({
                    "acc": idx,
                    "bucket": b_name,
                    "filename": file_version.file_name,
                    "size": file_version.size
                })
        except Exception:
            pass

    if b2_files_found:
        b2_table = Table(title=f"📦 Fail Fizikal Ditemui di Baldi B2 ({len(b2_files_found)} fail)", border_style="cyan")
        b2_table.add_column("No", justify="center", style="cyan", width=4)
        b2_table.add_column("Akaun B2", justify="center", style="green", width=12)
        b2_table.add_column("Nama Fail di B2", style="white")
        b2_table.add_column("Saiz", justify="right", style="yellow", width=12)

        for i, f in enumerate(b2_files_found, 1):
            sz_kb = f["size"] / 1024
            b2_table.add_row(
                str(i),
                f"Akaun #{f['acc']}",
                f["filename"].replace(b2_prefix, ""),
                f"{sz_kb:.1f} KB"
            )
        console.print(b2_table)
    else:
        console.print(f"[dim red]❌ Tiada fail fizikal ditemui di B2 di bawah folder '{b2_prefix}'.[/dim red]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Semak Sarikata di Redis dan B2")
    parser.add_argument("--imdb", default="tt1199099:1:2", help="Target IMDb ID (cth: tt1199099 atau tt1199099:1:2)")
    args = parser.parse_args()

    inspect_redis_and_b2(args.imdb)