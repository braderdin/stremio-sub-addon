#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO LIVE ENGINE - B2 PACKS CRON SYNC & PRE-WARM ENGINE (V1.1)
# LOKASI: /home/braderdin/stremio-sub-addon/live_engine/12_sync_b2_packs_to_active_storage.py
# ==============================================================================

import os
import sys
import re
import io
import json
import time
import zipfile
import sqlite3
import hashlib
import argparse
import importlib
import urllib.request
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List
from curl_cffi import requests
from dotenv import dotenv_values

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

# ==============================================================================
# IMPORT MODUL LIVE ENGINE (CONFIG, REDIS & B2)
# ==============================================================================
LIVE_ENGINE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = LIVE_ENGINE_DIR.parent
DATA_DIR = LIVE_ENGINE_DIR / "data"
SCRAPED_HISTORY_PATH = DATA_DIR / "scraped_history.json"
ACTIVE_SYNCED_TRACKER_DB = DATA_DIR / "b2_active_synced_tracker.db"

# Laluan pangkalan data rujukan (Hanya Baca / Read-Only)
ONDEMAND_DATA_DIR = PROJECT_ROOT / "ondemand_title_packs" / "data"
PACKAGED_TRACKER_PATH = ONDEMAND_DATA_DIR / "malay_packaged_tracker.db"
UPLOADED_TRACKER_PATH = ONDEMAND_DATA_DIR / "malay_b2_uploaded_tracker.db"

if str(LIVE_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(LIVE_ENGINE_DIR))

try:
    _config = importlib.import_module("00_config")
    _redis = importlib.import_module("01_redis_db")
    _b2 = importlib.import_module("02_b2_storage")
except ImportError as e:
    console.print(f"[bold red]❌ Ralat mengimport modul live_engine: {e}[/bold red]")
    sys.exit(1)

# ==============================================================================
# KONFIGURASI KUNCI KHAS TG UPSTASH REDIS (PENGAMBILAN METADATA PAKEJ)
# ==============================================================================
ENV_LOCAL_PATH = PROJECT_ROOT / ".env.local"
env_vars = dotenv_values(str(ENV_LOCAL_PATH)) if ENV_LOCAL_PATH.exists() else {}

TG_REDIS_URL = (
    os.getenv("TG_UPSTASH_REDIS_REST_URL")
    or env_vars.get("TG_UPSTASH_REDIS_REST_URL", "")
).strip().rstrip("/")

TG_REDIS_TOKEN = (
    os.getenv("TG_UPSTASH_REDIS_REST_TOKEN")
    or env_vars.get("TG_UPSTASH_REDIS_REST_TOKEN", "")
).strip()

# ==============================================================================
# INISIALISASI PANGKALAN DATA PENJEJAK AKTIF TEMPATAN
# ==============================================================================
def init_active_synced_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(ACTIVE_SYNCED_TRACKER_DB))
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS b2_active_synced (
            imdb_id TEXT PRIMARY KEY,
            canonical_title TEXT,
            release_year TEXT,
            media_type TEXT,
            zip_filename TEXT,
            zip_hash TEXT,
            total_subs_in_zip INTEGER,
            total_subs_uploaded INTEGER,
            redis_keys_updated INTEGER,
            synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'COMPLETED'
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_hash ON b2_active_synced (zip_hash);")
    conn.commit()
    return conn

# ==============================================================================
# PENGENDALIAN TEKS & PARSING EPISOD
# ==============================================================================
def decode_content(raw_bytes: bytes) -> str:
    encodings = ["utf-8-sig", "utf-8", "latin-1", "windows-1252", "cp1256", "iso-8859-1"]
    for enc in encodings:
        try:
            text = raw_bytes.decode(enc)
            return text.replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="ignore").replace("\r\n", "\n").replace("\r", "\n")

