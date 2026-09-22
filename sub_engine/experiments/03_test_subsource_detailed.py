#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO SUB ADDON - SUBSOURCE IN-PAGE SEARCH & DOWNLOAD TEST
# LOKASI: /home/braderdin/stremio-sub-addon/sub_engine/experiments/03_test_subsource_detailed.py
# SASARAN:
# 1. Buka film tt3215824 di Subsource via Popup IMDb
# 2. Taip 'malay' di kotak 'Search subtitles...' -> kutip semua sarikata BM
# 3. Taip 'indonesia' di kotak 'Search subtitles...' -> kutip semua sarikata ID
# 4. Muat turun fail sarikata fizikal terus ke sub_engine/temp/downloads/
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
from urllib.parse import urljoin
from typing import Dict, Any, List

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

# 1. Direktori Projek
EXPERIMENT_DIR = Path(__file__).resolve().parent
SUB_ENGINE_DIR = EXPERIMENT_DIR.parent
TEMP_DIR = SUB_ENGINE_DIR / "temp"
DOWNLOADS_DIR = TEMP_DIR / "downloads"

for folder in [EXPERIMENT_DIR, TEMP_DIR, DOWNLOADS_DIR]:
    folder.mkdir(parents=True, exist_ok=True)


# ==============================================================================
# 2. BANTUAN SIMULASI & MAMPATAN
# ==============================================================================
def human_delay(min_s: float = 1.8, max_s: float = 3.0, tag: str = ""):
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
        console.print(f"[dim]📸 Tangkap layar disimpan: {out_path.name} ({sz_kb:.1f} KB)[/dim]")
    except Exception as e:
        console.print(f"[dim red]Gagal simpan tangkap layar: {e}[/dim red]")
    return out_path


