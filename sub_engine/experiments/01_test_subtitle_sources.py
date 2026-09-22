#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO SUB ADDON - EXPERIMENT SUBTITLE SOURCES (API & CAMOUFOX STEALTH)
# LOKASI: /home/braderdin/stremio-sub-addon/sub_engine/experiments/01_test_subtitle_sources.py
# CIRI:
# 1. OpenSubtitles Public Stremio Resolver (curl_cffi - Tanpa API Key)
# 2. Camoufox Stealth GUI (Resolusi 1280x720 via context, Turnstile Resolver)
# 3. Pengetikan Manusia (press_sequentially) & Jeda Acak (human_delay)
# 4. Dump Diagnostik Otomatis (.html, .png, .json) ke sub_engine/temp/
# ==============================================================================

import os
import re
import sys
import time
import json
import random
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional

from curl_cffi import requests
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

# 1. Direktori sub_engine
EXPERIMENT_DIR = Path(__file__).resolve().parent
SUB_ENGINE_DIR = EXPERIMENT_DIR.parent
TEMP_DIR = SUB_ENGINE_DIR / "temp"
DATA_DIR = SUB_ENGINE_DIR / "data"

for folder in [EXPERIMENT_DIR, TEMP_DIR, DATA_DIR]:
    folder.mkdir(parents=True, exist_ok=True)


# ==============================================================================
# 2. FUNGSI SIMULASI MANUSIA & CLOUDFLARE RESOLVER (DARI 02_b2_browser_worker_v3)
# ==============================================================================
def human_delay(min_s: float = 2.0, max_s: float = 4.0, action_name: str = ""):
    """Jeda acak menyerupai manusia."""
    wait_time = random.uniform(min_s, max_s)
    tag = f" ({action_name})" if action_name else ""
    console.print(f"[dim]⏳ Menunggu {wait_time:.2f}s{tag}...[/dim]")
    time.sleep(wait_time)


def human_type(locator, text: str):
    """Mengetik karakter satu per satu dengan jeda natural."""
    locator.click()
    human_delay(0.8, 1.5)
    locator.press_sequentially(text, delay=random.randint(100, 220))
    human_delay(1.5, 2.5)


def resolve_cloudflare_turnstile(page, max_retries: int = 15):
    """Mendeteksi dan menyelesaikan checkbox Cloudflare Turnstile secara otomatis."""
    for _ in range(max_retries):
        page.wait_for_timeout(1000)
        try:
            for frame in page.frames:
                if "challenges.cloudflare.com" in frame.url or "turnstile" in frame.url:
                    chk = frame.query_selector("input[type=checkbox], .ctp-checkbox-label, #challenge-stage")
                    if chk:
                        console.print("[yellow]🛡️ Mendeteksi Cloudflare Turnstile, mencoba klik...[/yellow]")
                        chk.click()
                        page.wait_for_timeout(2000)

            content = page.content().lower()
            if "just a moment" not in content and "attention required" not in content:
                break
        except Exception:
            continue


