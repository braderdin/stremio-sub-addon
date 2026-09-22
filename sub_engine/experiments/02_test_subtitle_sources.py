#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO SUB ADDON - EXPERIMENT SUBTITLE SOURCES V2 (IMDb-FIRST & TMDB)
# LOKASI: /home/braderdin/stremio-sub-addon/sub_engine/experiments/02_test_subtitle_sources.py
# CIRI:
# 1. Resolver Metadata: Cinemeta (Utama) + TMDB API (.env.local) untuk Verifikasi Tahun.
# 2. Subsource IMDb-First Search: Cari 'tt...' dahulu, fallback ke 'Judul + Tahun'.
# 3. Verifikasi Hasil Carian: Hanya klik jika judul dan tahun rilis sepadan.
# 4. OpenSubtitles Public Stremio Resolver (curl_cffi - Movie & Series).
# 5. Kompresi Temp Otomatis: Gambar JPEG (~30KB) & Dump HTML terkompresi GZIP (.html.gz).
# ==============================================================================

import os
import re
import sys
import gzip
import time
import json
import random
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from curl_cffi import requests
from dotenv import dotenv_values
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

# 1. Konfigurasi Laluan Direktori
EXPERIMENT_DIR = Path(__file__).resolve().parent
SUB_ENGINE_DIR = EXPERIMENT_DIR.parent
PROJECT_ROOT = SUB_ENGINE_DIR.parent
TEMP_DIR = SUB_ENGINE_DIR / "temp"
DATA_DIR = SUB_ENGINE_DIR / "data"

