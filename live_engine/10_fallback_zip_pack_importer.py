#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO LIVE ENGINE - FALLBACK ZIP PACK IMPORTER (B2 TO MULTI-REDIS)
# LOKASI: /home/braderdin/stremio-sub-addon/live_engine/10_fallback_zip_pack_importer.py
# ==============================================================================

import os
import sys
import re
import io
import json
import time
import zipfile
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
# KONFIGURASI KUNCI KHAS TG UPSTASH REDIS
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

# ==============================================================================
# BACAAN PAKEJ DARI TG UPSTASH REDIS
# ==============================================================================
def fetch_pack_metadata(base_imdb_id: str) -> Optional[Dict[str, Any]]:
    """Mengambil metadata fail ZIP daripada TG Upstash Redis via REST API."""
    if not TG_REDIS_URL or not TG_REDIS_TOKEN:
        console.print("[bold red]❌ Kredensial TG_UPSTASH_REDIS tidak lengkap![/bold red]")
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
        console.print(f"[bold red]⚠️ Ralat membaca TG Redis ({key}): {e}[/bold red]")

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
    """Muat turun fail ZIP binari tulen dengan semakan pengepala PK\x03\x04."""
    raw_data = None
    headers = {
        "User-Agent": "Mozilla/5.0 (StremioFallbackRunner/1.0)",
        "Accept": "*/*"
    }

    # Percubaan 1: Menggunakan curl_cffi
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code == 200 and len(resp.content) > 50:
            raw_data = resp.content
    except Exception:
        pass

    # Percubaan 2: Menggunakan urllib sekiranya curl_cffi gagal
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

    # Pengesahan Format Fail ZIP (Magic Bytes: PK\x03\x04 atau PK\x05\x06)
    if not raw_data.startswith(b"PK"):
        preview = raw_data[:150].decode("utf-8", errors="ignore")
        console.print(f"[bold red]❌ Kandungan diterima bukan format ZIP binari yang sah! Respon pelayan:\n{preview}[/bold red]")
        return None

    return raw_data

