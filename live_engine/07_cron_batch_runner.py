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

# Tetapan laluan fail JSON untuk tajuk yang tiada sarikata BM/ID
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
NO_SUBS_HISTORY_FILE = os.path.join(DATA_DIR, "no_subs_history.json")

# Senarai fallback tempatan sekiranya rangkaian Cinemeta tergendala
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
    {"imdb_id": "tt0133093", "title": "The Matrix", "year": "1999"},
    {"imdb_id": "tt5952138", "title": "Munafik", "year": "2016"},
    {"imdb_id": "tt0466342", "title": "Dukun", "year": "2018"},
    {"imdb_id": "tt11032374", "title": "Ejen Ali: The Movie", "year": "2019"},
    {"imdb_id": "tt15398776", "title": "Oppenheimer", "year": "2023"},
    {"imdb_id": "tt1517268", "title": "Barbie", "year": "2023"},
    {"imdb_id": "tt1160419", "title": "Dune", "year": "2021"},
    {"imdb_id": "tt1431045", "title": "Deadpool", "year": "2016"},
    {"imdb_id": "tt0172495", "title": "Gladiator", "year": "2000"},
    {"imdb_id": "tt0816692", "title": "Interstellar", "year": "2014"},
    {"imdb_id": "tt6263850", "title": "Deadpool & Wolverine", "year": "2024"}
]

