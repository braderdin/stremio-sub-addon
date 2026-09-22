#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO SUB ADDON - SUBTITLE ENGINE CORE V3
# LOKASI: /home/braderdin/stremio-sub-addon/sub_engine/01_sub_engine_core.py
# CIRI:
# 1. Dwi-Sumber Pintar: OpenSubtitles Public API + Camoufox Subsource (IMDb-First)
# 2. Penapis Bahasa Ketat: HANYA Bahasa Melayu (ms) & Indonesia (id)
# 3. Penyahmampatan Memori: Ekstrak terus daripada binari .srt, .zip dan .rar
# 4. Sanitasi Format Titik: <lang>.<imdb_id>.<hash>.<tajuk_penuh_bersih>.<ext>
# 5. Zero-Egress Proxy: https://b2-private-stremio-sub-addon.braderdin360.workers.dev
# 6. Mengimport 100% Modul Asal: 00_config, 01_redis_db, 02_b2_storage, 03_zip_extractor
# ==============================================================================

import os
import re
import io
import sys
import zlib
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

for p in [SUB_ENGINE_DIR, PROJECT_ROOT, LIVE_ENGINE_DIR, TEMP_DIR]:
    if p.exists() and str(p) not in sys.path:
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
# 2. FUNGSI SANITASI TEKS, FORMAT TITIK & DEKOD KANDUNGAN
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


def sanitize_dot_string(text: str) -> str:
    """Menukarkan sebarang ruang kosong dan simbol khas kepada titik tunggal (.)."""
    clean = re.sub(r"[^a-zA-Z0-9]+", ".", str(text).strip())
    clean = re.sub(r"\.+", ".", clean).strip(".")
    return clean or "Unknown"


def split_sub_ext(name_or_path: str, default_ext: str = ".srt") -> Tuple[str, str]:
    """
    Mengasingkan nama dan sambungan (extension) hanya jika ia sambungan sarikata yang sah.
    Mencegah nombor ID seperti .1962013166 daripada dianggap sebagai extension oleh Path.suffix.
    """
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
    Contoh: id.tt27543632.14b87ecc.The.Housemaid.2025.OpenSubtitles.1962013166.srt
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


