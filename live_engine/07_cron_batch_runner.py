import sys
import os
import json
import time
import argparse
import importlib
from pathlib import Path
from curl_cffi import requests

# Import modul enjin secara dinamik
_config = importlib.import_module("00_config")
_redis = importlib.import_module("01_redis_db")
_b2 = importlib.import_module("02_b2_storage")
_history = importlib.import_module("04_history_tracker")
_scraper = importlib.import_module("05_subtitle_scraper")
_ondemand = importlib.import_module("06_ondemand_runner")

# Tetapan laluan fail JSON untuk sejarah semakan dan kolam tajuk
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
NO_SUBS_HISTORY_FILE = os.path.join(DATA_DIR, "no_subs_history.json")
MOVIE_POOL_FILE = os.path.join(DATA_DIR, "movie_pool.json")

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
    {"imdb_id": "tt0145487", "title": "Spider-Man", "year": "2002", "type": "movie"},
    {"imdb_id": "tt0499549", "title": "Avatar", "year": "2009", "type": "movie"},
    {"imdb_id": "tt0848228", "title": "The Avengers", "year": "2012", "type": "movie"},
    {"imdb_id": "tt0372784", "title": "Batman Begins", "year": "2005", "type": "movie"},
    {"imdb_id": "tt0241527", "title": "Harry Potter and the Sorcerer's Stone", "year": "2001", "type": "movie"},
    {"imdb_id": "tt0232500", "title": "The Fast and the Furious", "year": "2001", "type": "movie"},
    {"imdb_id": "tt2911666", "title": "John Wick", "year": "2014", "type": "movie"},
    {"imdb_id": "tt0418279", "title": "Transformers", "year": "2007", "type": "movie"},
    {"imdb_id": "tt0369610", "title": "Jurassic World", "year": "2015", "type": "movie"},
    {"imdb_id": "tt0133093", "title": "The Matrix", "year": "1999", "type": "movie"}
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

