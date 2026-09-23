#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO SUB ADDON - SUBSOURCE SMART SERIES & MOVIE SCRAPER TEST
# LOKASI: /home/braderdin/stremio-sub-addon/sub_engine/experiments/04_test_subsource_series_smart.py
# FITUR:
# 1. Bypass Backdrop Timeout via Penyadapan Respons API (api.subsource.net)
# 2. Deteksi Cerdas Serial TV: Mendukung Season Terpisah maupun Tabel Gabungan
# 3. Auto-Select Season Valid (> 0 subtitles) jika tidak ditentukan
# 4. In-Page Filter 'malay' & 'indonesia' + Unduhan Fisik Terverifikasi
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
from urllib.parse import urljoin, unquote
from typing import Dict, Any, List, Optional, Tuple

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

# 1. Konfigurasi Direktori
EXPERIMENT_DIR = Path(__file__).resolve().parent
SUB_ENGINE_DIR = EXPERIMENT_DIR.parent
TEMP_DIR = SUB_ENGINE_DIR / "temp"
DOWNLOADS_DIR = TEMP_DIR / "downloads"

for folder in [EXPERIMENT_DIR, TEMP_DIR, DOWNLOADS_DIR]:
    folder.mkdir(parents=True, exist_ok=True)


# ==============================================================================
# 2. BANTUAN SIMULASI & KOMPRESI
# ==============================================================================
def human_delay(min_s: float = 2.0, max_s: float = 3.5, tag: str = ""):
    wait = random.uniform(min_s, max_s)
    lbl = f" ({tag})" if tag else ""
    console.print(f"[dim]⏳ Menunggu {wait:.2f}s{lbl}...[/dim]")
    time.sleep(wait)


def save_compressed_html(html_text: str, filename_prefix: str) -> Path:
    out_path = TEMP_DIR / f"{filename_prefix}.html.gz"
    out_path.write_bytes(gzip.compress(html_text.encode("utf-8")))
    sz_kb = out_path.stat().st_size / 1024
    console.print(f"[dim]🗜️ HTML Gzip disimpan: {out_path.name} ({sz_kb:.1f} KB)[/dim]")
    return out_path


def save_compressed_screenshot(page, filename_prefix: str) -> Path:
    out_path = TEMP_DIR / f"{filename_prefix}.jpg"
    try:
        page.screenshot(path=str(out_path), type="jpeg", quality=28)
        sz_kb = out_path.stat().st_size / 1024
        console.print(f"[dim]📸 Screenshot disimpan: {out_path.name} ({sz_kb:.1f} KB)[/dim]")
    except Exception as e:
        console.print(f"[dim red]Gagal simpan screenshot: {e}[/dim red]")
    return out_path


def parse_imdb_input(raw_input: str) -> Tuple[str, Optional[int], Optional[int]]:
    """Mengekstrak Base IMDb ID, Season, dan Episode jika ada."""
    clean = unquote(raw_input).strip()
    parts = clean.split(":")
    base_imdb = parts[0]
    season = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    episode = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
    return base_imdb, season, episode


