#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO SUB ADDON - SUB ENGINE CORE V3 (SUBSOURCE STANDALONE ENGINE)
# LOKASI: /home/braderdin/stremio-sub-addon/sub_engine/02_sub_engine_core_subsource.py
# CIRI-CIRI UTAMA:
# 1. Sokongan Penuh Siri TV (/series/) & Filem (/subtitles/) Tanpa Ralat
# 2. Gelung Pintar Polling API (Anti-Race Condition di GitHub Actions)
# 3. Tiada Had Muat Turun (Unlimited Download: Sedut SEMUA sarikata ditemui)
# 4. In-Page Table Filter 'malay' & 'indonesia'
# 5. Penyahmampatan Memori (.zip/.rar/.srt) & De-duplikasi SHA-256
# 6. Pendaftaran Seimbang Multi-Akaun B2 & Shard Upstash Redis
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


# ==============================================================================
# 2. BANTUAN SIMULASI & SANITASI FORMAT TITIK
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
# 5. ENJIN UTAMA SUBSOURCE CERDAS (SMART SERIES/MOVIE & TANPA HAD)
# ==============================================================================
def scrape_and_download_subsource_full(meta: Dict[str, Any], headless: bool = True) -> List[Dict[str, Any]]:
    base_imdb = meta["base_imdb"]
    clean_imdb = meta["imdb_id"]
    title = meta["title"]
    target_season = meta.get("season")

    console.print(Panel.fit(
        f"[bold magenta]🦊 SUBSOURCE STANDALONE ENGINE V3 (HEADLESS: {headless})[/bold magenta]\n"
        f"Sasaran IMDb ID  : [bold yellow]{base_imdb}[/bold yellow] ({title})\n"
        f"Format / Musim   : [bold white]{f'Siri TV (Musim {target_season})' if target_season else 'Auto-Detect Siri/Filem'}[/bold white]\n"
        f"Had Muat Turun   : [bold green]TIADA HAD (Muat turun SEMUA sarikata ditemui)[/bold green]\n"
        f"Strategi Carian  : [cyan]Polling API Bypass + Multi-Pattern Modal Navigator[/cyan]",
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

            # Listener pintar bagi menyadap URL langsung dari respons API Subsource
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
                # LANGKAH 1: LAYARI LAMAN UTAMA & TAIP IMDB ID
                # -------------------------------------------------------------
                console.print("[cyan]🚀 [1/4] Melayari https://subsource.net...[/cyan]")
                page.goto("https://subsource.net", wait_until="domcontentloaded", timeout=45000)
                human_delay(2.0, 3.0, "pemuatan laman utama")

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
                # LANGKAH 2: POLLING RESPON API / KAD MODAL (ANTI-RACE CONDITION)
                # -------------------------------------------------------------
                console.print("[cyan]⏳ Menunggu popup cadangan atau respons API (maks 10s)...[/cyan]")
                start_polling = time.time()
                popup_link_found = ""

                while time.time() - start_polling < 10.0:
                    # 1. Semak jika pautan API berjaya disadap
                    if intercepted_direct_links:
                        popup_link_found = intercepted_direct_links[0]
                        break

                    # 2. Semak jika elemen kad siri (/series/) atau filem (/subtitles/) sudah terbit di DOM
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

                # Navigasi tepat ke sasaran (Bypass Backdrop)
                if popup_link_found:
                    target_content_url = urljoin("https://subsource.net", popup_link_found)
                    console.print(f"[bold green]🎯 [Navigasi Berjaya] Membuka:[/bold green] {target_content_url}")
                    page.goto(target_content_url, wait_until="domcontentloaded", timeout=35000)
                else:
                    console.print("[yellow]⚠️ Popup tidak dikesan, menekan kad pertama dalam modal carian...[/yellow]")
                    first_link = page.locator("header a, div[role='dialog'] a").first
                    if first_link.is_visible():
                        first_link.click(force=True)
                    else:
                        search_box.press("Enter")

                page.wait_for_load_state("domcontentloaded")
                human_delay(3.0, 4.0, "pemuatan halaman kandungan")

                # PENGESAHAN: Pastikan pelayar tidak tersangkut di laman utama!
                if page.url.rstrip("/") == "https://subsource.net":
                    console.print("[bold red]❌ Ralat: Pelayar masih tersangkut di halaman utama. Carian IMDb Subsource gagal memuatkan kad.[/bold red]")
                    return []

                # -------------------------------------------------------------
                # LANGKAH 3: LOGIK KHAS SIRI TV VS FILEM
                # -------------------------------------------------------------
                console.print("[cyan]🔍 [3/4] Memeriksa struktur halaman (Musim Siri TV vs Jadual Terus)...[/cyan]")
                season_elements = page.locator("a[href*='season-'], a[href*='/season/'], a:has-text('Season')").all()
                if not season_elements:
                    season_elements = page.locator("div[class*='season'], div:has(> p:has-text('Season'))").all()

                if season_elements:
                    console.print(f"[bold magenta]📺 Terdeteksi sebagai Siri TV ({len(season_elements)} entri musim dijumpai)![/bold magenta]")
                    selected_season_el = None
                    chosen_label = ""

                    # 1. Pilih musim spesifik jika diminta
                    if target_season:
                        pat = re.compile(rf"\bSeason\s*{target_season}\b", re.IGNORECASE)
                        for el in season_elements:
                            txt = el.inner_text().strip()
                            if pat.search(txt) or f"season-{target_season}" in (el.get_attribute("href") or ""):
                                selected_season_el = el
                                chosen_label = f"Season {target_season}"
                                break

                    # 2. Auto-detect musim pertama yang mengandungi sarikata (> 0 subtitles)
                    if not selected_season_el:
                        for el in season_elements:
                            txt = el.inner_text().strip()
                            if "0 subtitles" not in txt.lower() and "subtitles" in txt.lower():
                                selected_season_el = el
                                chosen_label = txt.split("\n")[0]
                                break

                    if not selected_season_el and season_elements:
                        selected_season_el = season_elements[0]
                        chosen_label = selected_season_el.inner_text().split("\n")[0]

                    if selected_season_el:
                        console.print(f"[bold green]   └─ Membuka Musim:[/bold green] [white]{chosen_label}[/white]")
                        s_href = selected_season_el.get_attribute("href")
                        if s_href:
                            page.goto(urljoin("https://subsource.net", s_href), wait_until="domcontentloaded", timeout=35000)
                        else:
                            selected_season_el.click(force=True)

                        page.wait_for_load_state("domcontentloaded")
                        human_delay(2.5, 4.0, "pemuatan jadual musim")
                else:
                    console.print("[green]🎬 Terdeteksi sebagai Filem atau Siri dengan jadual langsung.[/green]")

                # -------------------------------------------------------------
                # LANGKAH 4: IN-PAGE SEARCH & KUTIP SEMUA SARIKATA BM & ID
                # -------------------------------------------------------------
                table_search = page.locator("input[placeholder*='Search subtitles'], input[placeholder*='subtitles']").first
                if not table_search.is_visible():
                    page.reload(wait_until="domcontentloaded")
                    human_delay(2.5, 3.5, "refresh jadual sarikata")
                    table_search = page.locator("input[placeholder*='Search subtitles'], input[placeholder*='subtitles']").first

                targets_to_download: List[Dict[str, str]] = []
                seen_detail_urls = set()

                if table_search.is_visible():
                    # --- FASA A: BAHASA MELAYU (MALAY) ---
                    console.print("\n[yellow]🔍 [Saringan A] Menyaring 'malay'...[/yellow]")
                    table_search.click()
                    table_search.fill("")
                    table_search.press_sequentially("malay", delay=100)
                    human_delay(2.0, 3.5, "menunggu senarai Melayu")

                    rows_malay = page.locator("tbody tr, div[class*='table'] div[class*='row'], tr").all()
                    for r in rows_malay:
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
                                targets_to_download.append({"lang": "ms", "release_title": rel, "detail_url": full_u})

                    console.print(f"[green]✔ Berjaya mengesan {len([t for t in targets_to_download if t['lang'] == 'ms'])} sarikata Bahasa Melayu![/green]")

                    # --- FASA B: BAHASA INDONESIA (INDONESIA) ---
                    console.print("\n[yellow]🔍 [Saringan B] Menyaring 'indonesia'...[/yellow]")
                    table_search.click()
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Backspace")
                    time.sleep(0.4)
                    table_search.press_sequentially("indonesia", delay=100)
                    human_delay(2.0, 3.5, "menunggu senarai Indonesia")

                    rows_indo = page.locator("tbody tr, div[class*='table'] div[class*='row'], tr").all()
                    for r in rows_indo:
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
                                targets_to_download.append({"lang": "id", "release_title": rel, "detail_url": full_u})

                    console.print(f"[green]✔ Berjaya mengesan {len([t for t in targets_to_download if t['lang'] == 'id'])} sarikata Bahasa Indonesia![/green]")

                # -------------------------------------------------------------
                # LANGKAH 5: MUAT TURUN 100% SARIKATA (TANPA HAD KUOTA)
                # -------------------------------------------------------------
                total_dl = len(targets_to_download)
                if total_dl > 0:
                    console.print(f"\n[bold magenta]📥 [4/4] Memuat turun SEMUA {total_dl} sarikata fizikal dari Subsource...[/bold magenta]")

                    for idx, target in enumerate(targets_to_download, 1):
                        console.print(f"[cyan]   [{idx}/{total_dl}] Mengunduh:[/cyan] {target['release_title'][:50]} ({target['lang'].upper()})...")

                        dl_tab = context.new_page()
                        try:
                            dl_tab.goto(target["detail_url"], wait_until="domcontentloaded", timeout=35000)
                            human_delay(1.8, 3.0, f"halaman rilis #{idx}")

                            dl_btn = dl_tab.locator("button:has-text('Download'), a:has-text('Download'), button[class*='download']").first
                            if not dl_btn.is_visible():
                                dl_tab.reload(wait_until="domcontentloaded")
                                human_delay(2.0, 3.0, "refresh butang download")
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
                            console.print(f"[dim red]      └─ Gagal muat turun #{idx}: {dl_err}[/dim red]")
                        finally:
                            dl_tab.close()

                        human_delay(1.5, 2.8, "jeda antara muat turun")

            except Exception as page_err:
                console.print(f"[bold red]❌ Ralat automasi Subsource: {page_err}[/bold red]")
            finally:
                page.close()
                context.close()

    except Exception as browser_err:
        console.print(f"[bold red]❌ Gagal melancarkan Camoufox: {browser_err}[/bold red]")

    return extracted_records


# ==============================================================================
# 6. PIPELINE MUAT NAIK & PENDAFTARAN B2 / REDIS
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
            console.print(f"[bold yellow]⚠️ Tiada sarikata BM/ID ditemui di Subsource untuk {imdb_id}.[/bold yellow]")
            return False

        console.print(f"\n[bold cyan]📦 Memproses {len(subsource_results)} fail sarikata dari Subsource ke B2 & Redis...[/bold cyan]")

        uploaded_records = []
        seen_content_hashes = set()

        table = Table(title=f"📋 Audit Muat Naik Subsource V3: {meta['title']}", border_style="magenta")
        table.add_column("No", justify="center", style="cyan", width=4)
        table.add_column("Bahasa", justify="center", style="magenta", width=6)
        table.add_column("Nama Penuh Fail Sarikata (.srt)", style="white")
        table.add_column("Destinasi B2", justify="center", style="green", width=14)
        table.add_column("Redis Shard", justify="center", style="blue", width=12)
        table.add_column("Status", justify="center", style="bold green", width=10)

        for sub_item in subsource_results:
            lang = sub_item["lang"]
            content_str = sub_item["content"]
            ext = sub_item["ext"]
            raw_title = sub_item["release_title"]

            content_hash = hashlib.sha256(content_str.encode("utf-8", errors="ignore")).hexdigest()
            if content_hash in seen_content_hashes:
                continue
            seen_content_hashes.add(content_hash)

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
                uploaded_records.append({
                    "id": rec_id,
                    "lang": lang,
                    "url": proxy_stream_url,
                    "release": standard_fn,
                    "source": "Subsource",
                    "acc": int(acc_idx)
                })

                table.add_row(
                    str(len(uploaded_records)),
                    lang.upper(),
                    standard_fn[:58],
                    f"Akaun #{acc_idx}",
                    f"Shard #{shard_idx}",
                    "SUKSES"
                )
            except _b2.AllB2AccountsExhaustedException as e:
                console.print(f"[bold red]🚨 Had Penuh B2: {e}[/bold red]")
                break
            except Exception as ex:
                console.print(f"[dim red]Gagal muat naik ke B2: {ex}[/dim red]")

        if not uploaded_records:
            console.print("[bold red]❌ Tiada fail unik yang berjaya dimuat naik ke B2.[/bold red]")
            return False

        console.print(table)

        console.print(f"\n[cyan]💾 Mendaftarkan {len(uploaded_records)} sarikata ke Upstash Redis Shard #{shard_idx}...[/cyan]")
        _redis.save_subtitle_records_batch(imdb_id, uploaded_records)

        if meta["is_series"]:
            _redis.save_subtitle_records_batch(base_id, uploaded_records)

        console.print(Panel.fit(
            f"[bold green]🎉 TUGASAN SUBSOURCE SELESAI: SEMUA SARIKATA DIKEMAS KINI![/bold green]\n"
            f"├─ Sasaran IMDb   : [bold yellow]{imdb_id}[/bold yellow] ({meta['title']})\n"
            f"├─ Sarikata B2    : [bold green]{len(uploaded_records)} fail unik berjaya dimuat naik[/bold green]\n"
            f"├─ Kunci Redis    : [yellow]subs:{imdb_id}[/yellow] (Shard #{shard_idx})\n"
            f"└─ Proksi Zero    : [cyan]{CF_B2_SUB_PROXY}[/cyan]",
            border_style="green"
        ))
        return True

    finally:
        if hasattr(_redis, "remove_processing_lock"):
            _redis.remove_processing_lock(f"subsource:{imdb_id}")


# ==============================================================================
# 7. TITIK MASUK CLI
# ==============================================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Subsource Dedicated Engine V3 (Unlimited Downloads)")
    parser.add_argument("--imdb", required=True, help="Target IMDb ID (cth: tt1199099 atau tt1199099:1:2)")
    parser.add_argument("--visible", action="store_true", help="Buka GUI pelayar Camoufox (lalai: headless)")
    args = parser.parse_args()

    success = run_subsource_engine(args.imdb, headless=(not args.visible))
    if not success:
        sys.exit(1)