# ✅ KOD BETUL (SELARI DENGAN MODUL REDIS ASAL):
def get_redis_shard_index(imdb_id: str) -> int:
    """Mengira Shard Upstash Redis mengikut kaedah asal modulo nombor IMDb."""
    try:
        # Jika modul _redis ada fungsi pengiraan shard sendiri, utamakan panggilan tersebut
        if hasattr(_redis, "get_shard_index"):
            return _redis.get_shard_index(imdb_id)
        if hasattr(_redis, "get_redis_shard_index"):
            return _redis.get_redis_shard_index(imdb_id)

        # Fallback rasmi: Ekstrak digit angka IMDb (cth: tt3215824 -> 3215824)
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
# 5. PENAPIS KELAYAKAN PADANAN FILEM (ANTI-MISMATCH)
# ==============================================================================
def is_valid_title_match(candidate_text: str, target_title: str, target_year: str) -> bool:
    """Menolak filem lain atau sekuel berbeza menggunakan logik pencegahan mismatch."""
    c_lower = candidate_text.lower().strip()
    t_lower = target_title.lower().strip()

    # 1. Semakan Tahun (Tolak jika jurang > 1 tahun)
    m_yr = re.search(r"\b(19\d\d|20\d\d)\b", c_lower)
    if m_yr and target_year and str(target_year).isdigit():
        c_year = int(m_yr.group(1))
        t_year = int(str(target_year).strip())
        if abs(c_year - t_year) > 1:
            return False

    # 2. Penapis Sekuel Bertindih
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

                    # Jika nama asal hanya nombor (cth: 1962013166), cantumkan tajuk bermakna
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
# 7. SUMBER B: CAMOUFOX STEALTH (SUBSOURCE IMDb-FIRST SCRAPER)
# ==============================================================================
def scrape_subsource_candidates(meta: Dict[str, Any], headless: bool = True) -> List[Dict[str, Any]]:
    target_id = meta["imdb_id"]
    base_imdb = meta["base_imdb"]
    title = meta["title"]
    year = meta.get("year", "")

    console.print(f"[magenta]🦊 Melancarkan Camoufox Subsource (Headless: {headless})...[/magenta]")
    candidates = []

    try:
        from camoufox.sync_api import Camoufox
    except ImportError:
        console.print("[dim yellow]⚠️ Pustaka Camoufox tiada. Melangkau Subsource.[/dim yellow]")
        return []

    try:
        with Camoufox(headless=headless, geoip=True) as browser:
            context = browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"
            )
            page = context.new_page()

            try:
                page.goto("https://subsource.net", wait_until="domcontentloaded", timeout=40000)
                time.sleep(random.uniform(1.5, 3.0))

                search_box = page.locator("input[type='search'], input[placeholder*='Search'], input[name='query']").first
                if not search_box.is_visible():
                    search_box = page.locator("input").first

                found_target = False

                # Peringkat 1: Carian IMDb ID Tepat
                if search_box.is_visible():
                    search_box.click()
                    search_box.press_sequentially(base_imdb, delay=random.randint(90, 160))
                    search_box.press("Enter")
                    time.sleep(random.uniform(2.5, 4.0))

                    cards = page.locator("a[href*='/subtitles/']").all()
                    for card in cards:
                        c_text = card.inner_text()
                        if base_imdb.lower() in c_text.lower() or is_valid_title_match(c_text, title, year):
                            card.click()
                            found_target = True
                            break

                # Peringkat 2: Fallback Carian Tajuk + Tahun jika IMDb Tiada Hasil
                if not found_target:
                    query_text = f"{title} {year}".strip()
                    page.goto(f"https://subsource.net/search?q={query_text}", wait_until="domcontentloaded", timeout=30000)
                    time.sleep(random.uniform(2.5, 4.0))

                    cards = page.locator("a[href*='/subtitles/']").all()
                    for card in cards:
                        if is_valid_title_match(card.inner_text(), title, year):
                            card.click()
                            found_target = True
                            break

                # Ekstrak Pautan Muat Turun Sarikata BM / ID
                if found_target:
                    page.wait_for_load_state("domcontentloaded")
                    time.sleep(random.uniform(2.5, 4.0))

                    entries = page.locator(".subtitle-entry, tr, div[class*='item']").all()
                    for entry in entries:
                        entry_txt = entry.inner_text()
                        lang_code = parse_and_filter_language(entry_txt)
                        if not lang_code:
                            continue

                        dl_link = entry.locator("a[href*='/subtitles/'], a[href*='download']").first
                        if dl_link.is_visible():
                            href = dl_link.get_attribute("href") or ""
                            full_url = urljoin("https://subsource.net", href)
                            rel_name = entry_txt.split("\n")[0][:60]
                            candidates.append({
                                "source": "Subsource",
                                "lang": lang_code,
                                "download_url": full_url,
                                "release_title": rel_name
                            })
            except Exception as e:
                console.print(f"[dim red]⚠️ Ralat navigasi Camoufox: {e}[/dim red]")
            finally:
                page.close()
                context.close()
    except Exception as e:
        console.print(f"[dim red]⚠️ Gagal melancarkan Camoufox: {e}[/dim red]")

    return candidates


