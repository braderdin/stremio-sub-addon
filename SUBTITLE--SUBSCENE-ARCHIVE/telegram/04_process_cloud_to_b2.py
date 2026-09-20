import os
import sys
import re
import io
import time
import shutil
import sqlite3
import hashlib
import zipfile
import rarfile
import asyncio
import argparse
import subprocess
import urllib.request
import urllib.parse
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Tuple, Any, Set

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.markup import escape
from telethon import TelegramClient, errors
from guessit import guessit

console = Console()

# ==============================================================================
# 1. PENGESANAN PERSEKITARAN & LALUAN SISTEM
# ==============================================================================
IS_GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"

CURRENT_FILE = Path(__file__).resolve()
TELEGRAM_DIR = CURRENT_FILE.parent                          # .../SUBTITLE--SUBSCENE-ARCHIVE/telegram
BASE_WORK_DIR = TELEGRAM_DIR.parent                         # .../SUBTITLE--SUBSCENE-ARCHIVE
REPO_ROOT = BASE_WORK_DIR.parent                            # .../stremio-sub-addon

LIVE_ENGINE_PATH = REPO_ROOT / "live_engine"
ENV_LOCAL_PATH = REPO_ROOT / ".env.local"
DATA_DIR = BASE_WORK_DIR / "data"
OUTPUT_DIR = BASE_WORK_DIR / "output"
TEMP_RUNNER_DIR = BASE_WORK_DIR / "temp_runner"
SESSION_FILE = TELEGRAM_DIR / "processor_bot.session"

MANIFEST_DB = DATA_DIR / "cloud_parts_manifest.db"

VALID_SUB_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".smi"}

def load_environment():
    """Memuat pembolehubah persekitaran tempatan secara automatik."""
    if ENV_LOCAL_PATH.exists():
        with open(ENV_LOCAL_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

load_environment()

if str(LIVE_ENGINE_PATH) not in sys.path:
    sys.path.insert(0, str(LIVE_ENGINE_PATH))

import importlib
_config = importlib.import_module("00_config")
_redis_db = importlib.import_module("01_redis_db")
_b2_storage = importlib.import_module("02_b2_storage")

TG_API_ID = int(os.environ.get("TG_APP_API_ID", 0))
TG_API_HASH = os.environ.get("TG_APP_API_HASH", "")
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHANNEL_ID = int(os.environ.get("TG_CHANNEL_ID", 0))
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

# ==============================================================================
# 2. SISTEM NOTIFIKASI TELEGRAM BOT
# ==============================================================================
def send_telegram_notification(message_text: str):
    """Menghantar laporan status akhir pemprosesan ke Telegram bot pengguna."""
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
# 3. PENAPIS INTEGRITI & BONGKAR ARKIB BERSARANG (ZERO-DUPLICATE ENGINE)
# ==============================================================================
def is_valid_sub(filename: str) -> bool:
    """Menapis hanya sambungan sarikata yang sah dan menyekat fail sampah Mac/OS."""
    fn_lower = filename.lower()
    return (
        any(fn_lower.endswith(ext) for ext in VALID_SUB_EXTENSIONS)
        and not fn_lower.startswith("__macosx")
        and ".ds_store" not in fn_lower
    )

def sanitize_sub_stem(stem: str) -> str:
    """Membersihkan stem nama fail kepada aksara alfanumerik bertitik tunggal."""
    clean = re.sub(r"[^\w\d\-_]", ".", stem)
    clean = re.sub(r"\.+", ".", clean).strip(".")
    return clean or "subtitle"

def unpack_subtitles_recursive(raw_bytes: bytes, file_name: str, parent_zip: str, slug: str, target_list: List[Dict[str, Any]], depth: int = 0):
    """Membongkar arkib ZIP bersarang di dalam memori RAM secara rekursif."""
    if depth > 2 or not raw_bytes or len(raw_bytes) < 10:
        return

    # Jika dikesan magic bytes ZIP PK\x03\x04 atau sambungan .zip
    if raw_bytes.startswith(b"PK\x03\x04") or file_name.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(raw_bytes), "r") as zf:
                for zn in zf.namelist():
                    zn_lower = zn.lower()
                    if "__macosx" in zn_lower or ".ds_store" in zn_lower:
                        continue
                    inner_bytes = zf.read(zn)
                    if is_valid_sub(zn):
                        target_list.append({
                            "sub_filename": Path(zn).name,
                            "sub_bytes": inner_bytes,
                            "parent_zip": parent_name_resolver(parent_zip, file_name),
                            "slug": slug
                        })
                    elif inner_bytes.startswith(b"PK\x03\x04") or zn_lower.endswith(".zip"):
                        unpack_subtitles_recursive(inner_bytes, Path(zn).name, file_name, slug, target_list, depth + 1)
        except Exception:
            pass
    elif is_valid_sub(file_name):
        target_list.append({
            "sub_filename": Path(file_name).name,
            "sub_bytes": raw_bytes,
            "parent_zip": parent_zip,
            "slug": slug
        })