def parse_season_episode(filename: str) -> Tuple[Optional[int], Optional[int]]:
    clean_fn = re.sub(r"(?i)\b(?:2160|1080|720|480)\b", " ", filename)

    m1 = re.search(r"\b[sS](\d{1,2})[-_ ]*[eE](\d{1,3})\b", clean_fn)
    if m1:
        return int(m1.group(1)), int(m1.group(2))

    m2 = re.search(r"\b(\d{1,2})x(\d{1,3})\b", clean_fn)
    if m2:
        s, e = int(m2.group(1)), int(m2.group(2))
        if s < 50 and e < 500:
            return s, e

    m3 = re.search(r"\b(?:ep|episode|e)[-_\.\s]*(\d{1,3})\b", clean_fn, re.IGNORECASE)
    if m3:
        return 1, int(m3.group(1))

    return None, None

# ==============================================================================
# BACAAN PAKEJ DARI TG UPSTASH REDIS
# ==============================================================================
def fetch_pack_metadata(base_imdb_id: str) -> Optional[Dict[str, Any]]:
    if not TG_REDIS_URL or not TG_REDIS_TOKEN:
        return None

    key = f"pack:{base_imdb_id}"
    url = f"{TG_REDIS_URL}/get/{key}"
    headers = {"Authorization": f"Bearer {TG_REDIS_TOKEN}"}

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            val = data.get("result")
            if val:
                return json.loads(val) if isinstance(val, str) else val
    except Exception:
        pass

    return None

