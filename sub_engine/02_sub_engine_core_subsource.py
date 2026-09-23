#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO SUB ADDON - SUB ENGINE CORE V3 (SUBSOURCE SMART FALLBACK & DB)
# LOKASI: /home/braderdin/stremio-sub-addon/sub_engine/02_sub_engine_core_subsource.py
# FITUR UTAMA:
# 1. Pra-Pemeriksaan Metadata Subsource (ID, Uploaded Timestamp, File Bytes)
# 2. Deteksi Cerdas Pembaruan Pengunggah (Uploader Update / Resync)
# 3. Gelung Penuh Semua Musim Seri TV (Loop ALL Seasons jika IMDb ID Induk)
# 4. Basis Data SQLite: sub_engine/data/subsource_cache.db
# 5. Pasca-Pemeriksaan Hash SHA-256 (Anti-Duplikasi B2 & Redis)
# 6. Pemetaan Cerdas Episode Serial ke Kunci Induk & Kunci Spesifik Redis
# ==============================================================================

import os
import re
import io
import sys
import time
import json
import random
import sqlite3
import hashlib
import zipfile
import argparse
import importlib
from pathlib import Path
from urllib.parse import urljoin, unquote
from typing import Dict, Any, List, Optional, Tuple

from curl_cffi import requests
from dotenv import dotenv_values
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

# ==============================================================================
# 1. PENYELARASAN JALUR & IMPORT MODUL ASAL DARI LIVE_ENGINE/
# ==============================================================================
SUB_ENGINE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SUB_ENGINE_DIR.parent
LIVE_ENGINE_DIR = PROJECT_ROOT / "live_engine"
DATA_DIR = SUB_ENGINE_DIR / "data"
TEMP_DIR = SUB_ENGINE_DIR / "temp"
DOWNLOADS_DIR = TEMP_DIR / "downloads"
SQLITE_DB_PATH = DATA_DIR / "subsource_cache.db"

for p in [SUB_ENGINE_DIR, PROJECT_ROOT, LIVE_ENGINE_DIR, DATA_DIR, TEMP_DIR, DOWNLOADS_DIR]:
    p.mkdir(parents=True, exist_ok=True)
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    _config = importlib.import_module("00_config")
    _redis = importlib.import_module("01_redis_db")
    _b2 = importlib.import_module("02_b2_storage")
    _zip = importlib.import_module("03_zip_extractor")
    extract_srt_from_zip = getattr(_zip, "extract_srt_from_zip")
except ImportError as e:
    console.print(f"[bold red]❌ Ralat mengimport modul asas dari live_engine: {e}[/bold red]")
    sys.exit(1)

try:
    import rarfile
    HAS_RAR = True
except ImportError:
    HAS_RAR = False

# Domain Proksi Rasmi Zero-Egress bagi Sarikata B2
CF_B2_SUB_PROXY = (os.getenv("CF_WORKER_B2_STORAGE") or "https://b2-private-stremio-sub-addon.braderdin360.workers.dev").rstrip("/")

# Muat Variabel Lingkungan (.env.local)
ENV_LOCAL_PATH = PROJECT_ROOT / ".env.local"
env_vars = dotenv_values(str(ENV_LOCAL_PATH)) if ENV_LOCAL_PATH.exists() else {}

TMDB_API_KEY = (os.getenv("TMDB_API_KEY") or env_vars.get("TMDB_API_KEY", "")).strip()
TMDB_READ_TOKEN = (os.getenv("TMDB_READ_TOKEN") or env_vars.get("TMDB_READ_TOKEN", "")).strip()

KNOWN_SUB_EXTS = {".srt", ".vtt", ".ass", ".ssa"}


# ==============================================================================
# 2. PENGELOLA BASIS DATA SQLITE (subsource_cache.db)
# ==============================================================================
def init_subsource_sqlite_db():
    """Menginisialisasi tabel SQLite untuk melacak paket dan file sarikata Subsource."""
    try:
        conn = sqlite3.connect(str(SQLITE_DB_PATH))
        with conn:
            # Tabel 1: Melacak metadata halaman rilis Subsource (Pra-Unduh)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS subsource_meta (
                    subsource_id TEXT PRIMARY KEY,
                    imdb_id TEXT NOT NULL,
                    uploaded_at TEXT,
                    file_bytes TEXT,
                    detail_url TEXT,
                    last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_meta_imdb ON subsource_meta(imdb_id)")

            # Tabel 2: Melacak hash fisik berkas teks sarikata (Pasca-Unduh)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS subsource_files (
                    content_hash TEXT PRIMARY KEY,
                    subsource_id TEXT,
                    imdb_id TEXT NOT NULL,
                    release_title TEXT,
                    b2_url TEXT,
                    acc_idx INTEGER,
                    lang TEXT,
                    season INTEGER,
                    episode INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_imdb ON subsource_files(imdb_id)")
        conn.close()
    except Exception as e:
        console.print(f"[dim red]Gagal inisialisasi SQLite {SQLITE_DB_PATH.name}: {e}[/dim red]")


