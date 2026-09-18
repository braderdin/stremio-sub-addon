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
import json
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Tuple, Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.markup import escape
from telethon import TelegramClient, errors
from guessit import guessit

console = Console()

# ==============================================================================
# 1. PENGESANAN PERSEKITARAN & RESOLUSI LALUAN
# ==============================================================================
# [FUNGSI KOD]: Mengesan sama ada skrip dijalankan di GitHub Actions atau WSL tempatan
IS_GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"

# [FUNGSI KOD]: Resolusi direktori projek secara dinamik mengikut kedudukan fizikal fail
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

# [FUNGSI KOD]: Memuat kunci rahsia daripada .env.local untuk kegunaan pengujian tempatan
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

# [FUNGSI KOD]: Menambah folder modul live_engine ke dalam laluan import Python
if str(LIVE_ENGINE_PATH) not in sys.path:
    sys.path.insert(0, str(LIVE_ENGINE_PATH))

import importlib
_config = importlib.import_module("00_config")
_redis_db = importlib.import_module("01_redis_db")
_b2_storage = importlib.import_module("02_b2_storage")

# [FUNGSI KOD]: Kunci API Telegram Client dan Bot Notifikasi
TG_API_ID = int(os.environ.get("TG_APP_API_ID", 0))
TG_API_HASH = os.environ.get("TG_APP_API_HASH", "")
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHANNEL_ID = int(os.environ.get("TG_CHANNEL_ID", 0))
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

# ==============================================================================
# 2. SISTEM NOTIFIKASI TELEGRAM BOT
# ==============================================================================
# [FUNGSI KOD]: Menghantar mesej ringkasan status operasi ke chat peribadi Telegram
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
# 3. CARIAN METADATA SQLITE & FALLBACK CINEMETA
# ==============================================================================
METADATA_DBS = sorted(list(DATA_DIR.glob("subtitles_metadata_part_*.db")))

# [FUNGSI KOD]: Carian IMDb ID sandaran melalui API Cinemeta jika tiada rekod SQLite
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

# [FUNGSI KOD]: Membaca metadata sarikata dari fail SQLite atau pustaka GuessIt
def lookup_subtitle_metadata(parent_zip_name: str, slug: str, sub_filename: str) -> Dict[str, Any]:
    """Carian metadata pantas SQLite berdasarkan nama bungkusan arkib dan slug tajuk."""
    clean_zip = Path(parent_zip_name).name

    for db_file in METADATA_DBS:
        try:
            conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
            cur = conn.cursor()

            # 1. Padanan berasaskan nama pek arkib (.zip / .rar)
            cur.execute("""
                SELECT imdb_id, canonical_title, clean_title, media_type, year, season, episode, language, subscene_id
                FROM subtitle_metadata
                WHERE filename = ?
                LIMIT 1;
            """, (clean_zip,))
            row = cur.fetchone()

            # 2. Padanan berasaskan folder slug tajuk
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

    # 3. Analisis nama menggunakan GuessIt & Cinemeta jika tiada dalam SQLite
    try:
        guess = guessit(sub_filename)
        g_title = guess.get("title", slug.replace("-", " "))
        g_year = guess.get("year")
        g_season = guess.get("season")
        g_episode = guess.get("episode")
        if isinstance(g_season, list): g_season = g_season[0]
        if isinstance(g_episode, list): g_episode = g_episode[0]

        # Sekat musim tidak munasabah yang terhasil daripada tahun
        if g_season and int(g_season) > 99:
            g_season = None

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

# [FUNGSI KOD]: Menyahkod bait fail sarikata merentasi pelbagai format teks
def extract_text_content(file_bytes: bytes) -> Optional[str]:
    """Mengekstrak teks merentasi pelbagai format pengekodan abjad."""
    for enc in ["utf-8", "utf-8-sig", "cp1252", "latin-1", "iso-8859-1"]:
        try:
            return file_bytes.decode(enc)
        except Exception:
            continue
    return None

