import os
import sys
import time
import json
import asyncio
import sqlite3
from pathlib import Path
from datetime import datetime
import urllib.request
import urllib.error

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import (
    Progress,
    BarColumn,
    TextColumn,
    TransferSpeedColumn,
    TimeRemainingColumn,
    FileSizeColumn,
    TotalFileSizeColumn,
)

from telethon import TelegramClient, errors

console = Console()

# Direktori & Fail Pangkalan Data
BASE_DIR = Path("/home/braderdin/stremio-sub-addon/SUBTITLE--SUBSCENE-ARCHIVE")
DATA_DIR = BASE_DIR / "data"
MANIFEST_DB = DATA_DIR / "split_manifest.db"
SPLIT_DIR = BASE_DIR / "archive-split"
ENV_LOCAL_PATH = Path("/home/braderdin/stremio-sub-addon/.env.local")
SESSION_FILE = BASE_DIR / "telegram" / "uploader_bot.session"

def load_env_local(env_path: Path):
    """Membaca kunci konfigurasi terus daripada .env.local."""
    if not env_path.exists():
        console.print(f"[bold red]❌ Fail .env.local tidak dijumpai di {env_path}![/bold red]")
        sys.exit(1)

    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip('"').strip("'")

load_env_local(ENV_LOCAL_PATH)

# Konfigurasi Telegram
TG_API_ID = int(os.environ.get("TG_APP_API_ID", 0))
TG_API_HASH = os.environ.get("TG_APP_API_HASH", "")
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHANNEL_ID = int(os.environ.get("TG_CHANNEL_ID", 0))

# Konfigurasi Redis Telegram
REDIS_URL = os.environ.get("TG_UPSTASH_REDIS_REST_URL", "").rstrip("/")
REDIS_TOKEN = os.environ.get("TG_UPSTASH_REDIS_REST_TOKEN", "")

if not all([TG_API_ID, TG_API_HASH, TG_BOT_TOKEN, TG_CHANNEL_ID, REDIS_URL, REDIS_TOKEN]):
    console.print("[bold red]❌ Konfigurasi Telegram atau Redis tidak lengkap dalam .env.local![/bold red]")
    sys.exit(1)

def redis_set(key: str, value: str) -> bool:
    """Menyimpan data ke Upstash Redis melalui REST API standard."""
    url = f"{REDIS_URL}"
    headers = {
        "Authorization": f"Bearer {REDIS_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = json.dumps(["SET", key, value]).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        console.print(f"[yellow]⚠️ Ralat menulis ke Redis bagi {key}: {e}[/yellow]")
        return False

def get_pending_parts():
    """Mengambil senarai fail arkib yang belum dimuat naik dari SQLite."""
    conn = sqlite3.connect(MANIFEST_DB)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, archive_name, part_number, part_filename, size_bytes, size_mb, sha256_hash
        FROM archive_split_manifest
        WHERE status != 'uploaded'
        ORDER BY archive_name DESC, part_number ASC
    """)
    rows = cursor.fetchall()
    conn.close()
    return rows

def update_manifest_status(part_filename: str, tg_msg_id: int, tg_file_id: str):
    """Kemas kini rekod pecahan arkib kepada status uploaded."""
    conn = sqlite3.connect(MANIFEST_DB)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE archive_split_manifest
        SET status = 'uploaded',
            tg_message_id = ?,
            tg_file_id = ?,
            uploaded_at = CURRENT_TIMESTAMP
        WHERE part_filename = ?
    """, (tg_msg_id, str(tg_file_id), part_filename))
    conn.commit()
    conn.close()