# ==============================================================================
# 3. SUMBER 1: OPENSUBTITLES PUBLIC RESOLVER (TANPA API KEY)
# ==============================================================================
def fetch_opensubtitles_public(target_id: str) -> List[Dict[str, Any]]:
    """Mengambil subtitle dari OpenSubtitles Public Resolver menggunakan curl_cffi."""
    is_series = ":" in target_id
    media_type = "series" if is_series else "movie"
    url = f"https://opensubtitles-v3.strem.io/subtitles/{media_type}/{target_id}.json"

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
            data = resp.json()
            raw_subs = data.get("subtitles", [])

            # Simpan respons mentah ke folder temp
            raw_out_path = TEMP_DIR / f"raw_opensubtitles_{target_id.replace(':', '_')}.json"
            with open(raw_out_path, "w", encoding="utf-8") as f:
                json.dump(raw_subs, f, indent=2, ensure_ascii=False)

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
# 4. SUMBER 2: CAMOUFOX STEALTH BROWSER (SUBSOURCE SCRAPER)
# ==============================================================================
def scrape_subsource_with_camoufox(query_title: str, target_id: str) -> List[Dict[str, Any]]:
    """
    Menjalankan browser Camoufox GUI (1280x720) dengan penanganan Turnstile dan
    simulasi ketukan manusia untuk mengikis subtitle dari Subsource.
    """
    console.print(Panel.fit(
        f"[bold magenta]🦊 MELUNCURKAN CAMOUFOX STEALTH (1280x720 GUI)[/bold magenta]\n"
        f"Laman Target : [yellow]https://subsource.net[/yellow]\n"
        f"Pencarian    : [white]{query_title} ({target_id})[/white]",
        border_style="magenta"
    ))

    out_subs = []
    clean_target = target_id.replace(":", "_")
    base_imdb = target_id.split(":")[0]

    try:
        from camoufox.sync_api import Camoufox
    except ImportError:
        console.print("[bold red]❌ Pustaka 'camoufox' belum terpasang.[/bold red]")
        return []

    # Pola inisialisasi yang sudah terbukti di 02_b2_browser_worker_v3.py
    try:
        with Camoufox(headless=False, geoip=True) as browser:
            context = browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"
            )
            page = context.new_page()

            try:
                # 1. Buka halaman utama Subsource
                console.print("[cyan]🚀 Membuka subsource.net...[/cyan]")
                page.goto("https://subsource.net", wait_until="domcontentloaded", timeout=45000)
                resolve_cloudflare_turnstile(page)
                human_delay(2.0, 3.5, "loading awal")

                # 2. Cari kotak pencarian
                search_box = page.locator("input[type='search'], input[placeholder*='Search'], input[name='query']").first
                if not search_box.is_visible():
                    search_box = page.locator("input").first

                if search_box.is_visible():
                    console.print(f"[green]⌨️ Mengetik judul:[/green] [bold white]{query_title}[/bold white]")
                    human_type(search_box, query_title)
                    search_box.press("Enter")
                else:
                    console.print("[yellow]⚠️ Navigasi langsung ke URL pencarian...[/yellow]")
                    page.goto(f"https://subsource.net/search?q={query_title}", wait_until="domcontentloaded", timeout=30000)

                resolve_cloudflare_turnstile(page)
                human_delay(3.0, 4.5, "menunggu hasil pencarian")

                # Ambil tangkapan layar dan simpan HTML pencarian
                page.screenshot(path=str(TEMP_DIR / f"search_result_{clean_target}.png"))
                (TEMP_DIR / f"search_page_{clean_target}.html").write_text(page.content(), encoding="utf-8")

                # 3. Klik hasil yang cocok
                movie_link = page.locator(f"a[href*='/subtitles/']:has-text('{query_title}')").first
                if not movie_link.is_visible():
                    movie_link = page.locator("a[href*='/subtitles/']").first

                if movie_link.is_visible():
                    console.print("[green]🎯 Menemukan halaman entri media, membuka...[/green]")
                    movie_link.click()
                    page.wait_for_load_state("domcontentloaded")
                    resolve_cloudflare_turnstile(page)
                    human_delay(3.0, 5.0, "loading subtitle list")

                    # Simpan tangkapan layar halaman subtitle
                    page.screenshot(path=str(TEMP_DIR / f"subtitles_page_{clean_target}.png"))

                    # Cari subtitle bahasa Melayu atau Indonesia
                    rows = page.locator(".subtitle-entry, tr, div[class*='item']").all()
                    for r in rows:
                        txt = r.inner_text().lower()
                        if "malay" in txt or "indonesia" in txt:
                            dl_link = r.locator("a[href*='/subtitles/'], a[href*='download']").first
                            if dl_link.is_visible():
                                href = dl_link.get_attribute("href") or ""
                                full_url = f"https://subsource.net{href}" if href.startswith("/") else href
                                out_subs.append({
                                    "id": f"subsource_{len(out_subs) + 1}",
                                    "source": "Subsource",
                                    "lang": "ms" if "malay" in txt else "id",
                                    "url": full_url,
                                    "release": r.inner_text().split("\n")[0][:45],
                                })

            except Exception as page_err:
                console.print(f"[bold red]⚠️ Masalah navigasi di halaman Subsource:[/bold red] {page_err}")
                try:
                    page.screenshot(path=str(TEMP_DIR / f"crash_page_{clean_target}.png"))
                except Exception:
                    pass
            finally:
                page.close()
                context.close()

    except Exception as browser_err:
        console.print(f"[bold red]❌ Gagal meluncurkan browser Camoufox:[/bold red] {browser_err}")

    return out_subs


