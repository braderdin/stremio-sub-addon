import sys
import os
import json
import time
import importlib
from pathlib import Path
from curl_cffi import requests

print("=" * 80)
print("🎬 UJIAN PENEROKAAN PELBAGAI KATALOG (07_expV2_multi_catalogs.py)")
print("   Menguji: Top 200 Per Genre IMDb/Cinemeta + ThePirateBay + Siri TV")
print("=" * 80)

# Import modul tracker & data tempatan
BASE_DIR = Path(__file__).resolve().parent.parent
ENGINE_DIR = BASE_DIR / "live_engine"
DATA_DIR = ENGINE_DIR / "data"
sys.path.append(str(ENGINE_DIR))

# 1. Semak rekod siap kikis (B2)
try:
    _history = importlib.import_module("04_history_tracker")
    history_data = _history.load_history()
    processed_ids = set(history_data.get("processed_ids", {}).keys())
except Exception:
    processed_ids = set()

# 2. Semak rekod yang disahkan tiada sub BM/ID
no_subs_file = DATA_DIR / "no_subs_history.json"
no_subs_ids = set()
if no_subs_file.exists():
    try:
        with open(no_subs_file, "r", encoding="utf-8") as f:
            no_subs_ids = set(json.load(f).keys())
    except Exception:
        no_subs_ids = set()

# Output JSON baharu khusus untuk Exp V2
REPORT_PATH = Path(__file__).resolve().parent / "multi_catalogs_report.json"

# Senarai genre popular untuk filem (IMDb / Cinemeta)
TOP_GENRES = [
    "Action",
    "Horror",
    "Sci-Fi",
    "Animation",
    "Comedy",
    "Thriller",
    "Crime",
    "Adventure",
    "Drama",
    "Romance",
    "Fantasy",
    "Mystery"
]

def build_catalog_targets() -> list[dict]:
    """Membina senarai sasaran katalog komprehensif (Top 200 setiap genre + TPB)."""
    targets = []

    # 1. Filem Popular Umum IMDb (Top 300)
    targets.append({
        "source": "IMDb / Cinemeta",
        "name": "Top Movies (1 - 100)",
        "url": "https://v3-cinemeta.strem.io/catalog/movie/top.json",
        "type": "movie"
    })
    targets.append({
        "source": "IMDb / Cinemeta",
        "name": "Top Movies (101 - 200)",
        "url": "https://v3-cinemeta.strem.io/catalog/movie/top/skip=100.json",
        "type": "movie"
    })
    targets.append({
        "source": "IMDb / Cinemeta",
        "name": "Top Movies (201 - 300)",
        "url": "https://v3-cinemeta.strem.io/catalog/movie/top/skip=200.json",
        "type": "movie"
    })

    # 2. Top 200 Filem Setiap Genre Terbaik IMDb
    for genre in TOP_GENRES:
        # Laman 1: 1 - 100
        targets.append({
            "source": f"IMDb Genre: {genre}",
            "name": f"Top 100 {genre}",
            "url": f"https://v3-cinemeta.strem.io/catalog/movie/top/genre={genre}.json",
            "type": "movie"
        })
        # Laman 2: 101 - 200
        targets.append({
            "source": f"IMDb Genre: {genre}",
            "name": f"Top 101-200 {genre}",
            "url": f"https://v3-cinemeta.strem.io/catalog/movie/top/genre={genre}/skip=100.json",
            "type": "movie"
        })

    # 3. Siri TV Popular IMDb (Top 200)
    targets.append({
        "source": "IMDb Series",
        "name": "Top Series (1 - 100)",
        "url": "https://v3-cinemeta.strem.io/catalog/series/top.json",
        "type": "series"
    })
    targets.append({
        "source": "IMDb Series",
        "name": "Top Series (101 - 200)",
        "url": "https://v3-cinemeta.strem.io/catalog/series/top/skip=100.json",
        "type": "series"
    })

    # 4. ThePirateBay Catalog Addon (Stremio Official Community Mirror)
    targets.append({
        "source": "ThePirateBay",
        "name": "TPB Top Movies",
        "url": "https://piratebay-catalog.strem.fun/catalog/movie/top.json",
        "type": "movie"
    })
    targets.append({
        "source": "ThePirateBay",
        "name": "TPB Top TV Series",
        "url": "https://piratebay-catalog.strem.fun/catalog/series/top.json",
        "type": "series"
    })

    return targets

