#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO SUB ADDON - REDIS METADATA DUMP INSPECTOR
# LOKASI: /home/braderdin/stremio-sub-addon/sub_engine/experiments/06_dump_redis_metadata.py
# FUNGSI: Memaparkan keseluruhan metadata JSON mentah bagi kunci siri/filem
# ==============================================================================

import sys
import json
import argparse
import importlib
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

console = Console()

EXPERIMENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EXPERIMENT_DIR.parent.parent
LIVE_ENGINE_DIR = PROJECT_ROOT / "live_engine"

if str(LIVE_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(LIVE_ENGINE_DIR))

try:
    _redis = importlib.import_module("01_redis_db")
except ImportError as e:
    console.print(f"[bold red]❌ Ralat memuatkan modul 01_redis_db: {e}[/bold red]")
    sys.exit(1)


def dump_redis_metadata(imdb_id: str, highlight_index: int = 21):
    clean_id = imdb_id.strip().replace("subs:", "")

    shard_idx = 1
    if hasattr(_redis, "get_redis_shard_index"):
        shard_idx = _redis.get_redis_shard_index(clean_id)
    elif hasattr(_redis, "get_shard_index"):
        shard_idx = _redis.get_shard_index(clean_id)

    console.print(Panel.fit(
        f"[bold cyan]🔍 DUMP METADATA REDIS:[/bold cyan] [bold yellow]subs:{clean_id}[/bold yellow] (Shard #{shard_idx})\n"
        f"Fokus Sorotan : [magenta]Entri No. #{highlight_index}[/magenta]",
        border_style="cyan"
    ))

    # Ambil data mentah dari Redis
    records = _redis.get_subtitle_records(clean_id)

    if not records:
        console.print(f"[bold red]❌ Kunci 'subs:{clean_id}' kosong atau tiada dalam Shard #{shard_idx}![/bold red]")
        return

    console.print(f"[green]✔ Berjaya membaca {len(records)} rekod sarikata daripada Upstash Redis.[/green]\n")

    # 1. SOROTAN KHAS NO. 21 (ATAU INDEKS PILIHAN)
    if 1 <= highlight_index <= len(records):
        target_item = records[highlight_index - 1]
        target_json_str = json.dumps(target_item, indent=2, ensure_ascii=False)

        console.print(Panel(
            Syntax(target_json_str, "json", theme="monokai", line_numbers=True),
            title=f"🎯 [bold yellow]METADATA PENUH ENTRI NO. #{highlight_index} ({target_item.get('release', 'Tanpa Nama')})[/bold yellow]",
            border_style="yellow"
        ))
    else:
        console.print(f"[yellow]⚠️ Indeks #{highlight_index} di luar julat (1 hingga {len(records)}).[/yellow]")

    # 2. PAPARAN KESELURUHAN SENARAI JSON (SEMUA 69 ENTRI)
    console.print(f"\n[bold cyan]📋 KANDUNGAN LENGKAP KESEMUA {len(records)} ENTRI JSON:[/bold cyan]")
    full_json_str = json.dumps(records, indent=2, ensure_ascii=False)
    console.print(Syntax(full_json_str, "json", theme="monokai", line_numbers=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dump Metadata Redis JSON Lengkap")
    parser.add_argument("--imdb", default="tt1199099", help="Target IMDb ID (lalai: tt1199099)")
    parser.add_argument("--index", type=int, default=21, help="Nombor baris entri yang ingin disorot (lalai: 21)")
    args = parser.parse_args()

    dump_redis_metadata(args.imdb, highlight_index=args.index)