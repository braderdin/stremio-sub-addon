import sys
import os
import json
import time
import argparse
import importlib
from curl_cffi import requests

# Import modul enjin secara dinamik
_config = importlib.import_module("00_config")
_redis = importlib.import_module("01_redis_db")
_b2 = importlib.import_module("02_b2_storage")
_history = importlib.import_module("04_history_tracker")
_scraper = importlib.import_module("05_subtitle_scraper")
_ondemand = importlib.import_module("06_ondemand_runner")

# Tetapan laluan fail JSON untuk sejarah semakan
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
NO_SUBS_HISTORY_FILE = os.path.join(DATA_DIR, "no_subs_history.json")

# 16 Genre Utama Cinemeta (Filem)
ALL_MOVIE_GENRES = [
    "Action", "Horror", "Sci-Fi", "Animation", "Comedy", "Thriller",
    "Crime", "Adventure", "Drama", "Romance", "Fantasy", "Mystery",
    "Family", "History", "War", "Western"
]

# Genre Utama Siri TV
SERIES_GENRES = [
    "Drama", "Comedy", "Crime", "Sci-Fi", "Animation", "Action", "Mystery"
]

FALLBACK_SEED_LIST = [
    {"imdb_id": "tt0145487", "title": "Spider-Man", "year": "2002"},
    {"imdb_id": "tt0499549", "title": "Avatar", "year": "2009"},
    {"imdb_id": "tt0848228", "title": "The Avengers", "year": "2012"},
    {"imdb_id": "tt0372784", "title": "Batman Begins", "year": "2005"},
    {"imdb_id": "tt0241527", "title": "Harry Potter and the Sorcerer's Stone", "year": "2001"},
    {"imdb_id": "tt0232500", "title": "The Fast and the Furious", "year": "2001"},
    {"imdb_id": "tt2911666", "title": "John Wick", "year": "2014"},
    {"imdb_id": "tt0418279", "title": "Transformers", "year": "2007"},
    {"imdb_id": "tt0369610", "title": "Jurassic World", "year": "2015"},
    {"imdb_id": "tt0133093", "title": "The Matrix", "year": "1999"}
]

