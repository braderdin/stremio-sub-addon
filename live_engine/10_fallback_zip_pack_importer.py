#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO LIVE ENGINE - FALLBACK ZIP PACK IMPORTER (V2.0 SMART CLOUD FILTER)
# LOKASI: /home/braderdin/stremio-sub-addon/live_engine/10_fallback_zip_pack_importer.py
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

# Laluan rujukan utama: Pangkalan data fail yang telah selamat dimuat naik ke B2 (Read-Only)
ONDEMAND_DATA_DIR = PROJECT_ROOT / "ondemand_title_packs" / "data"
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
# KONFIGURASI KUNCI KHAS TG UPSTASH REDIS (SANDARAN METADATA PAKEJ)
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
# BACAAN REKOD AWAN B2 SECARA KETAT READ-ONLY
# ==============================================================================
def fetch_cloud_uploaded_pack(base_imdb_id: str) -> Optional[Dict[str, Any]]:
    """Membaca rekod pakej dari malay_b2_uploaded_tracker.db secara Read-Only."""
    if not UPLOADED_TRACKER_PATH.exists():
        return None

    try:
        # Mod uri=True dengan mode=ro menjamin tiada sebarang operasi write berlaku
        conn = sqlite3.connect(f"file:{UPLOADED_TRACKER_PATH}?mode=ro", uri=True)
        cur = conn.cursor()

        cur.execute("PRAGMA table_info(b2_uploaded_packs);")
        cols = [c[1] for c in cur.fetchall()]
        has_hash_col = "zip_hash" in cols

        query = f"""
            SELECT imdb_id, zip_filename, canonical_title, release_year, media_type, total_subs_count, zip_size_bytes,
                   {"zip_hash" if has_hash_col else "NULL"}, b2_url, bucket_name, account_index
            FROM b2_uploaded_packs
            WHERE imdb_id = ? AND b2_url IS NOT NULL AND b2_url != ''
            LIMIT 1;
        """
        cur.execute(query, (base_imdb_id,))
        row = cur.fetchone()
        conn.close()

        if row:
            return {
                "imdb_id": row[0],
                "zip_filename": row[1],
                "canonical_title": row[2],
                "release_year": row[3],
                "media_type": row[4],
                "total_subs_count": row[5],
                "zip_size_bytes": row[6],
                "zip_hash": row[7],
                "b2_url": row[8],
                "bucket_name": row[9],
                "account_index": row[10]
            }
    except Exception as e:
        console.print(f"[dim yellow]⚠️ Gagal membaca malay_b2_uploaded_tracker.db (Read-Only): {e}[/dim yellow]")

    return None

# ==============================================================================
# PENGENDALIAN TEKS & PARSING EPISOD
# ==============================================================================
def decode_content(raw_bytes: bytes) -> str:
    """Mengesan pengekodan teks dan menukar ke format UTF-8 standard Unix."""
    encodings = ["utf-8-sig", "utf-8", "latin-1", "windows-1252", "cp1256", "iso-8859-1"]
    for enc in encodings:
        try:
            text = raw_bytes.decode(enc)
            return text.replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="ignore").replace("\r\n", "\n").replace("\r", "\n")

def parse_season_episode(filename: str) -> Tuple[Optional[int], Optional[int]]:
    """Mengekstrak maklumat Musim dan Episod daripada nama fail sarikata."""
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

def get_redis_shard_index(imdb_id: str) -> int:
    """Mengira shard Redis utama mana yang digunakan berdasarkan modulo hashing."""
    try:
        if hasattr(_redis, "REDIS_ACCOUNTS") and _redis.REDIS_ACCOUNTS:
            total_shards = len(_redis.REDIS_ACCOUNTS)
            clean_id = str(imdb_id or "").strip()
            match = re.search(r"tt(\d+)", clean_id)
            if match:
                num = int(match.group(1))
                return (num % total_shards) + 1
            else:
                hash_val = int(hashlib.md5(clean_id.encode("utf-8")).hexdigest(), 16)
                return (hash_val % total_shards) + 1
    except Exception:
        pass
    return 1

