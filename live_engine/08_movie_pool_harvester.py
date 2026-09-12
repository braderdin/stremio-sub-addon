import sys
import os
import json
import time
import argparse
import importlib
from pathlib import Path
from curl_cffi import requests

# Pastikan laluan folder live_engine diutamakan dalam sys.path
LIVE_ENGINE_DIR = Path(__file__).resolve().parent
if str(LIVE_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(LIVE_ENGINE_DIR))

# Import modul konfigurasi dan penjejak sejarah secara dinamik
_config = importlib.import_module("00_config")
_history = importlib.import_module("04_history_tracker")

# Tetapan direktori dan fail data
DATA_DIR = _config.DATA_DIR
MOVIE_POOL_FILE = DATA_DIR / "movie_pool.json"
NO_SUBS_HISTORY_FILE = DATA_DIR / "no_subs_history.json"

# 16 Genre Utama Cinemeta (Filem)
ALL_MOVIE_GENRES = [
    "Action", "Horror", "Sci-Fi", "Animation", "Comedy", "Thriller",
    "Crime", "Adventure", "Drama", "Romance", "Fantasy", "Mystery",
    "Family", "History", "War", "Western"
]

# Genre Utama Siri TV Cinemeta
SERIES_GENRES = [
    "Drama", "Comedy", "Crime", "Sci-Fi", "Animation", "Action", "Mystery"
]

# Senarai Platform Penstriman CyberFlix Catalog
CYBERFLIX_PLATFORMS = [
    ("netflix", "Netflix"),
    ("disney", "Disney+"),
    ("hbo", "HBO Max"),
    ("apple", "Apple TV+"),
    ("amazon", "Amazon Prime"),
    ("paramount", "Paramount+"),
    ("hulu", "Hulu"),
    ("peacock", "Peacock")
]

# Senarai Benih Sandaran (Fallback Seed List)
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