async def main():
    pending_items = get_pending_parts()
    total_pending = len(pending_items)

    if total_pending == 0:
        console.print("[bold green]✔ Semua bahagian arkib telah selesai dimuat naik ke Telegram![/bold green]")
        return

    console.print(Panel.fit(
        f"[bold cyan]🚀 MEMULAKAN MUAT NAIK ARKIB KE TELEGRAM PRIVATE CHANNEL[/bold cyan]\n"
        f"[white]Channel ID:[/white] [yellow]{TG_CHANNEL_ID}[/yellow]\n"
        f"[white]Jumlah Bahagian Perlu Dimuat Naik:[/white] [green]{total_pending} fail[/green]\n"
        f"[white]Sela Masa (Delay):[/white] [magenta]2 saat per fail[/magenta]\n"
        f"[white]Penyegerakan:[/white] [cyan]split_manifest.db + Upstash Redis[/cyan]",
        border_style="cyan"
    ))

    # Inisialisasi Klien Telethon Bot
    client = TelegramClient(str(SESSION_FILE), TG_API_ID, TG_API_HASH)
    await client.start(bot_token=TG_BOT_TOKEN)

    uploaded_count = 0

    for idx, item in enumerate(pending_items, 1):
        db_id, archive_name, part_num, part_filename, size_bytes, size_mb, sha256_hash = item
        file_path = SPLIT_DIR / part_filename

        if not file_path.exists():
            console.print(f"[bold red]❌ Fail fizikal tidak ditemui: {file_path}[/bold red]")
            continue

        caption_text = (
            f"📦 <b>Arkib:</b> <code>{archive_name.upper()}</code>\n"
            f"🧩 <b>Pek:</b> #{part_num:03d} (<code>{part_filename}</code>)\n"
            f"📊 <b>Saiz:</b> {size_mb} MB\n"
            f"🔒 <b>SHA-256:</b> <code>{sha256_hash}</code>"
        )

        with Progress(
            TextColumn(f"[bold cyan][{idx}/{total_pending}][/bold cyan] {part_filename}"),
            BarColumn(),
            FileSizeColumn(),
            TotalFileSizeColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console
        ) as progress:
            upload_task = progress.add_task("Uploading", total=size_bytes)

            def progress_callback(current, total):
                progress.update(upload_task, completed=current)

            while True:
                try:
                    message = await client.send_file(
                        entity=TG_CHANNEL_ID,
                        file=file_path,
                        caption=caption_text,
                        parse_mode="html",
                        progress_callback=progress_callback
                    )
                    break
                except errors.FloodWaitError as fwe:
                    console.print(f"[bold yellow]⚠️ FloodWait: Rehat selama {fwe.seconds} saat...[/bold yellow]")
                    await asyncio.sleep(fwe.seconds + 1)
                except Exception as ex:
                    console.print(f"[bold red]❌ Ralat ketika muat naik {part_filename}: {ex}[/bold red]")
                    await asyncio.sleep(5)

        tg_msg_id = message.id
        tg_doc_id = message.media.document.id if message.media and hasattr(message.media, "document") else ""

        # 1. Simpan Metadata ke Upstash Redis
        redis_key = f"tg_archive:{archive_name}:part_{part_num:03d}"
        redis_data = {
            "archive_name": archive_name,
            "part_number": part_num,
            "part_filename": part_filename,
            "size_bytes": size_bytes,
            "size_mb": size_mb,
            "sha256_hash": sha256_hash,
            "tg_channel_id": TG_CHANNEL_ID,
            "tg_message_id": tg_msg_id,
            "tg_file_id": str(tg_doc_id),
            "uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        redis_set(redis_key, json.dumps(redis_data))

        # Simpan pemetaan carian pantas mengikut nama fail
        redis_set(f"tg_archive_filename:{part_filename}", str(tg_msg_id))

        # 2. Kemas kini SQLite split_manifest.db
        update_manifest_status(part_filename, tg_msg_id, str(tg_doc_id))

        uploaded_count += 1

        # Sela masa 2 saat sebelum muat naik fail berikutnya
        await asyncio.sleep(2)

    await client.disconnect()

    console.print(Panel.fit(
        f"[bold green]✨ SEMUA BAHAGIAN BERJAYA DIMUAT NAIK![/bold green]\n"
        f"[cyan]Jumlah Selesai Sesi Ini: {uploaded_count} fail arkib.[/cyan]",
        border_style="green"
    ))

if __name__ == "__main__":
    asyncio.run(main())