CINEMETA_CATALOG_URLS = [
    # Top Movies (Pagination 1 - 300)
    "https://v3-cinemeta.strem.io/catalog/movie/top.json",
    "https://v3-cinemeta.strem.io/catalog/movie/top/skip=100.json",
    "https://v3-cinemeta.strem.io/catalog/movie/top/skip=200.json",
    
    # Genre Hangat Filem
    "https://v3-cinemeta.strem.io/catalog/movie/top/genre=Action.json",
    "https://v3-cinemeta.strem.io/catalog/movie/top/genre=Horror.json",
    "https://v3-cinemeta.strem.io/catalog/movie/top/genre=Sci-Fi.json",
    "https://v3-cinemeta.strem.io/catalog/movie/top/genre=Animation.json",
    "https://v3-cinemeta.strem.io/catalog/movie/top/genre=Comedy.json",
    "https://v3-cinemeta.strem.io/catalog/movie/top/genre=Thriller.json",
    "https://v3-cinemeta.strem.io/catalog/movie/top/genre=Crime.json",
    
    # Siri TV Popular (1 - 200)
    "https://v3-cinemeta.strem.io/catalog/series/top.json",
    "https://v3-cinemeta.strem.io/catalog/series/top/skip=100.json"
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

def fetch_dynamic_cinemeta_queue() -> list[dict]:
    """
    Menyedut pelbagai katalog Cinemeta rasmi, menggabungkannya,
    dan membuang duplikasi tajuk secara automatik.
    """
    print("📡 [Cinemeta Fetcher] Mengumpulkan tajuk popular dari katalog Stremio...")
    unique_pool = {}

    for catalog_url in CINEMETA_CATALOG_URLS:
        try:
            res = requests.get(catalog_url, timeout=12)
            if res.status_code == 200:
                metas = res.json().get("metas", [])
                for item in metas:
                    imdb_id = item.get("id", "").strip()
                    name = item.get("name", "").strip()
                    rel_info = str(item.get("releaseInfo", "")).strip()
                    year = rel_info.split("–")[0].split("-")[0].strip()

                    if imdb_id and name and imdb_id.startswith("tt"):
                        if imdb_id not in unique_pool:
                            unique_pool[imdb_id] = {
                                "imdb_id": imdb_id,
                                "title": name,
                                "year": year
                            }
        except Exception as e:
            print(f"   ⚠️ Ralat menyedut katalog ({catalog_url.split('/')[-1]}): {e}")

    # Gabungkan dengan senarai fallback jika katalog gagal sepenuhnya
    if not unique_pool:
        print("   ⚠️ Menggunakan senarai sandaran fallback tempatan.")
        for item in FALLBACK_SEED_LIST:
            unique_pool[item["imdb_id"]] = item

    print(f"   ✅ Jumlah tajuk unik dikumpul dari Cinemeta: {len(unique_pool)} tajuk.")
    return list(unique_pool.values())

def run_batch_cron_scrape(target_limit: int = 15, delay_sec: float = 1.0):
    """
    Melaksanakan pengikisan kelompok berjadual secara modular.
    Memproses calon filem segar yang belum pernah dikikis ke B2 atau disahkan tiada sub.
    """
    print("=" * 80)
    print(f"🔄 MEMULAKAN CRON BATCH SUBTITLE SCRAPER")
    print(f"   ├─ Had Sasaran Sesi Ini: {target_limit} tajuk baru")
    print(f"   └─ Sela Masa (Delay)   : {delay_sec} saat")
    print("=" * 80)

    # Muat turun rekod tajuk tiada sarikata sedia ada
    no_subs_history = load_no_subs_history()
    print(f"📋 Rekod 'Tiada Sarikata' sedia ada dalam memori: {len(no_subs_history)} tajuk.")

    candidates = fetch_dynamic_cinemeta_queue()
    processed_count = 0
    skipped_exist_count = 0
    skipped_no_subs_count = 0

    for item in candidates:
        if processed_count >= target_limit:
            print(f"\n🎯 Had sasaran kelompok ({target_limit} tajuk) telah dicapai untuk sesi ini.")
            break

        imdb_id = item["imdb_id"]
        movie_title = item["title"]
        movie_year = item.get("year", "")

        # 1. Semak rekod kejayaan tempatan (jika sudah ada di B2/Redis, langkau)
        if _history.is_imdb_processed(imdb_id):
            skipped_exist_count += 1
            continue

        # 2. Semak rekod tajuk tiada sarikata (jika sudah pernah disemak dan tiada sub BM/ID, langkau)
        if imdb_id in no_subs_history:
            skipped_no_subs_count += 1
            continue

        print(f"\n📦 [{processed_count + 1}/{target_limit}] Memproses Calon Segar: {movie_title} ({movie_year}) -> {imdb_id}")

        # 3. Panggil enjin on-demand yang lengkap
        try:
            success = _ondemand.run_ondemand_scrape(imdb_id, custom_query=movie_title, year=movie_year)
            if success:
                processed_count += 1
                print(f"   ✅ Berjaya memuat naik sarikata bagi {movie_title} ({imdb_id})")
            else:
                # Rekodkan ke dalam no_subs_history.json supaya pusingan seterusnya terus skip
                save_no_subs_record(no_subs_history, imdb_id, movie_title, movie_year)
                print(f"   ⚠️ Tiada sarikata BM/ID ditemui. Disimpan ke rekod no_subs: {movie_title} ({imdb_id})")
        except Exception as e:
            print(f"   ❌ Ralat memproses {imdb_id}: {e}")

        time.sleep(delay_sec)

    total_skipped = skipped_exist_count + skipped_no_subs_count
    print("\n" + "=" * 80)
    print(f"✨ TUGASAN CRON BATCH SELESAI")
    print(f"   ├─ Tajuk Baharu Diproses & Dimuat Naik : {processed_count}")
    print(f"   ├─ Tajuk Dilangkau Kerana Sudah Wujud  : {skipped_exist_count}")
    print(f"   ├─ Tajuk Dilangkau Kerana Tiada Sub    : {skipped_no_subs_count}")
    print(f"   └─ Baki Calon Dalam Kolam Cinemeta     : {max(0, len(candidates) - (processed_count + total_skipped))}")
    print("=" * 80)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cron Batch Subtitle Scraper Runner")
    parser.add_argument("--limit", type=int, default=15, help="Jumlah had tajuk baru per pusingan")
    parser.add_argument("--delay", type=float, default=1.0, help="Sela masa permintaan antara filem (saat)")

    args = parser.parse_args()
    run_batch_cron_scrape(target_limit=args.limit, delay_sec=args.delay)