def load_movie_pool() -> dict:
    """Membaca kolam fail movie_pool.json sedia ada jika wujud."""
    if MOVIE_POOL_FILE.exists():
        try:
            with open(MOVIE_POOL_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            print(f"⚠️ Ralat membaca {MOVIE_POOL_FILE.name}: {e}")
    return {}

def save_movie_pool(pool: dict) -> bool:
    """Menyimpan kamus kolam terkini ke data/movie_pool.json."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(MOVIE_POOL_FILE, "w", encoding="utf-8") as f:
            json.dump(pool, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"❌ Ralat menyimpan {MOVIE_POOL_FILE.name}: {e}")
        return False

def load_no_subs_history() -> dict:
    """Membaca rekod tajuk yang disahkan tiada sarikata dari no_subs_history.json."""
    if NO_SUBS_HISTORY_FILE.exists():
        try:
            with open(NO_SUBS_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            print(f"⚠️ Ralat membaca {NO_SUBS_HISTORY_FILE.name}: {e}")
    return {}

def prune_processed_items(pool: dict) -> tuple[dict, int]:
    """
    Menyingkirkan tajuk daripada kolam yang telah pun wujud dalam:
    1. data/scraped_history.json (Sudah berjaya dikikis)
    2. data/no_subs_history.json (Disahkan tiada sarikata BM/ID)
    """
    no_subs_history = load_no_subs_history()
    cleaned_pool = {}
    pruned_count = 0

    for imdb_id, item in pool.items():
        # Semak rekod siap kikis
        if _history.is_imdb_processed(imdb_id):
            pruned_count += 1
            continue

        # Semak rekod tiada sarikata
        if imdb_id in no_subs_history:
            pruned_count += 1
            continue

        cleaned_pool[imdb_id] = item

    return cleaned_pool, pruned_count

# ==============================================================================
# MODUL 1: SEDUTAN KATALOG CINEMETA ASAS & GENRE (KEKALKAN GAYA ASAL 07)
# ==============================================================================
def harvest_cinemeta_standard() -> tuple[list[dict], list[dict]]:
    """
    Menyedut katalog Cinemeta standard (Top Movies, Movie Genres, Top Series, Series Genres).
    Memulangkan dua senarai: (movie_candidates, series_candidates).
    """
    print("\n🌐 [Modul 1] Menyedut Katalog Standard Cinemeta (Top & Multi-Genre)...")
    targets = []

    # 1. Top Movies Umum (Top 500 Filem)
    targets.append({
        "name": "Cinemeta Top Movies (1 - 50)",
        "url": "https://v3-cinemeta.strem.io/catalog/movie/top.json",
        "type": "movie"
    })
    for skip in range(50, 500, 50):
        targets.append({
            "name": f"Cinemeta Top Movies ({skip + 1} - {skip + 50})",
            "url": f"https://v3-cinemeta.strem.io/catalog/movie/top/skip={skip}.json",
            "type": "movie"
        })

    # 2. Filem Mengikut 16 Genre Utama
    for genre in ALL_MOVIE_GENRES:
        targets.append({
            "name": f"Cinemeta {genre} Movie (1 - 50)",
            "url": f"https://v3-cinemeta.strem.io/catalog/movie/top/genre={genre}.json",
            "type": "movie"
        })
        for skip in [50, 100, 150]:
            targets.append({
                "name": f"Cinemeta {genre} Movie ({skip + 1} - {skip + 50})",
                "url": f"https://v3-cinemeta.strem.io/catalog/movie/top/genre={genre}&skip={skip}.json",
                "type": "movie"
            })

    # 3. Top Series Umum (Top 250 Siri TV)
    targets.append({
        "name": "Cinemeta Top Series (1 - 50)",
        "url": "https://v3-cinemeta.strem.io/catalog/series/top.json",
        "type": "series"
    })
    for skip in range(50, 250, 50):
        targets.append({
            "name": f"Cinemeta Top Series ({skip + 1} - {skip + 50})",
            "url": f"https://v3-cinemeta.strem.io/catalog/series/top/skip={skip}.json",
            "type": "series"
        })

    # 4. Siri TV Mengikut 7 Genre Utama
    for genre in SERIES_GENRES:
        targets.append({
            "name": f"Cinemeta {genre} Series (1 - 50)",
            "url": f"https://v3-cinemeta.strem.io/catalog/series/top/genre={genre}.json",
            "type": "series"
        })
        for skip in [50, 100]:
            targets.append({
                "name": f"Cinemeta {genre} Series ({skip + 1} - {skip + 50})",
                "url": f"https://v3-cinemeta.strem.io/catalog/series/top/genre={genre}&skip={skip}.json",
                "type": "series"
            })

    movies = []
    series = []

    for idx, target in enumerate(targets, 1):
        try:
            res = requests.get(target["url"], timeout=8)
            if res.status_code == 200:
                metas = res.json().get("metas", [])
                for item in metas:
                    imdb_id = item.get("id", "").strip()
                    title = item.get("name", "").strip()
                    rel_info = str(item.get("releaseInfo", "")).strip()
                    year = rel_info.split("–")[0].split("-")[0].strip()

                    if imdb_id and title and imdb_id.startswith("tt"):
                        entry = {
                            "imdb_id": imdb_id,
                            "title": title,
                            "year": year,
                            "type": target["type"],
                            "source": "cinemeta_standard"
                        }
                        if target["type"] == "movie":
                            movies.append(entry)
                        else:
                            series.append(entry)
                print(f"   [{idx:03d}/{len(targets):03d}] ✅ {target['name']:38} -> {len(metas)} entri")
            else:
                print(f"   [{idx:03d}/{len(targets):03d}] ❌ (HTTP {res.status_code}) -> {target['name']}")
        except Exception:
            print(f"   [{idx:03d}/{len(targets):03d}] ⚠️ (Timeout/Langkau) -> {target['name']}")

        time.sleep(0.04)

    return movies, series

# ==============================================================================
# MODUL 2: SEDUTAN MENGIKUT TAHUN TERBITAN (YEAR-BASED CATALOGS)
# ==============================================================================
def harvest_cinemeta_by_years(start_year: int = 2026, end_year: int = 1990) -> list[dict]:
    """
    Menyedut Top Movies bagi setiap tahun dari start_year ke end_year.
    Menghasilkan ribuan judul malar segar (evergreen).
    """
    print(f"\n📅 [Modul 2] Menyedut Katalog Mengikut Tahun ({start_year} -> {end_year})...")
    candidates = []

    for yr in range(start_year, end_year - 1, -1):
        # 1. Halaman pertama per tahun (Top 50)
        url_page1 = f"https://v3-cinemeta.strem.io/catalog/movie/top/year={yr}.json"
        # 2. Halaman kedua per tahun (Top 51 - 100)
        url_page2 = f"https://v3-cinemeta.strem.io/catalog/movie/top/year={yr}&skip=50.json"

        for page_num, target_url in enumerate([url_page1, url_page2], 1):
            try:
                res = requests.get(target_url, timeout=8)
                if res.status_code == 200:
                    metas = res.json().get("metas", [])
                    for item in metas:
                        imdb_id = item.get("id", "").strip()
                        title = item.get("name", "").strip()
                        rel_info = str(item.get("releaseInfo", "")).strip()
                        year = rel_info.split("–")[0].split("-")[0].strip() or str(yr)

                        if imdb_id and title and imdb_id.startswith("tt"):
                            candidates.append({
                                "imdb_id": imdb_id,
                                "title": title,
                                "year": year,
                                "type": "movie",
                                "source": f"cinemeta_year_{yr}"
                            })
                    print(f"   🗓️ Tahun {yr} (Halaman {page_num}) -> {len(metas)} entri disedut.")
                else:
                    break
            except Exception:
                print(f"   ⚠️ Ralat/Timeout menyedut tahun {yr} (Halaman {page_num})")
                break

            time.sleep(0.04)

    return candidates

# ==============================================================================
# MODUL 3: INTEGRASI KATALOG CYBERFLIX & TMDB (PLATFORM PENSTRIMAN)
# ==============================================================================
def harvest_cyberflix_catalogs() -> tuple[list[dict], list[dict]]:
    """
    Menyedut katalog pihak ketiga CyberFlix yang dikelaskan mengikut platform
    penstriman popular (Netflix, Disney+, HBO, Apple TV+, Amazon Prime, dsb.).
    """
    print("\n📺 [Modul 3] Menyedut Katalog CyberFlix Mengikut Platform Penstriman...")
    movies = []
    series = []

    for platform_code, platform_name in CYBERFLIX_PLATFORMS:
        # Filem popular platform
        movie_url = f"https://cyberflix.elfhosted.com/catalog/movie/{platform_code}.popular.json"
        try:
            res_m = requests.get(movie_url, timeout=8)
            if res_m.status_code == 200:
                metas_m = res_m.json().get("metas", [])
                for item in metas_m:
                    imdb_id = item.get("id", "").strip()
                    title = item.get("name", "").strip()
                    rel_info = str(item.get("releaseInfo", "")).strip()
                    year = rel_info.split("–")[0].split("-")[0].strip()
                    if imdb_id and title and imdb_id.startswith("tt"):
                        movies.append({
                            "imdb_id": imdb_id,
                            "title": title,
                            "year": year,
                            "type": "movie",
                            "source": f"cyberflix_{platform_code}"
                        })
                print(f"   🎬 CyberFlix [{platform_name:12}] Filem -> {len(metas_m)} entri.")
        except Exception:
            print(f"   ⚠️ CyberFlix [{platform_name:12}] Filem gagal/timeout.")

        time.sleep(0.04)

        # Siri TV popular platform
        series_url = f"https://cyberflix.elfhosted.com/catalog/series/{platform_code}.popular.json"
        try:
            res_s = requests.get(series_url, timeout=8)
            if res_s.status_code == 200:
                metas_s = res_s.json().get("metas", [])
                for item in metas_s:
                    imdb_id = item.get("id", "").strip()
                    title = item.get("name", "").strip()
                    rel_info = str(item.get("releaseInfo", "")).strip()
                    year = rel_info.split("–")[0].split("-")[0].strip()
                    if imdb_id and title and imdb_id.startswith("tt"):
                        series.append({
                            "imdb_id": imdb_id,
                            "title": title,
                            "year": year,
                            "type": "series",
                            "source": f"cyberflix_{platform_code}"
                        })
                print(f"   📺 CyberFlix [{platform_name:12}] Siri  -> {len(metas_s)} entri.")
        except Exception:
            print(f"   ⚠️ CyberFlix [{platform_name:12}] Siri gagal/timeout.")

        time.sleep(0.04)

    return movies, series

# ==============================================================================
# MODUL 4: PENGEKSTRAKAN EPISOD SIRI TV POPULAR (tt...:season:episode)
# ==============================================================================
def expand_series_episodes(
    series_list: list[dict], 
    max_series_to_expand: int = 35, 
    max_episodes_per_series: int = 25
) -> list[dict]:
    """
    Mengekstrak episod terperinci bagi siri TV popular daripada Cinemeta Series Meta API.
    Format ID dihasilkan: ttXXXXXXX:season:episode (standard Stremio).
    """
    print(f"\n🎞️ [Modul 4] Mengekstrak Episod Siri TV Popular (Maksima {max_series_to_expand} Siri)...")
    
    # Singkirkan duplikasi siri TV berdasarkan root imdb_id
    unique_series_map = {}
    for s in series_list:
        s_id = s["imdb_id"]
        if s_id not in unique_series_map:
            unique_series_map[s_id] = s

    selected_series = list(unique_series_map.values())[:max_series_to_expand]
    episode_entries = []

    for idx, s_info in enumerate(selected_series, 1):
        root_id = s_info["imdb_id"]
        series_title = s_info["title"]
        series_year = s_info.get("year", "")
        
        meta_url = f"https://v3-cinemeta.strem.io/meta/series/{root_id}.json"
        try:
            res = requests.get(meta_url, timeout=9)
            if res.status_code == 200:
                meta = res.json().get("meta", {})
                videos = meta.get("videos", [])
                
                added_for_series = 0
                for v in videos:
                    season_num = v.get("season")
                    ep_num = v.get("number") or v.get("episode")
                    ep_title = v.get("name") or v.get("title") or f"Episod {ep_num}"
                    
                    if season_num is not None and ep_num is not None:
                        # Bina format Stremio ttXXXXXXX:season:episode
                        custom_ep_id = f"{root_id}:{int(season_num)}:{int(ep_num)}"
                        
                        episode_entries.append({
                            "imdb_id": custom_ep_id,
                            "series_id": root_id,
                            "title": series_title,
                            "episode_title": ep_title,
                            "season": int(season_num),
                            "episode": int(ep_num),
                            "year": series_year,
                            "type": "series",
                            "source": "cinemeta_episode_meta"
                        })
                        added_for_series += 1
                        
                        # Kawal had episod agar tidak memonopoli kolam
                        if added_for_series >= max_episodes_per_series:
                            break

                print(f"   [{idx:02d}/{len(selected_series):02d}] 📺 {series_title[:28]:28} ({root_id}) -> {added_for_series} episod diekstrak.")
            else:
                print(f"   [{idx:02d}/{len(selected_series):02d}] ❌ (HTTP {res.status_code}) -> {series_title}")
        except Exception:
            print(f"   [{idx:02d}/{len(selected_series):02d}] ⚠️ Gagal/Timeout mengekstrak episod -> {series_title}")

        time.sleep(0.05)

    return episode_entries

# ==============================================================================
# FUNGSI UTAMA HARVESTER & CANTUMAN KOLAM
# ==============================================================================
def run_movie_pool_harvest(
    start_year: int = 2026, 
    end_year: int = 1990, 
    max_series: int = 35
):
    """
    Melaksanakan keseluruhan proses penuaian kolam tajuk:
    1. Membaca kolam sedia ada dan menyingkirkan item siap diproses / tiada sub.
    2. Menyedut Cinemeta Standard, Cinemeta Mengikut Tahun, dan CyberFlix.
    3. Mengekstrak episod siri TV popular.
    4. Menggabungkan semua calon unik baharu dan menyimpan ke movie_pool.json.
    """
    print("=" * 80)
    print("🌾 MEMULAKAN PROSES PENUAIAN KOLAM TAJUK FILEM & SIRI (08_MOVIE_POOL_HARVESTER)")
    print(f"   ├─ Julat Tahun Sedutan : {start_year} hingga {end_year}")
    print(f"   ├─ Had Siri Diekstrak  : {max_series} siri drama")
    print(f"   └─ Lokasi Fail Kolam   : {MOVIE_POOL_FILE}")
    print("=" * 80)

    # 1. Baca dan bersihkan kolam semasa
    existing_pool = load_movie_pool()
    initial_count = len(existing_pool)
    print(f"📂 Kolam sedia ada sebelum pembersihan: {initial_count} tajuk.")

    cleaned_pool, pruned_count = prune_processed_items(existing_pool)
    print(f"🧹 Tajuk disaring keluar (Sudah diproses / Tiada sub): {pruned_count} tajuk.")
    print(f"📋 Baki calon sedia ada yang masih sah: {len(cleaned_pool)} tajuk.")

    # 2. Kutip dari semua sumber katalog
    all_raw_movies = []
    all_raw_series = []

    # Sumber 1: Cinemeta Standard
    cm_movies, cm_series = harvest_cinemeta_standard()
    all_raw_movies.extend(cm_movies)
    all_raw_series.extend(cm_series)

    # Sumber 2: Cinemeta Mengikut Tahun Terbitan
    year_movies = harvest_cinemeta_by_years(start_year=start_year, end_year=end_year)
    all_raw_movies.extend(year_movies)

    # Sumber 3: CyberFlix Platform Penstriman
    cb_movies, cb_series = harvest_cyberflix_catalogs()
    all_raw_movies.extend(cb_movies)
    all_raw_series.extend(cb_series)

    # Sumber 4: Episod Siri TV Popular
    episodes = expand_series_episodes(all_raw_series, max_series_to_expand=max_series)

    # 3. Kumpulkan semua calon dan singkirkan pertindihan
    no_subs_history = load_no_subs_history()
    newly_added = 0
    current_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())

    # Proses Calon Filem
    for m in all_raw_movies:
        mid = m["imdb_id"]
        if mid not in cleaned_pool and not _history.is_imdb_processed(mid) and mid not in no_subs_history:
            cleaned_pool[mid] = {
                "imdb_id": mid,
                "title": m["title"],
                "year": m.get("year", ""),
                "type": "movie",
                "source": m.get("source", "cinemeta"),
                "added_at": current_time_str
            }
            newly_added += 1

    # Proses Calon Episod Siri TV
    for ep in episodes:
        epid = ep["imdb_id"]
        if epid not in cleaned_pool and not _history.is_imdb_processed(epid) and epid not in no_subs_history:
            cleaned_pool[epid] = {
                "imdb_id": epid,
                "series_id": ep.get("series_id", ""),
                "title": ep["title"],
                "episode_title": ep.get("episode_title", ""),
                "season": ep.get("season"),
                "episode": ep.get("episode"),
                "year": ep.get("year", ""),
                "type": "series",
                "source": ep.get("source", "cinemeta_episode"),
                "added_at": current_time_str
            }
            newly_added += 1

    # Jika kolam masih kosong akibat ralat rangkaian luar, masukkan benih sandaran
    if not cleaned_pool:
        print("⚠️ Menggunakan benih sandaran (fallback seed) kerana kolam kosong.")
        for item in FALLBACK_SEED_LIST:
            fid = item["imdb_id"]
            if not _history.is_imdb_processed(fid) and fid not in no_subs_history:
                cleaned_pool[fid] = {
                    "imdb_id": fid,
                    "title": item["title"],
                    "year": item.get("year", ""),
                    "type": item.get("type", "movie"),
                    "source": "fallback_seed",
                    "added_at": current_time_str
                }
                newly_added += 1

    # 4. Simpan kolam ke fail fizikal JSON
    save_movie_pool(cleaned_pool)

    print("\n" + "=" * 80)
    print("✨ PENUAIAN KOLAM SELESAI")
    print(f"   ├─ Jumlah Asal Kolam         : {initial_count} tajuk")
    print(f"   ├─ Disaring Keluar (Pruned)  : {pruned_count} tajuk")
    print(f"   ├─ Tajuk Baharu Ditambah     : {newly_added} tajuk")
    print(f"   └─ Jumlah Akhir Kolam Aktif  : {len(cleaned_pool)} calon sedia dikikis")
    print("=" * 80)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Movie & Series Pool Harvester Runner")
    parser.add_argument("--start-year", type=int, default=2026, help="Tahun mula sedutan katalog")
    parser.add_argument("--end-year", type=int, default=1990, help="Tahun akhir sedutan katalog")
    parser.add_argument("--max-series", type=int, default=35, help="Jumlah had siri drama untuk diekstrak episodnya")

    args = parser.parse_args()
    run_movie_pool_harvest(
        start_year=args.start_year, 
        end_year=args.end_year, 
        max_series=args.max_series
    )