def load_movie_pool_data() -> dict:
    """Membaca rekod kolam tajuk dari fail data/movie_pool.json."""
    if os.path.exists(MOVIE_POOL_FILE):
        try:
            with open(MOVIE_POOL_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            print(f"⚠️ Ralat membaca {MOVIE_POOL_FILE}: {e}")
    return {}

def save_movie_pool_data(pool_dict: dict):
    """Menyimpan semula baki kolam tajuk ke fail data/movie_pool.json."""
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(MOVIE_POOL_FILE, "w", encoding="utf-8") as f:
            json.dump(pool_dict, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Gagal mengemas kini {MOVIE_POOL_FILE}: {e}")

def get_candidates_from_pool(pool_dict: dict, no_subs_history: dict) -> list[dict]:
    """
    Menyusun calon sedia ada daripada fail kolam dan menapis tajuk yang sudah diproses.
    Jika kolam kosong, menggunakan senarai benih sandaran tempatan.
    """
    valid_candidates = []

    for imdb_id, item in pool_dict.items():
        # 1. Langkau jika sudah diproses
        if _history.is_imdb_processed(imdb_id):
            continue

        # 2. Langkau jika tiada sarikata
        if imdb_id in no_subs_history:
            continue

        valid_candidates.append(item)

    # Fallback kecemasan sekiranya kolam kosong atau belum dijana oleh fail 08
    if not valid_candidates and not pool_dict:
        print("⚠️ Kolam movie_pool.json kosong atau belum dijana. Menggunakan fallback sandaran.")
        for item in FALLBACK_SEED_LIST:
            fid = item["imdb_id"]
            if not _history.is_imdb_processed(fid) and fid not in no_subs_history:
                valid_candidates.append(item)

    return valid_candidates

def run_batch_cron_scrape(target_limit: int = 20, delay_sec: float = 1.0):
    """
    Melaksanakan pengikisan kelompok berjadual secara modular.
    Menggunakan Round-Robin B2 storage dan Kolam Tajuk Setempat (movie_pool.json).
    """
    total_b2_accs = len(_config.B2_ACCOUNTS)
    print("=" * 80)
    print("🔄 MEMULAKAN CRON BATCH SUBTITLE SCRAPER (DARI KOLAM MOVIE_POOL.JSON)")
    print(f"   ├─ Had Sasaran Sesi Ini : {target_limit} tajuk baru")
    print(f"   ├─ Jumlah Akaun B2 Siap : {total_b2_accs} akaun (Round-Robin Active)")
    print(f"   └─ Sela Masa (Delay)    : {delay_sec} saat")
    print("=" * 80)

    no_subs_history = load_no_subs_history()
    print(f"📋 Rekod 'Tiada Sarikata' sedia ada: {len(no_subs_history)} tajuk.")

    # Baca kolam tajuk dari fail setempat
    movie_pool = load_movie_pool_data()
    print(f"📦 Jumlah keseluruhan entri dalam movie_pool.json: {len(movie_pool)} entri.")

    candidates = get_candidates_from_pool(movie_pool, no_subs_history)
    print(f"🎯 Calon sah yang sedia dikikis dalam giliran: {len(candidates)} entri.")

    processed_count = 0
    skipped_exist_count = 0
    skipped_no_subs_count = 0
    all_b2_dead = False
    pool_modified = False

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
        item_type = item.get("type", "movie")

        # 1. Semak rekod siap kikis tempatan
        if _history.is_imdb_processed(imdb_id):
            skipped_exist_count += 1
            if imdb_id in movie_pool:
                del movie_pool[imdb_id]
                pool_modified = True
            continue

        # 2. Semak rekod tiada sarikata tempatan
        if imdb_id in no_subs_history:
            skipped_no_subs_count += 1
            if imdb_id in movie_pool:
                del movie_pool[imdb_id]
                pool_modified = True
            continue

        # Format carian pintar: jika episod siri TV (S01E01), bantu enjin teks Subscene
        search_query = movie_title
        season_num = item.get("season")
        episode_num = item.get("episode")
        if item_type == "series" and season_num is not None and episode_num is not None:
            search_query = f"{movie_title} S{int(season_num):02d}E{int(episode_num):02d}"

        print(f"\n📦 [{processed_count + 1}/{target_limit}] Memproses: {search_query} ({movie_year}) -> {imdb_id}")

        # 3. Panggil enjin on-demand
        try:
            success = _ondemand.run_ondemand_scrape(imdb_id, custom_query=search_query, year=movie_year)
            if success:
                processed_count += 1
                print(f"   ✅ Berjaya memuat naik sarikata bagi {search_query} ({imdb_id})")
                
                # Singkirkan daripada kolam aktif kerana telah selesai diproses
                if imdb_id in movie_pool:
                    del movie_pool[imdb_id]
                    pool_modified = True
            else:
                if not _b2.is_all_b2_exhausted():
                    save_no_subs_record(no_subs_history, imdb_id, movie_title, movie_year)
                    print(f"   ⚠️ Disimpan ke no_subs_history: {movie_title} ({imdb_id})")
                    
                    # Singkirkan daripada kolam aktif kerana disahkan tiada sarikata
                    if imdb_id in movie_pool:
                        del movie_pool[imdb_id]
                        pool_modified = True
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

    # Simpan semula status terkini fail kolam jika terdapat penyingkiran item
    if pool_modified:
        save_movie_pool_data(movie_pool)
        print("💾 Kolam movie_pool.json berjaya dikemas kini.")

    total_skipped = skipped_exist_count + skipped_no_subs_count
    print("\n" + "=" * 80)
    print("✨ TUGASAN CRON BATCH SELESAI")
    print(f"   ├─ Tajuk Baharu Diproses & Dimuat Naik : {processed_count}")
    print(f"   ├─ Tajuk Dilangkau Kerana Sudah Wujud  : {skipped_exist_count}")
    print(f"   ├─ Tajuk Dilangkau Kerana Tiada Sub    : {skipped_no_subs_count}")
    print(f"   ├─ Status B2 Storage                  : {'⚠️ SEMUA AKAUN CAP' if all_b2_dead else '✅ BERFUNGSI (ROTATING)'}")
    print(f"   └─ Baki Calon Dalam Kolam             : {len(movie_pool)}")
    print("=" * 80)

    sys.exit(0)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cron Batch Subtitle Scraper Runner")
    parser.add_argument("--limit", type=int, default=20, help="Jumlah had tajuk baru per pusingan")
    parser.add_argument("--delay", type=float, default=1.0, help="Sela masa permintaan antara filem (saat)")

    args = parser.parse_args()
    run_batch_cron_scrape(target_limit=args.limit, delay_sec=args.delay)