for folder in [EXPERIMENT_DIR, TEMP_DIR, DATA_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

# 2. Muat Kredensial TMDB dari .env.local
ENV_LOCAL_PATH = PROJECT_ROOT / ".env.local"
env_vars = dotenv_values(str(ENV_LOCAL_PATH)) if ENV_LOCAL_PATH.exists() else {}

TMDB_API_KEY = (
    os.getenv("TMDB_API_KEY") or env_vars.get("TMDB_API_KEY", "")
).strip()
TMDB_READ_TOKEN = (
    os.getenv("TMDB_READ_TOKEN") or env_vars.get("TMDB_READ_TOKEN", "")
).strip()


# ==============================================================================
# 3. FUNGSI SIMULASI MANUSIA & PENGENDALIAN CLOUDFLARE
# ==============================================================================
def human_delay(min_s: float = 2.0, max_s: float = 4.0, action_name: str = ""):
    wait_time = random.uniform(min_s, max_s)
    tag = f" ({action_name})" if action_name else ""
    console.print(f"[dim]⏳ Menunggu {wait_time:.2f}s{tag}...[/dim]")
    time.sleep(wait_time)


def human_type(locator, text: str):
    locator.click()
    human_delay(0.6, 1.2)
    locator.press_sequentially(text, delay=random.randint(90, 180))
    human_delay(1.2, 2.0)


def resolve_cloudflare_turnstile(page, max_retries: int = 15):
    for _ in range(max_retries):
        page.wait_for_timeout(1000)
        try:
            for frame in page.frames:
                if "challenges.cloudflare.com" in frame.url or "turnstile" in frame.url:
                    chk = frame.query_selector("input[type=checkbox], .ctp-checkbox-label, #challenge-stage")
                    if chk:
                        console.print("[yellow]🛡️ Mendeteksi Cloudflare Turnstile, mencoba verifikasi...[/yellow]")
                        chk.click()
                        page.wait_for_timeout(2000)

            content = page.content().lower()
            if "just a moment" not in content and "attention required" not in content:
                break
        except Exception:
            continue


# ==============================================================================
# 4. FUNGSI KOMPRESI FILE DIAGNOSTIK (TARGET ~30KB)
# ==============================================================================
def save_compressed_screenshot(page, file_prefix: str) -> Path:
    """Menyimpan screenshot dalam format JPEG terkompresi dengan target ukuran ~30KB."""
    out_path = TEMP_DIR / f"{file_prefix}.jpg"
    try:
        # Gunakan Playwright native JPEG compression dengan kualitas 25-30%
        page.screenshot(path=str(out_path), type="jpeg", quality=28)
        sz_kb = out_path.stat().st_size / 1024
        console.print(f"[dim]📸 Tangkapan layar terkompresi disimpan: {out_path.name} ({sz_kb:.1f} KB)[/dim]")
    except Exception as e:
        console.print(f"[dim red]Gagal kompresi screenshot: {e}[/dim red]")
    return out_path


def save_compressed_html(html_text: str, file_prefix: str) -> Path:
    """Mengompresi teks HTML mentah menggunakan GZIP (.html.gz)."""
    out_path = TEMP_DIR / f"{file_prefix}.html.gz"
    try:
        compressed_data = gzip.compress(html_text.encode("utf-8"))
        out_path.write_bytes(compressed_data)
        sz_kb = out_path.stat().st_size / 1024
        console.print(f"[dim]🗜️ HTML Gzip disimpan: {out_path.name} ({sz_kb:.1f} KB)[/dim]")
    except Exception as e:
        console.print(f"[dim red]Gagal kompresi HTML: {e}[/dim red]")
    return out_path


# ==============================================================================
# 5. RESOLVER METADATA BERLAPIS (CINEMETA + TMDB ENRICHMENT)
# ==============================================================================
def resolve_media_metadata(imdb_id: str) -> Dict[str, Any]:
    """
    Mengambil metadata resmi dari Cinemeta dan melengkapinya dengan TMDB API.
    Memastikan kecocokan IMDb ID 100% dengan Stremio.
    """
    base_id = imdb_id.split(":")[0]
    is_series = ":" in imdb_id

    resolved = {
        "imdb_id": imdb_id,
        "base_imdb": base_id,
        "is_series": is_series,
        "title": base_id,
        "year": "",
        "media_type": "series" if is_series else "movie"
    }

    # 1. Panggilan Utama: Stremio Cinemeta API
    cinemeta_type = "series" if is_series else "movie"
    c_url = f"https://v3-cinemeta.strem.io/meta/{cinemeta_type}/{base_id}.json"
    try:
        c_resp = requests.get(c_url, impersonate="chrome120", timeout=8)
        if c_resp.status_code == 200:
            meta = c_resp.json().get("meta", {})
            name = meta.get("name", "")
            raw_year = str(meta.get("year", "")).split("–")[0].split("-")[0].strip()
            if name:
                resolved["title"] = name
            if raw_year:
                resolved["year"] = raw_year
    except Exception as e:
        console.print(f"[dim yellow]⚠️ Cinemeta API terlewat: {e}[/dim yellow]")

    # 2. Panggilan Pelengkap: TMDB API find/{imdb_id} (Jika Kredensial Ada)
    if TMDB_READ_TOKEN or TMDB_API_KEY:
        t_url = f"https://api.themoviedb.org/3/find/{base_id}?external_source=imdb_id"
        headers = {"Accept": "application/json"}
        if TMDB_READ_TOKEN:
            headers["Authorization"] = f"Bearer {TMDB_READ_TOKEN}"
        params = {} if TMDB_READ_TOKEN else {"api_key": TMDB_API_KEY}

        try:
            t_resp = requests.get(t_url, headers=headers, params=params, timeout=8)
            if t_resp.status_code == 200:
                data = t_resp.json()
                results = data.get("movie_results", []) or data.get("tv_results", [])
                if results:
                    best = results[0]
                    t_title = best.get("title") or best.get("name") or ""
                    date_str = best.get("release_date") or best.get("first_air_date") or ""
                    t_year = date_str.split("-")[0].strip()

                    # Utamakan tahun TMDB yang lebih akurat
                    if t_year:
                        resolved["year"] = t_year
                    # Jika judul Cinemeta kosong, gunakan TMDB
                    if not resolved["title"] or resolved["title"] == base_id:
                        resolved["title"] = t_title

                    console.print(f"[dim]🎬 TMDB Sinkron: '{t_title}' | Tahun: {t_year}[/dim]")
        except Exception as e:
            console.print(f"[dim yellow]⚠️ TMDB API fallback terlewat: {e}[/dim yellow]")

    return resolved


# ==============================================================================
# 6. SUMBER 1: OPENSUBTITLES PUBLIC RESOLVER (curl_cffi)
# ==============================================================================
def fetch_opensubtitles(target_id: str) -> List[Dict[str, Any]]:
    is_series = ":" in target_id
    endpoint_type = "series" if is_series else "movie"
    url = f"https://opensubtitles-v3.strem.io/subtitles/{endpoint_type}/{target_id}.json"

    console.print(f"[cyan]🌐 Mengakses OpenSubtitles Public API:[/cyan] [dim]{url}[/dim]")
    out_subs = []

    try:
        resp = requests.get(
            url,
            impersonate="chrome120",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        if resp.status_code == 200:
            raw_subs = resp.json().get("subtitles", [])

            # Simpan dump mentah terkompresi
            raw_json_str = json.dumps(raw_subs, indent=2, ensure_ascii=False)
            save_compressed_html(raw_json_str, f"raw_opensubs_{target_id.replace(':', '_')}")

            target_langs = {"ms", "may", "zsm", "malay", "id", "ind", "indonesian"}

            for s in raw_subs:
                lang = (s.get("lang") or "").lower().strip()
                sub_url = s.get("url") or ""
                sub_id = s.get("id") or ""

                if lang in target_langs or any(tl in lang for tl in ["malay", "indo"]):
                    out_subs.append({
                        "id": str(sub_id),
                        "source": "OpenSubtitles",
                        "lang": "ms" if "id" not in lang else "id",
                        "url": sub_url,
                        "release": Path(sub_url).stem or f"OpenSubtitles_{sub_id}",
                    })
    except Exception as e:
        console.print(f"[bold red]❌ Gagal memanggil OpenSubtitles API:[/bold red] {e}")

    return out_subs


# ==============================================================================
# 7. SUMBER 2: CAMOUFOX STEALTH (SUBSOURCE IMDb-FIRST SEARCH)
# ==============================================================================
def scrape_subsource_smart(meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    target_id = meta["imdb_id"]
    base_imdb = meta["base_imdb"]
    canonical_title = meta["title"]
    year = meta.get("year", "")
    clean_target = target_id.replace(":", "_")

    console.print(Panel.fit(
        f"[bold magenta]🦊 CAMOUFOX STEALTH (IMDb-FIRST FALLBACK)[/bold magenta]\n"
        f"Prioritas 1: Pencarian IMDb [yellow]{base_imdb}[/yellow]\n"
        f"Prioritas 2: Pencarian Teks [cyan]{canonical_title} {year}[/cyan]",
        border_style="magenta"
    ))

    out_subs = []

    try:
        from camoufox.sync_api import Camoufox
    except ImportError:
        console.print("[bold red]❌ Pustaka 'camoufox' belum terpasang.[/bold red]")
        return []

    try:
        with Camoufox(headless=False, geoip=True) as browser:
            context = browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"
            )
            page = context.new_page()

            try:
                # 1. Buka situs Subsource
                console.print("[cyan]🚀 Membuka Subsource...[/cyan]")
                page.goto("https://subsource.net", wait_until="domcontentloaded", timeout=45000)
                resolve_cloudflare_turnstile(page)
                human_delay(1.5, 3.0, "loading beranda")

                search_box = page.locator("input[type='search'], input[placeholder*='Search'], input[name='query']").first
                if not search_box.is_visible():
                    search_box = page.locator("input").first

                found_entry = False

                # ==============================================================
                # TAHAP A: CARI BERDASARKAN IMDb ID DAHULU
                # ==============================================================
                if search_box.is_visible():
                    console.print(f"[yellow]🔍 [Tahap 1] Mencari berdasarkan IMDb ID:[/yellow] [bold white]{base_imdb}[/bold white]")
                    human_type(search_box, base_imdb)
                    search_box.press("Enter")
                    resolve_cloudflare_turnstile(page)
                    human_delay(3.0, 4.5, "menunggu hasil IMDb")

                    # Periksa hasil
                    cards = page.locator("a[href*='/subtitles/']").all()
                    if cards:
                        for card in cards:
                            card_text = card.inner_text().lower()
                            if base_imdb.lower() in card_text or canonical_title.lower() in card_text:
                                console.print(f"[green]🎯 Ditemukan kecocokan entri IMDb: {card.inner_text()[:40]}[/green]")
                                card.click()
                                found_entry = True
                                break

                # ==============================================================
                # TAHAP B: FALLBACK JUDUL + TAHUN (JIKA IMDb TIDAK KELUAR)
                # ==============================================================
                if not found_entry:
                    search_query = f"{canonical_title} {year}".strip()
                    console.print(f"[yellow]🔄 [Tahap 2 Fallback] Mencari judul spesifik:[/yellow] [bold white]{search_query}[/bold white]")

                    # Bersihkan kotak teks atau navigasi langsung
                    page.goto(f"https://subsource.net/search?q={search_query}", wait_until="domcontentloaded", timeout=30000)
                    resolve_cloudflare_turnstile(page)
                    human_delay(3.0, 4.5, "menunggu hasil judul + tahun")

                    cards = page.locator("a[href*='/subtitles/']").all()
                    for card in cards:
                        card_text = card.inner_text()
                        # Verifikasi ganda: Judul COCOK dan Tahun COCOK
                        title_match = canonical_title.lower() in card_text.lower()
                        year_match = (year in card_text) if year else True

                        if title_match and year_match:
                            console.print(f"[green]🎯 Verifikasi sukses (Judul & Tahun Cocok):[/green] [bold white]{card_text[:50]}[/bold white]")
                            card.click()
                            found_entry = True
                            break

                # ==============================================================
                # TAHAP C: EKSTRAKSI SUBTITLE BM / ID
                # ==============================================================
                if found_entry:
                    page.wait_for_load_state("domcontentloaded")
                    resolve_cloudflare_turnstile(page)
                    human_delay(3.0, 4.5, "loading daftar subtitle")

                    # Simpan screenshot terkompresi (~30KB) & HTML Gzip
                    save_compressed_screenshot(page, f"subsource_verified_{clean_target}")
                    save_compressed_html(page.content(), f"subsource_page_{clean_target}")

                    # Ekstraksi baris subtitle
                    items = page.locator(".subtitle-entry, tr, div[class*='item']").all()
                    for item in items:
                        txt = item.inner_text().lower()
                        if "malay" in txt or "indonesia" in txt:
                            dl_link = item.locator("a[href*='/subtitles/'], a[href*='download']").first
                            if dl_link.is_visible():
                                href = dl_link.get_attribute("href") or ""
                                full_url = f"https://subsource.net{href}" if href.startswith("/") else href
                                out_subs.append({
                                    "id": f"subsource_{len(out_subs) + 1}",
                                    "source": "Subsource",
                                    "lang": "ms" if "malay" in txt else "id",
                                    "url": full_url,
                                    "release": item.inner_text().split("\n")[0][:45],
                                })
                else:
                    console.print(f"[yellow]⚠️ Tidak ditemukan entri Subsource yang cocok dengan {canonical_title} ({year}).[/yellow]")
                    save_compressed_screenshot(page, f"subsource_notfound_{clean_target}")

            except Exception as page_err:
                console.print(f"[bold red]⚠️ Ralat halaman Subsource:[/bold red] {page_err}")
                save_compressed_screenshot(page, f"crash_{clean_target}")
            finally:
                page.close()
                context.close()

    except Exception as browser_err:
        console.print(f"[bold red]❌ Browser Camoufox gagal meluncur:[/bold red] {browser_err}")

    return out_subs


# ==============================================================================
# 8. PIPELINE PENGUJIAN UTAMA
# ==============================================================================
def run_experiment(target_id: str):
    clean_target = target_id.strip()

    # 1. Resolusi Metadata Akurat (Cinemeta + TMDB)
    meta = resolve_media_metadata(clean_target)

    console.print(Panel.fit(
        f"[bold cyan]🧪 EKSPERIMEN SUBTITLE RESOLVER V2 (VERIFIKASI AKURAT)[/bold cyan]\n"
        f"Target ID   : [bold yellow]{clean_target}[/bold yellow] | Base IMDb: [white]{meta['base_imdb']}[/white]\n"
        f"Judul Resmi : [bold green]{meta['title']}[/bold green]\n"
        f"Tahun Rilis : [yellow]{meta.get('year', 'N/A')}[/yellow]\n"
        f"Kategori    : [magenta]{'Serial TV' if meta['is_series'] else 'Film'}[/magenta]",
        border_style="cyan"
    ))

    # --- UJIAN 1: OpenSubtitles API ---
    console.print("\n[bold white]▶ UJIAN 1: Memeriksa OpenSubtitles Resolver...[/bold white]")
    start_t = time.time()
    os_results = fetch_opensubtitles(clean_target)
    os_duration = time.time() - start_t
    console.print(f"✔ Selesai dalam {os_duration:.2f}s | Ditemukan: [bold green]{len(os_results)}[/bold green] subtitle.")

    # --- UJIAN 2: Camoufox Subsource (IMDb-First) ---
    console.print("\n[bold white]▶ UJIAN 2: Menjalankan Camoufox Subsource (IMDb-First & Verifikasi)...[/bold white]")
    subsource_results = scrape_subsource_smart(meta)
    console.print(f"✔ Selesai di Subsource | Ditemukan: [bold green]{len(subsource_results)}[/bold green] subtitle.")

    # --- REKAP GABUNGAN ---
    all_results = os_results + subsource_results

    result_file = TEMP_DIR / f"subs_v2_{clean_target.replace(':', '_')}.json"
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)

    if all_results:
        table = Table(
            title=f"📋 Hasil Subtitle Terverifikasi: {meta['title']} ({meta.get('year', '-')})",
            border_style="green",
        )
        table.add_column("No", justify="center", style="cyan", width=4)
        table.add_column("Sumber", justify="center", style="yellow", width=16)
        table.add_column("Bahasa", justify="center", style="magenta", width=8)
        table.add_column("Release / Judul", style="white")
        table.add_column("URL / Download Link", style="dim")

        for idx, sub in enumerate(all_results, 1):
            table.add_row(
                str(idx),
                sub["source"],
                sub["lang"].upper(),
                sub["release"][:40],
                sub["url"][:45] + "...",
            )

        console.print(table)
        console.print(f"\n[bold green]✅ Hasil gabungan disimpan di:[/bold green] [cyan]{result_file}[/cyan]\n")
    else:
        console.print(f"\n[bold red]❌ Tidak ditemukan subtitle BM/ID untuk {clean_target}.[/bold red]\n")


def main():
    parser = argparse.ArgumentParser(description="Subtitle Resolver Experiment V2")
    parser.add_argument("--imdb", default="tt35538033", help="Target IMDb ID (cth: tt35538033)")
    args = parser.parse_args()

    run_experiment(args.imdb)


if __name__ == "__main__":
    main()