# ==============================================================================
# PENGURUSAN REKOD SCRAPED HISTORY
# ==============================================================================
def append_to_scraped_history(imdb_id: str, title: str, year: str, media_type: str, subs_count: int):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    history = {}

    if SCRAPED_HISTORY_PATH.exists():
        try:
            with open(SCRAPED_HISTORY_PATH, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = {}

    history[imdb_id] = {
        "imdb_id": imdb_id,
        "title": title,
        "year": year,
        "media_type": media_type,
        "subs_count": subs_count,
        "source": "cron_b2_pack_sync",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    with open(SCRAPED_HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

# ==============================================================================
# MUAT TURUN FAIL BINARI ZIP DENGAN PENGESAHAN MAGIC BYTES
# ==============================================================================
def download_zip_binary(url: str) -> Optional[bytes]:
    raw_data = None
    headers = {
        "User-Agent": "Mozilla/5.0 (StremioCronSyncRunner/1.0)",
        "Accept": "*/*"
    }

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200 and len(resp.content) > 50:
            raw_data = resp.content
    except Exception:
        pass

    if not raw_data:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                if resp.status == 200:
                    raw_data = resp.read()
        except Exception:
            return None

    if not raw_data or len(raw_data) < 50 or not raw_data.startswith(b"PK"):
        return None

    return raw_data

# ==============================================================================
# ALUR UTAMA CRON SYNC & PRE-WARM
# ==============================================================================
def run_cron_sync(batch_limit: int, delay: float):
    console.print("\n" + "=" * 80)
    console.print("🚀 [bold green]CRON SYNC: MIGRASI PAKEJ ZIP B2 KE STESEN AKTIF REDIS & B2[/bold green]")
    console.print("=" * 80 + "\n")

    if not PACKAGED_TRACKER_PATH.exists():
        console.print(f"[bold red]❌ Ralat: Fail sumber rujukan tidak dijumpai: {PACKAGED_TRACKER_PATH}[/bold red]")
        sys.exit(1)

    # 1. Buka pangkalan data rujukan secara Read-Only (Strict Isolation)
    try:
        pkg_conn = sqlite3.connect(f"file:{PACKAGED_TRACKER_PATH}?mode=ro", uri=True)
        pkg_cur = pkg_conn.cursor()
        pkg_cur.execute("SELECT imdb_id, zip_filename, canonical_title, release_year, media_type, total_subs_count, zip_size_bytes, zip_hash FROM malay_packaged_tracker;")
        all_packages = pkg_cur.fetchall()
        pkg_conn.close()
    except Exception as e:
        console.print(f"[bold red]❌ Gagal membaca malay_packaged_tracker.db secara read-only: {e}[/bold red]")
        sys.exit(1)

    # 2. Buka penjejak aktif tempatan
    active_conn = init_active_synced_db()
    active_cur = active_conn.cursor()
    active_cur.execute("SELECT imdb_id, zip_hash, total_subs_uploaded FROM b2_active_synced;")
    local_synced_meta: Dict[str, Dict[str, Any]] = {
        r[0]: {"hash": r[1], "uploaded_count": r[2]}
        for r in active_cur.fetchall()
    }

    # 3. Logik Pintar 3-Lapisan untuk Menapis Senarai Tugasan
    tasks_to_process = []
    for pkg in all_packages:
        imdb_id, zip_fn, title, year, m_type, subs_count, sz_bytes, pkg_hash = pkg

        if imdb_id not in local_synced_meta:
            tasks_to_process.append((pkg, "BARU"))
            continue

        local_info = local_synced_meta[imdb_id]
        
        # Semak Lapisan Hash SHA-256
        if pkg_hash and local_info["hash"]:
            if pkg_hash != local_info["hash"]:
                tasks_to_process.append((pkg, "KEMAS_KINI"))
                continue
        
        # Semak Bilangan Sarikata
        if subs_count != local_info["uploaded_count"]:
            tasks_to_process.append((pkg, "KEMAS_KINI"))
            continue

        # Jika identikal, langkau (Skip)

    console.print(Panel.fit(
        f"[bold cyan]Status Penapisan Cron Tri-Metric:[/bold cyan]\n"
        f"├─ Jumlah Pakej dalam Katalog : [yellow]{len(all_packages):,}[/yellow] tajuk\n"
        f"├─ Sedia Ada di Stesen Aktif  : [green]{len(all_packages) - len(tasks_to_process):,}[/green] tajuk (Kekal Selari)\n"
        f"├─ Perlu Diproses Sesi Ini    : [cyan]{len(tasks_to_process):,}[/cyan] tajuk\n"
        f"└─ Had Kuota Batch Sesi Ini   : [bold magenta]{batch_limit if batch_limit > 0 else 'SEMUA'}[/bold magenta]",
        border_style="cyan"
    ))

    if not tasks_to_process:
        console.print("[bold green]✨ Kesemua pakej ZIP B2 telah diselaraskan sepenuhnya ke stesen aktif![/bold green]\n")
        active_conn.close()
        return

    batch_run = tasks_to_process[:batch_limit] if batch_limit > 0 else tasks_to_process
    success_processed = 0

    console.print(f"\n[bold yellow]⚡ Memproses {len(batch_run):,} tajuk pilihan...[/bold yellow]\n")

    for item, action_type in batch_run:
        imdb_id, zip_fn, title, year, m_type, subs_count, sz_bytes, pkg_hash = item

        # Ambil metadata URL B2 terkini dari TG Redis
        pack_meta = fetch_pack_metadata(imdb_id)
        if not pack_meta or not pack_meta.get("b2_url"):
            console.print(f"⚠️ [yellow]{imdb_id}[/yellow]: URL B2 tidak ditemui di TG Redis. Langkau.")
            continue

        b2_zip_url = pack_meta["b2_url"]
        
        # Muat turun ZIP dari B2
        zip_bytes = download_zip_binary(b2_zip_url)
        if not zip_bytes:
            console.print(f"❌ [red]{imdb_id}[/red]: Gagal memuat turun fail ZIP binari dari {b2_zip_url}")
            continue

        real_hash = hashlib.sha256(zip_bytes).hexdigest()

        # Ekstrak sarikata dari memori ZIP
        extracted_subs = []
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                for zname in zf.namelist():
                    zname_lower = zname.lower()
                    if zname_lower.endswith((".srt", ".ass", ".ssa", ".vtt")) and not zname_lower.startswith("__macosx"):
                        sub_raw = zf.read(zname)
                        if len(sub_raw) >= 50:
                            extracted_subs.append({
                                "filename": Path(zname).name,
                                "content": decode_content(sub_raw)
                            })
        except Exception as e:
            console.print(f"❌ [red]{imdb_id}[/red]: Ralat membaca struktur ZIP: {e}")
            continue

        if not extracted_subs:
            continue

        # Muat naik fail .srt individu ke B2 Aktif & Kumpul untuk Redis
        uploaded_records = []
        episodic_groups: Dict[str, List[Dict[str, Any]]] = {}

        for idx, sub_item in enumerate(extracted_subs, 1):
            raw_fn = sub_item["filename"]
            clean_rel = Path(raw_fn).stem
            ext = Path(raw_fn).suffix.lower() or ".srt"

            safe_rel = re.sub(r"[^\w\d]", ".", clean_rel)
            safe_rel = re.sub(r"\.+", ".", safe_rel).strip(".")

            b2_sub_path = f"subs/{imdb_id}/cron_pack_{idx}_{safe_rel}{ext}"

            try:
                b2_res = _b2.upload_subtitle_to_b2(b2_sub_path, sub_item["content"])
                rec_id = f"cron_{imdb_id}_{idx}"

                record = {
                    "id": rec_id,
                    "lang": "ms",
                    "url": b2_res["url"],
                    "release": clean_rel,
                    "source": "cron_b2_active_sync",
                    "acc": int(b2_res["account_index"])
                }

                s_num, e_num = parse_season_episode(raw_fn)
                if s_num is not None and e_num is not None:
                    record["season"] = s_num
                    record["episode"] = e_num
                    ep_key = f"{imdb_id}:{s_num}:{e_num}"
                    if ep_key not in episodic_groups:
                        episodic_groups[ep_key] = []
                    episodic_groups[ep_key].append(record)

                uploaded_records.append(record)

            except _b2.AllB2AccountsExhaustedException as e:
                console.print(f"\n[bold red]🚨 {e}[/bold red]")
                break
            except Exception:
                pass

        if not uploaded_records:
            continue

        # Simpan ke Redis utama (10 Sharded Accounts)
        _redis.save_subtitle_records_batch(imdb_id, uploaded_records)
        for ep_key, ep_recs in episodic_groups.items():
            _redis.save_subtitle_records_batch(ep_key, ep_recs)

        # Rekod ke scraped_history.json menggunakan m_type yang sah
        append_to_scraped_history(imdb_id, title, year, m_type, len(uploaded_records))

        # Kemas kini penjejak aktif tempatan
        active_cur.execute("""
            INSERT INTO b2_active_synced 
            (imdb_id, canonical_title, release_year, media_type, zip_filename, zip_hash, total_subs_in_zip, total_subs_uploaded, redis_keys_updated, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'COMPLETED')
            ON CONFLICT(imdb_id) DO UPDATE SET
                canonical_title = excluded.canonical_title,
                release_year = excluded.release_year,
                media_type = excluded.media_type,
                zip_filename = excluded.zip_filename,
                zip_hash = excluded.zip_hash,
                total_subs_in_zip = excluded.total_subs_in_zip,
                total_subs_uploaded = excluded.total_subs_uploaded,
                redis_keys_updated = excluded.redis_keys_updated,
                synced_at = CURRENT_TIMESTAMP,
                status = 'COMPLETED';
        """, (imdb_id, title, year, m_type, zip_fn, real_hash, subs_count, len(uploaded_records), len(uploaded_records) + len(episodic_groups)))
        active_conn.commit()

        success_processed += 1
        console.print(f"  ✔ [{success_processed}/{len(batch_run)}] Selesai diselaraskan: [bold white]{title}[/bold white] ([yellow]{imdb_id}[/yellow]) -> [green]{len(uploaded_records)} sarikata[/green]")

        if delay > 0:
            time.sleep(delay)

    active_cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    active_conn.close()

    console.print(Panel.fit(
        f"[bold green]✨ CRON SYNC SELESAI[/bold green]\n"
        f"├─ Berjaya Diproses & Diselaraskan : [yellow]{success_processed:,}[/yellow] tajuk\n"
        f"└─ Pangkalan Data Penjejak Aktif  : [cyan]{ACTIVE_SYNCED_TRACKER_DB}[/cyan]",
        title="Ringkasan Cron", border_style="green"
    ))

# ==============================================================================
# ENTRY POINT CLI
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cron Sync: Migrasi pakej ZIP B2 ke storan aktif dan Redis")
    parser.add_argument("--limit", type=int, default=50, help="Had bilangan pakej diproses setiap larian (lalai: 50)")
    parser.add_argument("--delay", type=float, default=0.1, help="Jeda masa (saat) antara muat naik (lalai: 0.1s)")
    args = parser.parse_args()

    run_cron_sync(batch_limit=args.limit, delay=args.delay)