# ==============================================================================
# 8. ENJIN UTAMA PEMPROSESAN & MUAT NAIK (RUN_SUB_ENGINE)
# ==============================================================================
def run_sub_engine(raw_imdb_id: str, headless: bool = True) -> bool:
    meta = resolve_media_metadata(raw_imdb_id)
    imdb_id = meta["imdb_id"]
    base_id = meta["base_imdb"]
    shard_idx = get_redis_shard_index(imdb_id)

    console.print(Panel.fit(
        f"[bold cyan]⚡ SUBTITLE ENGINE CORE V3: MEMPROSES TUGAS[/bold cyan]\n"
        f"ID Sasaran   : [bold yellow]{imdb_id}[/bold yellow] (Base: [white]{base_id}[/white])\n"
        f"Tajuk Sah    : [bold green]{meta['title']}[/bold green] (Tahun: [yellow]{meta.get('year', '-')}[/yellow])\n"
        f"Bahasa Fokus : [bold magenta]Bahasa Melayu (MS) & Indonesia (ID) SAHAJA[/bold magenta]",
        border_style="cyan"
    ))

    # Kunci Pemprosesan di Redis untuk Mengelak Perlumbaan Tugas (Race Condition)
    if hasattr(_redis, "set_processing_lock") and not _redis.set_processing_lock(imdb_id, ttl_seconds=300):
        console.print(f"[yellow]⚠️ Tugasan {imdb_id} sedang diproses oleh pelari lain. Tamat.[/yellow]")
        return True

    try:
        # 1. Kumpul Calon daripada Kedua-dua Punca
        os_list = fetch_opensubtitles_candidates(meta)
        camoufox_list = scrape_subsource_candidates(meta, headless=headless)

        all_candidates = os_list + camoufox_list
        console.print(f"[cyan]📦 Jumlah Calon Dijumpai:[/cyan] OpenSubtitles: {len(os_list)} | Subsource: {len(camoufox_list)}")

        if not all_candidates:
            console.print(f"[bold red]❌ Tiada sarikata BM/ID ditemui untuk {imdb_id}.[/bold red]")
            return False

        # 2. Muat Turun Binari, Nyahmampat & Formatkan Nama
        uploaded_records = []
        table = Table(title=f"📋 Audit Muat Naik Sarikata V3: {meta['title']}", border_style="green")
        table.add_column("No", justify="center", style="cyan", width=4)
        table.add_column("Bahasa", justify="center", style="magenta", width=6)
        table.add_column("Punca", justify="center", style="yellow", width=14)
        table.add_column("Nama Penuh Fail Sarikata (.srt)", style="white")
        table.add_column("Destinasi B2", justify="center", style="green", width=14)
        table.add_column("Redis Shard", justify="center", style="blue", width=12)
        table.add_column("Status", justify="center", style="bold green", width=10)

        for item in all_candidates:
            d_url = item["download_url"]
            lang = item["lang"]
            src = item["source"]
            rel_hint = item["release_title"]

            try:
                # Muat turun data binari (.srt, .zip, atau .rar)
                bin_resp = requests.get(d_url, impersonate="chrome120", timeout=15)
                if bin_resp.status_code != 200 or len(bin_resp.content) < 50:
                    continue

                extracted_subs = unpack_subtitles_in_memory(bin_resp.content, filename_hint=rel_hint)
                for sub_obj in extracted_subs:
                    content_str = sub_obj["content"]
                    ext = sub_obj["ext"]
                    sub_title = sub_obj["filename"]

                    # Bina Nama Format Titik Standard Penuh
                    standard_fn = build_standard_sub_filename(
                        lang=lang,
                        imdb_id=imdb_id,
                        raw_title=sub_title,
                        content=content_str,
                        ext=ext
                    )

                    # Destinasi Baldi B2
                    clean_imdb_folder = sanitize_dot_string(imdb_id)
                    b2_relative_path = f"subs/{clean_imdb_folder}/{standard_fn}"

                    # Muat naik menggunakan modul asal 02_b2_storage.py
                    b2_res = _b2.upload_subtitle_to_b2(b2_relative_path, content_str)

                    # Bina URL Proksi Rasmi
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
                        standard_fn,
                        f"Akaun #{acc_idx}",
                        f"Shard #{shard_idx}",
                        "SUKSES"
                    )

            except _b2.AllB2AccountsExhaustedException as e:
                console.print(f"[bold red]🚨 Had Penuh: {e}[/bold red]")
                break
            except Exception as ex:
                console.print(f"[dim red]Gagal memproses fail dari {src}: {ex}[/dim red]")

        if not uploaded_records:
            console.print("[bold red]❌ Tiada fail sarikata yang berjaya dipindahkan ke storan B2.[/bold red]")
            return False

        # 3. Paparkan Jadual Audit
        console.print(table)

        # 4. Pendaftaran Berkelompok ke Upstash Redis Menggunakan Modul Asal
        console.print(f"\n[cyan]💾 Mendaftarkan {len(uploaded_records)} sarikata ke Upstash Redis Shard #{shard_idx}...[/cyan]")
        _redis.save_subtitle_records_batch(imdb_id, uploaded_records)

        # Jika siri TV, daftarkan juga ke kunci Base ID untuk kemudahan katalog
        if meta["is_series"]:
            _redis.save_subtitle_records_batch(base_id, uploaded_records)

        console.print(Panel.fit(
            f"[bold green]🎉 TUGASAN SELESAI: SARIKATA BERJAYA DIKEMAS KINI KE B2 & REDIS![/bold green]\n"
            f"Kunci Redis : [yellow]subs:{imdb_id}[/yellow]\n"
            f"Proksi URL  : [cyan]{CF_B2_SUB_PROXY}[/cyan]",
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
    parser.add_argument("--imdb", required=True, help="Target IMDb ID (cth: tt35538033 atau tt0944947:1:1)")
    parser.add_argument("--visible", action="store_true", help="Buka GUI pelayar Camoufox (lalai: headless)")
    args = parser.parse_args()

    success = run_sub_engine(args.imdb, headless=(not args.visible))
    if not success:
        sys.exit(1)