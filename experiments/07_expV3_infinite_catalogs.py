import sys
import os
import json
import time
import importlib
from pathlib import Path
from curl_cffi import requests

print("=" * 80)
print("🚀 EKSPERIMEN V3: INFINITE CATALOG POOL HARVESTER")
print("   Menguji: Sintaks 'genre & skip', Deep Pagination (50-500) & Genre Siri TV")
print("=" * 80)

# Tetapkan laluan folder projek
BASE_DIR = Path(__file__).resolve().parent.parent
ENGINE_DIR = BASE_DIR / "live_engine"
DATA_DIR = ENGINE_DIR / "data"
sys.path.append(str(ENGINE_DIR))

# 1. Semak rekod siap kikis
try:
    _history = importlib.import_module("04_history_tracker")
    history_data = _history.load_history()
    processed_ids = set(history_data.get("processed_ids", {}).keys())
except Exception:
    processed_ids = set()

# 2. Semak rekod tiada sarikata
no_subs_file = DATA_DIR / "no_subs_history.json"
no_subs_ids = set()
if no_subs_file.exists():
    try:
        with open(no_subs_file, "r", encoding="utf-8") as f:
            no_subs_ids = set(json.load(f).keys())
    except Exception:
        no_subs_ids = set()

REPORT_PATH = Path(__file__).resolve().parent / "infinite_catalogs_report.json"

# Senarai 16 genre popular filem dan siri TV
ALL_GENRES = [
    "Action", "Horror", "Sci-Fi", "Animation", "Comedy", "Thriller",
    "Crime", "Adventure", "Drama", "Romance", "Fantasy", "Mystery",
    "Family", "History", "War", "Western"
]

def build_infinite_targets() -> list[dict]:
    """
    Membina senarai ratusan endpoint katalog dengan sintaks yang sah:
    Format multi-parameter Cinemeta: /catalog/{type}/top/genre={GENRE}&skip={OFFSET}.json
    """
    targets = []

    # ==============================================================
    # 1. DEEP PAGINATION: TOP MOVIES UMUM (Laman 1 hingga 10 = Top 500)
    # ==============================================================
    targets.append({
        "category": "Top Movies",
        "name": "Top Movies (1 - 50)",
        "url": "https://v3-cinemeta.strem.io/catalog/movie/top.json",
        "type": "movie"
    })
    for skip in range(50, 500, 50):
        targets.append({
            "category": "Top Movies",
            "name": f"Top Movies ({skip + 1} - {skip + 50})",
            "url": f"https://v3-cinemeta.strem.io/catalog/movie/top/skip={skip}.json",
            "type": "movie"
        })

    # ==============================================================
    # 2. FILEM MENGIKUT GENRE (Setiap genre disedut Laman 1 - 4 = Top 200)
    # ==============================================================
    for genre in ALL_GENRES:
        # Laman 1 (1 - 50)
        targets.append({
            "category": f"Movie: {genre}",
            "name": f"{genre} Movie (1 - 50)",
            "url": f"https://v3-cinemeta.strem.io/catalog/movie/top/genre={genre}.json",
            "type": "movie"
        })
        # Laman 2 - 4 (51 - 200) guna sintaks 'genre={genre}&skip={skip}.json'
        for skip in [50, 100, 150]:
            targets.append({
                "category": f"Movie: {genre}",
                "name": f"{genre} Movie ({skip + 1} - {skip + 50})",
                "url": f"https://v3-cinemeta.strem.io/catalog/movie/top/genre={genre}&skip={skip}.json",
                "type": "movie"
            })

    # ==============================================================
    # 3. DEEP PAGINATION: SIRI TV UMUM (Top 250 Siri)
    # ==============================================================
    targets.append({
        "category": "Top Series",
        "name": "Top Series (1 - 50)",
        "url": "https://v3-cinemeta.strem.io/catalog/series/top.json",
        "type": "series"
    })
    for skip in range(50, 250, 50):
        targets.append({
            "category": "Top Series",
            "name": f"Top Series ({skip + 1} - {skip + 50})",
            "url": f"https://v3-cinemeta.strem.io/catalog/series/top/skip={skip}.json",
            "type": "series"
        })

    # ==============================================================
    # 4. SIRI TV MENGIKUT GENRE (Drama, Comedy, Crime, Sci-Fi, Animation)
    # ==============================================================
    series_genres = ["Drama", "Comedy", "Crime", "Sci-Fi", "Animation", "Action", "Mystery"]
    for genre in series_genres:
        targets.append({
            "category": f"Series: {genre}",
            "name": f"{genre} Series (1 - 50)",
            "url": f"https://v3-cinemeta.strem.io/catalog/series/top/genre={genre}.json",
            "type": "series"
        })
        for skip in [50, 100]:
            targets.append({
                "category": f"Series: {genre}",
                "name": f"{genre} Series ({skip + 1} - {skip + 50})",
                "url": f"https://v3-cinemeta.strem.io/catalog/series/top/genre={genre}&skip={skip}.json",
                "type": "series"
            })

    return targets

