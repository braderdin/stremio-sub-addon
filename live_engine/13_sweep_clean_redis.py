#!/usr/bin/env python3
# ==============================================================================
# PROJEK: PEMBERSIH SURGICAL 10 SHARD REDIS (HAPUS URL BERSPASI SAHAJA)
# LOKASI: /home/braderdin/stremio-sub-addon/live_engine/sweep_clean_redis.py
# ==============================================================================

import os
import sys
import json
import importlib
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

LIVE_ENGINE_DIR = Path(__file__).resolve().parent
if str(LIVE_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(LIVE_ENGINE_DIR))

try:
    _config = importlib.import_module("00_config")
    _redis = importlib.import_module("01_redis_db")
except ImportError as e:
    console.print(f"[bold red]❌ Ralat import modul: {e}[/bold red]")
    sys.exit(1)

from curl_cffi import requests

def scan_all_keys_in_account(acc: dict, pattern: str = "sub:*") -> list:
    """Mengambil semua senarai kunci yang sepadan menggunakan cursor SCAN."""
    url = acc.get("url", "").rstrip("/")
    token = acc.get("token", "")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    all_keys = []
    cursor = "0"

    while True:
        payload = ["SCAN", cursor, "MATCH", pattern, "COUNT", "500"]
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=15)
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

    return all_keys

def clean_shard(acc: dict):
    acc_idx = acc["index"]
    console.print(f"🔍 [bold cyan]Mengimbas Akaun Redis Shard #{acc_idx}...[/bold cyan]")

    keys = scan_all_keys_in_account(acc, "sub:*")
    if not keys:
        console.print(f"   [dim]Tiada kunci 'sub:*' ditemui di Shard #{acc_idx}.[/dim]")
        return 0, 0, 0

    repaired_keys = 0
    deleted_keys = 0
    cleaned_items_count = 0

    url = acc.get("url", "").rstrip("/")
    token = acc.get("token", "")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    for key in keys:
        # 1. Baca nilai kunci
        try:
            resp = requests.post(url, headers=headers, json=["GET", key], timeout=10)
            if resp.status_code != 200:
                continue
            raw_val = resp.json().get("result")
            if not raw_val:
                continue

            records = json.loads(raw_val) if isinstance(raw_val, str) else raw_val
            if not isinstance(records, list):
                continue
        except Exception:
            continue

        clean_records = []
        dirty_in_key = 0

        # 2. Tapis rekod rosak
        for r in records:
            if not isinstance(r, dict):
                continue
            sub_url = str(r.get("url", ""))

            # Kriteria URL rosak: Ada ruang kosong ATAU tidak bermula dengan http
            if " " in sub_url or not sub_url.startswith("http"):
                dirty_in_key += 1
                cleaned_items_count += 1
            else:
                clean_records.append(r)

        # 3. Ambil tindakan jika ada rekod rosak dikesan
        if dirty_in_key > 0:
            if not clean_records:
                # Jika SEMUA rekod dalam kunci itu rosak, padamkan kunci sepenuhnya
                requests.post(url, headers=headers, json=["DEL", key], timeout=10)
                deleted_keys += 1
            else:
                # Jika ada baki rekod yang masih elok, simpan senarai bersih
                requests.post(
                    url,
                    headers=headers,
                    json=["SET", key, json.dumps(clean_records, ensure_ascii=False)],
                    timeout=10
                )
                repaired_keys += 1

    return len(keys), repaired_keys, deleted_keys, cleaned_items_count

def main():
    console.print(Panel.fit(
        "[bold green]🧹 ENJIN PEMBERSIHAN PINTAR REDIS (SURGICAL CLEAN)[/bold green]\n"
        "[white]Operasi:[/white] Menyingkirkan sarikata ber-URL ruang kosong sahaja tanpa mengusik fail sah.",
        border_style="green"
    ))

    redis_accounts = _config.REDIS_ACCOUNTS
    if not redis_accounts:
        console.print("[bold red]❌ Tiada akaun Redis ditemui dalam konfigurasi![/bold red]")
        sys.exit(1)

    table = Table(title="Laporan Pembersihan 10 Shard Redis", border_style="cyan")
    table.add_column("Shard", style="yellow")
    table.add_column("Jumlah Kunci Diimbas", justify="right", style="white")
    table.add_column("Kunci Dibaiki (Dipangkas)", justify="right", style="cyan")
    table.add_column("Kunci Kosong Dihapus", justify="right", style="magenta")
    table.add_column("Jumlah Sarikata Rosak Dibuang", justify="right", style="green")

    total_scanned = 0
    total_repaired = 0
    total_deleted = 0
    total_cleaned_items = 0

    for acc in redis_accounts:
        res = clean_shard(acc)
        if len(res) == 4:
            scanned, repaired, deleted, items = res
            total_scanned += scanned
            total_repaired += repaired
            total_deleted += deleted
            total_cleaned_items += items

            table.add_row(
                f"Redis #{acc['index']}",
                f"{scanned:,}",
                f"{repaired:,}",
                f"{deleted:,}",
                f"[bold red]{items:,}[/bold red]" if items > 0 else "0"
            )

    console.print("\n", table)
    console.print(Panel.fit(
        f"[bold green]✨ PEMBERSIHAN SELESAI[/bold green]\n"
        f"├─ Kunci Diimbas            : [yellow]{total_scanned:,}[/yellow]\n"
        f"├─ Kunci Dibaiki            : [cyan]{total_repaired:,}[/cyan]\n"
        f"├─ Kunci Kosong Dihapus     : [magenta]{total_deleted:,}[/magenta]\n"
        f"└─ Sarikata Rosak Disingkir : [bold green]{total_cleaned_items:,} fail[/bold green] (Sarikata sah kekal selamat)",
        border_style="green"
    ))

if __name__ == "__main__":
    main()