def check_subsource_package_cache(subsource_id: str, uploaded_at: str, file_bytes: str) -> bool:
    """
    Pemeriksaan Pra-Unduh:
    Mengembalikan True jika ID ada dan memiliki stempel waktu serta ukuran bita yang identik.
    Mengembalikan False jika ID belum ada atau berkas telah diperbarui oleh pengunggah.
    """
    if not subsource_id:
        return False
    try:
        conn = sqlite3.connect(str(SQLITE_DB_PATH))
        cur = conn.cursor()
        cur.execute("SELECT uploaded_at, file_bytes FROM subsource_meta WHERE subsource_id = ? LIMIT 1", (subsource_id,))
        row = cur.fetchone()
        conn.close()

        if row:
            db_uploaded, db_bytes = row
            # Jika ukuran dan waktu unggah sama, berkas dipastikan belum berubah
            if (db_uploaded or "").strip() == (uploaded_at or "").strip() and (db_bytes or "").strip() == (file_bytes or "").strip():
                return True
        return False
    except Exception:
        return False


def save_subsource_package_meta(subsource_id: str, imdb_id: str, uploaded_at: str, file_bytes: str, detail_url: str):
    """Menyimpan atau memperbarui metadata halaman rilis Subsource."""
    if not subsource_id:
        return
    try:
        conn = sqlite3.connect(str(SQLITE_DB_PATH))
        with conn:
            conn.execute("""
                INSERT INTO subsource_meta (subsource_id, imdb_id, uploaded_at, file_bytes, detail_url, last_checked)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(subsource_id) DO UPDATE SET
                    uploaded_at = excluded.uploaded_at,
                    file_bytes = excluded.file_bytes,
                    detail_url = excluded.detail_url,
                    last_checked = CURRENT_TIMESTAMP
            """, (subsource_id, imdb_id, uploaded_at, file_bytes, detail_url))
        conn.close()
    except Exception as e:
        console.print(f"[dim red]Ralat simpan subsource_meta: {e}[/dim red]")


def is_content_hash_cached(content_hash: str) -> bool:
    """Pemeriksaan Pasca-Unduh: Mengecek apakah hash konten fisik sudah ada di B2/Redis."""
    try:
        conn = sqlite3.connect(str(SQLITE_DB_PATH))
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM subsource_files WHERE content_hash = ? LIMIT 1", (content_hash,))
        row = cur.fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


