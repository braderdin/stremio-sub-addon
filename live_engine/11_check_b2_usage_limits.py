#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO LIVE ENGINE - B2 MULTI-ACCOUNT USAGE & LIMIT MONITOR
# LOKASI: /home/braderdin/stremio-sub-addon/live_engine/11_check_b2_usage_limits.py
# ==============================================================================

import sys
import os
import time
import importlib
from pathlib import Path
from typing import Dict, List, Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress_bar import ProgressBar

console = Console()

LIVE_ENGINE_PATH = Path(__file__).resolve().parent
if str(LIVE_ENGINE_PATH) not in sys.path:
    sys.path.insert(0, str(LIVE_ENGINE_PATH))

try:
    _config = importlib.import_module("00_config")
    _b2_storage = importlib.import_module("02_b2_storage")
except ImportError as e:
    console.print(f"[bold red]❌ Ralat mengimport modul konfigurasi B2: {e}[/bold red]")
    sys.exit(1)

B2_ACCOUNTS = getattr(_config, "B2_ACCOUNTS", [])
MAX_BYTES_PER_ACCOUNT = getattr(_config, "B2_MAX_BYTES_PER_ACCOUNT", int(9.5 * 1024 * 1024 * 1024))
FREE_TIER_CEILING_BYTES = int(10.0 * 1024 * 1024 * 1024)  # Had mutlak Backblaze Free-Tier (10 GB)

def format_size(bytes_val: int) -> str:
    """Menukar nilai bait kepada format mudah baca (KB, MB, GB)."""
    if bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} KB"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024):.2f} MB"
    else:
        return f"{bytes_val / (1024 * 1024 * 1024):.3f} GB"

