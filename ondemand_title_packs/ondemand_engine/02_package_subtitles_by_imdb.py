#!/usr/bin/env python3
# ==============================================================================
# PROJEK: STREMIO ONDEMAND SUBTITLE PACKAGER - V2.3 (ZERO-DUPLICATE & SMART MERGE)
# LOKASI: /home/braderdin/stremio-sub-addon/ondemand_title_packs/ondemand_engine/02_package_subtitles_by_imdb.py
# ==============================================================================

import os
import re
import sys
import io
import time
import zipfile
import sqlite3
import hashlib
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List, Set
from concurrent.futures import ThreadPoolExecutor, as_completed

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

console = Console()

# ==============================================================================
# KONFIGURASI DIREKTORI & PARAMETER
# ==============================================================================
PROJECT_DIR = Path("/home/braderdin/stremio-sub-addon/ondemand_title_packs")
DATA_DIR = PROJECT_DIR / "data"
TEMP_DIR = PROJECT_DIR / "temp"

OUTPUT_ZIP_DIR = PROJECT_DIR / "subtitles_by_title"
TRACKER_DB_PATH = DATA_DIR / "malay_packaged_tracker.db"
STAGING_DIR = TEMP_DIR / "staging_malay"

SOURCE_ARCHIVE_PATH = Path("/mnt/e/PROJEK-GITHUB/SUBTITLE--SUBSCENE-ARCHIVE/malay.7z")

CATALOG_DBS = [
    DATA_DIR / "malay_subtitles_catalog_part_01.db",
    DATA_DIR / "malay_subtitles_catalog_part_02.db"
]

for p in [DATA_DIR, TEMP_DIR, OUTPUT_ZIP_DIR]:
    p.mkdir(parents=True, exist_ok=True)

WORKER_THREADS = 12
MAX_FILENAME_LENGTH = 180
VALID_SUB_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".smi"}

def is_valid_sub(filename: str) -> bool:
    fn_lower = filename.lower()
    return (
        any(fn_lower.endswith(ext) for ext in VALID_SUB_EXTENSIONS)
        and not fn_lower.startswith("__macosx")
        and ".ds_store" not in fn_lower
    )

# ==============================================================================
# PEMBERSIHAN NAMA FAIL SARIKATA & PENYELESAIAN DUPLIKASI
# ==============================================================================
def sanitize_sub_filename(fn: str) -> Tuple[str, str]:
    """Membersihkan nama fail sarikata kepada format selamat tanpa ruang kosong/simbol pelik."""
    p = Path(fn)
    ext = p.suffix.lower() if p.suffix else ".srt"
    if ext not in VALID_SUB_EXTENSIONS:
        ext = ".srt"
    stem = p.stem
    # Gantikan semua selain abjad, angka, sempang, dan garis bawah kepada titik
    clean_stem = re.sub(r"[^\w\d\-_]", ".", stem)
    clean_stem = re.sub(r"\.+", ".", clean_stem).strip(".")
    if not clean_stem:
        clean_stem = "subtitle"
    return clean_stem, ext

def make_unique_entry_name(base_stem: str, ext: str, existing_names: Set[str], sub_id: Optional[str] = None) -> str:
    """Memastikan nama fail di dalam arkib ZIP 100% unik tanpa duplikasi."""
    candidate = f"{base_stem}{ext}"
    if candidate not in existing_names:
        return candidate

    # Jika bertindih dan ada sub_id unik dari Subscene
    if sub_id:
        safe_sub_id = re.sub(r"[^\w\d]", "", str(sub_id))
        candidate_with_id = f"{base_stem}.{safe_sub_id}{ext}"
        if candidate_with_id not in existing_names:
            return candidate_with_id

    # Jika masih bertindih, gunakan penomboran berurutan _02, _03, ...
    idx = 2
    while True:
        candidate_seq = f"{base_stem}_{idx:02d}{ext}"
        if candidate_seq not in existing_names:
            return candidate_seq
        idx += 1

