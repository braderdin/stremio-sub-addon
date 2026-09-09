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

# 12 Genre utama IMDb / Cinemeta
TOP_GENRES = [
    "Action", "Horror", "Sci-Fi", "Animation", "Comedy", "Thriller",
    "Crime", "Adventure", "Drama", "Romance", "Fantasy", "Mystery"
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

def build_catalog_targets() -> list[dict]:
    """Membina senarai sasaran 31 katalog komprehensif (Top 200 per genre + TPB)."""
    targets = []

    # 1. Filem Popular Umum Cinemeta (Top 300)
    targets.append({"name": "Cinemeta Top Movies (1 - 100)", "url": "https://v3-cinemeta.strem.io/catalog/movie/top.json"})
    targets.append({"name": "Cinemeta Top Movies (101 - 200)", "url": "https://v3-cinemeta.strem.io/catalog/movie/top/skip=100.json"})
    targets.append({"name": "Cinemeta Top Movies (201 - 300)", "url": "https://v3-cinemeta.strem.io/catalog/movie/top/skip=200.json"})

    # 2. Top 200 Setiap Genre Filem Cinemeta (12 Genre x 2 Laman = 24 Endpoint)
    for genre in TOP_GENRES:
        targets.append({
            "name": f"Cinemeta {genre} (1 - 100)",
            "url": f"https://v3-cinemeta.strem.io/catalog/movie/top/genre={genre}.json"
        })
        targets.append({
            "name": f"Cinemeta {genre} (101 - 200)",
            "url": f"https://v3-cinemeta.strem.io/catalog/movie/top/genre={genre}/skip=100.json"
        })

    # 3. Siri TV Popular Cinemeta (Top 200)
    targets.append({"name": "Cinemeta Series (1 - 100)", "url": "https://v3-cinemeta.strem.io/catalog/series/top.json"})
    targets.append({"name": "Cinemeta Series (101 - 200)", "url": "https://v3-cinemeta.strem.io/catalog/series/top/skip=100.json"})

    # 4. ThePirateBay Mirror Catalog (Stremio Official Community)
    targets.append({"name": "TPB Top Movies", "url": "https://piratebay-catalog.strem.fun/catalog/movie/top.json"})
    targets.append({"name": "TPB Top TV Series", "url": "https://piratebay-catalog.strem.fun/catalog/series/top.json"})

    return targets

def fetch_dynamic_multi_catalog_queue() -> list[dict]:
    """Menyedut katalog pelbagai sumber (Cinemeta + TPB) dengan perlindungan kendala rangkaian."""
    print("📡 [Multi-Catalog Fetcher] Mengumpulkan tajuk daripada 31 endpoint katalog...")
    unique_pool = {}
    targets = build_catalog_targets()

    for idx, target in enumerate(targets, 1):
        url = target["url"]
        name = target["name"]
        try:
            # Had masa singkat 10s supaya jika TPB/Cinemeta perlahan tidak menyekat runner
            res = requests.get(url, timeout=10)
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
                print(f"   [{idx:02d}/{len(targets):02d}] {name:32} -> Entri: {len(metas):3d} | Unik Baru: {new_added:3d}")
            else:
                print(f"   [{idx:02d}/{len(targets):02d}] {name:32} -> HTTP Error: {res.status_code}")
        except Exception:
            # Langkau senyap jika endpoint cermin (cth: TPB) tergendala
            print(f"   [{idx:02d}/{len(targets):02d}] {name:32} -> Sambungan tergendala (dilangkau).")

    if not unique_pool:
        print("   ⚠️ Pangkalan katalog gagal dihubungi, menggunakan senarai fallback sandaran.")
        for item in FALLBACK_SEED_LIST:
            unique_pool[item["imdb_id"]] = item

    print(f"✅ Jumlah calon unik terkumpul dalam kolam: {len(unique_pool)} tajuk.")
    return list(unique_pool.values())

def run_batch_cron_scrape(target_limit: int = 20, delay_sec: float = 1.0):
    """
    Melaksanakan pengikisan kelompok berjadual secara modular.
    Menggunakan agihan muat naik berputar (Round-Robin) merentasi kesemua akaun B2.
    """
    total_b2_accs = len(_config.B2_ACCOUNTS)
    print("=" * 80)
    print("🔄 MEMULAKAN CRON BATCH SUBTITLE SCRAPER (MULTI-CATALOG ENGINE)")
    print(f"   ├─ Had Sasaran Sesi Ini : {target_limit} tajuk baru")
    print(f"   ├─ Jumlah Akaun B2 Siap : {total_b2_accs} akaun (Round-Robin Active)")
    print(f"   └─ Sela Masa (Delay)    : {delay_sec} saat")
    print("=" * 80)

    no_subs_history = load_no_subs_history()
    print(f"📋 Rekod 'Tiada Sarikata' sedia ada: {len(no_subs_history)} tajuk.")

    candidates = fetch_dynamic_multi_catalog_queue()
    processed_count = 0
    skipped_exist_count = 0
    skipped_no_subs_count = 0
    all_b2_dead = False

    for item in candidates:
        if processed_count >= target_limit:
            print(f"\n🎯 Had sasaran kelompok ({target_limit} tajuk) telah dicapai untuk sesi ini.")
            break

        # Semak jika kesemua akaun B2 telah capai limit transaksi
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

        # 1. Semak jika sudah wujud dalam arkib kejayaan
        if _history.is_imdb_processed(imdb_id):
            skipped_exist_count += 1
            continue

        # 2. Semak jika telah disahkan tiada sarikata
        if imdb_id in no_subs_history:
            skipped_no_subs_count += 1
            continue

        print(f"\n📦 [{processed_count + 1}/{target_limit}] Memproses: {movie_title} ({movie_year}) -> {imdb_id}")

        # 3. Panggil enjin on-demand yang mempunyai fallback IMDb ID -> Teks berserta B2 Round-Robin
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