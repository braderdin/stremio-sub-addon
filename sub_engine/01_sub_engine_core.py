#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO SUB ADDON - SUBTITLE ENGINE CORE V3 (OPENSUBTITLES STANDALONE)
# LOKASI: /home/braderdin/stremio-sub-addon/sub_engine/01_sub_engine_core.py
# CIRI-CIRI UTAMA:
# 1. Khusus 100% untuk OpenSubtitles Public API (Pantau Laju IMDb-Direct)
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
import json
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


# ==============================================================================
# 2. BANTUAN SANITASI FORMAT TITIK & DEKOD KANDUNGAN
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


def build_standard_sub_filename(lang: str, imdb_id: str, raw_title: str, content: str, ext: str = ".srt") -> str:
    clean_imdb = sanitize_dot_string(imdb_id)
    stem_title, detected_ext = split_sub_ext(raw_title, default_ext=ext)
    clean_title = sanitize_dot_string(stem_title)
    short_hash = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()[:8]
    final_ext = detected_ext if detected_ext in KNOWN_SUB_EXTS else ".srt"

    return f"{lang.lower()}.{clean_imdb}.{short_hash}.{clean_title}{final_ext}"


def parse_and_filter_language(text: str) -> Optional[str]:
    clean = text.lower().strip()
    if "malayalam" in clean:
        return None
    if "bahasa melayu" in clean or "melayu" in clean or re.search(r"\b(malay|may|ms|zsm)\b", clean):
        return "ms"
    if "bahasa indonesia" in clean or "indonesian" in clean or re.search(r"\b(indonesia|ind|id)\b", clean):
        return "id"
    return None


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
# 3. PENYAHMAMPATAN PINTAR DALAM MEMORI (.SRT / .ZIP / .RAR)
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
# 4. RESOLVER METADATA MEDIA (CINEMETA + TMDB)
# ==============================================================================
def resolve_media_metadata(imdb_id: str) -> Dict[str, Any]:
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
# 5. SUMBER TUNGGAL: OPENSUBTITLES PUBLIC API RESOLVER
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
# 6. PIPELINE UTAMA (RUN_SUB_ENGINE)
# ==============================================================================
def run_sub_engine(raw_imdb_id: str) -> bool:
    meta = resolve_media_metadata(raw_imdb_id)
    imdb_id = meta["imdb_id"]
    base_id = meta["base_imdb"]
    shard_idx = get_redis_shard_index(imdb_id)

    console.print(Panel.fit(
        f"[bold cyan]⚡ OPENSUBTITLES ENGINE V3: MEMPROSES TUGAS[/bold cyan]\n"
        f"ID Sasaran   : [bold yellow]{imdb_id}[/bold yellow] (Base: [white]{base_id}[/white])\n"
        f"Tajuk Sah    : [bold green]{meta['title']}[/bold green] (Tahun: [yellow]{meta.get('year', '-')}[/yellow])\n"
        f"Bahasa Fokus : [bold magenta]Bahasa Melayu (MS) & Indonesia (ID) SAHAJA[/bold magenta]",
        border_style="cyan"
    ))

    # Kunci Pemprosesan di Redis untuk Mengelak Perlumbaan Tugas (Race Condition)
    if hasattr(_redis, "set_processing_lock") and not _redis.set_processing_lock(f"os:{imdb_id}", ttl_seconds=300):
        console.print(f"[yellow]⚠️ Tugasan OpenSubtitles untuk {imdb_id} sedang diproses oleh pelari lain. Tamat.[/yellow]")
        return True

    try:
        os_candidates = fetch_opensubtitles_candidates(meta)
        console.print(f"[cyan]📦 Jumlah Calon OpenSubtitles Ditemui:[/cyan] {len(os_candidates)}")

        if not os_candidates:
            console.print(f"[bold yellow]⚠️ Tiada sarikata BM/ID ditemui di OpenSubtitles untuk {imdb_id}.[/bold yellow]")
            return False

        uploaded_records = []
        seen_content_hashes = set()

        table = Table(title=f"📋 Audit Muat Naik OpenSubtitles: {meta['title']}", border_style="green")
        table.add_column("No", justify="center", style="cyan", width=4)
        table.add_column("Bahasa", justify="center", style="magenta", width=6)
        table.add_column("Nama Penuh Fail Sarikata (.srt)", style="white")
        table.add_column("Destinasi B2", justify="center", style="green", width=14)
        table.add_column("Redis Shard", justify="center", style="blue", width=12)
        table.add_column("Status", justify="center", style="bold green", width=10)

        for item in os_candidates:
            try:
                bin_resp = requests.get(item["download_url"], impersonate="chrome120", timeout=15)
                if bin_resp.status_code != 200 or len(bin_resp.content) < 50:
                    continue

                unpacked = unpack_subtitles_in_memory(bin_resp.content, filename_hint=item["release_title"])
                for sub_obj in unpacked:
                    content_str = sub_obj["content"]
                    ext = sub_obj["ext"]
                    raw_title = sub_obj["filename"]

                    content_hash = hashlib.sha256(content_str.encode("utf-8", errors="ignore")).hexdigest()
                    if content_hash in seen_content_hashes:
                        continue
                    seen_content_hashes.add(content_hash)

                    standard_fn = build_standard_sub_filename(
                        lang=item["lang"],
                        imdb_id=imdb_id,
                        raw_title=raw_title,
                        content=content_str,
                        ext=ext
                    )

                    clean_imdb_folder = sanitize_dot_string(imdb_id)
                    b2_relative_path = f"subs/{clean_imdb_folder}/{standard_fn}"

                    b2_res = _b2.upload_subtitle_to_b2(b2_relative_path, content_str)
                    bucket_name = b2_res.get("bucket_name", "")
                    proxy_stream_url = f"{CF_B2_SUB_PROXY}/{bucket_name}/{b2_relative_path.lstrip('/')}"
                    acc_idx = b2_res.get("account_index", 1)

                    rec_id = f"{item['lang']}_{clean_imdb_folder}_{len(uploaded_records) + 1}"
                    uploaded_records.append({
                        "id": rec_id,
                        "lang": item["lang"],
                        "url": proxy_stream_url,
                        "release": standard_fn,
                        "source": "OpenSubtitles",
                        "acc": int(acc_idx)
                    })

                    table.add_row(
                        str(len(uploaded_records)),
                        item["lang"].upper(),
                        standard_fn[:58],
                        f"Akaun #{acc_idx}",
                        f"Shard #{shard_idx}",
                        "SUKSES"
                    )

            except _b2.AllB2AccountsExhaustedException as e:
                console.print(f"[bold red]🚨 Had Penuh B2: {e}[/bold red]")
                break
            except Exception as ex:
                console.print(f"[dim red]Gagal muat naik: {ex}[/dim red]")

        if not uploaded_records:
            console.print("[bold red]❌ Tiada fail unik yang berjaya dimuat naik ke B2.[/bold red]")
            return False

        console.print(table)

        console.print(f"\n[cyan]💾 Mendaftarkan {len(uploaded_records)} sarikata ke Upstash Redis Shard #{shard_idx}...[/cyan]")
        _redis.save_subtitle_records_batch(imdb_id, uploaded_records)

        if meta["is_series"]:
            _redis.save_subtitle_records_batch(base_id, uploaded_records)

        console.print(Panel.fit(
            f"[bold green]🎉 TUGASAN OPENSUBTITLES SELESAI![/bold green]\n"
            f"├─ Sasaran IMDb   : [bold yellow]{imdb_id}[/bold yellow] ({meta['title']})\n"
            f"├─ Sarikata B2    : [bold green]{len(uploaded_records)} fail unik berjaya dimuat naik[/bold green]\n"
            f"├─ Kunci Redis    : [yellow]subs:{imdb_id}[/yellow] (Shard #{shard_idx})\n"
            f"└─ Proksi Zero    : [cyan]{CF_B2_SUB_PROXY}[/cyan]",
            border_style="green"
        ))
        return True

    finally:
        if hasattr(_redis, "remove_processing_lock"):
            _redis.remove_processing_lock(f"os:{imdb_id}")


# ==============================================================================
# 7. TITIK MASUK CLI
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OpenSubtitles Dedicated Engine V3")
    parser.add_argument("--imdb", required=True, help="Target IMDb ID (cth: tt3215824)")
    args = parser.parse_args()

    success = run_sub_engine(args.imdb)
    if not success:
        sys.exit(1)