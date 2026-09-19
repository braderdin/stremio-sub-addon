#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO ONDEMAND - B2 MULTI-ACCOUNT UPLOADER & TG REDIS SYNC
# LOKASI: /home/braderdin/stremio-sub-addon/ondemand_title_packs/ondemand_engine/03_upload_title_packs_to_b2.py
# ==============================================================================

import os
import sys
import json
import time
import sqlite3
import argparse
import importlib
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List
from curl_cffi import requests
from dotenv import dotenv_values

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

console = Console()

# ==============================================================================
# IMPORT MODUL LIVE ENGINE (B2 STORAGE)
# ==============================================================================
LIVE_ENGINE_PATH = "/home/braderdin/stremio-sub-addon/live_engine"
if LIVE_ENGINE_PATH not in sys.path:
    sys.path.insert(0, LIVE_ENGINE_PATH)

try:
    _config = importlib.import_module("00_config")
    _b2_storage = importlib.import_module("02_b2_storage")
except ImportError as e:
    console.print(f"[bold red]❌ Ralat mengimport modul live_engine: {e}[/bold red]")
    sys.exit(1)

# ==============================================================================
# KONFIGURASI DIREKTORI & PENGESAHAN PERSEKITARAN
# ==============================================================================
PROJECT_DIR = Path("/home/braderdin/stremio-sub-addon/ondemand_title_packs")
DATA_DIR = PROJECT_DIR / "data"
TEMP_DIR = PROJECT_DIR / "temp"
ZIP_DIR = PROJECT_DIR / "subtitles_by_title"

ENV_LOCAL_PATH = Path("/home/braderdin/stremio-sub-addon/.env.local")

PACKAGED_TRACKER_DB = DATA_DIR / "malay_packaged_tracker.db"
UPLOADED_TRACKER_DB = DATA_DIR / "malay_b2_uploaded_tracker.db"

# Muat turun pembolehubah TG Upstash Redis dari .env.local
env_vars = dotenv_values(str(ENV_LOCAL_PATH)) if ENV_LOCAL_PATH.exists() else {}
TG_REDIS_URL = (env_vars.get("TG_UPSTASH_REDIS_REST_URL") or os.getenv("TG_UPSTASH_REDIS_REST_URL", "")).rstrip("/")
TG_REDIS_TOKEN = env_vars.get("TG_UPSTASH_REDIS_REST_TOKEN") or os.getenv("TG_UPSTASH_REDIS_REST_TOKEN", "")

if not TG_REDIS_URL or not TG_REDIS_TOKEN:
    console.print(f"[bold red]❌ Ralat: Kunci TG_UPSTASH_REDIS tidak dijumpai di {ENV_LOCAL_PATH}![/bold red]")
    sys.exit(1)