def parent_name_resolver(parent: str, child: str) -> str:
    return child if not parent else parent

# ==============================================================================
# 4. CARIAN METADATA SQLITE & FALLBACK CINEMETA
# ==============================================================================
METADATA_DBS = sorted(list(DATA_DIR.glob("subtitles_metadata_part_*.db")))

def fetch_cinemeta_imdb(query_title: str, year: Optional[int], media_type: str = "movie") -> Tuple[Optional[str], Optional[str]]:
    """Mendapatkan IMDb ID melalui API awam Cinemeta jika pangkalan data SQLite kosong."""
    clean_q = re.sub(r"[-_]+", " ", query_title).strip()
    if not clean_q:
        return None, None

    encoded = urllib.parse.quote(clean_q)
    url = f"https://v3-cinemeta.strem.io/catalog/{media_type}/top/search={encoded}.json"
    headers = {"User-Agent": "Mozilla/5.0 (StremioSubArchiveFast/2.0)"}

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                metas = data.get("metas", [])
                if not metas and media_type == "series":
                    return fetch_cinemeta_imdb(clean_q, year, "movie")
                if metas:
                    for m in metas:
                        rel_yr = m.get("releaseInfo") or m.get("year")
                        if year and rel_yr:
                            try:
                                if abs(int(str(rel_yr)[:4]) - int(year)) <= 1:
                                    return m.get("imdb_id"), m.get("name")
                            except Exception:
                                pass
                    return metas[0].get("imdb_id"), metas[0].get("name")
    except Exception:
        pass
    return None, None

def lookup_subtitle_metadata(parent_zip_name: str, slug: str, sub_filename: str) -> Dict[str, Any]:
    """Carian metadata pantas SQLite berdasarkan nama bungkusan arkib dan slug tajuk."""
    clean_zip = Path(parent_zip_name).name

    for db_file in METADATA_DBS:
        try:
            conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
            cur = conn.cursor()

            cur.execute("""
                SELECT imdb_id, canonical_title, clean_title, media_type, year, season, episode, language, subscene_id
                FROM subtitle_metadata
                WHERE filename = ?
                LIMIT 1;
            """, (clean_zip,))
            row = cur.fetchone()

            if not row and slug:
                cur.execute("""
                    SELECT imdb_id, canonical_title, clean_title, media_type, year, season, episode, language, subscene_id
                    FROM subtitle_metadata
                    WHERE slug = ?
                    LIMIT 1;
                """, (slug,))
                row = cur.fetchone()

            conn.close()

            if row and row[0]:
                return {
                    "imdb_id": row[0],
                    "canonical_title": row[1] or row[2],
                    "clean_title": row[2] or slug,
                    "media_type": row[3] or "movie",
                    "year": row[4],
                    "season": row[5],
                    "episode": row[6],
                    "language": row[7] or ("ms" if "malay" in slug.lower() else "id"),
                    "subscene_id": row[8]
                }
        except Exception:
            continue

    try:
        guess = guessit(sub_filename)
        g_title = guess.get("title", slug.replace("-", " "))
        g_year = guess.get("year")
        g_season = guess.get("season")
        g_episode = guess.get("episode")
        if isinstance(g_season, list): g_season = g_season[0]
        if isinstance(g_episode, list): g_episode = g_episode[0]
        m_type = "series" if (g_season or g_episode) else "movie"

        imdb_id, canon_title = fetch_cinemeta_imdb(str(g_title), g_year, m_type)

        return {
            "imdb_id": imdb_id,
            "canonical_title": canon_title or str(g_title),
            "clean_title": str(g_title),
            "media_type": m_type,
            "year": g_year,
            "season": g_season,
            "episode": g_episode,
            "language": "ms" if "malay" in parent_zip_name.lower() else "id",
            "subscene_id": None
        }
    except Exception:
        return {}