# ==============================================================================
# BACAAN PAKEJ DARI TG UPSTASH REDIS (SANDARAN)
# ==============================================================================
def fetch_pack_metadata(base_imdb_id: str) -> Optional[Dict[str, Any]]:
    """Mengambil metadata fail ZIP daripada TG Upstash Redis via REST API jika pangkalan tempatan tiada."""
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
    except Exception as e:
        console.print(f"[dim yellow]⚠️ Ralat membaca TG Redis ({key}): {e}[/dim yellow]")

    return None

# ==============================================================================
# PENGURUSAN REKOD SCRAPED HISTORY
# ==============================================================================
def append_to_scraped_history(imdb_id: str, title: str, year: str, media_type: str, subs_count: int):
    """Menyimpan entri ke scraped_history.json sahaja tanpa menyentuh no_subs_history.json."""
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
        "source": "fallback_zip_pack",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    with open(SCRAPED_HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

# ==============================================================================
# MUAT TURUN FAIL BINARI DENGAN PENGESAHAN MAGIC BYTES
# ==============================================================================
def download_zip_binary(url: str) -> Optional[bytes]:
    """Muat turun fail ZIP binari tulen dengan semakan pengepala PK."""
    raw_data = None
    headers = {
        "User-Agent": "Mozilla/5.0 (StremioFallbackRunner/2.0)",
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
        except Exception as e:
            console.print(f"[bold red]❌ Ralat muat turun arkib ZIP: {e}[/bold red]")
            return None

    if not raw_data or len(raw_data) < 50:
        console.print("[bold red]❌ Gagal: Data diterima daripada Cloudflare Worker kosong atau tidak lengkap.[/bold red]")
        return None

    if not raw_data.startswith(b"PK"):
        preview = raw_data[:150].decode("utf-8", errors="ignore")
        console.print(f"[bold red]❌ Kandungan diterima bukan format ZIP binari yang sah! Respon pelayan:\n{preview}[/bold red]")
        return None

    return raw_data

# ==============================================================================
# ALUR UTAMA PENGIMPORTAN FALLBACK DENGAN SISTEM PENAPISAN HASH
# ==============================================================================
def run_fallback_importer(target_imdb: str) -> bool:
    clean_target = str(target_imdb).strip()
    base_imdb_id = clean_target.split(":")[0]

    console.print(Panel.fit(
        f"[bold cyan]📦 PROSES FALLBACK: IMPORT PAKEJ ARKIB B2 KE 10 REDIS UTAMA[/bold cyan]\n"
        f"[white]Sasaran IMDb :[/white] [yellow]{target_imdb}[/yellow] (Base: [cyan]{base_imdb_id}[/cyan])",
        border_style="cyan"
    ))

    # 1. Dapatkan metadata pakej (Utamakan pangkalan data tempatan Read-Only, sandaran ke TG Redis)
    pack_data = fetch_cloud_uploaded_pack(base_imdb_id)
    if not pack_data:
        pack_data = fetch_pack_metadata(base_imdb_id)

    if not pack_data:
        console.print(f"[dim]ℹ️ Tiada fail pakej arkib ditemui untuk {base_imdb_id} di penjejak B2 mahupun TG Redis. Tamat tanpa tindakan.[/dim]")
        return False

    b2_zip_url = pack_data.get("b2_url")
    title = pack_data.get("canonical_title", base_imdb_id)
    year = pack_data.get("release_year", "")
    media_type = pack_data.get("media_type", "movie")
    zip_fn = pack_data.get("zip_filename", "")
    cloud_hash = pack_data.get("zip_hash")
    expected_subs_count = pack_data.get("total_subs_count")

    # 2. Semak status integriti di pangkalan data aktif tempatan
    active_conn = init_active_synced_db()
    active_cur = active_conn.cursor()
    active_cur.execute(
        "SELECT zip_hash, total_subs_uploaded, status FROM b2_active_synced WHERE imdb_id = ?;",
        (base_imdb_id,)
    )
    local_row = active_cur.fetchone()

    needs_process = False
    action_reason = "BARU"

    if not local_row:
        needs_process = True
        action_reason = "REKOD_BARU"
    else:
        local_hash, local_uploaded_count, local_status = local_row

        if local_status != "COMPLETED":
            needs_process = True
            action_reason = "STATUS_BELUM_LENGKAP"
        elif cloud_hash and local_hash and cloud_hash != local_hash:
            needs_process = True
            action_reason = f"HASH_BERUBAH ({local_hash[:8]} -> {cloud_hash[:8]})"
        elif expected_subs_count is not None and expected_subs_count != local_uploaded_count:
            needs_process = True
            action_reason = f"JUMLAH_SARIKATA_BERBEZA ({local_uploaded_count} -> {expected_subs_count})"
        else:
            # Pakej di awan B2 dan stesen aktif identikal sepenuhnya
            needs_process = False

    if not needs_process:
        console.print(Panel.fit(
            f"[bold green]✨ Pakej ZIP sah & selari sepenuhnya![/bold green]\n"
            f"├─ Sasaran IMDb    : [yellow]{base_imdb_id}[/yellow] ({title})\n"
            f"├─ Integriti Hash  : [cyan]{cloud_hash[:12] if cloud_hash else 'TIADA'}... (Sepadan)[/cyan]\n"
            f"├─ Bil. Sarikata   : [green]{local_row[1]} fail tersedia[/green]\n"
            f"└─ Tindakan        : [bold white]Langkau Muat Turun & Muat Naik (Tiada Perubahan)[/bold white]",
            border_style="green"
        ))
        active_conn.close()
        return True

    console.print(f"⚡ [bold yellow]Tindakan Diperlukan ({action_reason}):[/bold yellow] Memulakan proses kemaskini stesen aktif...")
    console.print(f"🎯 Pakej Ditemui: [bold white]{title} ({year or '-'})[/bold white]")
    console.print(f"   ├─ Fail ZIP : [yellow]{zip_fn}[/yellow]")
    console.print(f"   └─ URL B2   : [link={b2_zip_url}]{b2_zip_url}[/link]")

    # 3. Muat turun fail ZIP binari dalam memori
    console.print("📥 Memuat turun fail ZIP dari B2 via Cloudflare Zero-Egress Proxy...")
    zip_bytes = download_zip_binary(b2_zip_url)
    if not zip_bytes:
        active_conn.close()
        return False

    real_hash = hashlib.sha256(zip_bytes).hexdigest()

    # 4. Ekstrak fail sarikata di dalam memori
    extracted_subs = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for zname in zf.namelist():
                zname_lower = zname.lower()
                if zname_lower.endswith((".srt", ".ass", ".ssa", ".vtt")) and not zname_lower.startswith("__macosx"):
                    sub_raw = zf.read(zname)
                    if len(sub_raw) < 50:
                        continue
                    text_content = decode_content(sub_raw)
                    extracted_subs.append({
                        "filename": Path(zname).name,
                        "content": text_content
                    })
    except Exception as e:
        console.print(f"[bold red]❌ Ralat membuka arkib ZIP: {e}[/bold red]")
        active_conn.close()
        return False

    if not extracted_subs:
        console.print("[bold yellow]⚠️ Fail ZIP tidak mengandungi sebarang sarikata sah.[/bold yellow]")
        active_conn.close()
        return False

    console.print(f"✔ Berjaya mengekstrak [green]{len(extracted_subs)}[/green] sarikata dari pakej.")

    # 5. Muat naik fail sarikata ke B2 Aktif dan petakan episod
    uploaded_records = []
    episodic_groups: Dict[str, List[Dict[str, Any]]] = {}
    shard_idx = get_redis_shard_index(base_imdb_id)

    table = Table(title=f"📦 Pakej: [bold cyan]{zip_fn}[/bold cyan] ({title})", border_style="cyan")
    table.add_column("No", style="dim", justify="right")
    table.add_column("Nama Fail Sarikata (Ekstrak)", style="white")
    table.add_column("Destinasi B2 Aktif", style="yellow")
    table.add_column("Redis Utama", style="green")

    for idx, sub_item in enumerate(extracted_subs, 1):
        raw_fn = sub_item["filename"]
        clean_rel = Path(raw_fn).stem
        ext = Path(raw_fn).suffix.lower() or ".srt"

        safe_rel = re.sub(r"[^\w\d]", ".", clean_rel)
        safe_rel = re.sub(r"\.+", ".", safe_rel).strip(".")

        b2_sub_path = f"subs/{base_imdb_id}/pack_{idx}_{safe_rel}{ext}"

        try:
            b2_res = _b2.upload_subtitle_to_b2(b2_sub_path, sub_item["content"])
            rec_id = f"pack_{base_imdb_id}_{idx}"

            record = {
                "id": rec_id,
                "lang": "ms",
                "url": b2_res["url"],
                "release": clean_rel,
                "source": "subscene_archive_pack",
                "acc": int(b2_res["account_index"])
            }

            s_num, e_num = parse_season_episode(raw_fn)
            if s_num is not None and e_num is not None:
                record["season"] = s_num
                record["episode"] = e_num
                ep_key = f"{base_imdb_id}:{s_num}:{e_num}"
                if ep_key not in episodic_groups:
                    episodic_groups[ep_key] = []
                episodic_groups[ep_key].append(record)

            uploaded_records.append(record)

            b2_desc = f"B2 Akaun #{b2_res['account_index']}"
            redis_desc = f"Redis Shard #{shard_idx}"
            table.add_row(str(idx), f"{safe_rel}{ext}", b2_desc, redis_desc)

        except _b2.AllB2AccountsExhaustedException as e:
            console.print(f"[bold red]🚨 {e}[/bold red]")
            break
        except Exception as e:
            console.print(f"  ❌ Gagal muat naik sarikata #{idx}: {e}")

    if not uploaded_records:
        console.print("[bold red]❌ Tiada sarikata yang berjaya dimuat naik ke B2.[/bold red]")
        active_conn.close()
        return False

    console.print(table)

    # 6. Simpan rekod ke 10 Redis utama (Modulo Sharded)
    console.print("\n💾 Menyimpan rekod ke pangkalan data Redis utama (10 Akaun Sharded)...")
    _redis.save_subtitle_records_batch(base_imdb_id, uploaded_records)

    for ep_key, ep_recs in episodic_groups.items():
        _redis.save_subtitle_records_batch(ep_key, ep_recs)

    # 7. Kemas kini scraped_history.json
    append_to_scraped_history(base_imdb_id, title, year, media_type, len(uploaded_records))

    # 8. Kunci hash rujukan secara idempoten di b2_active_synced_tracker.db
    target_sync_hash = cloud_hash if cloud_hash else real_hash

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
    """, (base_imdb_id, title, year, media_type, zip_fn, target_sync_hash, len(extracted_subs), len(uploaded_records), len(uploaded_records) + len(episodic_groups)))
    active_conn.commit()

    active_cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    active_conn.close()

    console.print(Panel.fit(
        f"[bold green]✨ PENGIMPORTAN FALLBACK SELESAI[/bold green]\n"
        f"├─ Sasaran IMDb          : [yellow]{base_imdb_id}[/yellow]\n"
        f"├─ Hash Disahkan         : [cyan]{target_sync_hash[:12]}...[/cyan]\n"
        f"├─ Sarikata Berjaya B2   : [green]{len(uploaded_records)} fail[/green]\n"
        f"├─ Kumpulan Episod Siri  : [magenta]{len(episodic_groups)} episod[/magenta]\n"
        f"└─ Pangkalan Data Aktif  : [cyan]{ACTIVE_SYNCED_TRACKER_DB.name}[/cyan] (Dikemaskini)",
        title="Ringkasan Fallback", border_style="green"
    ))

    return True

# ==============================================================================
# ENTRY POINT CLI
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import fail ZIP dari cold storage B2 ke 10 Redis Utama")
    parser.add_argument("--imdb", type=str, required=True, help="Target IMDb ID (cth: tt0145487 atau tt123456:1:1)")
    args = parser.parse_args()

    run_fallback_importer(args.imdb)