#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO SUB ADDON - SUBTITLE ENGINE CORE V3 (FULL SCRAPER & AUTO DOWNLOAD)
# LOKASI: /home/braderdin/stremio-sub-addon/sub_engine/01_sub_engine_core.py
# CIRI:
# 1. Dwi-Sumber Pintar: OpenSubtitles Public API + Camoufox Subsource (In-Page Search)
# 2. Muat Turun Penuh: Muat turun SEMUA sarikata BM & ID tanpa had (No Cap)
# 3. Human Delay Dinamik: Jeda 2.5s - 5.0s & Fallback Web Refresh (Anti-Stuck)
# 4. Penyahmampatan Memori: Ekstrak terus daripada binari .srt, .zip dan .rar
# 5. Sanitasi Format Titik: <lang>.<imdb_id>.<hash>.<tajuk_penuh_bersih>.<ext>
# 6. Zero-Egress Proxy: https://b2-private-stremio-sub-addon.braderdin360.workers.dev
# 7. Mengimport 100% Modul Asal: 00_config, 01_redis_db, 02_b2_storage, 03_zip_extractor
# ==============================================================================

import os
import re
import io
import sys
import time
import json
import random
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
# 1. PENYELARASAN LALUAN & IMPORT MODUL ASAL DARI LIVE_ENGINE/
# ==============================================================================
SUB_ENGINE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SUB_ENGINE_DIR.parent
LIVE_ENGINE_DIR = PROJECT_ROOT / "live_engine"
TEMP_DIR = SUB_ENGINE_DIR / "temp"
DOWNLOADS_DIR = TEMP_DIR / "downloads"

for p in [SUB_ENGINE_DIR, PROJECT_ROOT, LIVE_ENGINE_DIR, TEMP_DIR, DOWNLOADS_DIR]:
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

# Sokongan modul arkib RAR jika tersedia
try:
    import rarfile
    HAS_RAR = True
except ImportError:
    HAS_RAR = False

# Domain Proksi Rasmi Zero-Egress bagi Sarikata B2
CF_B2_SUB_PROXY = (os.getenv("CF_WORKER_B2_STORAGE") or "https://b2-private-stremio-sub-addon.braderdin360.workers.dev").rstrip("/")

# Muat Pembolehubah Persekitaran (.env.local)
ENV_LOCAL_PATH = PROJECT_ROOT / ".env.local"
env_vars = dotenv_values(str(ENV_LOCAL_PATH)) if ENV_LOCAL_PATH.exists() else {}

TMDB_API_KEY = (os.getenv("TMDB_API_KEY") or env_vars.get("TMDB_API_KEY", "")).strip()
TMDB_READ_TOKEN = (os.getenv("TMDB_READ_TOKEN") or env_vars.get("TMDB_READ_TOKEN", "")).strip()

KNOWN_SUB_EXTS = {".srt", ".vtt", ".ass", ".ssa"}

# Corak Pengesanan Sekuel & Kata Henti (Anti-Mismatch Guard)
SEQUEL_PATTERNS = [
    r"\b2\b", r"\b3\b", r"\b4\b", r"\b5\b", r"\b6\b", r"\b7\b", r"\b8\b", r"\b9\b",
    r"\bii\b", r"\biii\b", r"\biv\b", r"\bvi\b", r"\bvii\b", r"\bviii\b", r"\bix\b",
    r"\bpart\s*2\b", r"\bpart\s*3\b", r"\bpart\s*4\b", r"\bpart\s*5\b",
    r"\bchapter\s*2\b", r"\bchapter\s*3\b", r"\bchapter\s*4\b", r"\bchapter\s*5\b",
    r"\bvolume\s*2\b", r"\bvolume\s*3\b", r"\bvol\s*2\b", r"\bvol\s*3\b"
]