def fetch_catalog_items(target: dict) -> list[dict]:
    url = target["url"]
    source = target["source"]
    name = target["name"]
    
    try:
        res = requests.get(url, timeout=10)
        if res.status_code != 200:
            return []

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

            # Pastikan format IMDb ID sah (bermula dengan 'tt')
            if imdb_id and imdb_id.startswith("tt") and title:
                parsed.append({
                    "imdb_id": imdb_id,
                    "title": title,
                    "year": year,
                    "rating": rating,
                    "genres": genres,
                    "type": target["type"],
                    "source": source
                })
        return parsed

    except Exception:
        # Jika host TPB atau network tergendala, langkau secara senyap
        return []

if __name__ == "__main__":
    targets = build_catalog_targets()
    all_candidates = {}
    source_stats = {}

    print(f"📊 Rekod Sejarah Tempatan:")
    print(f"   ├─ ✅ Sudah Selesai di B2         : {len(processed_ids)} tajuk")
    print(f"   └─ ⚠️ Pernah Semak & Tiada Sub BM : {len(no_subs_ids)} tajuk")
    print(f"\n🚀 Memulakan sedutan daripada {len(targets)} endpoint katalog...")

    for idx, target in enumerate(targets, 1):
        items = fetch_catalog_items(target)
        source_name = target["source"]
        source_stats[source_name] = source_stats.get(source_name, 0) + len(items)

        new_in_batch = 0
        for it in items:
            iid = it["imdb_id"]
            if iid not in all_candidates:
                all_candidates[iid] = it
                new_in_batch += 1

        print(f"   [{idx:02d}/{len(targets):02d}] {target['name']:32} -> Diterima: {len(items):3d} | Unik Baru: {new_in_batch:3d}")
        time.sleep(0.15)

    # Kategorikan status setiap calon
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
    print("📈 KEPUTUSAN KESELURUHAN PENGUMPULAN KATALOG:")
    print(f"   ├─ Jumlah Keseluruhan Tajuk Unik Dijumpai : {len(all_candidates)} tajuk")
    print(f"   ├─ ✅ Sudah Tersimpan di B2 Storage        : {len(already_in_b2)} tajuk")
    print(f"   ├─ ⚠️ Disahkan Tiada Sarikata (Skip)       : {len(already_marked_no_subs)} tajuk")
    print(f"   └─ 🆕 Calon Segar Siap Diproses (Fresh)    : {len(ready_fresh_queue)} tajuk")
    print("=" * 80)

    print("\n📋 Sampel 10 Calon Segar Teratas Untuk Sesi Cron:")
    for idx, item in enumerate(ready_fresh_queue[:10], 1):
        g_str = ", ".join(item['genres'][:2]) if item['genres'] else "General"
        print(f"   [{idx:02d}] {item['imdb_id']} | {item['title']} ({item['year']}) | ⭐ {item['rating']} | {g_str} ({item['source']})")

    # Simpan hasil analisis ke multi_catalogs_report.json
    report_data = {
        "summary": {
            "total_unique_discovered": len(all_candidates),
            "already_in_b2_count": len(already_in_b2),
            "no_subs_history_count": len(already_marked_no_subs),
            "fresh_unscraped_queue_count": len(ready_fresh_queue)
        },
        "source_breakdown": source_stats,
        "fresh_queue": ready_fresh_queue
    }

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 80)
    print(f"📄 Laporan penuh telah disimpan di: {REPORT_PATH}")
    print("=" * 80)