# ==============================================================================
# PENGESANAN PINTAR LALUAN FOLDER STAGING
# ==============================================================================
def resolve_actual_staging_root(staging_path: Path) -> Path:
    p_exact = staging_path / "malay" / "malay subtitles"
    if p_exact.exists() and p_exact.is_dir():
        return p_exact

    p_sub = staging_path / "malay subtitles"
    if p_sub.exists() and p_sub.is_dir():
        return p_sub

    p_m = staging_path / "malay"
    if p_m.exists() and p_m.is_dir():
        for sub in p_m.iterdir():
            if sub.is_dir() and "subtitle" in sub.name.lower():
                return sub
        return p_m

    for root, dirs, _ in os.walk(staging_path):
        if len(dirs) > 500:
            return Path(root)

    return staging_path

# ==============================================================================
# INISIALISASI PANGKALAN DATA PENJEJAK DENGAN MIGRASI SHA-256
# ==============================================================================
def init_tracker_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(TRACKER_DB_PATH))
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS malay_packaged_tracker (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            imdb_id TEXT UNIQUE NOT NULL,
            zip_filename TEXT NOT NULL,
            canonical_title TEXT NOT NULL,
            release_year TEXT,
            media_type TEXT NOT NULL,
            total_subs_count INTEGER NOT NULL,
            zip_size_bytes INTEGER NOT NULL,
            zip_hash TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur = conn.cursor()
    cur.execute("PRAGMA table_info(malay_packaged_tracker);")
    cols = [col[1] for col in cur.fetchall()]
    if "zip_hash" not in cols:
        cur.execute("ALTER TABLE malay_packaged_tracker ADD COLUMN zip_hash TEXT;")
        conn.commit()

    conn.execute("CREATE INDEX IF NOT EXISTS idx_trk_imdb ON malay_packaged_tracker (imdb_id);")
    conn.commit()
    return conn

# ==============================================================================
# FORMAT NAMA FAIL ZIP (BERSIH TANPA RUANG KOSONG)
# ==============================================================================
def sanitize_title_for_filename(title: str, fallback_slug: str) -> str:
    if not title:
        title = fallback_slug

    ascii_clean = re.sub(r"[^\x00-\x7F]+", "", title).strip()
    if len(ascii_clean) < 2 and fallback_slug:
        title = fallback_slug

    cleaned = re.sub(r"[^\w\d]", ".", title)
    cleaned = re.sub(r"\.+", ".", cleaned).strip(".")
    return cleaned

def generate_zip_name(imdb_id: str, title: str, year: Optional[str], slug: str) -> str:
    clean_t = sanitize_title_for_filename(title, slug)
    parts = ["ms", imdb_id, clean_t]
    if year and str(year).strip().isdigit():
        parts.append(str(year).strip()[:4])

    raw_name = ".".join([p for p in parts if p])
    if len(raw_name) > MAX_FILENAME_LENGTH:
        raw_name = raw_name[:MAX_FILENAME_LENGTH].rstrip(".")

    return f"{raw_name}.zip"

# ==============================================================================
# FASA 1: SEMAKAN STAGING (ELAK EKSTRAK BERULANG)
# ==============================================================================
def ensure_staging_ready() -> Path:
    actual_root = resolve_actual_staging_root(STAGING_DIR)

    if actual_root.exists() and any(actual_root.iterdir()):
        count_dirs = sum(1 for d in actual_root.iterdir() if d.is_dir())
        console.print(f"   [green]✔ Folder staging sah dikesan:[/green] [yellow]{actual_root}[/yellow]")
        console.print(f"   [green]✔ Jumlah folder judul (slug) tersedia:[/green] [cyan]{count_dirs:,}[/cyan]\n")
        return actual_root

    if not SOURCE_ARCHIVE_PATH.exists():
        console.print(f"[bold red]❌ Ralat: Fail arkib sumber tidak dijumpai di:[/bold red] {SOURCE_ARCHIVE_PATH}")
        sys.exit(1)

    console.print(Panel.fit(
        f"[bold cyan]⚡ FASA 1: Mengekstrak malay.7z ke Staging Tempatan WSL[/bold cyan]\n"
        f"[white]Fail Sumber:[/white] [yellow]{SOURCE_ARCHIVE_PATH}[/yellow]\n"
        f"[white]Destinasi Staging:[/white] [green]{STAGING_DIR}[/green]",
        border_style="cyan"
    ))

    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    cmd = ["7z", "x", "-y", f"-o{STAGING_DIR}", str(SOURCE_ARCHIVE_PATH), "-bso0", "-bsp1"]
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        console.print(f"[bold red]❌ Gagal mengekstrak {SOURCE_ARCHIVE_PATH.name}![/bold red]")
        sys.exit(1)

    actual_root = resolve_actual_staging_root(STAGING_DIR)
    console.print(f"   [bold green]✔ Selesai ekstrak ke:[/bold green] [yellow]{actual_root}[/yellow]\n")
    return actual_root

# ==============================================================================
# FASA 2: EKSTRAKSI SARIKATA DARI DALAM PAKEJ SUMBER
# ==============================================================================
def find_source_file_path(slug_root: Path, slug: str, pkg_name: Optional[str], sub_fn: str) -> Optional[Path]:
    slug_dir = slug_root / slug
    if not slug_dir.exists() or not slug_dir.is_dir():
        return None

    if pkg_name:
        p = slug_dir / pkg_name
        if p.exists() and p.is_file():
            return p

    if sub_fn:
        p = slug_dir / sub_fn
        if p.exists() and p.is_file():
            return p

    target_names = {x.lower() for x in [pkg_name, sub_fn] if x}
    try:
        for f in slug_dir.iterdir():
            if f.is_file() and f.name.lower() in target_names:
                return f
    except Exception:
        pass

    return None

def extract_raw_subtitle_bytes(pkg_path: Path, internal_path: str) -> Optional[bytes]:
    ext = pkg_path.suffix.lower()

    if ext in VALID_SUB_EXTENSIONS:
        try:
            return pkg_path.read_bytes()
        except Exception:
            return None

    if ext == ".zip":
        try:
            with zipfile.ZipFile(pkg_path, "r") as zf:
                target_name = internal_path.replace("\\", "/")
                names = zf.namelist()
                if target_name in names:
                    return zf.read(target_name)
                base_name = Path(target_name).name.lower()
                for n in names:
                    if Path(n).name.lower() == base_name:
                        return zf.read(n)
                for n in names:
                    if not n.startswith("__MACOSX") and any(n.lower().endswith(sub_ext) for sub_ext in VALID_SUB_EXTENSIONS):
                        return zf.read(n)
        except Exception:
            pass

    if ext == ".rar":
        try:
            import rarfile
            with rarfile.RarFile(pkg_path, "r") as rf:
                target_name = internal_path.replace("\\", "/")
                names = rf.namelist()
                if target_name in names:
                    return rf.read(target_name)
                base_name = Path(target_name).name.lower()
                for n in names:
                    if Path(n).name.lower() == base_name:
                        return rf.read(n)
        except Exception:
            pass

    try:
        base_fn = Path(internal_path).name
        cmd = ["7z", "e", "-so", "-y", str(pkg_path), base_fn]
        res = subprocess.run(cmd, capture_output=True, timeout=10)
        if res.returncode == 0 and len(res.stdout) > 0:
            return res.stdout
    except Exception:
        pass

    return None

# ==============================================================================
# FASA 3: PEKERJA PEMBUNGKUSAN PINTAR (SMART MERGE, ZERO-DUPLICATE & ATOMIC WRITE)
# ==============================================================================
def package_single_imdb_worker(group_data: Dict[str, Any], slug_root: Path) -> Optional[Tuple[str, str, str, str, str, int, int, str]]:
    imdb_id = group_data["imdb_id"]
    canonical_title = group_data["canonical_title"]
    release_year = group_data["release_year"]
    media_type = group_data["media_type"]
    slug = group_data["slug"]
    records = group_data["records"]

    zip_fn = generate_zip_name(imdb_id, canonical_title, release_year, slug)
    target_zip_path = OUTPUT_ZIP_DIR / zip_fn
    temp_zip_path = OUTPUT_ZIP_DIR / f"{target_zip_path.stem}.{os.getpid()}.{int(time.time() * 1000)}.tmp"

    collected_subs: Dict[str, bytes] = {}
    seen_hashes: Set[str] = set()

    def register_sub(raw_fn: str, data: bytes, sub_id: Optional[str] = None):
        """Mendaftarkan sari kata dengan sanitasi awal, tapisan MD5, dan nama unik mutlak."""
        if not data or len(data) < 10:
            return

        h = hashlib.md5(data).hexdigest()
        # Jika kandungan sama persis sudah wujud, abaikan untuk jimat storan
        if h in seen_hashes:
            return
        seen_hashes.add(h)

        stem, ext = sanitize_sub_filename(raw_fn)
        final_name = make_unique_entry_name(stem, ext, set(collected_subs.keys()), sub_id)
        collected_subs[final_name] = data

    # 1. BUKA & EKSTRAK FAIL .ZIP SEDIA ADA (JIKA WUJUD) UNTUK DIGABUNGKAN
    if target_zip_path.exists():
        try:
            with zipfile.ZipFile(target_zip_path, "r") as z_in:
                for item in z_in.infolist():
                    if item.is_dir():
                        continue
                    name_lower = item.filename.lower()
                    if "__macosx" in name_lower or ".ds_store" in name_lower:
                        continue

                    raw_data = z_in.read(item.filename)
                    if len(raw_data) < 10:
                        continue

                    # BONGKAR JIKA SEBELUM INI ADA ZIP DI DALAM ZIP
                    if name_lower.endswith(".zip") or raw_data.startswith(b"PK\x03\x04"):
                        try:
                            with zipfile.ZipFile(io.BytesIO(raw_data), "r") as inner_z:
                                for inner_item in inner_z.infolist():
                                    if inner_item.is_dir():
                                        continue
                                    if is_valid_sub(inner_item.filename):
                                        inner_bytes = inner_z.read(inner_item.filename)
                                        register_sub(Path(inner_item.filename).name, inner_bytes)
                        except Exception:
                            pass
                    elif is_valid_sub(item.filename):
                        register_sub(Path(item.filename).name, raw_data)
        except Exception:
            collected_subs.clear()
            seen_hashes.clear()

    # 2. EKSTRAK SARIKATA BAHARU DARIPADA KATALOG & GABUNGKAN
    for rec in records:
        pkg_name = rec["package_name"]
        sub_fn = rec["sub_filename"]
        internal_path = rec["internal_sub_path"]
        sub_id = rec["subscene_id"] or "sub"

        pkg_p = find_source_file_path(slug_root, rec["slug"], pkg_name, sub_fn)
        if not pkg_p:
            continue

        sub_bytes = extract_raw_subtitle_bytes(pkg_p, internal_path)
        if not sub_bytes or len(sub_bytes) < 10:
            continue

        # SIFAR ZIP BERSARANG: BONGKAR JIKA SUMBER ADALAH ZIP
        if sub_bytes.startswith(b"PK\x03\x04") or (sub_fn and sub_fn.lower().endswith(".zip")):
            try:
                with zipfile.ZipFile(io.BytesIO(sub_bytes), "r") as inner_z:
                    for inner_item in inner_z.infolist():
                        if inner_item.is_dir():
                            continue
                        if is_valid_sub(inner_item.filename):
                            inner_bytes = inner_z.read(inner_item.filename)
                            register_sub(Path(inner_item.filename).name, inner_bytes, sub_id)
            except Exception:
                pass
            continue

        # FAIL SARIKATA TEKS BIASA
        register_sub(Path(sub_fn).name, sub_bytes, sub_id)

    if not collected_subs:
        if target_zip_path.exists():
            try:
                target_zip_path.unlink()
            except Exception:
                pass
        return None

    # 3. PENULISAN ATOMIK KE FAIL .TMP LALU MENGGANTIKAN FAIL ASAL
    try:
        with zipfile.ZipFile(temp_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z_out:
            for entry_name, s_bytes in collected_subs.items():
                z_out.writestr(entry_name, s_bytes)

        # Semak integriti fail zip sebelum menimpa fail sasaran
        with zipfile.ZipFile(temp_zip_path, "r") as z_test:
            if z_test.testzip() is not None:
                raise zipfile.BadZipFile("Ujian integriti fail zip gagal!")

        temp_zip_path.replace(target_zip_path)

        final_bytes = target_zip_path.read_bytes()
        sz_bytes = len(final_bytes)
        zip_sha256 = hashlib.sha256(final_bytes).hexdigest()
        total_packed = len(collected_subs)

        return (
            imdb_id, zip_fn, canonical_title,
            release_year or "", media_type, total_packed, sz_bytes, zip_sha256
        )

    except Exception:
        if temp_zip_path.exists():
            try:
                temp_zip_path.unlink()
            except Exception:
                pass
        return None

# ==============================================================================
# ALUR KERJA UTAMA
# ==============================================================================
def main():
    console.print("\n" + "=" * 80)
    console.print("📦 [bold green]ENJIN PENGELOMPOKKAN SARIKATA MENGIKUT IMDB ID (V2.3 - ZERO-DUPLICATE & SMART MERGE)[/bold green]")
    console.print("=" * 80 + "\n")

    slug_root = ensure_staging_ready()

    tracker_conn = init_tracker_db()
    tracker_cur = tracker_conn.cursor()

    tracker_cur.execute("SELECT imdb_id, total_subs_count, zip_hash, zip_filename FROM malay_packaged_tracker;")
    existing_tracker: Dict[str, Dict[str, Any]] = {
        r[0]: {"total_subs": r[1], "hash": r[2], "zip_filename": r[3]}
        for r in tracker_cur.fetchall()
    }

    console.print(f"📋 Rekod tracker sedia ada: [green]{len(existing_tracker):,}[/green] tajuk.")

    console.print("🔍 [cyan]Mengumpulkan entri sarikata sah daripada katalog...[/cyan]")
    imdb_groups: Dict[str, Dict[str, Any]] = {}

    for cat_db in CATALOG_DBS:
        if not cat_db.exists():
            console.print(f"[bold red]❌ Ralat: Fail katalog {cat_db.name} tidak wujud![/bold red]")
            sys.exit(1)

        conn = sqlite3.connect(str(cat_db))
        cur = conn.cursor()
        cur.execute("""
            SELECT imdb_id, canonical_title, release_year, media_type, slug,
                   package_name, sub_filename, sub_extension, internal_sub_path, subscene_id
            FROM subtitle_catalog
            WHERE imdb_id IS NOT NULL AND imdb_id != '';
        """)

        for row in cur.fetchall():
            imdb_id, title, year, m_type, slug, pkg, sub_fn, sub_ext, int_p, sub_id = row

            if imdb_id not in imdb_groups:
                imdb_groups[imdb_id] = {
                    "imdb_id": imdb_id,
                    "canonical_title": title,
                    "release_year": year,
                    "media_type": m_type,
                    "slug": slug,
                    "records": []
                }

            imdb_groups[imdb_id]["records"].append({
                "slug": slug,
                "package_name": pkg,
                "sub_filename": sub_fn,
                "sub_extension": sub_ext,
                "internal_sub_path": int_p,
                "subscene_id": sub_id
            })
        conn.close()

    total_valid_groups = len(imdb_groups)
    console.print(f"   [green]✔ Ditemui [yellow]{total_valid_groups:,}[/yellow] tajuk IMDb unik untuk dipakej.[/green]")

    # PENAPISAN PINTAR: Semak jika bilangan fail dalam katalog meningkat selepas baiki NULL
    groups_to_process = []
    for imdb, g_data in imdb_groups.items():
        current_catalog_subs = len(g_data["records"])
        expected_zip = generate_zip_name(imdb, g_data["canonical_title"], g_data["release_year"], g_data["slug"])
        zip_path = OUTPUT_ZIP_DIR / expected_zip
        zip_exists = zip_path.exists()

        if imdb in existing_tracker and zip_exists:
            tracked_info = existing_tracker[imdb]
            # Langkau hanya jika jumlah fail sama dan hash SHA-256 sudah direkodkan
            if current_catalog_subs == tracked_info["total_subs"] and tracked_info["hash"]:
                continue

        groups_to_process.append(g_data)

    console.print(f"   [yellow]⚡ Judul yang perlu dipakej / dikemas kini: {len(groups_to_process):,} tajuk.[/yellow]\n")

    if not groups_to_process:
        console.print("[bold green]✨ Semua tajuk dengan IMDb sah telah siap dipakej sepenuhnya![/bold green]\n")
        tracker_conn.close()
        return

    success_count = 0
    total_subs_packed = 0
    batch_tracker = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[bold yellow]{task.completed}/{task.total} Tajuk"),
        TimeElapsedColumn(),
        console=console
    ) as progress:
        task = progress.add_task("Membungkus & mengemas kini fail ZIP mengikut IMDb...", total=len(groups_to_process))

        with ThreadPoolExecutor(max_workers=WORKER_THREADS) as executor:
            future_to_group = {
                executor.submit(package_single_imdb_worker, g, slug_root): g
                for g in groups_to_process
            }

            for future in as_completed(future_to_group):
                res = future.result()
                if res:
                    imdb_id, zip_fn, title, year, m_type, count_subs, sz_bytes, z_hash = res
                    batch_tracker.append((imdb_id, zip_fn, title, year, m_type, count_subs, sz_bytes, z_hash))
                    success_count += 1
                    total_subs_packed += count_subs

                    if len(batch_tracker) >= 100:
                        tracker_cur.executemany("""
                            INSERT INTO malay_packaged_tracker 
                            (imdb_id, zip_filename, canonical_title, release_year, media_type, total_subs_count, zip_size_bytes, zip_hash)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(imdb_id) DO UPDATE SET
                                zip_filename = excluded.zip_filename,
                                canonical_title = excluded.canonical_title,
                                release_year = excluded.release_year,
                                media_type = excluded.media_type,
                                total_subs_count = excluded.total_subs_count,
                                zip_size_bytes = excluded.zip_size_bytes,
                                zip_hash = excluded.zip_hash;
                        """, batch_tracker)
                        tracker_conn.commit()
                        batch_tracker.clear()

                progress.update(task, advance=1)

        if batch_tracker:
            tracker_cur.executemany("""
                INSERT INTO malay_packaged_tracker 
                (imdb_id, zip_filename, canonical_title, release_year, media_type, total_subs_count, zip_size_bytes, zip_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(imdb_id) DO UPDATE SET
                    zip_filename = excluded.zip_filename,
                    canonical_title = excluded.canonical_title,
                    release_year = excluded.release_year,
                    media_type = excluded.media_type,
                    total_subs_count = excluded.total_subs_count,
                    zip_size_bytes = excluded.zip_size_bytes,
                    zip_hash = excluded.zip_hash;
            """, batch_tracker)
            tracker_conn.commit()

    tracker_cur.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    tracker_conn.close()

    table = Table(title="📋 Ringkasan Pakej Sari Kata Siap (Zero-Duplicate & Smart Merge)", border_style="cyan")
    table.add_column("Status / Maklumat", style="yellow")
    table.add_column("Jumlah Rekod", justify="right", style="green")

    table.add_row("Tajuk Selesai / Dikemas Kini", f"{success_count:,}")
    table.add_row("Jumlah Fail Sari Kata Bersih Dipakej", f"{total_subs_packed:,}")
    table.add_row("Direktori Simpanan Pakej", str(OUTPUT_ZIP_DIR))
    table.add_row("Pangkalan Data Penjejak Selesai", str(TRACKER_DB_PATH))

    console.print("\n", table)
    console.print(f"\n[bold green]✨ SEMPURNA! Arkib sedia tanpa sebarang isu fail berulang di masa hadapan![/bold green]\n")

if __name__ == "__main__":
    main()