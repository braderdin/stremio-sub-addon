import os
import sys
import re
import time
import shutil
import sqlite3
import zipfile
import rarfile
import asyncio
import argparse
import subprocess
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from telethon import TelegramClient, errors
from guessit import guessit

console = Console()

# ==============================================================================
# 1. INTEGRASI PERSEKITARAN (.env.local / GITHUB SECRETS)
# ==============================================================================
BASE_WORK_DIR = Path("/home/braderdin/stremio-sub-addon/SUBTITLE--SUBSCENE-ARCHIVE")
ENV_LOCAL_PATH = Path("/home/braderdin/stremio-sub-addon/.env.local")
DATA_DIR = BASE_WORK_DIR / "data"
OUTPUT_DIR = BASE_WORK_DIR / "output"
TEMP_RUNNER_DIR = BASE_WORK_DIR / "temp_runner"
SESSION_FILE = BASE_WORK_DIR / "telegram" / "processor_bot.session"

MANIFEST_DB = DATA_DIR / "cloud_parts_manifest.db"

def load_environment():
    """Memuat konfigurasi daripada .env.local jika wujud, sebaliknya guna os.environ."""
    if ENV_LOCAL_PATH.exists():
        with open(ENV_LOCAL_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

load_environment()

# Import modul live_engine sedia ada
LIVE_ENGINE_PATH = "/home/braderdin/stremio-sub-addon/live_engine"
if LIVE_ENGINE_PATH not in sys.path:
    sys.path.append(LIVE_ENGINE_PATH)

import importlib
_config = importlib.import_module("00_config")
_redis_db = importlib.import_module("01_redis_db")
_b2_storage = importlib.import_module("02_b2_storage")

# Kunci Telegram
TG_API_ID = int(os.environ.get("TG_APP_API_ID", 0))
TG_API_HASH = os.environ.get("TG_APP_API_HASH", "")
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHANNEL_ID = int(os.environ.get("TG_CHANNEL_ID", 0))
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

# ==============================================================================
# 2. PENGURUSAN NOTIFIKASI TELEGRAM BOT
# ==============================================================================
def send_telegram_notification(message_text: str):
    """Menghantar laporan status terus kepada Telegram Chat ID pengguna."""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": TG_CHAT_ID,
        "text": message_text,
        "parse_mode": "HTML"
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            pass
    except Exception as e:
        console.print(f"[yellow]⚠️ Gagal menghantar notifikasi Telegram: {e}[/yellow]")

# ==============================================================================
# 3. KORPUS CARIAN PANGKALAN DATA METADATA SQLITE
# ==============================================================================
METADATA_DBS = sorted(list(DATA_DIR.glob("subtitles_metadata_part_*.db")))

def lookup_subtitle_metadata(clean_filename: str, original_path: str) -> Dict:
    """Mencari maklumat IMDb dan metadata daripada 6 fail part .db tanpa API luaran."""
    clean_fn = Path(clean_filename).name

    # 1. Semak merentasi fail-fail pangkalan data metadata SQLite
    for db_file in METADATA_DBS:
        try:
            conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
            cur = conn.cursor()
            cur.execute("""
                SELECT imdb_id, canonical_title, clean_title, media_type, year, season, episode, language, subscene_id
                FROM subtitle_metadata
                WHERE filename = ? OR archive_path LIKE ?
                LIMIT 1;
            """, (clean_fn, f"%{clean_fn}%"))
            row = cur.fetchone()
            conn.close()

            if row:
                return {
                    "imdb_id": row[0],
                    "canonical_title": row[1],
                    "clean_title": row[2],
                    "media_type": row[3],
                    "year": row[4],
                    "season": row[5],
                    "episode": row[6],
                    "language": row[7],
                    "subscene_id": row[8]
                }
        except Exception:
            continue

    # 2. Penapis sokongan GuessIt jika entri tidak wujud dalam katalog
    try:
        guess = guessit(clean_filename)
        title = guess.get("title", Path(clean_filename).stem)
        year = guess.get("year")
        season = guess.get("season")
        episode = guess.get("episode")
        if isinstance(season, list): season = season[0]
        if isinstance(episode, list): episode = episode[0]
        media_type = "series" if (season or episode) else "movie"

        return {
            "imdb_id": None,
            "canonical_title": str(title),
            "clean_title": str(title),
            "media_type": media_type,
            "year": year,
            "season": season,
            "episode": episode,
            "language": "ms" if "malay" in clean_filename.lower() else "id",
            "subscene_id": None
        }
    except Exception:
        return {}

def extract_text_content(file_bytes: bytes) -> Optional[str]:
    """Mengekstrak teks sarikata merentasi pelbagai format pengekodan."""
    for enc in ["utf-8", "utf-8-sig", "cp1252", "latin-1", "iso-8859-1"]:
        try:
            return file_bytes.decode(enc)
        except Exception:
            continue
    return None

# ==============================================================================
# 4. PEMILIHAN & KEMAS KINI STATUS PEK ARKIB
# ==============================================================================
def get_next_uploaded_part(specified_part: Optional[str] = None) -> Optional[Tuple]:
    """Mengambil bahagian arkib yang sudah sedia di Telegram tetapi belum dimuat naik ke B2."""
    conn = sqlite3.connect(MANIFEST_DB)
    cur = conn.cursor()

    if specified_part:
        cur.execute("""
            SELECT id, archive_name, part_number, part_filename, size_bytes, tg_message_id, tg_file_id
            FROM archive_split_manifest
            WHERE part_filename = ? AND status = 'uploaded';
        """, (specified_part,))
    else:
        cur.execute("""
            SELECT id, archive_name, part_number, part_filename, size_bytes, tg_message_id, tg_file_id
            FROM archive_split_manifest
            WHERE status = 'uploaded'
            ORDER BY id ASC
            LIMIT 1;
        """)

    row = cur.fetchone()
    conn.close()
    return row

def update_part_status(part_filename: str, new_status: str):
    """Mengemas kini status pemprosesan di cloud_parts_manifest.db."""
    conn = sqlite3.connect(MANIFEST_DB)
    cur = conn.cursor()
    cur.execute("""
        UPDATE archive_split_manifest
        SET status = ?
        WHERE part_filename = ?;
    """, (new_status, part_filename))
    conn.commit()
    conn.close()

# ==============================================================================
# 5. ALIRAN PEMPROSESAN UTAMA TELETHON ➔ B2 ➔ REDIS
# ==============================================================================
async def process_cloud_archive(target_part: Optional[str] = None):
    part_record = get_next_uploaded_part(target_part)
    if not part_record:
        console.print("[bold yellow]ℹ️ Tiada bahagian arkib baharu berstatus 'uploaded' untuk diproses.[/bold yellow]")
        return

    db_id, archive_name, part_num, part_filename, size_bytes, tg_msg_id, tg_file_id = part_record

    console.print(Panel.fit(
        f"[bold cyan]🚀 ENJIN PEMPROSESAN AWAN SUBTITLE (TELEGRAM ➔ B2 ➔ REDIS)[/bold cyan]\n"
        f"[white]Pek Sasaran:[/white] [yellow]{part_filename}[/yellow] (Pek #{part_num})\n"
        f"[white]Telegram Message ID:[/white] [green]{tg_msg_id}[/green]\n"
        f"[white]Infrastruktur:[/white] [magenta]B2 Multi-Bucket & Upstash Sharding[/magenta]",
        border_style="cyan"
    ))

    # Sediakan direktori sementara yang bersih
    if TEMP_RUNNER_DIR.exists():
        shutil.rmtree(TEMP_RUNNER_DIR, ignore_errors=True)
    TEMP_RUNNER_DIR.mkdir(parents=True, exist_ok=True)

    downloaded_archive_path = TEMP_RUNNER_DIR / part_filename
    extract_target_dir = TEMP_RUNNER_DIR / "extracted"
    extract_target_dir.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(str(SESSION_FILE), TG_API_ID, TG_API_HASH)
    await client.start(bot_token=TG_BOT_TOKEN)

    start_time_stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    uploaded_b2_count = 0
    errors_encountered: List[str] = []

    try:
        # 1. Tarik Fail 50MB dari Saluran Telegram
        console.print(f"[cyan]📥 Menarik arkib dari Telegram Channel (Msg ID: {tg_msg_id})...[/cyan]")
        msg = await client.get_messages(TG_CHANNEL_ID, ids=tg_msg_id)
        if not msg:
            raise Exception(f"Mesej Telegram {tg_msg_id} tidak ditemui!")

        await client.download_media(msg, file=str(downloaded_archive_path))
        console.print(f"   [green]✔ Selesai muat turun arkib:[/green] {downloaded_archive_path.name}")

        # 2. Ekstrak arkib .7z ke folder sementara
        console.print("[cyan]📦 Mengekstrak pek arkib kendiri...[/cyan]")
        cmd_extract = ["7z", "x", "-y", f"-o{extract_target_dir}", str(downloaded_archive_path)]
        res_7z = subprocess.run(cmd_extract, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if res_7z.returncode != 0:
            raise Exception("Gagal mengekstrak arkib menggunakan arahan 7z!")

        # Padam fail arkib .7z yang dimuat turun untuk jimat ruang storan serta-merta
        downloaded_archive_path.unlink(missing_ok=True)

        # 3. Kumpulkan semua fail sarikata fizikal
        discovered_subs: List[Tuple[str, bytes]] = []

        for root, _, files in os.walk(extract_target_dir):
            for fn in files:
                f_path = Path(root) / fn
                ext = f_path.suffix.lower()

                # Jika terdapat arkib bersarang (.zip / .rar), bongkar dalam memori
                if ext == ".zip":
                    try:
                        with zipfile.ZipFile(f_path, "r") as zf:
                            for zn in zf.namelist():
                                if zn.lower().endswith((".srt", ".ass", ".ssa")):
                                    discovered_subs.append((Path(zn).name, zf.read(zn)))
                    except Exception as e:
                        errors_encountered.append(f"Ralat baca zip {fn}: {e}")
                elif ext == ".rar":
                    try:
                        with rarfile.RarFile(f_path, "r") as rf:
                            for rn in rf.namelist():
                                if rn.lower().endswith((".srt", ".ass", ".ssa")):
                                    discovered_subs.append((Path(rn).name, rf.read(rn)))
                    except Exception as e:
                        errors_encountered.append(f"Ralat baca rar {fn}: {e}")
                elif ext in [".srt", ".ass", ".ssa"]:
                    try:
                        with open(f_path, "rb") as sf:
                            discovered_subs.append((f_path.name, sf.read()))
                    except Exception as e:
                        errors_encountered.append(f"Ralat baca fail srt {fn}: {e}")

        console.print(f"   [green]✔ Sebanyak {len(discovered_subs):,} fail sarikata ditemui dalam pek ini.[/green]")

        # 4. Padanan Metadata, Muat Naik ke B2 & Rekod ke Redis
        lang_code = "ms" if archive_name == "malay" else "id"

        for sub_name, sub_bytes in discovered_subs:
            srt_content = extract_text_content(sub_bytes)
            if not srt_content or len(srt_content.strip()) < 50:
                continue

            meta = lookup_subtitle_metadata(sub_name, sub_name)
            imdb_id = meta.get("imdb_id")
            if not imdb_id or not imdb_id.startswith("tt"):
                continue

            # Semak rekod sedia ada di Redis
            existing_records = _redis_db.get_subtitle_records(imdb_id)
            existing_ids = {str(r.get("id")) for r in existing_records if isinstance(r, dict) and "id" in r}

            subscene_id = meta.get("subscene_id") or str(int(time.time()))
            record_id = f"sub_{subscene_id}_{Path(sub_name).stem}"

            if record_id in existing_ids:
                continue

            # Muat naik fail sarikata ke B2
            b2_sub_ext = Path(sub_name).suffix.lower() or ".srt"
            b2_path = f"subs/{imdb_id}/{lang_code}_{record_id}{b2_sub_ext}"
            release_name = Path(sub_name).stem

            try:
                b2_res = _b2_storage.upload_subtitle_to_b2(b2_path, srt_content)
                b2_url = b2_res["url"]
                b2_acc_idx = b2_res["account_index"]

                new_record = {
                    "id": record_id,
                    "lang": lang_code,
                    "url": b2_url,
                    "release": release_name,
                    "source": "subscene",
                    "acc": int(b2_acc_idx)
                }

                # Simpan rekod ke Upstash Redis
                season = meta.get("season")
                episode = meta.get("episode")

                if season and episode:
                    new_record["season"] = int(season)
                    new_record["episode"] = int(episode)
                    ep_imdb_key = f"{imdb_id}:{season}:{episode}"
                    _redis_db.save_subtitle_records_batch(ep_imdb_key, [new_record])

                _redis_db.save_subtitle_records_batch(imdb_id, [new_record])
                uploaded_b2_count += 1

            except _b2_storage.AllB2AccountsExhaustedException:
                errors_encountered.append("Semua kuota akaun Backblaze B2 telah habis!")
                break
            except Exception as up_err:
                errors_encountered.append(f"Muat naik B2 gagal bagi {sub_name}: {up_err}")

        # Kemas kini status dalam SQLite manifes
        update_part_status(part_filename, "completed")
        console.print(f"\n[bold green]✨ Bahagian {part_filename} siap diproses sepenuhnya![/bold green]")

    except Exception as ex:
        console.print(f"[bold red]❌ Ralat kritikal memproses bahagian {part_filename}: {ex}[/bold red]")
        errors_encountered.append(str(ex))
    finally:
        await client.disconnect()
        # Pembersihan cakera penuh (Zero Disk Footprint)
        if TEMP_RUNNER_DIR.exists():
            shutil.rmtree(TEMP_RUNNER_DIR, ignore_errors=True)

    # 5. Hantar Notifikasi Lengkap ke Telegram Pengguna
    end_time_stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    error_summary = "\n".join([f"• {e}" for e in errors_encountered[:5]]) if errors_encountered else "Tiada Ralat"

    status_icon = "✅ BERJAYA" if not errors_encountered else "⚠️ SELESAI DENGAN AMARAN"
    telegram_msg = (
        f"<b>🤖 STATUS PEMPROSESAN AWAN SUBTITLE</b>\n\n"
        f"<b>Status:</b> {status_icon}\n"
        f"<b>Pek Diproses:</b> <code>{part_filename}</code>\n"
        f"<b>Mula:</b> {start_time_stamp}\n"
        f"<b>Tamat:</b> {end_time_stamp}\n"
        f"<b>Jumlah Sarikata Dimuat Naik ke B2:</b> {uploaded_b2_count:,} fail\n"
        f"<b>Ralat:</b>\n<code>{error_summary}</code>"
    )

    send_telegram_notification(telegram_msg)

# ==============================================================================
# TITIK MASUK UTAMA CLI
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enjin Pemprosesan Awan Subtitle Telethon ke B2 & Redis")
    parser.add_argument("--part", type=str, default=None, help="Nama bahagian arkib spesifik (contoh: malay_part_001.7z)")
    args = parser.parse_args()

    asyncio.run(process_cloud_archive(target_part=args.part))