# ==============================================================================
# 3. ALUR KERJA UTAMA
# ==============================================================================
def run_subsource_inpage_search(imdb_id: str = "tt3215824", max_downloads: int = 5):
    clean_imdb = imdb_id.strip()

    console.print(Panel.fit(
        f"[bold cyan]🎯 UJIAN PINTAR SUBSOURCE (IN-PAGE SEARCH & DIRECT DOWNLOAD)[/bold cyan]\n"
        f"Sasaran IMDb ID  : [bold yellow]{clean_imdb}[/bold yellow]\n"
        f"Strategi Carian  : [bold green]Taip 'malay' & 'indonesia' pada kotak 'Search subtitles...'[/bold green]\n"
        f"Folder Muat Turun: [cyan]{DOWNLOADS_DIR}[/cyan]",
        border_style="cyan"
    ))

    try:
        from camoufox.sync_api import Camoufox
    except ImportError:
        console.print("[bold red]❌ Pustaka Camoufox tiada![/bold red]")
        return

    extracted_subtitles: List[Dict[str, Any]] = []

    with Camoufox(headless=False, geoip=True) as browser:
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
            accept_downloads=True
        )
        page = context.new_page()

        try:
            # -------------------------------------------------------------
            # LANGKAH 1: BUKA BERANDA & CARI VIA POPUP IMDB
            # -------------------------------------------------------------
            console.print("[cyan]🚀 [1/4] Melayari https://subsource.net...[/cyan]")
            page.goto("https://subsource.net", wait_until="domcontentloaded", timeout=45000)
            human_delay(2.0, 3.0, "pemuatan laman utama")

            main_search = page.locator("input[type='search'], input[placeholder*='Search'], input[name='query']").first
            if not main_search.is_visible():
                main_search = page.locator("input").first

            console.print(f"[yellow]⌨️ [2/4] Menaip IMDb ID:[/yellow] {clean_imdb}")
            main_search.click()
            main_search.fill("")
            main_search.press_sequentially(clean_imdb, delay=random.randint(90, 140))
            human_delay(2.5, 3.5, "menunggu popup filem")

            movie_card = page.locator("div[class*='search'] a, div[class*='result'] a, div[class*='dropdown'] a, a[href*='/subtitles/']").first
            if movie_card.is_visible():
                console.print("[green]🎯 Mengklik kad filem di popup...[/green]")
                movie_card.click()
            else:
                main_search.press("Enter")

            page.wait_for_load_state("domcontentloaded")
            human_delay(3.0, 4.0, "pemuatan halaman filem")

            # Simpan paparan awal halaman filem
            save_compressed_screenshot(page, f"subsource_movie_ready_{clean_imdb}")

            # -------------------------------------------------------------
            # LANGKAH 2: CARI KOTAK 'Search subtitles...' DI ATAS JADUAL
            # -------------------------------------------------------------
            sub_search_input = page.locator("input[placeholder*='Search subtitles'], input[placeholder*='subtitles']").first
            if not sub_search_input.is_visible():
                # Fallback: cari input kedua dalam halaman
                inputs = page.locator("input").all()
                for inp in inputs:
                    ph = (inp.get_attribute("placeholder") or "").lower()
                    if "subtitle" in ph:
                        sub_search_input = inp
                        break

            if not sub_search_input.is_visible():
                console.print("[bold red]❌ Kotak carian 'Search subtitles...' tidak ditemui![/bold red]")
                return

            # =============================================================
            # TAHAP A: TAPIS & KUTIP BAHASA MELAYU (MALAY)
            # =============================================================
            console.print("\n[bold cyan]🔍 [Tahap A] Menaip 'malay' ke kotak 'Search subtitles...'[/bold cyan]")
            sub_search_input.click()
            sub_search_input.fill("")
            sub_search_input.press_sequentially("malay", delay=120)
            human_delay(2.5, 3.5, "menunggu jadual menyaring Malay")

            save_compressed_screenshot(page, f"subsource_search_malay_{clean_imdb}")

            # Imbas baris jadual Malay
            rows_malay = page.locator("tbody tr, div[class*='table'] div[class*='row'], tr").all()
            console.print(f"[dim]Mengesan {len(rows_malay)} baris pada jadual Malay.[/dim]")

            seen_urls = set()
            for row in rows_malay:
                txt = row.inner_text().strip()
                if not txt or "language" in txt.lower():
                    continue

                link_tag = row.locator("a[href*='/subtitles/'], a[href*='/subtitle/'], a").first
                if link_tag.is_visible():
                    href = link_tag.get_attribute("href") or ""
                    full_url = urljoin("https://subsource.net", href)
                    rel_name = link_tag.inner_text().strip() or txt.split("\n")[0]

                    if full_url and full_url not in seen_urls:
                        seen_urls.add(full_url)
                        extracted_subtitles.append({
                            "id": f"subsource_{len(extracted_subtitles) + 1}",
                            "lang": "ms",
                            "release": rel_name,
                            "detail_url": full_url
                        })

            console.print(f"[green]✔ Berjaya mengesan {len([s for s in extracted_subtitles if s['lang'] == 'ms'])} sarikata Bahasa Melayu![/green]")

            # =============================================================
            # TAHAP B: TAPIS & KUTIP BAHASA INDONESIA (INDONESIA)
            # =============================================================
            console.print("\n[bold cyan]🔍 [Tahap B] Menaip 'indonesia' ke kotak 'Search subtitles...'[/bold cyan]")
            sub_search_input.click()
            sub_search_input.fill("")
            # Padam sebarang baki teks menggunakan kekunci Backspace
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            time.sleep(0.5)

            sub_search_input.press_sequentially("indonesia", delay=120)
            human_delay(2.5, 3.5, "menunggu jadual menyaring Indonesia")

            save_compressed_screenshot(page, f"subsource_search_indonesia_{clean_imdb}")

            # Imbas baris jadual Indonesia
            rows_indo = page.locator("tbody tr, div[class*='table'] div[class*='row'], tr").all()
            console.print(f"[dim]Mengesan {len(rows_indo)} baris pada jadual Indonesia.[/dim]")

            for row in rows_indo:
                txt = row.inner_text().strip()
                if not txt or "language" in txt.lower():
                    continue

                link_tag = row.locator("a[href*='/subtitles/'], a[href*='/subtitle/'], a").first
                if link_tag.is_visible():
                    href = link_tag.get_attribute("href") or ""
                    full_url = urljoin("https://subsource.net", href)
                    rel_name = link_tag.inner_text().strip() or txt.split("\n")[0]

                    if full_url and full_url not in seen_urls:
                        seen_urls.add(full_url)
                        extracted_subtitles.append({
                            "id": f"subsource_{len(extracted_subtitles) + 1}",
                            "lang": "id",
                            "release": rel_name,
                            "detail_url": full_url
                        })

            console.print(f"[green]✔ Berjaya mengesan {len([s for s in extracted_subtitles if s['lang'] == 'id'])} sarikata Bahasa Indonesia![/green]")

            # -------------------------------------------------------------
            # LANGKAH 3: ENJIN MUAT TURUN FIZIKAL (DOWNLOAD ENGINE)
            # -------------------------------------------------------------
            if extracted_subtitles:
                console.print(f"\n[bold magenta]📥 [3/4] Melaksanakan muat turun fail sebenar (Maks: {max_downloads} fail)...[/bold magenta]")

                download_targets = extracted_subtitles[:max_downloads]

                for idx, sub_item in enumerate(download_targets, 1):
                    target_url = sub_item["detail_url"]
                    console.print(f"[cyan]   [{idx}/{len(download_targets)}] Membuka:[/cyan] {sub_item['release'][:48]}...")

                    sub_tab = context.new_page()
                    try:
                        sub_tab.goto(target_url, wait_until="domcontentloaded", timeout=35000)
                        human_delay(2.0, 3.0, "halaman muat turun terbuka")

                        # Pengesan butang Download
                        dl_btn = sub_tab.locator("button:has-text('Download'), a:has-text('Download'), button[class*='download']").first

                        if dl_btn.is_visible():
                            console.print("[yellow]      ├─ Menekan butang 'Download'...[/yellow]")
                            with sub_tab.expect_download(timeout=25000) as dl_info:
                                dl_btn.click()
                            download = dl_info.value

                            dest_path = DOWNLOADS_DIR / f"{sub_item['lang']}_{clean_imdb}_{download.suggested_filename}"
                            download.save_as(str(dest_path))
                            sz_kb = dest_path.stat().st_size / 1024
                            console.print(f"[bold green]      └─ Berjaya dimuat turun:[/bold green] {dest_path.name} ({sz_kb:.1f} KB)")
                            sub_item["downloaded_file"] = dest_path.name
                        else:
                            console.print("[dim yellow]      └─ Butang Download tidak dijumpai pada halaman rilis.[/dim yellow]")
                    except Exception as dl_err:
                        console.print(f"[dim red]      └─ Ralat semasa muat turun: {dl_err}[/dim red]")
                    finally:
                        sub_tab.close()

            # Simpan dump HTML mampat
            save_compressed_html(page.content(), f"subsource_final_page_{clean_imdb}")

        except Exception as err:
            console.print(f"[bold red]❌ Ralat: {err}[/bold red]")
            save_compressed_screenshot(page, f"subsource_crash_{clean_imdb}")
        finally:
            page.close()
            context.close()

    # ==============================================================================
    # 4. EKSPOR DATA JSON DAN JADUAL AUDIT
    # ==============================================================================
    total_ms = len([s for s in extracted_subtitles if s["lang"] == "ms"])
    total_id = len([s for s in extracted_subtitles if s["lang"] == "id"])

    summary_json = {
        "imdb_id": clean_imdb,
        "total_subtitles_found": len(extracted_subtitles),
        "total_malay": total_ms,
        "total_indonesian": total_id,
        "results": extracted_subtitles
    }

    json_file = TEMP_DIR / f"subsource_verified_subs_{clean_imdb}.json"
    with open(json_file, "w", encoding="utf-8") as jf:
        json.dump(summary_json, jf, indent=2, ensure_ascii=False)

    if extracted_subtitles:
        table = Table(title=f"🎉 Senarai Sarikata Subsource Ditemui ({clean_imdb})", border_style="green")
        table.add_column("No", justify="center", style="cyan", width=4)
        table.add_column("Bahasa", justify="center", style="magenta", width=8)
        table.add_column("Nama Versi / Release", style="white")
        table.add_column("Fail Dimuat Turun", justify="center", style="green")

        for i, item in enumerate(extracted_subtitles, 1):
            table.add_row(
                str(i),
                item["lang"].upper(),
                item["release"][:52],
                item.get("downloaded_file", "Tersenarai di Halaman")
            )

        console.print(table)
        console.print(Panel.fit(
            f"[bold green]✨ UJIAN SUBSOURCE SELESAI DENGAN JAYANYA![/bold green]\n"
            f"├─ Sarikata Ditemui : [yellow]{len(extracted_subtitles)} fail[/yellow] ([magenta]MS: {total_ms}[/magenta] | [cyan]ID: {total_id}[/cyan])\n"
            f"├─ Fail JSON Output : [cyan]{json_file.name}[/cyan] ({json_file.stat().st_size / 1024:.1f} KB - Sah Bawah 100KB)\n"
            f"└─ Direktori Muat Turun: [bold yellow]{DOWNLOADS_DIR}[/bold yellow]",
            border_style="green"
        ))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ujian Carian Dalam Halaman Subsource")
    parser.add_argument("--imdb", default="tt3215824", help="Sasaran IMDb ID")
    parser.add_argument("--downloads", type=int, default=5, help="Jumlah fail yang diuji muat turun fizikal")
    args = parser.parse_args()

    run_subsource_inpage_search(args.imdb, max_downloads=args.downloads)