def extract_text_content(file_bytes: bytes) -> Optional[str]:
    """Mengekstrak teks merentasi pelbagai format pengekodan abjad."""
    for enc in ["utf-8", "utf-8-sig", "cp1252", "latin-1", "iso-8859-1"]:
        try:
            return file_bytes.decode(enc)
        except Exception:
            continue
    return None

def detect_season_episode(sub_filename: str, default_season: Optional[int] = None) -> Tuple[Optional[int], Optional[int]]:
    """Mengekstrak nombor Musim dan Episod untuk rujukan siri televisyen."""
    m = re.search(r"\b[sS](\d+)[eE](\d+)\b", sub_filename)
    if m:
        return int(m.group(1)), int(m.group(2))

    m2 = re.search(r"\b(\d+)x(\d+)\b", sub_filename)
    if m2:
        return int(m2.group(1)), int(m2.group(2))

    m3 = re.search(r"\b(?:ep|episode|e)[.\s_-]*(\d+)\b", sub_filename, re.I)
    if m3:
        s = default_season if default_season is not None else 1
        return s, int(m3.group(1))

    return default_season, None

# ==============================================================================
# 5. PENGURUSAN STATUS MANIFES AWAN (SQLite)
# ==============================================================================
def get_next_uploaded_part(specified_part: Optional[str] = None) -> Optional[Tuple]:
    """Mengambil rekod pek seterusnya yang berstatus 'uploaded'."""
    if not MANIFEST_DB.exists():
        console.print(f"[bold red]❌ Pangkalan data {MANIFEST_DB} tidak dijumpai![/bold red]")
        return None

    conn = sqlite3.connect(str(MANIFEST_DB))
    cur = conn.cursor()

    if specified_part:
        cur.execute("""
            SELECT id, archive_name, part_number, part_filename, size_bytes, tg_message_id, tg_file_id
            FROM archive_split_manifest
            WHERE part_filename = ?;
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
    """Mengemas kini status bahagian arkib kepada 'completed'."""
    conn = sqlite3.connect(str(MANIFEST_DB))
    cur = conn.cursor()
    cur.execute("""
        UPDATE archive_split_manifest
        SET status = ?
        WHERE part_filename = ?;
    """, (new_status, part_filename))
    conn.commit()
    conn.close()

# ==============================================================================
# 6. ALIRAN PEMPROSESAN UTAMA (TELEGRAM ➔ B2 ➔ REDIS)
# ==============================================================================
async def process_cloud_archive(target_part: Optional[str] = None):
    part_record = get_next_uploaded_part(target_part)
    if not part_record:
        console.print("[bold yellow]ℹ️ Tiada bahagian arkib baharu berstatus 'uploaded' untuk diproses.[/bold yellow]")
        return

    db_id, archive_name, part_num, part_filename, size_bytes, tg_msg_id, tg_file_id = part_record

    console.print(Panel.fit(
        f"[bold cyan]🚀 ENJIN PEMPROSESAN AWAN SUBTITLE (TELEGRAM ➔ B2 ➔ REDIS - ZERO-DUPLICATE)[/bold cyan]\n"
        f"[white]Pek Sasaran:[/white] [yellow]{part_filename}[/yellow] (Pek #{part_num})\n"
        f"[white]Telegram Message ID:[/white] [green]{tg_msg_id}[/green]\n"
        f"[white]Mod Paparan Log:[/white] [magenta]{'GitHub Actions (Padat 1-Baris)' if IS_GITHUB_ACTIONS else 'Tempatan (Jadual Interaktif)'}[/magenta]",
        border_style="cyan"
    ))

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
    skipped_exist_count = 0
    skipped_duplicate_count = 0
    errors_encountered: List[str] = []

    try:
        # 1. Menarik fail pek arkib dari Saluran Telegram
        console.print(f"[cyan]📥 Menarik arkib dari Saluran Telegram (Msg ID: {tg_msg_id})...[/cyan]")
        msg = await client.get_messages(TG_CHANNEL_ID, ids=tg_msg_id)
        if not msg:
            raise Exception(f"Mesej Telegram ID {tg_msg_id} tidak ditemui!")

        await client.download_media(msg, file=str(downloaded_archive_path))
        console.print(f"   [green]✔ Muat turun arkib selesai:[/green] {downloaded_archive_path.name}")

        # 2. Mengekstrak pek arkib kendiri
        console.print("[cyan]📦 Mengekstrak pek arkib ke direktori sementara...[/cyan]")
        cmd_extract = ["7z", "x", "-y", f"-o{extract_target_dir}", str(downloaded_archive_path)]
        res_7z = subprocess.run(cmd_extract, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if res_7z.returncode != 0:
            raise Exception("Gagal mengekstrak fail .7z menggunakan arahan sistem 7z!")

        downloaded_archive_path.unlink(missing_ok=True)

        # 3. Ekstraksi Pintar: Mengumpul sarikata & membongkar ZIP bersarang
        discovered_subs: List[Dict[str, Any]] = []

        for root, _, files in os.walk(extract_target_dir):
            for fn in files:
                f_path = Path(root) / fn
                ext = f_path.suffix.lower()
                rel_parts = f_path.relative_to(extract_target_dir).parts
                slug = rel_parts[-2] if len(rel_parts) >= 2 else f_path.parent.name

                if ext == ".zip":
                    try:
                        with zipfile.ZipFile(f_path, "r") as zf:
                            for zn in zf.namelist():
                                zn_lower = zn.lower()
                                if "__macosx" in zn_lower or ".ds_store" in zn_lower:
                                    continue
                                raw_bytes = zf.read(zn)
                                unpack_subtitles_recursive(raw_bytes, Path(zn).name, fn, slug, discovered_subs)
                    except Exception as e:
                        errors_encountered.append(f"Ralat ekstrak zip {fn}: {e}")
                elif ext == ".rar":
                    try:
                        with rarfile.RarFile(f_path, "r") as rf:
                            for rn in rf.namelist():
                                rn_lower = rn.lower()
                                if "__macosx" in rn_lower or ".ds_store" in rn_lower:
                                    continue
                                raw_bytes = rf.read(rn)
                                unpack_subtitles_recursive(raw_bytes, Path(rn).name, fn, slug, discovered_subs)
                    except Exception as e:
                        errors_encountered.append(f"Ralat ekstrak rar {fn}: {e}")
                elif is_valid_sub(fn):
                    try:
                        with open(f_path, "rb") as sf:
                            raw_bytes = sf.read()
                            unpack_subtitles_recursive(raw_bytes, fn, fn, slug, discovered_subs)
                    except Exception as e:
                        errors_encountered.append(f"Ralat baca fail srt {fn}: {e}")

        total_discovered = len(discovered_subs)
        console.print(f"   [green]✔ Sebanyak {total_discovered:,} fail sarikata bersih ditemui dalam pek ini.[/green]\n")

        # 4. Muat naik sarikata ke B2 dan kemas kini rekod ke Redis (Dengan Tapisan Duplikasi MD5)
        lang_code = "ms" if archive_name == "malay" else "id"
        seen_imdb_hashes: Dict[str, Set[str]] = {}

        for item in discovered_subs:
            sub_filename = item["sub_filename"]
            sub_bytes = item["sub_bytes"]
            parent_zip = item["parent_zip"]
            slug = item["slug"]

            srt_content = extract_text_content(sub_bytes)
            if not srt_content or len(srt_content.strip()) < 50:
                continue

            meta = lookup_subtitle_metadata(parent_zip, slug, sub_filename)
            imdb_id = meta.get("imdb_id")

            if not imdb_id or not imdb_id.startswith("tt"):
                continue

            # ==============================================================
            # SEMAKAN CAP JARI KANDUNGAN (CONTENT MD5 HASH ANTI-DUPLICATE)
            # ==============================================================
            content_md5 = hashlib.md5(srt_content.strip().encode("utf-8", errors="ignore")).hexdigest()
            if imdb_id not in seen_imdb_hashes:
                seen_imdb_hashes[imdb_id] = set()

            if content_md5 in seen_imdb_hashes[imdb_id]:
                skipped_duplicate_count += 1
                continue  # Gugurkan fail duplikasi serta-merta tanpa muat naik ke B2

            seen_imdb_hashes[imdb_id].add(content_md5)

            raw_release = Path(sub_filename).stem
            safe_stem = sanitize_sub_stem(raw_release)
            subscene_id = meta.get("subscene_id") or str(abs(hash(raw_release)) % 10000000)
            
            record_id = f"sub_{subscene_id}_{safe_stem[:30]}".rstrip(".")

            # Semak status rekod sedia ada di Redis
            existing_records = _redis_db.get_subtitle_records(imdb_id)
            has_valid_clean_record = any(
                isinstance(r, dict)
                and (r.get("id") == record_id or r.get("id") == f"sub_{subscene_id}_{raw_release[:30]}")
                and " " not in r.get("url", "")
                and r.get("url", "").startswith("http")
                for r in existing_records
            )

            if has_valid_clean_record:
                skipped_exist_count += 1
                continue

            # Menjana laluan fail Backblaze B2 (Bebas Ruang Kosong)
            b2_sub_ext = Path(sub_filename).suffix.lower() if Path(sub_filename).suffix in VALID_SUB_EXTENSIONS else ".srt"
            b2_path = f"subs/{imdb_id}/{lang_code}_{record_id}{b2_sub_ext}"

            try:
                b2_res = _b2_storage.upload_subtitle_to_b2(b2_path, srt_content)
                b2_url = b2_res["url"]
                b2_acc_idx = b2_res["account_index"]

                target_redis = _redis_db.get_target_redis_account(imdb_id)
                target_redis_idx = target_redis.get("index", 1)

                new_record = {
                    "id": record_id,
                    "lang": lang_code,
                    "url": b2_url,
                    "release": raw_release,
                    "source": "subscene",
                    "acc": int(b2_acc_idx)
                }

                s_num, e_num = detect_season_episode(sub_filename, default_season=meta.get("season"))

                if s_num is not None and e_num is not None:
                    new_record["season"] = s_num
                    new_record["episode"] = e_num
                    ep_imdb_key = f"{imdb_id}:{s_num}:{e_num}"
                    
                    _redis_db.save_subtitle_records_batch(ep_imdb_key, [new_record])
                    _redis_db.save_subtitle_records_batch(imdb_id, [new_record])
                    display_target = f"{imdb_id} (S{s_num:02d}E{e_num:02d} ➔ {ep_imdb_key})"
                else:
                    _redis_db.save_subtitle_records_batch(imdb_id, [new_record])
                    display_target = imdb_id

                uploaded_b2_count += 1

                try:
                    safe_fn = escape(sub_filename[:42])
                    safe_url = escape(b2_url)

                    if IS_GITHUB_ACTIONS:
                        console.print(
                            f"[bold green]✓ [{uploaded_b2_count}][/bold green] "
                            f"[cyan]{display_target}[/cyan] | "
                            f"[bold yellow]B2 #{b2_acc_idx}[/bold yellow] | "
                            f"[bold magenta]Redis #{target_redis_idx}[/bold magenta] | "
                            f"[dim]{safe_fn}[/dim]"
                        )
                    else:
                        table = Table(title=f"✨ Status Muat Naik [{uploaded_b2_count}]", border_style="green")
                        table.add_column("Atribut", style="cyan", no_wrap=True)
                        table.add_column("Nilai / Keterangan", style="white")

                        table.add_row("Pek Arkib", part_filename)
                        table.add_row("Nama Fail", safe_fn)
                        table.add_row("IMDb Sasaran", display_target)
                        table.add_row("Akaun B2 Digunakan", f"[bold yellow]Akaun B2 #{b2_acc_idx}[/bold yellow] ({b2_res['bucket_name']})")
                        table.add_row("Cloudflare Worker URL", safe_url)
                        table.add_row("Akaun Redis Digunakan", f"[bold magenta]Akaun Redis #{target_redis_idx}[/bold magenta] (Shard Modulo)")

                        console.print(table)
                except Exception:
                    pass

            except _b2_storage.AllB2AccountsExhaustedException:
                errors_encountered.append("Kesemua kuota akaun Backblaze B2 telah habis!")
                break
            except Exception as up_err:
                errors_encountered.append(f"Gagal muat naik {sub_filename}: {up_err}")

        # Mengemas kini status manifes jika berjaya diproses
        if uploaded_b2_count > 0 or (skipped_exist_count + skipped_duplicate_count) == total_discovered:
            update_part_status(part_filename, "completed")
            console.print(f"\n[bold green]✨ Bahagian {part_filename} selesai diproses sepenuhnya![/bold green]")
        else:
            console.print(f"\n[bold red]⚠️ Amaran: 0 fail dimuat naik bagi {part_filename}. Status kekal 'uploaded'.[/bold red]")

    except Exception as ex:
        console.print(f"[bold red]❌ Ralat kritikal pada {part_filename}: {ex}[/bold red]")
        errors_encountered.append(str(ex))
    finally:
        await client.disconnect()
        if TEMP_RUNNER_DIR.exists():
            shutil.rmtree(TEMP_RUNNER_DIR, ignore_errors=True)

    # 5. Menghantar ringkasan laporan status ke Telegram
    end_time_stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    error_summary = "\n".join([f"• {e}" for e in errors_encountered[:5]]) if errors_encountered else "Tiada Ralat"

    status_icon = "✅ BERJAYA" if (uploaded_b2_count > 0 and not errors_encountered) else "⚠️ PERIKSA LAPORAN"
    telegram_msg = (
        f"<b>🤖 STATUS PEMPROSESAN AWAN SUBTITLE (V2.3 ZERO-DUPLICATE)</b>\n\n"
        f"<b>Status:</b> {status_icon}\n"
        f"<b>Pek Diproses:</b> <code>{part_filename}</code>\n"
        f"<b>Mula:</b> {start_time_stamp}\n"
        f"<b>Tamat:</b> {end_time_stamp}\n"
        f"<b>Jumlah Dimuat Naik ke B2:</b> {uploaded_b2_count:,} fail\n"
        f"<b>Dilangkau (Duplikasi Kandungan):</b> {skipped_duplicate_count:,} fail\n"
        f"<b>Dilangkau (Sedia Wujud di Redis):</b> {skipped_exist_count:,} fail\n"
        f"<b>Ralat:</b>\n<code>{error_summary}</code>"
    )

    send_telegram_notification(telegram_msg)

# ==============================================================================
# TITIK MASUK UTAMA SKRIP (CLI)
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enjin Pemprosesan Awan Subtitle Telethon ke B2 & Redis (Zero-Duplicate)")
    parser.add_argument("--part", type=str, default=None, help="Nama bahagian arkib spesifik (contoh: indonesian_part_001.7z)")
    args = parser.parse_args()

    asyncio.run(process_cloud_archive(target_part=args.part))