# ==============================================================================
# ALUR UTAMA PENGIMPORTAN FALLBACK
# ==============================================================================
def run_fallback_importer(target_imdb: str) -> bool:
    clean_target = str(target_imdb).strip()
    base_imdb_id = clean_target.split(":")[0]

    console.print(Panel.fit(
        f"[bold cyan]📦 PROSES FALLBACK: IMPORT PAKEJ ARKIB B2 KE 10 REDIS UTAMA[/bold cyan]\n"
        f"[white]Sasaran IMDb :[/white] [yellow]{target_imdb}[/yellow] (Base: [cyan]{base_imdb_id}[/cyan])",
        border_style="cyan"
    ))

    # 1. Semak sama ada sarikata daripada arkib ini sudah sedia ada di Redis utama
    existing_records = _redis.get_subtitle_records(base_imdb_id)
    has_pack_already = any(
        isinstance(r, dict) and r.get("source") == "subscene_archive_pack"
        for r in existing_records
    )
    if has_pack_already:
        console.print(f"ℹ️ Sarikata arkib untuk [yellow]{base_imdb_id}[/yellow] telah sedia wujud di Redis utama. Langkau muat naik.")
        return True

    # 2. Ambil metadata fail ZIP daripada TG Upstash Redis
    console.print(f"🔍 Menyemak pangkalan data TG Redis untuk [cyan]pack:{base_imdb_id}[/cyan]...")
    pack_data = fetch_pack_metadata(base_imdb_id)

    if not pack_data:
        console.print(f"[dim]ℹ️ Tiada fail pakej arkib ditemui di TG Redis untuk {base_imdb_id}. Tamat tanpa tindakan.[/dim]")
        return False

    b2_zip_url = pack_data.get("b2_url")
    title = pack_data.get("canonical_title", base_imdb_id)
    year = pack_data.get("release_year", "")
    media_type = pack_data.get("media_type", "movie")
    zip_fn = pack_data.get("zip_filename", "")

    console.print(f"🎯 Pakej Ditemui: [bold white]{title} ({year or '-'})[/bold white]")
    console.print(f"   ├─ Fail ZIP : [yellow]{zip_fn}[/yellow]")
    console.print(f"   └─ URL B2   : [link={b2_zip_url}]{b2_zip_url}[/link]")

    # 3. Muat turun fail ZIP binari dalam memori (Zero disk storage)
    console.print("📥 Memuat turun fail ZIP dari B2 via Cloudflare Zero-Egress Proxy...")
    zip_bytes = download_zip_binary(b2_zip_url)
    if not zip_bytes:
        return False

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
        return False

    if not extracted_subs:
        console.print("[bold yellow]⚠️ Fail ZIP tidak mengandungi sebarang sarikata sah.[/bold yellow]")
        return False

    console.print(f"✔ Berjaya mengekstrak [green]{len(extracted_subs)}[/green] sarikata dari pakej.")

    # 5. Muat naik fail .srt individu ke B2 dan bina rekod Redis
    uploaded_records = []
    episodic_groups: Dict[str, List[Dict[str, Any]]] = {}

    for idx, sub_item in enumerate(extracted_subs, 1):
        raw_fn = sub_item["filename"]
        clean_rel = Path(raw_fn).stem
        ext = Path(raw_fn).suffix.lower() or ".srt"

        b2_sub_path = f"subs/{base_imdb_id}/ms_pack_{idx}_{clean_rel}{ext}"

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
            console.print(f"  ✔ [B2 Acc #{b2_res['account_index']}] Diproses: [dim]{raw_fn}[/dim]")

        except _b2.AllB2AccountsExhaustedException as e:
            console.print(f"[bold red]🚨 {e}[/bold red]")
            break
        except Exception as e:
            console.print(f"  ❌ Gagal muat naik sarikata #{idx}: {e}")

    if not uploaded_records:
        console.print("[bold red]❌ Tiada sarikata yang berjaya dimuat naik ke B2.[/bold red]")
        return False

    # 6. Simpan rekod secara berkelompok ke 10 Redis utama (Modulo Sharded)
    console.print("\n💾 Menyimpan rekod ke pangkalan data Redis utama (10 Akaun Sharded)...")
    _redis.save_subtitle_records_batch(base_imdb_id, uploaded_records)

    for ep_key, ep_recs in episodic_groups.items():
        _redis.save_subtitle_records_batch(ep_key, ep_recs)

    # 7. Kemas kini scraped_history.json sahaja
    append_to_scraped_history(base_imdb_id, title, year, media_type, len(uploaded_records))

    table = Table(title="✨ Status Selesai: Fallback ZIP Pack Importer", border_style="green")
    table.add_column("Atribut", style="cyan")
    table.add_column("Perincian Rekod", style="white")

    table.add_row("IMDb ID Asas", f"[bold yellow]{base_imdb_id}[/bold yellow]")
    table.add_row("Tajuk Kanonikal", f"{title} ({year or '-'})")
    table.add_row("Jenis Media", media_type.upper())
    table.add_row("Jumlah Sarikata Diimport", f"[bold green]{len(uploaded_records)} fail[/bold green]")
    table.add_row("Kumpulan Episod Bersiri", f"{len(episodic_groups)} episod dipetakan" if episodic_groups else "N/A (Filem)")
    table.add_row("Status Sejarah", "Direkod ke scraped_history.json (no_subs_history diabaikan)")

    console.print(table)
    return True

# ==============================================================================
# ENTRY POINT CLI
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import fail ZIP dari cold storage B2 ke 10 Redis Utama")
    parser.add_argument("--imdb", type=str, required=True, help="Target IMDb ID (cth: tt0145487 atau tt123456:1:1)")
    args = parser.parse_args()

    run_fallback_importer(args.imdb)