# ==============================================================================
# 2. BANTUAN SIMULASI MANUSIA & SANITASI TEKS
# ==============================================================================
def human_delay(min_s: float = 2.5, max_s: float = 5.0, tag: str = ""):
    """Jeda masa rawak 2.5s hingga 5.0s agar web sentiasa bersedia merender elemen."""
    wait = random.uniform(min_s, max_s)
    lbl = f" ({tag})" if tag else ""
    console.print(f"[dim]⏳ Menunggu {wait:.2f}s{lbl}...[/dim]")
    time.sleep(wait)


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


def sanitize_dot_string(text: str) -> str:
    """Menukarkan sebarang ruang kosong dan simbol khas kepada titik tunggal (.)."""
    clean = re.sub(r"[^a-zA-Z0-9]+", ".", str(text).strip())
    clean = re.sub(r"\.+", ".", clean).strip(".")
    return clean or "Unknown"


def split_sub_ext(name_or_path: str, default_ext: str = ".srt") -> Tuple[str, str]:
    """Mengasingkan nama dan sambungan fail sarikata yang sah."""
    raw_str = str(name_or_path).strip()
    match = re.search(r"(\.(?:srt|vtt|ass|ssa))$", raw_str, re.IGNORECASE)
    if match:
        ext = match.group(1).lower()
        stem = raw_str[:match.start()]
        return stem, ext
    return raw_str, default_ext if default_ext in KNOWN_SUB_EXTS else ".srt"


def build_standard_sub_filename(lang: str, imdb_id: str, raw_title: str, content: str, ext: str = ".srt") -> str:
    """
    Format Piawai: <bahasa>.<nilai_imdb>.<hash_8_aksara>.<tajuk_penuh_bersih>.<ext>
    Contoh: ms.tt3215824.7a4b12c0.Those.Who.Wish.Me.Dead.2021.Bluray.MTeam.srt
    """
    clean_imdb = sanitize_dot_string(imdb_id)
    stem_title, detected_ext = split_sub_ext(raw_title, default_ext=ext)
    clean_title = sanitize_dot_string(stem_title)
    short_hash = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()[:8]
    final_ext = detected_ext if detected_ext in KNOWN_SUB_EXTS else ".srt"

    return f"{lang.lower()}.{clean_imdb}.{short_hash}.{clean_title}{final_ext}"


def parse_and_filter_language(text: str) -> Optional[str]:
    """HANYA meluluskan Bahasa Melayu (ms) dan Bahasa Indonesia (id)."""
    clean = text.lower().strip()
    if "malayalam" in clean:
        return None
    if "bahasa melayu" in clean or "melayu" in clean or re.search(r"\b(malay|may|ms|zsm)\b", clean):
        return "ms"
    if "bahasa indonesia" in clean or "indonesian" in clean or re.search(r"\b(indonesia|ind|id)\b", clean):
        return "id"
    return None