# ==============================================================================
# 3. ALUR KERJA UTAMA PENGUJIAN CERDAS
# ==============================================================================
def run_smart_subsource_test(raw_imdb: str = "tt1199099", target_season_opt: Optional[int] = None, max_downloads: int = 3):
    base_imdb, parsed_season, parsed_ep = parse_imdb_input(raw_imdb)
    target_season = target_season_opt or parsed_season

    console.print(Panel.fit(
        f"[bold cyan]🎯 PENGUJIAN CERDAS SUBSOURCE (SERIES & MOVIE ENGINE)[/bold cyan]\n"
        f"Target IMDb ID   : [bold yellow]{base_imdb}[/bold yellow] (Input: {raw_imdb})\n"
        f"Target Season    : [bold magenta]{f'Season {target_season}' if target_season else 'Auto-Detect (>0 Subs)'}[/bold magenta]\n"
        f"Maksimal Unduhan : [green]{max_downloads} berkas[/green]\n"
        f"Folder Output    : [cyan]{DOWNLOADS_DIR}[/cyan]",
        border_style="cyan"
    ))

    try:
        from camoufox.sync_api import Camoufox
    except ImportError:
        console.print("[bold red]❌ Pustaka Camoufox belum terpasang![/bold red]")
        return

    extracted_subtitles: List[Dict[str, Any]] = []
    intercepted_movie_links: List[str] = []

    with Camoufox(headless=False, geoip=True) as browser:
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
            accept_downloads=True
        )
        page = context.new_page()

        # Tangkap respons pencarian API Subsource secara asinkron
        def handle_response(resp):
            if "search" in resp.url and "api.subsource.net" in resp.url:
                try:
                    data = resp.json()
                    results = data.get("results", [])
                    for r in results:
                        link = r.get("link")
                        if link and link not in intercepted_movie_links:
                            intercepted_movie_links.append(link)
                except Exception:
                    pass

        page.on("response", handle_response)

        try:
            # -------------------------------------------------------------
            # TAHAP 1: BUKA BERANDA & KETIK IMDB ID
            # -------------------------------------------------------------
            console.print("[cyan]🚀 [1/5] Membuka https://subsource.net...[/cyan]")
            page.goto("https://subsource.net", wait_until="domcontentloaded", timeout=45000)
            human_delay(2.0, 3.0, "pemuatan beranda")

            search_box = page.locator("input[type='search'], input[placeholder*='Search'], input[name='query']").first
            if not search_box.is_visible():
                search_box = page.locator("input").first

            console.print(f"[yellow]⌨️ [2/5] Mengetik IMDb ID:[/yellow] {base_imdb}")
            search_box.click()
            search_box.fill("")
            search_box.press_sequentially(base_imdb, delay=random.randint(90, 140))
            human_delay(2.5, 4.0, "menunggu respons carian & popup")

            save_compressed_screenshot(page, f"search_popup_{base_imdb}")

            # -------------------------------------------------------------
            # TAHAP 2: NAVIGASI KE HALAMAN KONTEN (BYPASS BACKDROP)
            # -------------------------------------------------------------
            navigated = False

            # Prioritas 1: Gunakan link yang berhasil disadap dari API latar belakang
            if intercepted_movie_links:
                direct_url = urljoin("https://subsource.net", intercepted_movie_links[0])
                console.print(f"[bold green]🎯 [Bypass Sukses] Menavigasi langsung via API Link:[/bold green] {direct_url}")
                page.goto(direct_url, wait_until="domcontentloaded", timeout=35000)
                navigated = True

            # Prioritas 2: Ambil atribut href dari kartu di dalam header popup
            if not navigated:
                popup_link_el = page.locator("header a[href*='/subtitles/'], div[role='dialog'] a[href*='/subtitles/']").first
                if popup_link_el.is_visible():
                    href = popup_link_el.get_attribute("href")
                    if href:
                        target_url = urljoin("https://subsource.net", href)
                        console.print(f"[green]🎯 Mengakses via atribut tautan popup:[/green] {target_url}")
                        page.goto(target_url, wait_until="domcontentloaded", timeout=35000)
                        navigated = True

            # Prioritas 3: Klik paksa (force=True) untuk mengatasi tumpang tindih backdrop
            if not navigated:
                popup_card = page.locator("header a[href*='/subtitles/'], div[role='dialog'] a").first
                if popup_card.is_visible():
                    console.print("[yellow]⚠️ Mengklik kartu dengan opsi force=True...[/yellow]")
                    popup_card.click(force=True, timeout=8000)
                    navigated = True
                else:
                    console.print("[dim yellow]⚠️ Menekan tombol Enter pada kotak pencarian...[/dim yellow]")
                    search_box.press("Enter")
                    navigated = True

            page.wait_for_load_state("domcontentloaded")
            human_delay(3.0, 4.5, "pemuatan halaman konten")
            save_compressed_screenshot(page, f"content_page_ready_{base_imdb}")

            # -------------------------------------------------------------
            # TAHAP 3: CABANG LOGIKA SERIAL TV VS FILM
            # -------------------------------------------------------------
            console.print("[cyan]🔍 [3/5] Memeriksa apakah konten adalah Serial TV dengan pilihan Musim (Season)...[/cyan]")

            # Cari elemen kartu/tautan Season
            season_elements = page.locator("a[href*='season-'], a[href*='/season/'], a:has-text('Season')").all()
            
            # Jika tidak terdeteksi via tag <a>, cari container kartu musim
            if not season_elements:
                season_elements = page.locator("div[class*='season'], div:has(> p:has-text('Season'))").all()

            if season_elements:
                console.print(f"[bold magenta]📺 Terdeteksi sebagai Serial TV ({len(season_elements)} entri musim ditemukan)![/bold magenta]")

                selected_season_el = None
                chosen_season_label = ""

                # 1. Jika pengguna meminta season spesifik
                if target_season:
                    season_pattern = re.compile(rf"\bSeason\s*{target_season}\b", re.IGNORECASE)
                    for el in season_elements:
                        txt = el.inner_text().strip()
                        if season_pattern.search(txt) or f"season-{target_season}" in (el.get_attribute("href") or ""):
                            selected_season_el = el
                            chosen_season_label = f"Season {target_season}"
                            break

                # 2. Jika tidak ditentukan, cari Season pertama yang berisi > 0 sarikata
                if not selected_season_el:
                    console.print("[yellow]   ├─ Mencari Season aktif dengan jumlah sarikata > 0...[/yellow]")
                    for el in season_elements:
                        txt = el.inner_text().strip()
                        # Jangan pilih yang berisi '0 subtitles'
                        if "0 subtitles" not in txt.lower() and "subtitles" in txt.lower():
                            selected_season_el = el
                            chosen_season_label = txt.split("\n")[0]
                            break

                # Fallback: jika tetap tidak ada, ambil elemen pertama
                if not selected_season_el and season_elements:
                    selected_season_el = season_elements[0]
                    chosen_season_label = selected_season_el.inner_text().split("\n")[0]

                if selected_season_el:
                    console.print(f"[bold green]   └─ Memilih Musim:[/bold green] [white]{chosen_season_label}[/white]")
                    
                    # Cek apakah elemen memiliki href
                    season_href = selected_season_el.get_attribute("href")
                    if season_href:
                        page.goto(urljoin("https://subsource.net", season_href), wait_until="domcontentloaded", timeout=35000)
                    else:
                        selected_season_el.click(force=True)

                    page.wait_for_load_state("domcontentloaded")
                    human_delay(3.0, 4.5, "pemuatan tabel sarikata musim")
                    save_compressed_screenshot(page, f"season_table_{base_imdb}")

            else:
                console.print("[green]🎬 Terdeteksi sebagai Film Bioskop atau Serial dengan tabel langsung (tanpa menu musim).[/green]")

            # -------------------------------------------------------------
            # TAHAP 4: FILTER TABEL BAHASA (IN-PAGE SEARCH: MALAY & INDONESIA)
            # -------------------------------------------------------------
            console.print("\n[cyan]🔍 [4/5] Mengoperasikan kotak pencarian tabel 'Search subtitles...'[/cyan]")

            table_search = page.locator("input[placeholder*='Search subtitles'], input[placeholder*='subtitles']").first
            if not table_search.is_visible():
                console.print("[dim yellow]⚠️ Input pencarian tabel belum terlihat, memuat ulang halaman...[/dim yellow]")
                page.reload(wait_until="domcontentloaded")
                human_delay(2.5, 4.0, "selepas refresh")
                table_search = page.locator("input[placeholder*='Search subtitles'], input[placeholder*='subtitles']").first

            if not table_search.is_visible():
                console.print("[bold red]❌ Gagal mendeteksi kotak pencarian tabel sarikata![/bold red]")
                return

            seen_urls = set()

            # --- SIKLUS A: BAHASA MELAYU (MALAY) ---
            console.print("[yellow]   ├─ Menyaring: 'malay'...[/yellow]")
            table_search.click()
            table_search.fill("")
            table_search.press_sequentially("malay", delay=110)
            human_delay(2.5, 4.0, "menunggu filter Malay")

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
                    if full_u and full_u not in seen_urls:
                        seen_urls.add(full_u)
                        extracted_subtitles.append({"lang": "ms", "release": rel, "url": full_u})

            console.print(f"[green]   ├─ Ditemukan {len([s for s in extracted_subtitles if s['lang'] == 'ms'])} sarikata Melayu.[/green]")

            # --- SIKLUS B: BAHASA INDONESIA (INDONESIA) ---
            console.print("[yellow]   ├─ Menyaring: 'indonesia'...[/yellow]")
            table_search.click()
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            time.sleep(0.5)
            table_search.press_sequentially("indonesia", delay=110)
            human_delay(2.5, 4.0, "menunggu filter Indonesia")

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
                    if full_u and full_u not in seen_urls:
                        seen_urls.add(full_u)
                        extracted_subtitles.append({"lang": "id", "release": rel, "url": full_u})

            console.print(f"[green]   └─ Ditemukan {len([s for s in extracted_subtitles if s['lang'] == 'id'])} sarikata Indonesia.[/green]")

            # -------------------------------------------------------------
            # TAHAP 5: UJI PENGUNDUHAN BERKAS FISIK
            # -------------------------------------------------------------
            if extracted_subtitles:
                console.print(f"\n[bold magenta]📥 [5/5] Melakukan uji pengunduhan fisik (Maks: {max_downloads} berkas)...[/bold magenta]")
                test_targets = extracted_subtitles[:max_downloads]

                for idx, sub in enumerate(test_targets, 1):
                    console.print(f"[cyan]   [{idx}/{len(test_targets)}] Mengunduh:[/cyan] {sub['release'][:50]} ({sub['lang'].upper()})...")
                    dl_tab = context.new_page()
                    try:
                        dl_tab.goto(sub["url"], wait_until="domcontentloaded", timeout=35000)
                        human_delay(2.0, 3.5, "halaman rilis terbuka")

                        dl_btn = dl_tab.locator("button:has-text('Download'), a:has-text('Download'), button[class*='download']").first
                        if not dl_btn.is_visible():
                            dl_tab.reload(wait_until="domcontentloaded")
                            human_delay(2.0, 3.0, "refresh halaman unduh")
                            dl_btn = dl_tab.locator("button:has-text('Download'), a:has-text('Download'), button[class*='download']").first

                        if dl_btn.is_visible():
                            with dl_tab.expect_download(timeout=25000) as dl_info:
                                dl_btn.click(force=True)
                            dl = dl_info.value

                            dest_name = f"{sub['lang']}_{base_imdb}_{dl.suggested_filename}"
                            dest_path = DOWNLOADS_DIR / dest_name
                            dl.save_as(str(dest_path))
                            console.print(f"[bold green]      └─ Berhasil diunduh:[/bold green] {dest_name} ({dest_path.stat().st_size / 1024:.1f} KB)")
                            sub["file"] = dest_name
                        else:
                            console.print("[dim yellow]      └─ Tombol Download tidak terdeteksi.[/dim yellow]")
                    except Exception as err_dl:
                        console.print(f"[dim red]      └─ Gagal unduh #{idx}: {err_dl}[/dim red]")
                    finally:
                        dl_tab.close()
                    human_delay(2.0, 3.5, "jeda antar unduhan")

            save_compressed_html(page.content(), f"final_state_{base_imdb}")

        except Exception as err:
            console.print(f"[bold red]❌ Terjadi kendala: {err}[/bold red]")
            save_compressed_screenshot(page, f"crash_{base_imdb}")
        finally:
            page.close()
            context.close()

    # ==============================================================================
    # 4. LAPORAN HASIL PENGUJIAN
    # ==============================================================================
    total_ms = len([s for s in extracted_subtitles if s["lang"] == "ms"])
    total_id = len([s for s in extracted_subtitles if s["lang"] == "id"])

    summary_file = TEMP_DIR / f"smart_test_result_{base_imdb}.json"
    with open(summary_file, "w", encoding="utf-8") as jf:
        json.dump({
            "imdb_id": base_imdb,
            "total_found": len(extracted_subtitles),
            "malay": total_ms,
            "indonesian": total_id,
            "results": extracted_subtitles
        }, jf, indent=2, ensure_ascii=False)

    if extracted_subtitles:
        tbl = Table(title=f"🎉 Hasil Pengujian Subsource: {base_imdb}", border_style="green")
        tbl.add_column("No", justify="center", style="cyan", width=4)
        tbl.add_column("Bahasa", justify="center", style="magenta", width=8)
        tbl.add_column("Nama Versi / Rilis", style="white")
        tbl.add_column("Status Unduhan", justify="center", style="green")

        for i, s in enumerate(extracted_subtitles, 1):
            tbl.add_row(str(i), s["lang"].upper(), s["release"][:55], s.get("file", "Tercatat di Tabel"))

        console.print(tbl)
        console.print(Panel.fit(
            f"[bold green]✨ PENGUJIAN SERIAL & FILM BERHASIL DILAKUKAN![/bold green]\n"
            f"├─ Total Sarikata Terfilter : [yellow]{len(extracted_subtitles)} berkas[/yellow] ([magenta]MS: {total_ms}[/magenta] | [cyan]ID: {total_id}[/cyan])\n"
            f"├─ Ringkasan JSON Output    : [cyan]{summary_file.name}[/cyan]\n"
            f"└─ Berkas Fisik Terunduh    : [yellow]{DOWNLOADS_DIR}[/yellow]",
            border_style="green"
        ))
    else:
        console.print(f"[bold red]❌ Tidak ada sarikata ditemukan untuk {base_imdb}. Periksa screenshot di {TEMP_DIR}.[/bold red]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Subsource Smart Series & Movie Scraper")
    parser.add_argument("--imdb", default="tt1199099", help="IMDb ID sasaran (contoh: tt1199099 atau tt1199099:2:1)")
    parser.add_argument("--season", type=int, default=None, help="Pilihan musim tertentu jika serial")
    parser.add_argument("--downloads", type=int, default=3, help="Jumlah berkas yang diunduh fisik")
    args = parser.parse_args()

    run_smart_subsource_test(raw_imdb=args.imdb, target_season_opt=args.season, max_downloads=args.downloads)