def fetch_catalog_items(target: dict) -> list[dict]:
    url = target["url"]
    try:
        res = requests.get(url, timeout=8)
        if res.status_code != 200:
            return None # Menandakan ralat HTTP (contoh: 404)

        data = res.json()
        metas = data.get("metas", [])
        
        parsed = []
        for item in metas:
            imdb_id = item.get("id", "").strip()
            title = item.get("name", "").strip()
            rel_info = str(item.get("releaseInfo", "")).strip()
            year = rel_info.split("–")[0].split("-")[0].strip()
            genres = item.get("genres", [])
            rating = item.get("imdbRating", "N/A")

            if imdb_id and imdb_id.startswith("tt") and title:
                parsed.append({
                    "imdb_id": imdb_id,
                    "title": title,
                    "year": year,
                    "rating": rating,
                    "genres": genres,
                    "type": target["type"],
                    "source": target["category"]
                })
        return parsed

    except Exception:
        return None

if __name__ == "__main__":
    targets = build_infinite_targets()
    all_candidates = {}
    success_endpoints = 0
    failed_endpoints = 0

    print(f"📊 Status Rekod Tempatan Sedia Ada:")
    print(f"   ├─ ✅ Sudah Berjaya di B2 : {len(processed_ids)} tajuk")
    print(f"   └─ ⚠️ Disahkan Tiada Sub   : {len(no_subs_ids)} tajuk")
    print(f"\n📡 Menguji {len(targets)} endpoint katalog berstruktur...\n")

    for idx, target in enumerate(targets, 1):
        items = fetch_catalog_items(target)
        
        if items is None:
            failed_endpoints += 1
            print(f"   [{idx:03d}/{len(targets):03d}] ❌ FAIL (404/Timeout) -> {target['name']}")
            continue

        success_endpoints += 1
        new_in_batch = 0
        for it in items:
            iid = it["imdb_id"]
            if iid not in all_candidates:
                all_candidates[iid] = it
                new_in_batch += 1

        print(f"   [{idx:03d}/{len(targets):03d}] ✅ {target['name']:32} -> Entri: {len(items):2d} | Unik Baru: {new_in_batch:2d}")
        time.sleep(0.08) # Sela pantas bagi mengelakkan rate limit

    # Pengelasan status calon
    already_in_b2 = []
    already_marked_no_subs = []
    ready_fresh_queue = []

    for iid, item in all_candidates.items():
        if iid in processed_ids:
            already_in_b2.append(item)
        elif iid in no_subs_ids:
            already_marked_no_subs.append(item)
        else:
            ready_fresh_queue.append(item)

    print("\n" + "=" * 80)
    print("📈 KEPUTUSAN UJIAN EKSPERIMEN V3:")
    print(f"   ├─ Jumlah Endpoint Berjaya Diuji         : {success_endpoints} / {len(targets)}")
    print(f"   ├─ Jumlah Endpoint Gagal                 : {failed_endpoints}")
    print(f"   ├─ Jumlah Keseluruhan Tajuk Unik Dikumpul: {len(all_candidates)} tajuk")
    print(f"   ├─ ✅ Sudah Tersimpan di B2 Storage       : {len(already_in_b2)} tajuk")
    print(f"   ├─ ⚠️ Pernah Disemak & Tiada Sarikata     : {len(already_marked_no_subs)} tajuk")
    print(f"   └─ 🆕 Calon Segar Siap Diproses (Fresh)   : {len(ready_fresh_queue)} tajuk")
    print("=" * 80)

    # Simpan laporan terperinci
    report_data = {
        "summary": {
            "total_endpoints_tested": len(targets),
            "successful_endpoints": success_endpoints,
            "total_unique_discovered": len(all_candidates),
            "already_in_b2": len(already_in_b2),
            "no_subs": len(already_marked_no_subs),
            "fresh_queue_count": len(ready_fresh_queue)
        },
        "fresh_queue": ready_fresh_queue
    }

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2, ensure_ascii=False)

    print(f"\n📄 Laporan penuh disimpan di: {REPORT_PATH}")
    print("=" * 80)