# ==============================================================================
# INISIALISASI PANGKALAN DATA PENJEJAK MUAT NAIK
# ==============================================================================
def init_uploaded_tracker_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(UPLOADED_TRACKER_DB))
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS b2_uploaded_packs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            imdb_id TEXT UNIQUE NOT NULL,
            zip_filename TEXT NOT NULL,
            canonical_title TEXT NOT NULL,
            release_year TEXT,
            media_type TEXT NOT NULL,
            b2_url TEXT NOT NULL,
            bucket_name TEXT NOT NULL,
            account_index INTEGER NOT NULL,
            total_subs_count INTEGER NOT NULL,
            zip_size_bytes INTEGER NOT NULL,
            redis_synced INTEGER DEFAULT 0,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_b2_imdb ON b2_uploaded_packs (imdb_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_b2_zip ON b2_uploaded_packs (zip_filename);")
    conn.commit()
    return conn

# ==============================================================================
# FUNGSI MUAT NAIK FAIL ZIP BINARI KE MULTI-AKAUN B2 (ROUND-ROBIN)
# ==============================================================================
def upload_zip_pack_to_b2(b2_path: str, zip_bytes: bytes) -> Dict[str, Any]:
    """
    Memuat naik fail .zip ke B2 menggunakan putaran giliran seimbang (Round-Robin).
    Mengguna pakai konfigurasi akaun dan pemantauan had storan daripada 02_b2_storage.py.
    """
    if not _b2_storage.B2_ACCOUNTS:
        _b2_storage._all_accounts_exhausted_flag = True
        raise _b2_storage.AllB2AccountsExhaustedException("❌ Tiada akaun B2 dijumpai dalam persekitaran!")

    if _b2_storage.is_all_b2_exhausted():
        raise _b2_storage.AllB2AccountsExhaustedException("❌ Kesemua akaun B2 telah mencapai had transaksi atau storan penuh!")

    file_size = len(zip_bytes)
    total_accounts = len(_b2_storage.B2_ACCOUNTS)
    last_error = None

    for attempt in range(total_accounts):
        acc_list_index = (_b2_storage._current_account_pointer + attempt) % total_accounts
        acc = _b2_storage.B2_ACCOUNTS[acc_list_index]
        acc_idx = acc["index"]

        if acc_idx in _b2_storage._exhausted_accounts:
            continue

        # Semak saiz storan bucket (< 9.5 GB)
        used_bytes = _b2_storage.check_bucket_used_bytes(acc)
        if (used_bytes + file_size) >= _b2_storage.B2_MAX_BYTES_PER_ACCOUNT:
            _b2_storage._exhausted_accounts.add(acc_idx)
            continue

        try:
            b2_api = _b2_storage._get_b2_api(acc)
            bucket = b2_api.get_bucket_by_name(acc["bucket_name"])

            uploaded_file = bucket.upload_bytes(
                data_bytes=zip_bytes,
                file_name=b2_path,
                content_type="application/zip"
            )

            if acc_idx in _b2_storage._cached_bucket_bytes:
                _b2_storage._cached_bucket_bytes[acc_idx] += file_size

            # Gerakkan giliran akaun ke hadapan
            _b2_storage._current_account_pointer = (acc_list_index + 1) % total_accounts

            proxy_host = _b2_storage.CF_WORKER_B2_STORAGE.rstrip("/")
            public_url = f"{proxy_host}/{acc['bucket_name']}/{b2_path}"

            return {
                "url": public_url,
                "account_index": acc["index"],
                "bucket_name": acc["bucket_name"],
                "file_id": uploaded_file.id_,
                "b2_path": b2_path,
                "size_bytes": file_size
            }

        except Exception as e:
            err_str = str(e).lower()
            last_error = e

            if "cap exceeded" in err_str or "transaction" in err_str or "limit" in err_str:
                _b2_storage._exhausted_accounts.add(acc_idx)
            else:
                _b2_storage._exhausted_accounts.add(acc_idx)
            continue

    _b2_storage._all_accounts_exhausted_flag = True
    raise _b2_storage.AllB2AccountsExhaustedException(
        f"❌ Kesemua {total_accounts} akaun B2 tidak dapat digunakan atau mencapai had transaksi! ({last_error})"
    )

# ==============================================================================
# FUNGSI SINKRONISASI KE TG UPSTASH REDIS
# ==============================================================================
def save_pack_to_tg_redis(imdb_id: str, pack_payload: Dict[str, Any]) -> bool:
    """Menyimpan pemetaan katalog fail ZIP ke akaun TG Upstash Redis via REST API."""
    key = f"pack:{imdb_id}"
    json_str = json.dumps(pack_payload, ensure_ascii=False)
    headers = {
        "Authorization": f"Bearer {TG_REDIS_TOKEN}",
        "Content-Type": "application/json"
    }

    # Hantar arahan SET dan SADD dalam kelompok Pipeline REST
    pipeline_payload = [
        ["SET", key, json_str],
        ["SADD", "all_packs", imdb_id]
    ]

    try:
        resp = requests.post(f"{TG_REDIS_URL}/pipeline", headers=headers, json=pipeline_payload, timeout=10)
        if resp.status_code == 200:
            res_list = resp.json()
            if isinstance(res_list, list) and len(res_list) >= 1:
                return res_list[0].get("result") == "OK"
    except Exception as e:
        console.print(f"[bold red]❌ Ralat komunikasi TG Redis: {e}[/bold red]")

    return False

# ==============================================================================
# ALUR KERJA UTAMA
# ==============================================================================
def run_uploader(limit: int, delay: float):
    console.print("\n" + "=" * 80)
    console.print("🚀 [bold green]PROSES MUAT NAIK PAKEJ ZIP KE B2 & SINKRONISASI TG REDIS[/bold green]")
    console.print("=" * 80 + "\n")

    if not PACKAGED_TRACKER_DB.exists():
        console.print(f"[bold red]❌ Ralat: Fail {PACKAGED_TRACKER_DB.name} tidak wujud! Sila jalankan 02_package_subtitles_by_imdb.py dahulu.[/bold red]")
        sys.exit(1)

    if not ZIP_DIR.exists():
        console.print(f"[bold red]❌ Ralat: Direktori {ZIP_DIR} tidak wujud![/bold red]")
        sys.exit(1)

    # 1. Semak penjejak muat naik tempatan
    tracker_conn = init_uploaded_tracker_db()
    tracker_cur = tracker_conn.cursor()
    tracker_cur.execute("SELECT imdb_id FROM b2_uploaded_packs WHERE b2_url IS NOT NULL AND b2_url != '';")
    already_uploaded: set = {r[0] for r in tracker_cur.fetchall()}

    # 2. Dapatkan senarai sasaran daripada malay_packaged_tracker.db
    pkg_conn = sqlite3.connect(str(PACKAGED_TRACKER_DB))
    pkg_cur = pkg_conn.cursor()
    pkg_cur.execute("""
        SELECT imdb_id, zip_filename, canonical_title, release_year, media_type, total_subs_count, zip_size_bytes
        FROM malay_packaged_tracker
        ORDER BY id ASC;
    """)
    all_packages = pkg_cur.fetchall()
    pkg_conn.close()

    total_packages_available = len(all_packages)
    pending_items = [p for p in all_packages if p[0] not in already_uploaded]

    total_b2_accounts = len(_b2_storage.B2_ACCOUNTS)
    active_b2_accounts = _b2_storage.get_active_accounts_count()

    console.print(Panel.fit(
        f"[bold cyan]Maklumat Konfigurasi & Status Semasa:[/bold cyan]\n"
        f"├─ Jumlah Keseluruhan Pakej  : [yellow]{total_packages_available:,}[/yellow] fail ZIP\n"
        f"├─ Sudah Dimuat Naik (Siap)   : [green]{len(already_uploaded):,}[/green] tajuk\n"
        f"├─ Baki Belum Dimuat Naik     : [cyan]{len(pending_items):,}[/cyan] tajuk\n"
        f"├─ Had Sasaran Sesi Ini       : [bold magenta]{limit if limit > 0 else 'SEMUA BAKI'}[/bold magenta]\n"
        f"├─ Akaun B2 Tersedia          : [yellow]{active_b2_accounts}/{total_b2_accounts} akaun aktif[/yellow]\n"
        f"└─ TG Upstash Redis Endpoint  : [dim]{TG_REDIS_URL}[/dim]",
        border_style="cyan"
    ))

    if not pending_items:
        console.print("[bold green]✨ Kesemua fail pakej ZIP telah selesai dimuat naik ke B2 dan diselaraskan ke TG Redis![/bold green]\n")
        tracker_conn.close()
        return

    items_to_process = pending_items[:limit] if limit > 0 else pending_items
    processed_count = 0
    total_bytes_uploaded = 0

    console.print(f"\n[bold yellow]⚡ Memulakan muat naik bagi {len(items_to_process):,} fail ZIP...[/bold yellow]\n")

    for item in items_to_process:
        if _b2_storage.is_all_b2_exhausted():
            console.print("\n[bold red]🚨 HENTI KECEMASAN: Kesemua akaun B2 telah mencapai had transaksi harian atau storan penuh![/bold red]")
            break

        imdb_id, zip_fn, title, year, m_type, subs_count, sz_bytes = item
        local_zip_path = ZIP_DIR / zip_fn

        if not local_zip_path.exists():
            console.print(f"[bold red]⚠️ Fail fizikal tidak dijumpai: {zip_fn}[/bold red]")
            continue

        try:
            zip_bytes = local_zip_path.read_bytes()
            b2_dest_path = f"title_packs/{zip_fn}"

            # 1. Muat naik ke B2 secara Round-Robin
            upload_res = upload_zip_pack_to_b2(b2_dest_path, zip_bytes)

            b2_public_url = upload_res["url"]
            b2_bucket = upload_res["bucket_name"]
            b2_acc_idx = upload_res["account_index"]
            actual_size = upload_res["size_bytes"]

            # 2. Bentuk objek data pemetaan untuk TG Upstash Redis
            redis_pack_payload = {
                "imdb_id": imdb_id,
                "zip_filename": zip_fn,
                "canonical_title": title,
                "release_year": str(year) if year else "",
                "media_type": m_type,
                "b2_url": b2_public_url,
                "bucket_name": b2_bucket,
                "account_index": b2_acc_idx,
                "total_subs_count": subs_count,
                "zip_size_bytes": actual_size,
                "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            }

            # 3. Simpan ke TG Upstash Redis
            redis_success = save_pack_to_tg_redis(imdb_id, redis_pack_payload)

            # 4. Rekod ke Pangkalan Data Penjejak SQLite Tempatan
            tracker_cur.execute("""
                INSERT INTO b2_uploaded_packs 
                (imdb_id, zip_filename, canonical_title, release_year, media_type, b2_url, bucket_name, account_index, total_subs_count, zip_size_bytes, redis_synced)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(imdb_id) DO UPDATE SET
                    b2_url = excluded.b2_url,
                    bucket_name = excluded.bucket_name,
                    account_index = excluded.account_index,
                    redis_synced = excluded.redis_synced,
                    uploaded_at = CURRENT_TIMESTAMP;
            """, (imdb_id, zip_fn, title, year, m_type, b2_public_url, b2_bucket, b2_acc_idx, subs_count, actual_size, 1 if redis_success else 0))
            tracker_conn.commit()

            processed_count += 1
            total_bytes_uploaded += actual_size

            # Paparan Jadual Terperinci Selesai Per-Item
            table = Table(title=f"📦 Berjaya Dimuat Naik [{processed_count}/{len(items_to_process)}]", border_style="green")
            table.add_column("Atribut", style="cyan", no_wrap=True)
            table.add_column("Perincian Rekod", style="white")

            table.add_row("IMDb ID", f"[bold yellow]{imdb_id}[/bold yellow]")
            table.add_row("Tajuk Kanonikal", f"[bold white]{title}[/bold white] ({year or '-'})")
            table.add_row("Nama Fail ZIP", zip_fn)
            table.add_row("Kandungan Pakej", f"{subs_count} sarikata ({actual_size / 1024:.1f} KB)")
            table.add_row("Akaun B2 Digunakan", f"[bold yellow]Akaun B2 #{b2_acc_idx}[/bold yellow] ([magenta]{b2_bucket}[/magenta])")
            table.add_row("Cloudflare B2 URL", f"[link={b2_public_url}]{b2_public_url}[/link]")
            table.add_row("Status TG Redis", "[bold green]✔ Disimpan (pack:" + imdb_id + ")[/bold green]" if redis_success else "[bold red]❌ Gagal Disimpan[/bold red]")

            console.print(table)

            if delay > 0:
                time.sleep(delay)

        except _b2_storage.AllB2AccountsExhaustedException as e:
            console.print(f"\n[bold red]{e}[/bold red]")
            break
        except Exception as e:
            console.print(f"[bold red]❌ Ralat muat naik untuk {zip_fn}: {e}[/bold red]")

    tracker_cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    tracker_conn.close()

    total_mb_uploaded = total_bytes_uploaded / (1024 * 1024)
    console.print(Panel.fit(
        f"[bold green]✨ SESI MUAT NAIK SELESAI[/bold green]\n"
        f"├─ Jumlah Pakej Berjaya Dimuat Naik : [bold yellow]{processed_count:,}[/bold yellow] judul\n"
        f"├─ Jumlah Data Dipindahkan          : [magenta]{total_mb_uploaded:.2f} MB[/magenta]\n"
        f"└─ Pangkalan Data Penjejak          : [cyan]{UPLOADED_TRACKER_DB}[/cyan]",
        title="Ringkasan Akhir", border_style="green"
    ))

# ==============================================================================
# ENTRY POINT CLI
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Muat naik pakej fail ZIP ke multi-akaun B2 & sinkronisasi ke TG Redis")
    parser.add_argument("--limit", type=int, default=100, help="Had bilangan fail diproses sesi ini (cth: 100, 500, atau 0 untuk semua)")
    parser.add_argument("--delay", type=float, default=0.2, help="Sela masa (saat) antara setiap muat naik (lalai: 0.2s)")
    args = parser.parse_args()

    run_uploader(limit=args.limit, delay=args.delay)