def scan_all_b2_accounts():
    total_accounts = len(B2_ACCOUNTS)
    if total_accounts == 0:
        console.print("[bold red]❌ Tiada akaun B2 dikesan dalam fail konfigurasi![/bold red]")
        return

    console.print(Panel.fit(
        f"[bold cyan]🔍 BACKBLAZE B2 MULTI-ACCOUNT STORAGE & LIMIT CHECKER[/bold cyan]\n"
        f"[yellow]Sistem {total_accounts} Akaun Round-Robin | Ambang Had Selamat: 9.5 GB / Akaun[/yellow]\n"
        f"[white]Kapasiti Keseluruhan: {format_size(total_accounts * MAX_BYTES_PER_ACCOUNT)} | Had Percuma: 10 GB / Akaun[/white]",
        title="Stremio Sub-Addon Diagnostics", border_style="cyan"
    ))

    console.print(f"[cyan]Mengimbas saiz storan dan fail aktif bagi kesemua {total_accounts} akaun B2...[/cyan]\n")

    account_stats = []
    total_sys_files = 0
    total_sys_bytes = 0

    for acc in B2_ACCOUNTS:
        idx = acc["index"]
        bucket_name = acc["bucket_name"]

        start_t = time.perf_counter()
        file_count = 0
        used_bytes = 0
        is_online = True
        err_msg = ""

        try:
            b2_api = _b2_storage._get_b2_api(acc)
            bucket = b2_api.get_bucket_by_name(bucket_name)

            for file_version, _ in bucket.ls(recursive=True):
                file_count += 1
                used_bytes += file_version.size

        except Exception as e:
            is_online = False
            err_msg = str(e)

        elapsed = (time.perf_counter() - start_t) * 1000
        pct_used = (used_bytes / MAX_BYTES_PER_ACCOUNT) * 100

        total_sys_files += file_count
        total_sys_bytes += used_bytes

        if not is_online:
            status_text = "[bold red]RALAT / CAP[/bold red]"
        elif used_bytes >= MAX_BYTES_PER_ACCOUNT:
            status_text = "[bold red]ZON MERAH (PENUH)[/bold red]"
        elif pct_used > 70.0:
            status_text = "[bold yellow]ZON KUNING[/bold yellow]"
        else:
            status_text = "[bold green]ZON HIJAU (AKTIF)[/bold green]"

        account_stats.append({
            "index": idx,
            "bucket_name": bucket_name,
            "online": is_online,
            "files": file_count,
            "bytes": used_bytes,
            "pct": pct_used,
            "latency_ms": elapsed,
            "status": status_text,
            "error": err_msg
        })

    # =========================================================================
    # JADUAL 1: PERINCIAN SETIAP AKAUN B2
    # =========================================================================
    table = Table(title=f"📊 Status Kapasiti Storan {total_accounts} Akaun Backblaze B2", border_style="cyan")
    table.add_column("Akaun", justify="center", style="cyan", no_wrap=True)
    table.add_column("Nama Bucket", style="white")
    table.add_column("Masa Semak", justify="right", style="dim")
    table.add_column("Jumlah Fail", justify="right", style="yellow")
    table.add_column("Storan Digunakan", justify="right", style="white")
    table.add_column("Peratus Had 9.5GB", justify="center", style="white")
    table.add_column("Status Akaun", justify="center")

    for a in account_stats:
        if not a["online"]:
            table.add_row(
                f"#{a['index']}", a["bucket_name"], "N/A", "0", "N/A", "0.0%", a["status"]
            )
            continue

        table.add_row(
            f"#{a['index']}",
            a["bucket_name"],
            f"{a['latency_ms']:.0f} ms",
            f"{a['files']:,} fail",
            format_size(a["bytes"]),
            f"{a['pct']:.2f}%",
            a["status"]
        )

    console.print(table)

    # =========================================================================
    # JADUAL 2: RINGKASAN KESELURUHAN SISTEM B2
    # =========================================================================
    sys_total_safe_bytes = total_accounts * MAX_BYTES_PER_ACCOUNT
    sys_pct_used = (total_sys_bytes / sys_total_safe_bytes) * 100
    baki_safe_bytes = max(0, sys_total_safe_bytes - total_sys_bytes)

    summary_table = Table(title="🌐 Ringkasan Agregat Storan B2 (Keseluruhan)", border_style="green")
    summary_table.add_column("Metrik Keseluruhan", style="cyan", no_wrap=True)
    summary_table.add_column("Nilai Semasa", style="white")
    summary_table.add_column("Kapasiti Had Sistem", style="white")
    summary_table.add_column("Keterangan", style="white")

    summary_table.add_row(
        "Jumlah Fail Tersimpan",
        f"[bold yellow]{total_sys_files:,} fail[/bold yellow]",
        "Termasuk ZIP & SRT",
        "Merentasi semua akaun B2 aktif"
    )
    summary_table.add_row(
        "Jumlah Storan Digunakan",
        f"[bold green]{format_size(total_sys_bytes)}[/bold green]",
        f"Had Selamat: {format_size(sys_total_safe_bytes)}",
        f"{sys_pct_used:.2f}% digunakan daripada kuota 9.5GB"
    )
    summary_table.add_row(
        "Baki Storan Selamat Boleh Guna",
        f"[bold cyan]{format_size(baki_safe_bytes)}[/bold cyan]",
        f"Maksimum: {format_size(total_accounts * FREE_TIER_CEILING_BYTES)}",
        "Kiraan sebelum mencecah had 9.5GB per akaun"
    )
    summary_table.add_row(
        "Akaun Berfungsi / Aktif",
        f"{sum(1 for a in account_stats if a['online'])} / {total_accounts} akaun",
        "Round-Robin Dinamik",
        "[green]Sedia menerima fail on-demand[/green]"
    )

    console.print(summary_table)

    console.print(f"\n[bold]Kemajuan Storan Keseluruhan ({format_size(total_sys_bytes)} / {format_size(sys_total_safe_bytes)}):[/bold]")
    bar_storage = ProgressBar(total=100, completed=min(100.0, sys_pct_used), width=50)
    console.print(bar_storage)
    console.print()

if __name__ == "__main__":
    scan_all_b2_accounts()