def get_redis_shard_index(imdb_id: str) -> int:
    """Mengira Shard Upstash Redis mengikut kaedah asal modulo nombor IMDb."""
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
# 3. PENYAHMAMPATAN PINTAR DALAM MEMORI (.SRT / .ZIP / .RAR)
# ==============================================================================
def unpack_subtitles_in_memory(raw_bytes: bytes, filename_hint: str = "") -> List[Dict[str, str]]:
    """Mengekstrak fail teks sarikata daripada binari mentah (.srt, .zip, atau .rar)."""
    out_files = []

    # 1. Semakan Arkib ZIP (Magic Bytes: PK)
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

    # 2. Semakan Arkib RAR (Magic Bytes: Rar!)
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

    # 3. Sandaran: Kandungan Teks Mentah Langsung (.srt / .vtt)
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
# 4. RESOLVER METADATA BERLAPIS (CINEMETA + TMDB ENRICHMENT)
# ==============================================================================
def resolve_media_metadata(imdb_id: str) -> Dict[str, Any]:
    """Mendapatkan maklumat rasmi media bagi memastikan padanan IMDb ID 100% tepat."""
    clean_id = unquote(imdb_id).strip()
    base_id = clean_id.split(":")[0]
    is_series = ":" in clean_id

    season = 1
    episode = 1
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

    # A. Stremio Cinemeta API (Ground Truth)
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

    # B. TMDB API Pengesahan Tahun (Jika Kredensial Ada)
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
# 5. PENAPIS KELAYAKAN PADANAN FILEM (ANTI-MISMATCH KETAT)
# ==============================================================================
def is_valid_title_match(candidate_text: str, target_title: str, target_year: str) -> bool:
    """Menolak filem lain dengan semakan Tajuk Wajib, Tahun, dan Nombor Sekuel."""
    c_lower = candidate_text.lower().strip()
    t_lower = target_title.lower().strip()

    # 1. Semakan Tajuk Wajib (Mesti ada sekurang-kurangnya 60% perkataan sepadan)
    clean_words = [w for w in re.sub(r"[^a-zA-Z0-9\s]", " ", t_lower).split() if len(w) > 2]
    if clean_words:
        matched = [w for w in clean_words if w in c_lower]
        if (len(matched) / len(clean_words)) < 0.6:
            return False

    # 2. Semakan Tahun Terbitan (Tolak jika jurang melebihi 1 tahun)
    m_yr = re.search(r"\b(19\d\d|20\d\d)\b", c_lower)
    if m_yr and target_year and str(target_year).isdigit():
        c_year = int(m_yr.group(1))
        t_year = int(str(target_year).strip())
        if abs(c_year - t_year) > 1:
            return False

    # 3. Penapis Sekuel Bertindih
    for pat in SEQUEL_PATTERNS:
        in_c = bool(re.search(pat, c_lower))
        in_t = bool(re.search(pat, t_lower))
        if in_c != in_t:
            return False

    return True


