import os
import sqlite3
import json
from pathlib import Path
from rich.console import Console

console = Console()

# ==============================================================================
# KONFIGURASI DIREKTORI & SASARAN SAIZ FAIL (20MB - 30MB)
# ==============================================================================
PROJECT_DIR = Path("/home/braderdin/stremio-sub-addon/ondemand_title_packs")
DATA_DIR = PROJECT_DIR / "data"
TEMP_DIR = PROJECT_DIR / "temp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# Cari semua fail katalog yang dijana oleh skrip sync (corak malay_subtitles_catalog_part_*.db)
CATALOG_DBS = sorted(list(DATA_DIR.glob("malay_subtitles_catalog_part_*.db")))

# Sasaran saiz setiap fail JSON output (anggaran ~22MB, selamat dalam julat 20MB - 30MB)
TARGET_FILE_SIZE_BYTES = 22 * 1024 * 1024  

def extract_and_split_nulls():
    all_nulls = []
    
    if not CATALOG_DBS:
        console.print(f"[bold red]❌ Tiada fail katalog dijumpai di {DATA_DIR} menggunakan corak malay_subtitles_catalog_part_*.db![/bold red]")
        return

    for db_path in CATALOG_DBS:
        console.print(f"📂 Sedang mengimbas fail katalog: [yellow]{db_path.name}[/yellow]")
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        try:
            # Sasarkan jadual 'subtitle_catalog' dan cari yang imdb_id nya NULL atau kosong
            cursor.execute("SELECT * FROM subtitle_catalog WHERE imdb_id IS NULL OR imdb_id = '';")
            rows = cursor.fetchall()
            
            for r in rows:
                row_dict = dict(r)
                row_dict['_source_db'] = db_path.name
                all_nulls.append(row_dict)
                
            console.print(f"   └─ Dijumpai [cyan]{len(rows):,}[/cyan] rekod IMDb NULL/gagal dalam {db_path.name}")
        except Exception as e:
            console.print(f"   [bold red]❌ Ralat membaca jadual dalam {db_path.name}: {e}[/bold red]")
        
        conn.close()

    console.print(f"\n📊 Jumlah keseluruhan rekod IMDb NULL / gagal ditemui: [bold green]{len(all_nulls):,}[/bold green]")
    
    if not all_nulls:
        console.print("[yellow]⚠️ Tiada rekod NULL dijumpai. Semua sarikata telah mempunyai IMDb ID![/yellow]")
        return

    # Proses pemecahan fail (splitting) mengikut saiz sasaran (20MB - 30MB)
    file_index = 1
    current_batch = []
    
    output_path = TEMP_DIR / f"null_subtitles_part_{file_index:03d}.json"
    
    for item in all_nulls:
        current_batch.append(item)
        
        # Semak saiz setiap 1000 rekod bagi mengelakkan beban memori
        if len(current_batch) >= 1000:
            partial_json = json.dumps(current_batch, ensure_ascii=False)
            current_size_bytes = len(partial_json.encode('utf-8'))
            
            if current_size_bytes >= TARGET_FILE_SIZE_BYTES:
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(partial_json)
                sz_mb = output_path.stat().st_size / (1024 * 1024)
                console.print(f"💾 [green]Disimpan:[/green] {output_path.name} ([magenta]{sz_mb:.2f} MB[/magenta])")
                
                # Sediakan fail bahagian seterusnya
                file_index += 1
                output_path = TEMP_DIR / f"null_subtitles_part_{file_index:03d}.json"
                current_batch = []

    # Simpan baki rekod terakhir yang belum ditulis
    if current_batch:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(current_batch, f, ensure_ascii=False, indent=2)
        if output_path.exists() and output_path.stat().st_size > 0:
            sz_mb = output_path.stat().st_size / (1024 * 1024)
            console.print(f"💾 [green]Disimpan (Bahagian Akhir):[/green] {output_path.name} ([magenta]{sz_mb:.2f} MB[/magenta])")

    console.print(f"\n[bold green]✨ Proses ekstrak & split selesai! Fail JSON disimpan di {TEMP_DIR}[/bold green]")

if __name__ == "__main__":
    extract_and_split_nulls()