def save_subsource_file_cache(content_hash: str, subsource_id: str, imdb_id: str, release_title: str, b2_url: str, acc_idx: int, lang: str, season: Optional[int], episode: Optional[int]):
    """Menyimpan berkas yang berhasil diunggah ke tabel subsource_files."""
    try:
        conn = sqlite3.connect(str(SQLITE_DB_PATH))
        with conn:
            conn.execute("""
                INSERT OR IGNORE INTO subsource_files 
                (content_hash, subsource_id, imdb_id, release_title, b2_url, acc_idx, lang, season, episode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (content_hash, subsource_id, imdb_id, release_title, b2_url, acc_idx, lang, season, episode))
        conn.close()
    except Exception as e:
        console.print(f"[dim red]Ralat simpan subsource_files: {e}[/dim red]")


# ==============================================================================
# 3. BANTUAN SIMULASI & SANITASI FORMAT TITIK
# ==============================================================================
def human_delay(min_s: float = 2.0, max_s: float = 3.5, tag: str = ""):
    wait = random.uniform(min_s, max_s)
    lbl = f" ({tag})" if tag else ""
    console.print(f"[dim]⏳ Menunggu {wait:.2f}s{lbl}...[/dim]")
    time.sleep(wait)


def decode_content(raw_bytes: bytes) -> str:
    encodings = ["utf-8-sig", "utf-8", "latin-1", "windows-1252", "cp1256", "iso-8859-1"]
    for enc in encodings:
        try:
            text = raw_bytes.decode(enc)
            return text.replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="ignore").replace("\r\n", "\n").replace("\r", "\n")


def sanitize_dot_string(text: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9]+", ".", str(text).strip())
    clean = re.sub(r"\.+", ".", clean).strip(".")
    return clean or "Unknown"


def split_sub_ext(name_or_path: str, default_ext: str = ".srt") -> Tuple[str, str]:
    raw_str = str(name_or_path).strip()
    match = re.search(r"(\.(?:srt|vtt|ass|ssa))$", raw_str, re.IGNORECASE)
    if match:
        ext = match.group(1).lower()
        stem = raw_str[:match.start()]
        return stem, ext
    return raw_str, default_ext if default_ext in KNOWN_SUB_EXTS else ".srt"


def parse_season_episode_from_text(filename: str) -> Tuple[Optional[int], Optional[int]]:
    clean_fn = re.sub(r"(?i)\b(?:2160|1080|720|480)\b", " ", filename)

    m1 = re.search(r"(?i)\b[sS](\d{1,2})[-_ .]*[eE](\d{1,3})(?:[^0-9a-zA-Z]|$)", clean_fn)
    if m1:
        return int(m1.group(1)), int(m1.group(2))

    m2 = re.search(r"\b(\d{1,2})x(\d{1,3})\b", clean_fn)
    if m2:
        return int(m2.group(1)), int(m2.group(2))

    m3 = re.search(r"(?i)\bseason[-_ .]*(\d{1,2})[-_ .]+(?:episode|episod|eps|ep)[-_ .]*(\d{1,3})\b", clean_fn)
    if m3:
        return int(m3.group(1)), int(m3.group(2))

    return None, None


def build_standard_sub_filename(lang: str, imdb_id: str, raw_title: str, content: str, ext: str = ".srt") -> str:
    clean_imdb = sanitize_dot_string(imdb_id)
    stem_title, detected_ext = split_sub_ext(raw_title, default_ext=ext)
    clean_title = sanitize_dot_string(stem_title)
    short_hash = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()[:8]
    final_ext = detected_ext if detected_ext in KNOWN_SUB_EXTS else ".srt"

    return f"{lang.lower()}.{clean_imdb}.{short_hash}.{clean_title}{final_ext}"


def get_redis_shard_index(imdb_id: str) -> int:
    try:
        if hasattr(_redis, "get_shard_index"):
            return _redis.get_shard_index(imdb_id)
        if hasattr(_redis, "get_redis_shard_index"):
            return _redis.get_redis_shard_index(imdb_id)

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
# 4. EKSTRAKSI DALAM MEMORI (.SRT / .ZIP / .RAR)
# ==============================================================================
def unpack_subtitles_in_memory(raw_bytes: bytes, filename_hint: str = "") -> List[Dict[str, str]]:
    out_files = []

    if raw_bytes.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                for zname in zf.namelist():
                    zlower = zname.lower()
                    if zlower.endswith((".srt", ".vtt", ".ass", ".ssa")) and not zlower.startswith("__macosx"):
                        sub_bytes = zf.read(zname)
                        if len(sub_bytes) > 50:
                            stem, ext = split_sub_ext(Path(zname).name, default_ext=".srt")
                            out_files.append({
                                "filename": stem,
                                "content": decode_content(sub_bytes),
                                "ext": ext
                            })
            if out_files:
                return out_files
        except Exception:
            pass

    if raw_bytes.startswith(b"Rar!") and HAS_RAR:
        try:
            with rarfile.RarFile(io.BytesIO(raw_bytes)) as rf:
                for rname in rf.namelist():
                    rlower = rname.lower()
                    if rlower.endswith((".srt", ".vtt", ".ass", ".ssa")):
                        sub_bytes = rf.read(rname)
                        if len(sub_bytes) > 50:
                            stem, ext = split_sub_ext(Path(rname).name, default_ext=".srt")
                            out_files.append({
                                "filename": stem,
                                "content": decode_content(sub_bytes),
                                "ext": ext
                            })
            if out_files:
                return out_files
        except Exception:
            pass

    if len(raw_bytes) > 50:
        text_content = decode_content(raw_bytes)
        if "-->" in text_content:
            stem, ext = split_sub_ext(filename_hint or "subtitle.srt", default_ext=".srt")
            out_files.append({
                "filename": stem,
                "content": text_content,
                "ext": ext
            })

    return out_files


# ==============================================================================
# 5. RESOLVER METADATA MEDIA (CINEMETA + TMDB)
# ==============================================================================
def resolve_media_metadata(raw_imdb_id: str) -> Dict[str, Any]:
    clean_id = unquote(raw_imdb_id).strip()
    base_id = clean_id.split(":")[0]
    is_series = ":" in clean_id

    season = None
    episode = None
    if is_series:
        parts = clean_id.split(":")
        season = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
        episode = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1

    meta = {
        "imdb_id": clean_id,
        "base_imdb": base_id,
        "is_series": is_series,
        "season": season,
        "episode": episode,
        "title": base_id,
        "year": "",
        "media_type": "series" if is_series else "movie"
    }

    c_type = "series" if is_series else "movie"
    url = f"https://v3-cinemeta.strem.io/meta/{c_type}/{base_id}.json"
    try:
        resp = requests.get(url, impersonate="chrome120", timeout=8)
        if resp.status_code == 200:
            m = resp.json().get("meta", {})
            name = m.get("name")
            year_val = str(m.get("year", "")).split("–")[0].split("-")[0].strip()
            if name:
                meta["title"] = name
            if year_val:
                meta["year"] = year_val
    except Exception:
        pass

    if TMDB_READ_TOKEN or TMDB_API_KEY:
        t_url = f"https://api.themoviedb.org/3/find/{base_id}?external_source=imdb_id"
        headers = {"Accept": "application/json"}
        if TMDB_READ_TOKEN:
            headers["Authorization"] = f"Bearer {TMDB_READ_TOKEN}"
        params = {} if TMDB_READ_TOKEN else {"api_key": TMDB_API_KEY}

        try:
            t_resp = requests.get(t_url, headers=headers, params=params, timeout=8)
            if t_resp.status_code == 200:
                res = t_resp.json()
                items = res.get("movie_results", []) or res.get("tv_results", [])
                if items:
                    first = items[0]
                    t_title = first.get("title") or first.get("name") or ""
                    d_str = first.get("release_date") or first.get("first_air_date") or ""
                    t_yr = d_str.split("-")[0].strip()
                    if t_yr:
                        meta["year"] = t_yr
                    if not meta["title"] or meta["title"] == base_id:
                        meta["title"] = t_title
        except Exception:
            pass

    return meta


# ==============================================================================
# 6. ENJIN UTAMA SUBSOURCE DENGAN PENYARINGAN CERDAS DUA TAHAP
# ==============================================================================
def scrape_and_download_subsource_full(meta: Dict[str, Any], headless: bool = True) -> List[Dict[str, Any]]:
    init_subsource_sqlite_db()
    base_imdb = meta["base_imdb"]
    clean_imdb = meta["imdb_id"]
    title = meta["title"]
    target_season = meta.get("season")

    console.print(Panel.fit(
        f"[bold magenta]🦊 SUBSOURCE STANDALONE ENGINE V3 (HEADLESS: {headless})[/bold magenta]\n"
        f"Sasaran IMDb ID  : [bold yellow]{base_imdb}[/bold yellow] ({title})\n"
        f"Mod Operasi      : [bold white]{f'Musim Spesifik: {target_season}' if target_season else 'GELUNG SEMUA MUSIM SIRI TV (UNLIMITED)'}[/bold white]\n"
        f"Basis Data Cache : [cyan]{SQLITE_DB_PATH.name}[/cyan] (Filter Pra-Unduh & Pasca-Unduh Aktif)\n"
        f"Strategi Carian  : [green]Polling API + Multi-Season Loop + Subsource Page Details[/green]",
        border_style="magenta"
    ))

    extracted_records: List[Dict[str, Any]] = []

    try:
        from camoufox.sync_api import Camoufox
    except ImportError:
        console.print("[bold red]❌ Pustaka Camoufox tiada. Subsource dilangkau.[/bold red]")
        return []

    intercepted_direct_links: List[str] = []

    try:
        with Camoufox(headless=headless, geoip=True) as browser:
            context = browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
                accept_downloads=True
            )
            page = context.new_page()

            def on_response_listener(resp):
                if "search" in resp.url and "api.subsource.net" in resp.url:
                    try:
                        data = resp.json()
                        for r in data.get("results", []):
                            lnk = r.get("link")
                            if lnk and lnk not in intercepted_direct_links:
                                intercepted_direct_links.append(lnk)
                    except Exception:
                        pass

            page.on("response", on_response_listener)

            try:
                # -------------------------------------------------------------
                # LANGKAH 1: BUKA BERANDA & KETIK IMDB ID
                # -------------------------------------------------------------
                console.print("[cyan]🚀 [1/4] Melayari https://subsource.net...[/cyan]")
                page.goto("https://subsource.net", wait_until="domcontentloaded", timeout=45000)
                human_delay(2.0, 3.0, "pemuatan beranda")

                search_box = page.locator("input[type='search'], input[placeholder*='Search'], input[name='query']").first
                if not search_box.is_visible():
                    page.reload(wait_until="domcontentloaded")
                    human_delay(2.5, 3.5, "refresh carian")
                    search_box = page.locator("input").first

                console.print(f"[yellow]⌨️ [2/4] Menaip IMDb ID:[/yellow] {base_imdb}")
                search_box.click()
                search_box.fill("")
                search_box.press_sequentially(base_imdb, delay=random.randint(80, 130))

                # -------------------------------------------------------------
                # LANGKAH 2: POLLING RESPON API / KARTU MODAL (ANTI-RACE CONDITION)
                # -------------------------------------------------------------
                console.print("[cyan]⏳ Menunggu popup cadangan atau respons API (maks 10s)...[/cyan]")
                start_polling = time.time()
                popup_link_found = ""

                while time.time() - start_polling < 10.0:
                    if intercepted_direct_links:
                        popup_link_found = intercepted_direct_links[0]
                        break

                    candidate_cards = page.locator(
                        "header a[href*='/series/'], header a[href*='/subtitles/'], "
                        "div[role='dialog'] a[href*='/series/'], div[role='dialog'] a[href*='/subtitles/'], "
                        "div[class*='search'] a[href*='/series/'], div[class*='search'] a[href*='/subtitles/'], "
                        "div[class*='dropdown'] a"
                    ).all()

                    for card in candidate_cards:
                        if card.is_visible():
                            h = card.get_attribute("href")
                            if h and ("/series/" in h or "/subtitles/" in h):
                                popup_link_found = h
                                break

                    if popup_link_found:
                        break

                    time.sleep(0.4)

                if popup_link_found:
                    target_content_url = urljoin("https://subsource.net", popup_link_found)
                    console.print(f"[bold green]🎯 [Navigasi Berjaya] Membuka:[/bold green] {target_content_url}")
                    page.goto(target_content_url, wait_until="domcontentloaded", timeout=35000)
                else:
                    console.print("[yellow]⚠️ Popup tidak dikesan, menekan kad pertama dalam modal...[/yellow]")
                    first_link = page.locator("header a, div[role='dialog'] a").first
                    if first_link.is_visible():
                        first_link.click(force=True)
                    else:
                        search_box.press("Enter")

                page.wait_for_load_state("domcontentloaded")
                human_delay(3.0, 4.0, "pemuatan halaman kandungan")

                if page.url.rstrip("/") == "https://subsource.net":
                    console.print("[bold red]❌ Ralat: Pelayar tersangkut di beranda. Kad siri/filem gagal dibuka.[/bold red]")
                    return []

                # -------------------------------------------------------------
                # LANGKAH 3: KUMPULKAN SELURUH MUSIM (TIADA HAD / FULL LOOP)
                # -------------------------------------------------------------
                console.print("[cyan]🔍 [3/4] Mengesan struktur siri TV (mengutip semua musim)...[/cyan]")
                season_cards = page.locator("a[href*='season-'], a[href*='/season/']").all()
                if not season_cards:
                    season_cards = page.locator("div[class*='season'], a:has-text('Season')").all()

                seasons_to_scrape: List[Dict[str, Any]] = []
                seen_season_urls = set()

                for sc in season_cards:
                    txt = sc.inner_text().strip()
                    href = sc.get_attribute("href") or ""
                    if not href:
                        continue
                    full_s_url = urljoin("https://subsource.net", href)
                    if full_s_url in seen_season_urls:
                        continue
                    seen_season_urls.add(full_s_url)

                    m_num = re.search(r"season[- ]*(\d+)", f"{href} {txt}", re.IGNORECASE)
                    s_num = int(m_num.group(1)) if m_num else None
                    has_zero = "0 subtitles" in txt.lower()

                    seasons_to_scrape.append({
                        "season_num": s_num,
                        "url": full_s_url,
                        "label": txt.split("\n")[0] if txt else f"Season {s_num}",
                        "zero_subs": has_zero
                    })

                if target_season:
                    filtered = [s for s in seasons_to_scrape if s["season_num"] == target_season]
                    if filtered:
                        seasons_to_scrape = filtered
                    else:
                        seasons_to_scrape = [s for s in seasons_to_scrape if f"season-{target_season}" in s["url"].lower()]

                if not seasons_to_scrape:
                    seasons_to_scrape = [{
                        "season_num": target_season,
                        "url": page.url,
                        "label": "Jadual Langsung (Filem / Siri Terbuka)",
                        "zero_subs": False
                    }]
                else:
                    seasons_to_scrape.sort(key=lambda x: (x["season_num"] or 999))
                    console.print(f"[bold magenta]📺 Mengesan {len(seasons_to_scrape)} musim untuk diproses sepenuhnya![/bold magenta]")

                # -------------------------------------------------------------
                # LANGKAH 4: PROSES TIAP MUSIM DENGAN FILTER PRA-UNDUH CERDAS
                # -------------------------------------------------------------
                for s_idx, s_info in enumerate(seasons_to_scrape, 1):
                    if s_info["zero_subs"]:
                        console.print(f"[dim yellow]⏩ Melangkau {s_info['label']} (0 sarikata tersenarai).[/dim yellow]")
                        continue

                    console.print(f"\n[bold cyan]📂 [Musim {s_idx}/{len(seasons_to_scrape)}] Membuka: {s_info['label']}[/bold cyan] -> {s_info['url']}")
                    if page.url != s_info["url"]:
                        page.goto(s_info["url"], wait_until="domcontentloaded", timeout=35000)
                        human_delay(2.5, 3.5, f"pemuatan {s_info['label']}")

                    table_search = page.locator("input[placeholder*='Search subtitles'], input[placeholder*='subtitles']").first
                    if not table_search.is_visible():
                        page.reload(wait_until="domcontentloaded")
                        human_delay(2.5, 3.5, "refresh jadual")
                        table_search = page.locator("input[placeholder*='Search subtitles'], input[placeholder*='subtitles']").first

                    if not table_search.is_visible():
                        console.print(f"[dim red]⚠️ Kotak saringan tidak ditemui pada {s_info['label']}.[/dim red]")
                        continue

                    targets_to_download: List[Dict[str, str]] = []
                    seen_detail_urls = set()

                    # Saringan Melayu
                    table_search.click()
                    table_search.fill("")
                    table_search.press_sequentially("malay", delay=90)
                    human_delay(2.0, 3.0, "saringan Malay")

                    for r in page.locator("tbody tr, div[class*='table'] div[class*='row'], tr").all():
                        txt = r.inner_text().strip()
                        if not txt or "language" in txt.lower():
                            continue
                        link = r.locator("a[href*='/subtitles/'], a[href*='/subtitle/'], a").first
                        if link.is_visible():
                            href = link.get_attribute("href") or ""
                            full_u = urljoin("https://subsource.net", href)
                            rel = link.inner_text().strip() or txt.split("\n")[0]
                            if full_u and full_u not in seen_detail_urls:
                                seen_detail_urls.add(full_u)
                                targets_to_download.append({"lang": "ms", "release_title": rel, "detail_url": full_u, "season_num": s_info["season_num"]})

                    # Saringan Indonesia
                    table_search.click()
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Backspace")
                    time.sleep(0.4)
                    table_search.press_sequentially("indonesia", delay=90)
                    human_delay(2.0, 3.0, "saringan Indonesia")

                    for r in page.locator("tbody tr, div[class*='table'] div[class*='row'], tr").all():
                        txt = r.inner_text().strip()
                        if not txt or "language" in txt.lower():
                            continue
                        link = r.locator("a[href*='/subtitles/'], a[href*='/subtitle/'], a").first
                        if link.is_visible():
                            href = link.get_attribute("href") or ""
                            full_u = urljoin("https://subsource.net", href)
                            rel = link.inner_text().strip() or txt.split("\n")[0]
                            if full_u and full_u not in seen_detail_urls:
                                seen_detail_urls.add(full_u)
                                targets_to_download.append({"lang": "id", "release_title": rel, "detail_url": full_u, "season_num": s_info["season_num"]})

                    console.print(f"[green]✔ {s_info['label']}: Ditemui {len(targets_to_download)} kandidat sarikata BM & ID.[/green]")

                    # ---------------------------------------------------------
                    # PENGUNDUHAN BERKAS FISIK DENGAN FILTER PRA-UNDUH SQLITE
                    # ---------------------------------------------------------
                    for idx, target in enumerate(targets_to_download, 1):
                        target_url = target["detail_url"]

                        # Ekstraksi Subtitle ID dari URL (contoh: /indonesian/366348 -> 366348)
                        m_sub_id = re.search(r"/(\d+)(?:[?#]|$)", target_url)
                        subsource_id = m_sub_id.group(1) if m_sub_id else ""

                        dl_tab = context.new_page()
                        try:
                            dl_tab.goto(target_url, wait_until="domcontentloaded", timeout=35000)
                            human_delay(1.5, 2.5, "halaman rilis")

                            # 1. BACA METADATA DETAIL PADA HALAMAN SEBELUM MENGUNDUH
                            uploaded_at_val = ""
                            file_bytes_val = ""

                            details_el = dl_tab.locator("div:has-text('Subtitle Details'), div[class*='details']").first
                            if details_el.is_visible():
                                d_text = details_el.inner_text()
                                # Ekstraksi waktu unggah (contoh: Uploaded: 2010/09/22 15:28)
                                m_up = re.search(r"Uploaded:\s*([0-9/:\s-]+?)(?:\s*\(|$)", d_text, re.IGNORECASE)
                                if m_up:
                                    uploaded_at_val = m_up.group(1).strip()

                                # Ekstraksi ukuran bita (contoh: 80,083 Bytes atau 0 Bytes)
                                m_by = re.search(r"([\d,]+)\s*Bytes", d_text, re.IGNORECASE)
                                if m_by:
                                    file_bytes_val = m_by.group(1).replace(",", "").strip()

                            # 2. FILTER PRA-UNDUH: Cek apakah paket ini sudah pernah disimpan dan tidak berubah
                            if subsource_id and uploaded_at_val and file_bytes_val:
                                if check_subsource_package_cache(subsource_id, uploaded_at_val, file_bytes_val):
                                    console.print(f"[dim yellow]   ├─ [{idx}/{len(targets_to_download)}] Dilangkau Cerdas (ID #{subsource_id}): Berkas tidak berubah ({file_bytes_val} Bytes | {uploaded_at_val})[/dim yellow]")
                                    dl_tab.close()
                                    time.sleep(0.3)
                                    continue

                            console.print(f"[cyan]   ├─ [{idx}/{len(targets_to_download)}] Mengunduh:[/cyan] {target['release_title'][:45]} ({target['lang'].upper()} | ID #{subsource_id})...")

                            dl_btn = dl_tab.locator("button:has-text('Download'), a:has-text('Download'), button[class*='download']").first
                            if not dl_btn.is_visible():
                                dl_tab.reload(wait_until="domcontentloaded")
                                human_delay(1.8, 2.5, "refresh tombol")
                                dl_btn = dl_tab.locator("button:has-text('Download'), a:has-text('Download'), button[class*='download']").first

                            if dl_btn.is_visible():
                                with dl_tab.expect_download(timeout=25000) as dl_info:
                                    dl_btn.click(force=True)
                                download = dl_info.value

                                safe_fn = f"{target['lang']}_{clean_imdb}_{download.suggested_filename}"
                                dest_path = DOWNLOADS_DIR / safe_fn
                                download.save_as(str(dest_path))

                                raw_bytes = dest_path.read_bytes()
                                if len(raw_bytes) > 50:
                                    unpacked = unpack_subtitles_in_memory(raw_bytes, filename_hint=download.suggested_filename)
                                    for sub_obj in unpacked:
                                        s_parsed, e_parsed = parse_season_episode_from_text(sub_obj["filename"] or target["release_title"])
                                        final_s = s_parsed or target["season_num"]

                                        extracted_records.append({
                                            "source": "Subsource",
                                            "subsource_id": subsource_id,
                                            "uploaded_at": uploaded_at_val,
                                            "file_bytes": file_bytes_val,
                                            "detail_url": target_url,
                                            "lang": target["lang"],
                                            "content": sub_obj["content"],
                                            "ext": sub_obj["ext"],
                                            "release_title": sub_obj["filename"] or target["release_title"],
                                            "season": final_s,
                                            "episode": e_parsed
                                        })
                                    console.print(f"[bold green]   │  └─ Berjaya diekstrak ({len(unpacked)} sarikata):[/bold green] {download.suggested_filename}")
                            else:
                                console.print("[dim yellow]   │  └─ Tombol Download tidak terdeteksi.[/dim yellow]")

                        except Exception as dl_err:
                            console.print(f"[dim red]   │  └─ Ralat unduh #{idx}: {dl_err}[/dim red]")
                        finally:
                            dl_tab.close()

                        human_delay(1.5, 2.5, "jeda antar unduhan")

            except Exception as page_err:
                console.print(f"[bold red]❌ Ralat automasi Subsource: {page_err}[/bold red]")
            finally:
                page.close()
                context.close()

    except Exception as browser_err:
        console.print(f"[bold red]❌ Gagal melancarkan Camoufox: {browser_err}[/bold red]")

    return extracted_records


# ==============================================================================
# 7. PIPELINE UNGGAH & PENDAFTARAN B2 / REDIS (PASCA-UNDUH SHA-256 CHECK)
# ==============================================================================
def run_subsource_engine(raw_imdb_id: str, headless: bool = True) -> bool:
    meta = resolve_media_metadata(raw_imdb_id)
    imdb_id = meta["imdb_id"]
    base_id = meta["base_imdb"]
    shard_idx = get_redis_shard_index(imdb_id)

    console.print(Panel.fit(
        f"[bold cyan]⚡ SUB SOURCE ENGINE CORE V3: PROSES SARIKATA TERAS[/bold cyan]\n"
        f"ID Sasaran   : [bold yellow]{imdb_id}[/bold yellow] (Base: [white]{base_id}[/white])\n"
        f"Tajuk Sah    : [bold green]{meta['title']}[/bold green] (Tahun: [yellow]{meta.get('year', '-')}[/yellow])\n"
        f"Bahasa Fokus : [bold magenta]Bahasa Melayu (MS) & Indonesia (ID) SAHAJA[/bold magenta]",
        border_style="cyan"
    ))

    if hasattr(_redis, "set_processing_lock") and not _redis.set_processing_lock(f"subsource:{imdb_id}", ttl_seconds=600):
        console.print(f"[yellow]⚠️ Tugasan Subsource untuk {imdb_id} sedang diproses oleh pelari lain. Tamat.[/yellow]")
        return True

    try:
        subsource_results = scrape_and_download_subsource_full(meta, headless=headless)

        if not subsource_results:
            console.print(f"[bold yellow]⚠️ Tiada sarikata baharu yang perlu dimuat naik ke B2/Redis untuk {imdb_id}.[/bold yellow]")
            return True

        console.print(f"\n[bold cyan]📦 Memproses {len(subsource_results)} fail sarikata ke B2 & Redis (Validasi SHA-256)...[/bold cyan]")

        uploaded_records = []
        seen_session_hashes = set()
        skipped_hash_count = 0

        table = Table(title=f"📋 Audit Muat Naik Subsource V3: {meta['title']}", border_style="magenta")
        table.add_column("No", justify="center", style="cyan", width=4)
        table.add_column("Bahasa", justify="center", style="magenta", width=6)
        table.add_column("Musim/Ep", justify="center", style="yellow", width=10)
        table.add_column("Nama Penuh Fail Sarikata (.srt)", style="white")
        table.add_column("Destinasi B2", justify="center", style="green", width=14)
        table.add_column("Redis Shard", justify="center", style="blue", width=12)
        table.add_column("Status", justify="center", style="bold green", width=10)

        for sub_item in subsource_results:
            lang = sub_item["lang"]
            content_str = sub_item["content"]
            ext = sub_item["ext"]
            raw_title = sub_item["release_title"]
            s_val = sub_item.get("season")
            e_val = sub_item.get("episode")
            sub_id = sub_item.get("subsource_id") or ""
            up_at = sub_item.get("uploaded_at") or ""
            f_bytes = sub_item.get("file_bytes") or ""
            d_url = sub_item.get("detail_url") or ""

            # 1. Hashing Konten Teks Asli Berkas
            content_hash = hashlib.sha256(content_str.encode("utf-8", errors="ignore")).hexdigest()

            # 2. De-duplikasi dalam sesi yang sama
            if content_hash in seen_session_hashes:
                continue
            seen_session_hashes.add(content_hash)

            # 3. FILTER PASCA-UNDUH: Cek apakah hash isi konten ini sudah pernah diunggah ke B2 sebelumnya
            if is_content_hash_cached(content_hash):
                skipped_hash_count += 1
                # Simpan metadata paket agar pra-unduh sesi berikutnya langsung melewatinya
                save_subsource_package_meta(sub_id, imdb_id, up_at, f_bytes, d_url)
                continue

            standard_fn = build_standard_sub_filename(
                lang=lang,
                imdb_id=imdb_id,
                raw_title=raw_title,
                content=content_str,
                ext=ext
            )

            clean_imdb_folder = sanitize_dot_string(imdb_id)
            b2_relative_path = f"subs/{clean_imdb_folder}/{standard_fn}"

            try:
                b2_res = _b2.upload_subtitle_to_b2(b2_relative_path, content_str)
                bucket_name = b2_res.get("bucket_name", "")
                proxy_stream_url = f"{CF_B2_SUB_PROXY}/{bucket_name}/{b2_relative_path.lstrip('/')}"
                acc_idx = b2_res.get("account_index", 1)

                rec_id = f"{lang}_{clean_imdb_folder}_{len(uploaded_records) + 1}"
                new_rec = {
                    "id": rec_id,
                    "lang": lang,
                    "url": proxy_stream_url,
                    "release": standard_fn,
                    "source": "Subsource",
                    "acc": int(acc_idx),
                    "season": s_val,
                    "episode": e_val
                }
                uploaded_records.append(new_rec)

                # Simpan ke tabel subsource_files
                save_subsource_file_cache(
                    content_hash=content_hash,
                    subsource_id=sub_id,
                    imdb_id=imdb_id,
                    release_title=standard_fn,
                    b2_url=proxy_stream_url,
                    acc_idx=int(acc_idx),
                    lang=lang,
                    season=s_val,
                    episode=e_val
                )

                # Simpan juga ke tabel subsource_meta untuk pra-pemeriksaan masa depan
                save_subsource_package_meta(sub_id, imdb_id, up_at, f_bytes, d_url)

                se_label = f"S{s_val:02d}E{e_val:02d}" if (s_val and e_val) else (f"S{s_val:02d}" if s_val else "-")
                table.add_row(
                    str(len(uploaded_records)),
                    lang.upper(),
                    se_label,
                    standard_fn[:52],
                    f"Akaun #{acc_idx}",
                    f"Shard #{shard_idx}",
                    "SUKSES"
                )
            except _b2.AllB2AccountsExhaustedException as e:
                console.print(f"[bold red]🚨 Had Penuh B2: {e}[/bold red]")
                break
            except Exception as ex:
                console.print(f"[dim red]Gagal muat naik ke B2: {ex}[/dim red]")

        if skipped_hash_count > 0:
            console.print(f"[bold green]✔ {skipped_hash_count} sarikata dilewati karena hash isi konten identik dengan data di B2.[/bold green]")

        if not uploaded_records:
            console.print("[yellow]ℹ️ Semua berkas sarikata yang diunduh sudah ada di basis data B2 & Redis.[/yellow]")
            return True

        console.print(table)

        # -------------------------------------------------------------
        # PENDAFTARAN KE REDIS (KUNCI INDUK & KUNCI EPISODE)
        # -------------------------------------------------------------
        console.print(f"\n[cyan]💾 Mendaftarkan {len(uploaded_records)} sarikata ke Upstash Redis Shard #{shard_idx}...[/cyan]")
        _redis.save_subtitle_records_batch(imdb_id, uploaded_records)

        if meta["is_series"] or any(r.get("season") for r in uploaded_records):
            _redis.save_subtitle_records_batch(base_id, uploaded_records)

            episode_groups: Dict[str, List[Dict[str, Any]]] = {}
            for rec in uploaded_records:
                s = rec.get("season")
                e = rec.get("episode")
                if s and e:
                    ep_k = f"{base_id}:{s}:{e}"
                    episode_groups.setdefault(ep_k, []).append(rec)

            for ep_key, ep_subs in episode_groups.items():
                existing = _redis.get_subtitle_records(ep_key) or []
                existing_urls = {x.get("url") for x in existing}
                added_list = [s for s in ep_subs if s.get("url") not in existing_urls]
                if added_list:
                    _redis.save_subtitle_records_batch(ep_key, existing + added_list)
                    console.print(f"[dim green]   ├─ Kunci episod {ep_key} dikemas kini (+{len(added_list)} sarikata)[/dim green]")

        console.print(Panel.fit(
            f"[bold green]🎉 TUGASAN SUBSOURCE SELESAI: SEMUA SARIKATA BERJAYA DIKEMAS KINI![/bold green]\n"
            f"├─ Sasaran IMDb   : [bold yellow]{imdb_id}[/bold yellow] ({meta['title']})\n"
            f"├─ Fail Baharu    : [bold green]{len(uploaded_records)} fail berjaya dimuat naik ke B2 & Redis[/bold green]\n"
            f"├─ Rekod SQLite   : [cyan]{SQLITE_DB_PATH.name}[/cyan] dikemas kini secara otomatis\n"
            f"└─ Proksi Zero    : [cyan]{CF_B2_SUB_PROXY}[/cyan]",
            border_style="green"
        ))
        return True

    finally:
        if hasattr(_redis, "remove_processing_lock"):
            _redis.remove_processing_lock(f"subsource:{imdb_id}")


# ==============================================================================
# 8. TITIK MASUK CLI
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Subsource Dedicated Engine V3 (Smart Fallback & Multi-Season)")
    parser.add_argument("--imdb", required=True, help="Target IMDb ID (cth: tt1199099 atau tt1199099:1:2)")
    parser.add_argument("--visible", action="store_true", help="Buka GUI pelayar Camoufox (lalai: headless)")
    args = parser.parse_args()

    success = run_subsource_engine(args.imdb, headless=(not args.visible))
    if not success:
        sys.exit(1)