def load_no_subs_history() -> dict:
    """Membaca senarai tajuk yang telah disahkan tiada sarikata BM/ID."""
    if os.path.exists(NO_SUBS_HISTORY_FILE):
        try:
            with open(NO_SUBS_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Ralat membaca {NO_SUBS_HISTORY_FILE}: {e}")
    return {}

def save_no_subs_record(history_dict: dict, imdb_id: str, title: str, year: str):
    """Menyimpan rekod IMDb ID yang tiada sarikata ke dalam no_subs_history.json."""
    os.makedirs(DATA_DIR, exist_ok=True)
    history_dict[imdb_id] = {
        "title": title,
        "year": str(year),
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    }
    try:
        with open(NO_SUBS_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history_dict, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Gagal mengemas kini {NO_SUBS_HISTORY_FILE}: {e}")

def build_infinite_catalog_targets() -> list[dict]:
    """
    Membina 100 endpoint katalog berstruktur (Deep Pagination & Multi-Genre).
    Menggunakan sintaks rasmi Stremio: genre={genre}&skip={skip}.json
    """
    targets = []

    # 1. Top Movies Umum (Deep Pagination: Top 500 Filem)
    targets.append({
        "name": "Top Movies (1 - 50)",
        "url": "https://v3-cinemeta.strem.io/catalog/movie/top.json"
    })
    for skip in range(50, 500, 50):
        targets.append({
            "name": f"Top Movies ({skip + 1} - {skip + 50})",
            "url": f"https://v3-cinemeta.strem.io/catalog/movie/top/skip={skip}.json"
        })

    # 2. Filem Mengikut Genre (16 Genre x 4 Halaman = Top 200 per genre)
    for genre in ALL_MOVIE_GENRES:
        targets.append({
            "name": f"{genre} Movie (1 - 50)",
            "url": f"https://v3-cinemeta.strem.io/catalog/movie/top/genre={genre}.json"
        })
        for skip in [50, 100, 150]:
            targets.append({
                "name": f"{genre} Movie ({skip + 1} - {skip + 50})",
                "url": f"https://v3-cinemeta.strem.io/catalog/movie/top/genre={genre}&skip={skip}.json"
            })

    # 3. Top Series Umum (Top 250 Siri TV)
    targets.append({
        "name": "Top Series (1 - 50)",
        "url": "https://v3-cinemeta.strem.io/catalog/series/top.json"
    })
    for skip in range(50, 250, 50):
        targets.append({
            "name": f"Top Series ({skip + 1} - {skip + 50})",
            "url": f"https://v3-cinemeta.strem.io/catalog/series/top/skip={skip}.json"
        })

    # 4. Siri TV Mengikut Genre (7 Genre x 3 Halaman = Top 150 per genre)
    for genre in SERIES_GENRES:
        targets.append({
            "name": f"{genre} Series (1 - 50)",
            "url": f"https://v3-cinemeta.strem.io/catalog/series/top/genre={genre}.json"
        })
        for skip in [50, 100]:
            targets.append({
                "name": f"{genre} Series ({skip + 1} - {skip + 50})",
                "url": f"https://v3-cinemeta.strem.io/catalog/series/top/genre={genre}&skip={skip}.json"
            })

    return targets

def fetch_dynamic_infinite_queue() -> list[dict]:
    """
    Menyedut 100 endpoint katalog Cinemeta dengan pengasingan tajuk unik automatik.
    """
    targets = build_infinite_catalog_targets()
    print(f"📡 [Infinite Catalog Fetcher] Menyedut daripada {len(targets)} endpoint katalog Cinemeta...")
    unique_pool = {}

    for idx, target in enumerate(targets, 1):
        url = target["url"]
        name = target["name"]
        try:
            res = requests.get(url, timeout=8)
            if res.status_code == 200:
                metas = res.json().get("metas", [])
                new_added = 0
                for item in metas:
                    imdb_id = item.get("id", "").strip()
                    title = item.get("name", "").strip()
                    rel_info = str(item.get("releaseInfo", "")).strip()
                    year = rel_info.split("–")[0].split("-")[0].strip()

                    if imdb_id and title and imdb_id.startswith("tt"):
                        if imdb_id not in unique_pool:
                            unique_pool[imdb_id] = {
                                "imdb_id": imdb_id,
                                "title": title,
                                "year": year
                            }
                            new_added += 1
                print(f"   [{idx:03d}/{len(targets):03d}] ✅ {name:32} -> Entri: {len(metas):2d} | Unik Baru: {new_added:2d}")
            else:
                print(f"   [{idx:03d}/{len(targets):03d}] ❌ (HTTP {res.status_code}) -> {name}")
        except Exception:
            print(f"   [{idx:03d}/{len(targets):03d}] ⚠️ (Timeout/Gagal) -> {name}")

        time.sleep(0.05)

    if not unique_pool:
        print("   ⚠️ Gagal menyedut katalog Cinemeta, menggunakan fallback tempatan.")
        for item in FALLBACK_SEED_LIST:
            unique_pool[item["imdb_id"]] = item

    print(f"✅ Jumlah calon unik sedia ada dalam kolam: {len(unique_pool)} tajuk.")
    return list(unique_pool.values())

def run_batch_cron_scrape(target_limit: int = 20, delay_sec: float = 1.0):
    """
    Melaksanakan pengikisan kelompok berjadual secara modular.
    Menggunakan Round-Robin B2 storage dan Infinite Catalog Pool.
    """
    total_b2_accs = len(_config.B2_ACCOUNTS)
    print("=" * 80)
    print("🔄 MEMULAKAN CRON BATCH SUBTITLE SCRAPER (INFINITE CATALOG ENGINE)")
    print(f"   ├─ Had Sasaran Sesi Ini : {target_limit} tajuk baru")
    print(f"   ├─ Jumlah Akaun B2 Siap : {total_b2_accs} akaun (Round-Robin Active)")
    print(f"   └─ Sela Masa (Delay)    : {delay_sec} saat")
    print("=" * 80)

    no_subs_history = load_no_subs_history()
    print(f"📋 Rekod 'Tiada Sarikata' sedia ada: {len(no_subs_history)} tajuk.")

    candidates = fetch_dynamic_infinite_queue()
    processed_count = 0
    skipped_exist_count = 0
    skipped_no_subs_count = 0
    all_b2_dead = False

    for item in candidates:
        if processed_count >= target_limit:
            print(f"\n🎯 Had sasaran kelompok ({target_limit} tajuk) telah dicapai untuk sesi ini.")
            break

        # Semak jika kesemua akaun B2 telah capai had transaksi
        if _b2.is_all_b2_exhausted():
            print("\n" + "!" * 80)
            print(f"🚨 HENTI KECEMASAN: Kesemua {total_b2_accs} akaun B2 telah mencapai had transaksi!")
            print("🛑 Menutup sesi secara selamat bagi membolehkan fail JSON disegerakkan.")
            print("!" * 80)
            all_b2_dead = True
            break

        imdb_id = item["imdb_id"]
        movie_title = item["title"]
        movie_year = item.get("year", "")

        # 1. Semak rekod siap kikis tempatan
        if _history.is_imdb_processed(imdb_id):
            skipped_exist_count += 1
            continue

        # 2. Semak rekod tiada sarikata tempatan
        if imdb_id in no_subs_history:
            skipped_no_subs_count += 1
            continue

        print(f"\n📦 [{processed_count + 1}/{target_limit}] Memproses: {movie_title} ({movie_year}) -> {imdb_id}")

        # 3. Panggil enjin on-demand
        try:
            success = _ondemand.run_ondemand_scrape(imdb_id, custom_query=movie_title, year=movie_year)
            if success:
                processed_count += 1
                print(f"   ✅ Berjaya memuat naik sarikata bagi {movie_title} ({imdb_id})")
            else:
                if not _b2.is_all_b2_exhausted():
                    save_no_subs_record(no_subs_history, imdb_id, movie_title, movie_year)
                    print(f"   ⚠️ Disimpan ke no_subs_history: {movie_title} ({imdb_id})")
                else:
                    print(f"   ⚠️ Dibatalkan daripada rekod no_subs (Ralat storan B2 dikesan).")
                    all_b2_dead = True
                    break

        except _b2.AllB2AccountsExhaustedException:
            print("\n" + "!" * 80)
            print(f"🚨 KESEMUA {total_b2_accs} AKAUN B2 MENCECAH CAP HARIAN!")
            print("!" * 80)
            all_b2_dead = True
            break
        except Exception as e:
            print(f"   ❌ Ralat memproses {imdb_id}: {e}")

        time.sleep(delay_sec)

    total_skipped = skipped_exist_count + skipped_no_subs_count
    print("\n" + "=" * 80)
    print("✨ TUGASAN CRON BATCH SELESAI")
    print(f"   ├─ Tajuk Baharu Diproses & Dimuat Naik : {processed_count}")
    print(f"   ├─ Tajuk Dilangkau Kerana Sudah Wujud  : {skipped_exist_count}")
    print(f"   ├─ Tajuk Dilangkau Kerana Tiada Sub    : {skipped_no_subs_count}")
    print(f"   ├─ Status B2 Storage                  : {'⚠️ SEMUA AKAUN CAP' if all_b2_dead else '✅ BERFUNGSI (ROTATING)'}")
    print(f"   └─ Baki Calon Dalam Kolam             : {max(0, len(candidates) - (processed_count + total_skipped))}")
    print("=" * 80)

    sys.exit(0)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cron Batch Subtitle Scraper Runner")
    parser.add_argument("--limit", type=int, default=20, help="Jumlah had tajuk baru per pusingan")
    parser.add_argument("--delay", type=float, default=1.0, help="Sela masa permintaan antara filem (saat)")

    args = parser.parse_args()
    run_batch_cron_scrape(target_limit=args.limit, delay_sec=args.delay)