# ==============================================================================
# 6. SUMBER A: OPENSUBTITLES PUBLIC API RESOLVER (curl_cffi)
# ==============================================================================
def fetch_opensubtitles_candidates(meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    target_id = meta["imdb_id"]
    endpoint_type = "series" if meta["is_series"] else "movie"
    url = f"https://opensubtitles-v3.strem.io/subtitles/{endpoint_type}/{target_id}.json"

    console.print(f"[cyan]🌐 Menyaring OpenSubtitles Public API:[/cyan] [dim]{url}[/dim]")
    candidates = []

    try:
        resp = requests.get(
            url,
            impersonate="chrome120",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        if resp.status_code == 200:
            subs = resp.json().get("subtitles", [])
            for s in subs:
                lang_raw = s.get("lang") or ""
                lang_code = parse_and_filter_language(lang_raw)
                if not lang_code:
                    continue

                sub_url = s.get("url") or ""
                sub_id = str(s.get("id") or "").strip()
                if sub_url:
                    raw_stem, _ = split_sub_ext(Path(sub_url).name, default_ext=".srt")
                    canonical = meta.get("title", "Video")
                    year_str = str(meta.get("year", "")).strip()
                    season_str = f"S{meta['season']:02d}E{meta['episode']:02d}" if meta["is_series"] else ""

                    if raw_stem.isdigit() or not raw_stem:
                        parts = [canonical, year_str, season_str, "OpenSubtitles", sub_id or raw_stem]
                    else:
                        if canonical.lower() not in raw_stem.lower():
                            parts = [canonical, year_str, season_str, raw_stem]
                        else:
                            parts = [raw_stem]

                    release_name = ".".join([sanitize_dot_string(p) for p in parts if p])

                    candidates.append({
                        "source": "OpenSubtitles",
                        "lang": lang_code,
                        "download_url": sub_url,
                        "release_title": release_name
                    })
    except Exception as e:
        console.print(f"[dim red]⚠️ OpenSubtitles gagal diakses: {e}[/dim red]")

    return candidates


# ==============================================================================
# 7. SUMBER B: CAMOUFOX SUBSOURCE (IN-PAGE SEARCH & MUAT TURUN 100% SARIKATA)
# ==============================================================================
def scrape_and_download_subsource(meta: Dict[str, Any], headless: bool = True) -> List[Dict[str, Any]]:
    """
    Enjin Subsource Penuh:
    1. Masuk ke halaman filem via popup carian IMDb ID.
    2. Menyaring perkataan 'malay' & 'indonesia' terus di atas kotak jadual.
    3. Memuat turun SEMUA fail sarikata BM & ID fizikal tanpa had dengan delay 2.5s-5.0s.
    4. Fallback page reload jika mana-mana elemen tertangguh.
    """
    base_imdb = meta["base_imdb"]
    clean_imdb = meta["imdb_id"]
    title = meta["title"]
    year = str(meta.get("year", "")).strip()

    console.print(Panel.fit(
        f"[bold magenta]🦊 CAMOUFOX SUBSOURCE FULL ENGINE (HEADLESS: {headless})[/bold magenta]\n"
        f"Sasaran IMDb ID  : [bold yellow]{base_imdb}[/bold yellow] ({title})\n"
        f"Strategi Carian  : [bold green]In-Page Search 'malay' & 'indonesia'[/bold green]\n"
        f"Human Delay      : [cyan]2.5s - 5.0s per tindakan[/cyan] | [yellow]Fallback Web Refresh Aktif[/yellow]",
        border_style="magenta"
    ))

    extracted_records: List[Dict[str, Any]] = []

    try:
        from camoufox.sync_api import Camoufox
    except ImportError:
        console.print("[dim yellow]⚠️ Pustaka Camoufox tiada. Melangkau Subsource.[/dim yellow]")
        return []

    try:
        with Camoufox(headless=headless, geoip=True) as browser:
            context = browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
                accept_downloads=True
            )
            page = context.new_page()

            try:
                # ---------------------------------------------------------
                # LANGKAH 1: LAYARI LAMAN & CARI VIA POPUP IMDB
                # ---------------------------------------------------------
                console.print("[cyan]🚀 [Subsource 1/4] Melayari https://subsource.net...[/cyan]")
                page.goto("https://subsource.net", wait_until="domcontentloaded", timeout=45000)
                human_delay(2.5, 4.0, "pemuatan beranda")

                # Semakan input carian utama (dengan fallback refresh jika tiada)
                main_search = page.locator("input[type='search'], input[placeholder*='Search'], input[name='query']").first
                if not main_search.is_visible():
                    console.print("[dim yellow]⚠️ Kotak carian utama tiada, memuat semula laman (Refresh)...[/dim yellow]")
                    page.reload(wait_until="domcontentloaded")
                    human_delay(2.5, 4.0, "selepas refresh")
                    main_search = page.locator("input").first

                console.print(f"[yellow]⌨️ [Subsource 2/4] Menaip IMDb ID ke kotak carian:[/yellow] [bold white]{base_imdb}[/bold white]")
                main_search.click()
                main_search.fill("")
                main_search.press_sequentially(base_imdb, delay=random.randint(90, 150))
                human_delay(2.5, 4.0, "menunggu popup cadangan")

                # Klik kad filem pada popup
                popup_card = page.locator(
                    "div[class*='search'] a, div[class*='result'] a, div[class*='dropdown'] a, a[href*='/subtitles/']"
                ).first

                if popup_card.is_visible():
                    console.print("[green]🎯 Mengklik kad filem di popup...[/green]")
                    popup_card.click()
                else:
                    console.print("[dim yellow]⚠️ Popup kad tidak kelihatan, menekan kekunci Enter...[/dim yellow]")
                    main_search.press("Enter")

                page.wait_for_load_state("domcontentloaded")
                human_delay(3.0, 5.0, "pemuatan halaman sarikata filem")

                # ---------------------------------------------------------
                # LANGKAH 2: CARI KOTAK 'Search subtitles...' DALAM HALAMAN
                # ---------------------------------------------------------
                sub_search_input = page.locator("input[placeholder*='Search subtitles'], input[placeholder*='subtitles']").first
                if not sub_search_input.is_visible():
                    console.print("[dim yellow]⚠️ Kotak 'Search subtitles...' belum bersedia. Mencuba refresh halaman...[/dim yellow]")
                    page.reload(wait_until="domcontentloaded")
                    human_delay(3.0, 5.0, "selepas refresh jadual")
                    sub_search_input = page.locator("input[placeholder*='Search subtitles'], input[placeholder*='subtitles']").first

                targets_to_download: List[Dict[str, str]] = []
                seen_detail_urls = set()

                if sub_search_input.is_visible():
                    # --- FASA A: CARIAN BAHASA MELAYU (MALAY) ---
                    console.print("\n[bold cyan]🔍 [Subsource 3/4 - A] Menyaring 'malay'...[/bold cyan]")
                    sub_search_input.click()
                    sub_search_input.fill("")
                    sub_search_input.press_sequentially("malay", delay=110)
                    human_delay(2.5, 4.5, "menunggu senarai Bahasa Melayu")

                    rows_malay = page.locator("tbody tr, div[class*='table'] div[class*='row'], tr").all()
                    for r in rows_malay:
                        txt = r.inner_text().strip()
                        if not txt or "language" in txt.lower():
                            continue
                        link_tag = r.locator("a[href*='/subtitles/'], a[href*='/subtitle/'], a").first
                        if link_tag.is_visible():
                            href = link_tag.get_attribute("href") or ""
                            full_url = urljoin("https://subsource.net", href)
                            rel_name = link_tag.inner_text().strip() or txt.split("\n")[0]
                            if full_url and full_url not in seen_detail_urls:
                                seen_detail_urls.add(full_url)
                                targets_to_download.append({
                                    "lang": "ms",
                                    "release_title": rel_name,
                                    "detail_url": full_url
                                })

                    console.print(f"[green]✔ Berjaya mengesan {len([t for t in targets_to_download if t['lang'] == 'ms'])} sarikata Bahasa Melayu![/green]")

                    # --- FASA B: CARIAN BAHASA INDONESIA (INDONESIA) ---
                    console.print("\n[bold cyan]🔍 [Subsource 3/4 - B] Menyaring 'indonesia'...[/bold cyan]")
                    sub_search_input.click()
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Backspace")
                    time.sleep(0.6)
                    sub_search_input.press_sequentially("indonesia", delay=110)
                    human_delay(2.5, 4.5, "menunggu senarai Bahasa Indonesia")

                    rows_indo = page.locator("tbody tr, div[class*='table'] div[class*='row'], tr").all()
                    for r in rows_indo:
                        txt = r.inner_text().strip()
                        if not txt or "language" in txt.lower():
                            continue
                        link_tag = r.locator("a[href*='/subtitles/'], a[href*='/subtitle/'], a").first
                        if link_tag.is_visible():
                            href = link_tag.get_attribute("href") or ""
                            full_url = urljoin("https://subsource.net", href)
                            rel_name = link_tag.inner_text().strip() or txt.split("\n")[0]
                            if full_url and full_url not in seen_detail_urls:
                                seen_detail_urls.add(full_url)
                                targets_to_download.append({
                                    "lang": "id",
                                    "release_title": rel_name,
                                    "detail_url": full_url
                                })

                    console.print(f"[green]✔ Berjaya mengesan {len([t for t in targets_to_download if t['lang'] == 'id'])} sarikata Bahasa Indonesia![/green]")
                else:
                    console.print("[bold red]❌ Gagal mengesan kotak saringan teks di Subsource.[/bold red]")

                # ---------------------------------------------------------
                # LANGKAH 3: ENJIN MUAT TURUN 100% SARIKATA (SEMUA FAIL)
                # ---------------------------------------------------------
                if targets_to_download:
                    console.print(f"\n[bold magenta]📥 [Subsource 4/4] Memuat turun SEMUA {len(targets_to_download)} sarikata BM & ID dari Subsource...[/bold magenta]")

                    for idx, target in enumerate(targets_to_download, 1):
                        console.print(f"[cyan]   [{idx}/{len(targets_to_download)}] Mengunduh:[/cyan] {target['release_title'][:48]} ({target['lang'].upper()})...")
                        
                        dl_tab = context.new_page()
                        try:
                            dl_tab.goto(target["detail_url"], wait_until="domcontentloaded", timeout=35000)
                            human_delay(2.5, 4.5, f"halaman muat turun #{idx}")

                            dl_btn = dl_tab.locator("button:has-text('Download'), a:has-text('Download'), button[class*='download']").first
                            
                            # Fallback refresh jika butang muat turun belum muncul
                            if not dl_btn.is_visible():
                                console.print("[dim yellow]      ⚠️ Butang muat turun belum terbit, memuat semula...[/dim yellow]")
                                dl_tab.reload(wait_until="domcontentloaded")
                                human_delay(2.5, 4.0, "selepas refresh")
                                dl_btn = dl_tab.locator("button:has-text('Download'), a:has-text('Download'), button[class*='download']").first

                            if dl_btn.is_visible():
                                with dl_tab.expect_download(timeout=25000) as dl_info:
                                    dl_btn.click()
                                download = dl_info.value

                                safe_fn = f"{target['lang']}_{clean_imdb}_{download.suggested_filename}"
                                dest_path = DOWNLOADS_DIR / safe_fn
                                download.save_as(str(dest_path))

                                # Baca binari terus dari fail yang selamat dimuat turun
                                raw_bytes = dest_path.read_bytes()
                                if len(raw_bytes) > 50:
                                    unpacked = unpack_subtitles_in_memory(raw_bytes, filename_hint=download.suggested_filename)
                                    for sub_obj in unpacked:
                                        extracted_records.append({
                                            "source": "Subsource",
                                            "lang": target["lang"],
                                            "content": sub_obj["content"],
                                            "ext": sub_obj["ext"],
                                            "release_title": sub_obj["filename"] or target["release_title"]
                                        })
                                    console.print(f"[bold green]      └─ Berjaya diekstrak ({len(unpacked)} sarikata):[/bold green] {download.suggested_filename}")
                            else:
                                console.print("[dim yellow]      └─ Butang muat turun tiada pada halaman ini.[/dim yellow]")

                        except Exception as dl_err:
                            console.print(f"[dim red]      └─ Ralat muat turun sarikata #{idx}: {dl_err}[/dim red]")
                        finally:
                            dl_tab.close()

                        # Human Delay antara muat turun agar pelayan Subsource stabil
                        human_delay(2.5, 4.5, "jeda antara muat turun")

            except Exception as page_err:
                console.print(f"[bold red]❌ Ralat automasi Subsource: {page_err}[/bold red]")
            finally:
                page.close()
                context.close()

    except Exception as browser_err:
        console.print(f"[bold red]❌ Gagal melancarkan Camoufox: {browser_err}[/bold red]")

    return extracted_records


# ==============================================================================
# 8. PIPELINE UTAMA (RUN_SUB_ENGINE)
# ==============================================================================
def run_sub_engine(raw_imdb_id: str, headless: bool = True) -> bool:
    meta = resolve_media_metadata(raw_imdb_id)
    imdb_id = meta["imdb_id"]
    base_id = meta["base_imdb"]
    shard_idx = get_redis_shard_index(imdb_id)

    console.print(Panel.fit(
        f"[bold cyan]⚡ SUBTITLE ENGINE CORE V3: MEMPROSES TUGAS LENGKAP[/bold cyan]\n"
        f"ID Sasaran   : [bold yellow]{imdb_id}[/bold yellow] (Base: [white]{base_id}[/white])\n"
        f"Tajuk Sah    : [bold green]{meta['title']}[/bold green] (Tahun: [yellow]{meta.get('year', '-')}[/yellow])\n"
        f"Bahasa Fokus : [bold magenta]Bahasa Melayu (MS) & Indonesia (ID) SAHAJA[/bold magenta]",
        border_style="cyan"
    ))

    # Kunci Pemprosesan di Redis untuk Mengelak Perlumbaan Tugas (Race Condition)
    if hasattr(_redis, "set_processing_lock") and not _redis.set_processing_lock(imdb_id, ttl_seconds=600):
        console.print(f"[yellow]⚠️ Tugasan {imdb_id} sedang diproses oleh pelari lain. Tamat.[/yellow]")
        return True

    try:
        # Senarai objek sarikata bersatu yang siap dimuat naik
        all_subtitles_to_process: List[Dict[str, Any]] = []

        # -------------------------------------------------------------
        # 1. KUTIP DARI OPENSUBTITLES API
        # -------------------------------------------------------------
        os_candidates = fetch_opensubtitles_candidates(meta)
        console.print(f"[cyan]🌐 Mengunduh {len(os_candidates)} sarikata terus dari OpenSubtitles CDN...[/cyan]")
        
        for item in os_candidates:
            try:
                bin_resp = requests.get(item["download_url"], impersonate="chrome120", timeout=15)
                if bin_resp.status_code == 200 and len(bin_resp.content) > 50:
                    unpacked = unpack_subtitles_in_memory(bin_resp.content, filename_hint=item["release_title"])
                    for sub_obj in unpacked:
                        all_subtitles_to_process.append({
                            "source": "OpenSubtitles",
                            "lang": item["lang"],
                            "content": sub_obj["content"],
                            "ext": sub_obj["ext"],
                            "release_title": item["release_title"]
                        })
            except Exception as e:
                console.print(f"[dim red]Gagal muat turun OpenSubtitles: {e}[/dim red]")

        # -------------------------------------------------------------
        # 2. KUTIP DARI CAMOUFOX SUBSOURCE (SEMUA FAIL)
        # -------------------------------------------------------------
        subsource_results = scrape_and_download_subsource(meta, headless=headless)
        all_subtitles_to_process.extend(subsource_results)

        console.print(f"\n[bold cyan]📦 Jumlah Keseluruhan Sarikata Berjaya Dikutip:[/bold cyan] [bold green]{len(all_subtitles_to_process)}[/bold green] (OS: {len(os_candidates)} | Subsource: {len(subsource_results)})")

        if not all_subtitles_to_process:
            console.print(f"[bold red]❌ Tiada sarikata BM/ID ditemui untuk {imdb_id} dari kedua-dua punca.[/bold red]")
            return False

        # -------------------------------------------------------------
        # 3. MUAT NAIK KE B2 & CIPTA REKOD FORMAT TITIK PIAWAI
        # -------------------------------------------------------------
        uploaded_records = []
        seen_content_hashes = set()

        table = Table(title=f"📋 Audit Muat Naik Sarikata V3: {meta['title']}", border_style="green")
        table.add_column("No", justify="center", style="cyan", width=4)
        table.add_column("Bahasa", justify="center", style="magenta", width=6)
        table.add_column("Punca", justify="center", style="yellow", width=14)
        table.add_column("Nama Penuh Fail Sarikata (.srt)", style="white")
        table.add_column("Destinasi B2", justify="center", style="green", width=14)
        table.add_column("Redis Shard", justify="center", style="blue", width=12)
        table.add_column("Status", justify="center", style="bold green", width=10)

        for sub_item in all_subtitles_to_process:
            lang = sub_item["lang"]
            src = sub_item["source"]
            content_str = sub_item["content"]
            ext = sub_item["ext"]
            raw_title = sub_item["release_title"]

            # Elak muat naik fail sarikata yang 100% identikal
            content_hash = hashlib.sha256(content_str.encode("utf-8", errors="ignore")).hexdigest()
            if content_hash in seen_content_hashes:
                continue
            seen_content_hashes.add(content_hash)

            # Bina nama titik piawai
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
                # Muat naik fail ke Backblaze B2 menggunakan modul asal 02_b2_storage.py
                b2_res = _b2.upload_subtitle_to_b2(b2_relative_path, content_str)

                bucket_name = b2_res.get("bucket_name", "")
                proxy_stream_url = f"{CF_B2_SUB_PROXY}/{bucket_name}/{b2_relative_path.lstrip('/')}"
                acc_idx = b2_res.get("account_index", 1)

                rec_id = f"{lang}_{clean_imdb_folder}_{len(uploaded_records) + 1}"
                uploaded_records.append({
                    "id": rec_id,
                    "lang": lang,
                    "url": proxy_stream_url,
                    "release": standard_fn,
                    "source": src,
                    "acc": int(acc_idx)
                })

                table.add_row(
                    str(len(uploaded_records)),
                    lang.upper(),
                    src,
                    standard_fn[:58],
                    f"Akaun #{acc_idx}",
                    f"Shard #{shard_idx}",
                    "SUKSES"
                )

            except _b2.AllB2AccountsExhaustedException as e:
                console.print(f"[bold red]🚨 Had Penuh B2: {e}[/bold red]")
                break
            except Exception as ex:
                console.print(f"[dim red]Gagal muat naik sarikata ke B2: {ex}[/dim red]")

        if not uploaded_records:
            console.print("[bold red]❌ Tiada fail sarikata yang berjaya dimuat naik ke storan B2.[/bold red]")
            return False

        # Paparkan Jadual Audit Penuh
        console.print(table)

        # -------------------------------------------------------------
        # 4. PENDAFTARAN BERKELOMPOK KE UPSTASH REDIS MODUL ASAL
        # -------------------------------------------------------------
        console.print(f"\n[cyan]💾 Mendaftarkan {len(uploaded_records)} sarikata ke Upstash Redis Shard #{shard_idx}...[/cyan]")
        _redis.save_subtitle_records_batch(imdb_id, uploaded_records)

        if meta["is_series"]:
            _redis.save_subtitle_records_batch(base_id, uploaded_records)

        console.print(Panel.fit(
            f"[bold green]🎉 TUGASAN SELESAI: KESEMUA SARIKATA BERJAYA DIKEMAS KINI![/bold green]\n"
            f"├─ Sasaran IMDb   : [bold yellow]{imdb_id}[/bold yellow] ({meta['title']})\n"
            f"├─ Sarikata B2    : [bold green]{len(uploaded_records)} fail berjaya dimuat naik[/bold green]\n"
            f"├─ Kunci Redis    : [yellow]subs:{imdb_id}[/yellow] (Shard #{shard_idx})\n"
            f"└─ Proksi Zero    : [cyan]{CF_B2_SUB_PROXY}[/cyan]",
            border_style="green"
        ))
        return True

    finally:
        if hasattr(_redis, "remove_processing_lock"):
            _redis.remove_processing_lock(imdb_id)


# ==============================================================================
# 9. TITIK MASUK CLI
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Subtitle Engine Core V3 (BM & ID Only)")
    parser.add_argument("--imdb", required=True, help="Target IMDb ID (cth: tt3215824)")
    parser.add_argument("--visible", action="store_true", help="Buka GUI pelayar Camoufox (lalai: headless)")
    args = parser.parse_args()

    success = run_sub_engine(args.imdb, headless=(not args.visible))
    if not success:
        sys.exit(1)