# [FUNGSI KOD]: Mengesan Musim dan Episod dengan penapis kalis resolusi skrin (848x480) & tahun
def detect_season_episode(sub_filename: str, default_season: Optional[int] = None) -> Tuple[Optional[int], Optional[int]]:
    """
    Mengekstrak nombor Musim dan Episod untuk rujukan siri televisyen.
    Kalis daripada kekeliruan resolusi skrin (cth: 848x480) dan tahun (cth: 2018/2019).
    """
    # 1. Bersihkan resolusi video lazim dan tag kualiti terlebih dahulu
    clean_name = re.sub(r"\b(?:\d{3,4}x\d{3,4}|480p|576p|720p|1080p|2160p|4k|uhd)\b", "", sub_filename, flags=re.I)

    # 2. Corak piawai: S01E02 / S1E2 / S01.E02 (Musim: 1-99, Episod: 1-1500)
    m = re.search(r"\b[sS](\d{1,2})[.\s_-]*[eE](\d{1,3})\b", clean_name)
    if m:
        s, e = int(m.group(1)), int(m.group(2))
        if 1 <= s <= 99 and 1 <= e <= 1500:
            return s, e

    # 3. Corak perkataan penuh episod: ep10, episode 09
    m3 = re.search(r"\b(?:ep|episode)[.\s_-]*(\d{1,3})\b", clean_name, re.I)
    if m3:
        e = int(m3.group(1))
        s = default_season if (default_season is not None and 1 <= default_season <= 99) else 1
        if 1 <= s <= 99 and 1 <= e <= 1500:
            return s, e

    # Corak singkatan e01 / e12 (sempadan perkataan ketat agar tidak padan perkataan seperti 'pahe'/'finale')
    m3_alt = re.search(r"\b[eE](\d{1,3})\b", clean_name)
    if m3_alt:
        e = int(m3_alt.group(1))
        s = default_season if (default_season is not None and 1 <= default_season <= 99) else 1
        if 1 <= s <= 99 and 1 <= e <= 1500:
            return s, e

    # 4. Corak 1x02 / 01x02 (Dihadkan ketat: Musim 1-2 digit, Episod 1-3 digit)
    m2 = re.search(r"\b(\d{1,2})x(\d{1,3})\b", clean_name)
    if m2:
        s, e = int(m2.group(1)), int(m2.group(2))
        if 1 <= s <= 99 and 1 <= e <= 1500:
            return s, e

    # 5. Nilai musim sedia ada dari metadata jika tiada nombor episod spesifik
    if default_season is not None and 1 <= default_season <= 99:
        return default_season, None

    return None, None

# ==============================================================================
# 4. PENGURUSAN STATUS MANIFES AWAN (SQLite)
# ==============================================================================
# [FUNGSI KOD]: Mengambil giliran rekod pek arkib yang berstatus 'uploaded'
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

