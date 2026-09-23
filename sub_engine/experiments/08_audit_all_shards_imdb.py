#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO SUB ADDON - 10-SHARD REDIS KEY AUDITOR
# LOKASI: sub_engine/experiments/08_audit_all_shards_imdb.py
# FUNGSI: Mengimbas kesemua 10 akaun Redis untuk mengesan sebaran kunci IMDb
# ==============================================================================

import sys
import json
import argparse
import importlib
from pathlib import Path
from urllib.parse import quote
from curl_cffi import requests
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
except ImportError as e:
    console.print(f"[bold red]❌ Ralat memuatkan modul 01_redis_db: {e}[/bold red]")
    sys.exit(1)


def audit_shards(imdb_target: str = "tt1199099"):
    clean_target = imdb_target.strip().replace("subs:", "").replace("sub:", "")
    base_id = clean_target.split(":")[0]

    accounts = getattr(_redis, "REDIS_ACCOUNTS", [])
    if not accounts:
        console.print("[bold red]❌ Tiada senarai REDIS_ACCOUNTS ditemui di dalam 01_redis_db.[/bold red]")
        return

    # Kira shard rasmi berdasarkan fungsi get_shard_index
    expected_shard = 1
    if hasattr(_redis, "get_redis_shard_index"):
        expected_shard = _redis.get_redis_shard_index(base_id)
    elif hasattr(_redis, "get_shard_index"):
        expected_shard = _redis.get_shard_index(base_id)

    console.print(Panel.fit(
        f"[bold cyan]🔍 AUDIT KUNCI REDIS MERENTASI 10 SHARD[/bold cyan]\n"
        f"Sasaran IMDb ID      : [bold yellow]{clean_target}[/bold yellow] (Base: [white]{base_id}[/white])\n"
        f"Shard Sasaran Rasmi  : [bold green]Shard #{expected_shard}[/bold green]\n"
        f"Jumlah Shard Diimbas : [magenta]{len(accounts)} Akaun Upstash[/magenta]",
        border_style="cyan"
    ))

    table = Table(title=f"📋 Taburan Kunci '{clean_target}' di Upstash Redis", border_style="cyan")
    table.add_column("Shard", justify="center", style="bold cyan", width=8)
    table.add_column("Hos Upstash", style="white", width=30)
    table.add_column("Status", justify="center", width=14)
    table.add_column("Kunci Ditemui", style="yellow")
    table.add_column("Jumlah Item", justify="center", style="magenta", width=12)

    total_keys_found = 0
    shards_with_keys = []

    for acc in accounts:
        idx = acc.get("index", 1)
        url = (acc.get("url") or acc.get("redis_rest_url") or "").rstrip("/")
        token = acc.get("token") or acc.get("redis_rest_token") or ""
        host_label = url.replace("https://", "").split(".")[0]

        if not url or not token:
            table.add_row(f"#{idx}", host_label, "[red]NO CONFIG[/red]", "-", "-")
            continue

        headers = {"Authorization": f"Bearer {token}"}
        scan_pattern = f"*{base_id}*"
        keys_endpoint = f"{url}/keys/{quote(scan_pattern)}"

        found_keys = []
        try:
            resp = requests.get(keys_endpoint, headers=headers, timeout=10)
            if resp.status_code == 200:
                res_data = resp.json()
                found_keys = res_data.get("result", []) or []
        except Exception as err:
            table.add_row(f"#{idx}", host_label, "[dim red]ERROR[/dim red]", str(err)[:30], "-")
            continue

        if found_keys:
            total_keys_found += len(found_keys)
            shards_with_keys.append(idx)
            
            # Ambil perincian saiz kandungan bagi setiap kunci
            key_details = []
            item_counts = []
            for k in sorted(found_keys):
                get_ep = f"{url}/get/{quote(k)}"
                try:
                    g_res = requests.get(get_ep, headers=headers, timeout=8)
                    if g_res.status_code == 200:
                        val = g_res.json().get("result")
                        if val:
                            parsed_val = json.loads(val) if isinstance(val, str) else val
                            c = len(parsed_val) if isinstance(parsed_val, list) else 1
                            key_details.append(f"[bold white]{k}[/bold white] ({c} subs)")
                            item_counts.append(c)
                        else:
                            key_details.append(k)
                            item_counts.append(0)
                    else:
                        key_details.append(k)
                except Exception:
                    key_details.append(k)

            status_style = "[bold green]ADA KUNCI[/bold green]" if idx == expected_shard else "[bold yellow]ADA (SELEWENG)[/bold yellow]"
            table.add_row(
                f"#{idx}",
                host_label,
                status_style,
                "\n".join(key_details),
                str(sum(item_counts))
            )
        else:
            table.add_row(
                f"#{idx}",
                host_label,
                "[dim]KOSONG[/dim]",
                "[dim]-[/dim]",
                "[dim]0[/dim]"
            )

    console.print(table)

    # Rumusan diagnostik
    if len(shards_with_keys) == 1 and shards_with_keys[0] == expected_shard:
        console.print(f"\n[bold green]✅ SEMPURNA: Kunci hanya wujud pada Shard rasmi #{expected_shard}. Tiada data berterabur![/bold green]")
    elif len(shards_with_keys) == 1 and shards_with_keys[0] != expected_shard:
        console.print(f"\n[bold yellow]⚠️ PERHATIAN: Kunci hanya wujud di Shard #{shards_with_keys[0]}, tetapi formula modulo menyasarkan Shard #{expected_shard}.[/bold yellow]")
    elif len(shards_with_keys) > 1:
        console.print(f"\n[bold red]🚨 ISU BERTERABUR DIKESAN: Kunci IMDb ini bertaburan di {len(shards_with_keys)} shard berbeza: {shards_with_keys}![/bold red]")
        console.print("[dim]Ini berlaku jika ada skrip yang guna Shard #1 sebagai lalai manakala skrip lain guna formula Modulo.[/dim]")
    else:
        console.print(f"\n[yellow]ℹ️ Tiada sebarang kunci dijumpai untuk {clean_target} di mana-mana 10 Shard.[/yellow]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit Taburan Kunci IMDb di 10 Shard Upstash Redis")
    parser.add_argument("--imdb", default="tt1199099", help="IMDb target (lalai: tt1199099)")
    args = parser.parse_args()

    audit_shards(args.imdb)