# ==============================================================================
# 5. RESOLVER JUDUL VIA CINEMETA
# ==============================================================================
def fetch_title_from_cinemeta(imdb_id: str) -> str:
    """Mengambil judul resmi dari API Cinemeta Stremio."""
    base_id = imdb_id.split(":")[0]
    for m_type in ["movie", "series"]:
        url = f"https://v3-cinemeta.strem.io/meta/{m_type}/{base_id}.json"
        try:
            resp = requests.get(url, impersonate="chrome120", timeout=8)
            if resp.status_code == 200:
                name = resp.json().get("meta", {}).get("name")
                if name:
                    return name
        except Exception:
            continue
    return base_id


# ==============================================================================
# 6. PIPELINE UTAMA PENGUJIAN
# ==============================================================================
def run_experiment(target_id: str):
    clean_target = target_id.strip()
    is_series = ":" in clean_target
    base_id = clean_target.split(":")[0]

    canonical_title = fetch_title_from_cinemeta(base_id)

    console.print(Panel.fit(
        f"[bold cyan]🧪 EKSPERIMEN SUBTITLE RESOLVER MULTI-SOURCE[/bold cyan]\n"
        f"Target ID : [bold yellow]{clean_target}[/bold yellow] | Base IMDb: [white]{base_id}[/white]\n"
        f"Judul     : [bold green]{canonical_title}[/bold green]\n"
        f"Kategori  : [magenta]{'Serial TV' if is_series else 'Film'}[/magenta]",
        border_style="cyan"
    ))

    # --- UJIAN 1: OpenSubtitles API (Cepat & Ringan) ---
    console.print("\n[bold white]▶ UJIAN 1: Memeriksa OpenSubtitles Resolver (curl_cffi)...[/bold white]")
    start_t = time.time()
    os_results = fetch_opensubtitles_public(clean_target)
    os_duration = time.time() - start_t
    console.print(f"✔ Selesai dalam {os_duration:.2f}s | Ditemukan: [bold green]{len(os_results)}[/bold green] subtitle.")

    # --- UJIAN 2: Camoufox GUI Stealth Browser ---
    console.print("\n[bold white]▶ UJIAN 2: Menjalankan Camoufox Stealth (Subsource Scraper)...[/bold white]")
    subsource_results = scrape_subsource_with_camoufox(canonical_title, clean_target)
    console.print(f"✔ Selesai di Subsource | Ditemukan: [bold green]{len(subsource_results)}[/bold green] subtitle.")

    # --- REKAP HASIL GABUNGAN ---
    all_results = os_results + subsource_results

    result_file = TEMP_DIR / f"subs_result_{clean_target.replace(':', '_')}.json"
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)

    if all_results:
        table = Table(
            title=f"📋 Ringkasan Subtitle Ditemukan: {canonical_title} ({clean_target})",
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
        console.print(f"\n[bold green]✅ Hasil gabungan berhasil disimpan di:[/bold green] [cyan]{result_file}[/cyan]\n")
    else:
        console.print(f"\n[bold red]❌ Tidak ditemukan subtitle BM/ID untuk {clean_target} dari kedua sumber.[/bold red]\n")


def main():
    parser = argparse.ArgumentParser(description="Subtitle Resolver Experiment")
    parser.add_argument("--imdb", default="tt27165187", help="Target IMDb ID (misal: tt27165187 atau tt0944947:1:1)")
    args = parser.parse_args()

    run_experiment(args.imdb)


if __name__ == "__main__":
    main()