# [FUNGSI KOD]: Mengemas kini status penyelesaian pek arkib kepada 'completed'
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
# 5. ALIRAN PEMPROSESAN UTAMA (TELEGRAM ➔ B2 ➔ REDIS)
# ==============================================================================
# [FUNGSI KOD]: Aliran kerja muat turun, ekstraksi memori, tapisan teks, muat naik B2, & rekod Redis
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
        f"[white]Mod Paparan Log:[/white] [magenta]{'GitHub Actions (Padat 1-Baris)' if IS_GITHUB_ACTIONS else 'Tempatan (Jadual Interaktif)'}[/magenta]",
        border_style="cyan"
    ))

    # [FUNGSI KOD]: Persediaan direktori operasi sementara
    if TEMP_RUNNER_DIR.exists():
        shutil.rmtree(TEMP_RUNNER_DIR, ignore_errors=True)
    TEMP_RUNNER_DIR.mkdir(parents=True, exist_ok=True)

    downloaded_archive_path = TEMP_RUNNER_DIR / part_filename
    extract_target_dir = TEMP_RUNNER_DIR / "extracted"
    extract_target_dir.mkdir(parents=True, exist_ok=True)

    # [FUNGSI KOD]: Permulaan klien MTProto Telethon
    client = TelegramClient(str(SESSION_FILE), TG_API_ID, TG_API_HASH)
    await client.start(bot_token=TG_BOT_TOKEN)

    start_time_stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    uploaded_b2_count = 0
    skipped_exist_count = 0
    errors_encountered: List[str] = []

    try:
        # [FUNGSI KOD]: 1. Menarik fail pek arkib dari Saluran Telegram
        console.print(f"[cyan]📥 Menarik arkib dari Saluran Telegram (Msg ID: {tg_msg_id})...[/cyan]")
        msg = await client.get_messages(TG_CHANNEL_ID, ids=tg_msg_id)
        if not msg:
            raise Exception(f"Mesej Telegram ID {tg_msg_id} tidak ditemui!")

        await client.download_media(msg, file=str(downloaded_archive_path))
        console.print(f"   [green]✔ Muat turun arkib selesai:[/green] {downloaded_archive_path.name}")

        # [FUNGSI KOD]: 2. Ekstraksi fail arkib .7z menggunakan binary sistem p7zip
        console.print("[cyan]📦 Mengekstrak pek arkib ke direktori sementara...[/cyan]")
        cmd_extract = ["7z", "x", "-y", f"-o{extract_target_dir}", str(downloaded_archive_path)]
        res_7z = subprocess.run(cmd_extract, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if res_7z.returncode != 0:
            raise Exception("Gagal mengekstrak fail .7z menggunakan arahan sistem 7z!")

        downloaded_archive_path.unlink(missing_ok=True)

        # [FUNGSI KOD]: 3. Mengimbas fail arkib dalaman (.zip/.rar/.srt) ke dalam bait memori RAM
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
                                if zn.lower().endswith((".srt", ".ass", ".ssa")):
                                    discovered_subs.append({
                                        "sub_filename": Path(zn).name,
                                        "sub_bytes": zf.read(zn),
                                        "parent_zip": fn,
                                        "slug": slug
                                    })
                    except Exception as e:
                        errors_encountered.append(f"Ralat ekstrak zip {fn}: {e}")
                elif ext == ".rar":
                    try:
                        with rarfile.RarFile(f_path, "r") as rf:
                            for rn in rf.namelist():
                                if rn.lower().endswith((".srt", ".ass", ".ssa")):
                                    discovered_subs.append({
                                        "sub_filename": Path(rn).name,
                                        "sub_bytes": rf.read(rn),
                                        "parent_zip": fn,
                                        "slug": slug
                                    })
                    except Exception as e:
                        errors_encountered.append(f"Ralat ekstrak rar {fn}: {e}")
                elif ext in [".srt", ".ass", ".ssa"]:
                    try:
                        with open(f_path, "rb") as sf:
                            discovered_subs.append({
                                "sub_filename": fn,
                                "sub_bytes": sf.read(),
                                "parent_zip": fn,
                                "slug": slug
                            })
                    except Exception as e:
                        errors_encountered.append(f"Ralat baca fail srt {fn}: {e}")

        total_discovered = len(discovered_subs)
        console.print(f"   [green]✔ Sebanyak {total_discovered:,} fail sarikata ditemui dalam pek ini.[/green]\n")

        # [FUNGSI KOD]: 4. Pemprosesan, penapisan bait, muat naik B2 & rekod Redis
        lang_code = "ms" if archive_name == "malay" else "id"

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

            # [FUNGSI KOD]: Semak rekod sedia ada di Redis (ID unik & nama release)
            existing_records = _redis_db.get_subtitle_records(imdb_id)
            existing_ids = {str(r.get("id", "")) for r in existing_records if isinstance(r, dict)}
            existing_releases = {str(r.get("release", "")) for r in existing_records if isinstance(r, dict)}

            # [FUNGSI KOD]: Pilihan B - Pembersihan aksara kawalan ASCII (< 32, 127-159) & simbol terlarang sistem fail
            raw_stem = Path(sub_filename).stem
            sanitized_stem = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", raw_stem)
            clean_release = re.sub(r'[\\/*?:"<>|]', "_", sanitized_stem).strip()
            if not clean_release:
                clean_release = "release_unknown"

            # [FUNGSI KOD]: Hash MD5 konsisten (menggantikan hash() bawaan Python yang rawak antara larian)
            subscene_id = meta.get("subscene_id")
            if not subscene_id:
                subscene_id = hashlib.md5(clean_release.encode("utf-8", errors="ignore")).hexdigest()[:8]

            # [FUNGSI KOD]: Had panjang nama keluaran 120 aksara selamat
            record_id = f"sub_{subscene_id}_{clean_release[:120]}"

            # [FUNGSI KOD]: Dua lapisan perlindungan pendua (elak muat naik fail serupa)
            if record_id in existing_ids or clean_release in existing_releases:
                skipped_exist_count += 1
                continue

            # [FUNGSI KOD]: Menjana laluan storan Backblaze B2 yang bebas ralat penamaan
            b2_sub_ext = Path(sub_filename).suffix.lower() or ".srt"
            b2_path = f"subs/{imdb_id}/{lang_code}_{record_id}{b2_sub_ext}"

            try:
                # [FUNGSI KOD]: Muat naik fail sarikata ke B2 melalui giliran multi-akaun
                b2_res = _b2_storage.upload_subtitle_to_b2(b2_path, srt_content)
                b2_url = b2_res["url"]
                b2_acc_idx = b2_res["account_index"]

                # [FUNGSI KOD]: Menentukan shard akaun Redis sasaran (Modulo Sharding)
                target_redis = _redis_db.get_target_redis_account(imdb_id)
                target_redis_idx = target_redis.get("index", 1)

                new_record = {
                    "id": record_id,
                    "lang": lang_code,
                    "url": b2_url,
                    "release": clean_release,
                    "source": "subscene",
                    "acc": int(b2_acc_idx)
                }

                s_num, e_num = detect_season_episode(sub_filename, default_season=meta.get("season"))

                # [FUNGSI KOD]: Mendaftar kunci episodik (:S:E) dan kunci induk siri/filem
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

                # [FUNGSI KOD]: Cetakan log terminal Rich bebas daripada ralat tag kurungan
                try:
                    safe_fn = escape(clean_release[:90])
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

                # [FUNGSI KOD]: Jeda mikro untuk kelancaran operasi di komputer tempatan
                if not IS_GITHUB_ACTIONS:
                    time.sleep(0.2)

            except _b2_storage.AllB2AccountsExhaustedException:
                errors_encountered.append("Kesemua kuota akaun Backblaze B2 telah habis!")
                break
            except Exception as up_err:
                errors_encountered.append(f"Gagal muat naik {sub_filename}: {up_err}")

        # [FUNGSI KOD]: Mengemas kini manifes SQLite hanya jika terdapat muat naik sah atau sudah wujud
        if uploaded_b2_count > 0 or skipped_exist_count == total_discovered:
            update_part_status(part_filename, "completed")
            console.print(f"\n[bold green]✨ Bahagian {part_filename} selesai diproses sepenuhnya![/bold green]")
        else:
            console.print(f"\n[bold red]⚠️ Amaran: 0 fail dimuat naik bagi {part_filename}. Status kekal 'uploaded'.[/bold red]")

    except Exception as ex:
        console.print(f"[bold red]❌ Ralat kritikal pada {part_filename}: {ex}[/bold red]")
        errors_encountered.append(str(ex))
    finally:
        # [FUNGSI KOD]: Penutupan sesi MTProto dan pembersihan menyeluruh fail sementara
        await client.disconnect()
        if TEMP_RUNNER_DIR.exists():
            shutil.rmtree(TEMP_RUNNER_DIR, ignore_errors=True)

    # [FUNGSI KOD]: Menjana format laporan status akhir dan hantar ke Telegram
    end_time_stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    error_summary = "\n".join([f"• {e}" for e in errors_encountered[:5]]) if errors_encountered else "Tiada Ralat"

    status_icon = "✅ BERJAYA" if (uploaded_b2_count > 0 and not errors_encountered) else "⚠️ PERIKSA LAPORAN"
    telegram_msg = (
        f"<b>🤖 STATUS PEMPROSESAN AWAN SUBTITLE</b>\n\n"
        f"<b>Status:</b> {status_icon}\n"
        f"<b>Pek Diproses:</b> <code>{part_filename}</code>\n"
        f"<b>Mula:</b> {start_time_stamp}\n"
        f"<b>Tamat:</b> {end_time_stamp}\n"
        f"<b>Jumlah Dimuat Naik ke B2:</b> {uploaded_b2_count:,} fail\n"
        f"<b>Dilangkau (Sedia Wujud):</b> {skipped_exist_count:,} fail\n"
        f"<b>Ralat:</b>\n<code>{error_summary}</code>"
    )

    send_telegram_notification(telegram_msg)

# ==============================================================================
# TITIK MASUK UTAMA SKRIP (CLI)
# ==============================================================================
# [FUNGSI KOD]: Menerima parameter nama pek arkib spesifik pilihan pengguna
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enjin Pemprosesan Awan Subtitle Telethon ke B2 & Redis")
    parser.add_argument("--part", type=str, default=None, help="Nama bahagian arkib spesifik (contoh: indonesian_part_001.7z)")
    args = parser.parse_args()

    asyncio.run(process_cloud_archive(target_part=args.part))