import sys
import os
import json
import importlib
from pathlib import Path
from curl_cffi import requests

print("=" * 80)
print("🎬 UJIAN PENEROKAAN KATALOG RASMI CINEMETA STREMIO (07_expV1_cinemeta_catalogs.py)")
print("   Menguji: Top Movies, Pagination (skip), Series & Genre Filtering")
print("=" * 80)

# Import modul history tempatan untuk perbandingan
ENGINE_DIR = Path(__file__).resolve().parent.parent / "live_engine"
sys.path.append(str(ENGINE_DIR))

try:
    _history = importlib.import_module("04_history_tracker")
    history_data = _history.load_history()
    processed_ids = set(history_data.get("processed_ids", {}).keys())
except Exception:
    processed_ids = set()

REPORT_PATH = Path(__file__).resolve().parent / "cinemeta_catalogs_report.json"

# Senarai endpoint katalog Cinemeta yang berguna
CATALOG_TARGETS = [
    {
        "name": "Top Movies (Laman 1 / 100 Teratas)",
        "url": "https://v3-cinemeta.strem.io/catalog/movie/top.json",
        "type": "movie"
    },
    {
        "name": "Top Movies (Laman 2 / 100 Seterusnya)",
        "url": "https://v3-cinemeta.strem.io/catalog/movie/top/skip=100.json",
        "type": "movie"
    },
    {
        "name": "Top Action Movies",
        "url": "https://v3-cinemeta.strem.io/catalog/movie/top/genre=Action.json",
        "type": "movie"
    },
    {
        "name": "Top Horror & Thriller Movies",
        "url": "https://v3-cinemeta.strem.io/catalog/movie/top/genre=Horror.json",
        "type": "movie"
    },
    {
        "name": "Top Popular Series / Drama",
        "url": "https://v3-cinemeta.strem.io/catalog/series/top.json",
        "type": "series"
    }
]

def fetch_catalog(target: dict) -> list[dict]:
    url = target["url"]
    print(f"\n📡 Menghubungi: {target['name']}")
    print(f"   └ URL: {url}")
    
    try:
        res = requests.get(url, timeout=12)
        if res.status_code != 200:
            print(f"   ❌ HTTP Error: {res.status_code}")
            return []

        data = res.json()
        metas = data.get("metas", [])
        print(f"   ✅ Diterima: {len(metas)} entri.")
        
        parsed = []
        for item in metas:
            imdb_id = item.get("id", "")
            name = item.get("name", "")
            release_info = str(item.get("releaseInfo", "")).strip()
            # Ambil tahun pertama (cth: "2024" atau "2019-2023" -> "2019")
            year = release_info.split("–")[0].split("-")[0].strip()
            genres = item.get("genres", [])
            rating = item.get("imdbRating", "N/A")

            if imdb_id and name:
                parsed.append({
                    "imdb_id": imdb_id,
                    "title": name,
                    "year": year,
                    "rating": rating,
                    "genres": genres,
                    "type": target["type"]
                })
        return parsed

    except Exception as e:
        print(f"   ❌ Ralat sambungan: {e}")
        return []

if __name__ == "__main__":
    all_candidates = {}
    catalog_summary = {}

    print(f"\n📊 Rekod Tempatan: {len(processed_ids)} IMDb ID telah siap dikikis sebelum ini.")

    for target in CATALOG_TARGETS:
        items = fetch_catalog(target)
        catalog_summary[target["name"]] = len(items)
        
        for it in items:
            iid = it["imdb_id"]
            if iid not in all_candidates:
                all_candidates[iid] = it

    # Asingkan antara filem baharu vs yang sudah wujud di B2
    new_unscraped = []
    already_scraped = []

    for iid, item in all_candidates.items():
        if iid in processed_ids:
            already_scraped.append(item)
        else:
            new_unscraped.append(item)

    # Paparkan sampel struktur objek Cinemeta
    sample = list(all_candidates.values())[0] if all_candidates else {}
    print("\n" + "=" * 80)
    print("🔍 STRUKTUR METADATA CINEMETA (SAMPEL):")
    print(json.dumps(sample, indent=2))
    print("=" * 80)

    print("\n📈 KEPUTUSAN KESELURUHAN CARIAN KATALOG:")
    for cat_name, count in catalog_summary.items():
        print(f"   ├─ {cat_name:42}: {count} tajuk")
    print(f"   └─ Jumlah Tajuk Unik Dikumpul               : {len(all_candidates)} tajuk")

    print("\n🎯 STATUS GILIRAN CRON (QUEUE STATUS):")
    print(f"   ├─ ✅ Sudah sedia wujud di B2 / History     : {len(already_scraped)} tajuk")
    print(f"   └─ 🆕 Calon Segar Bersedia Untuk Dikikis    : {len(new_unscraped)} tajuk")

    print("\n📋 10 Calon Teratas Dalam Giliran Seterusnya:")
    for idx, item in enumerate(new_unscraped[:10]):
        genres_str = ", ".join(item['genres'][:2]) if item['genres'] else "General"
        print(f"   [{idx + 1:02d}] {item['imdb_id']} | {item['title']} ({item['year']}) | ⭐ {item['rating']} | {genres_str}")

    # Simpan laporan ke fail JSON
    report = {
        "total_unique_discovered": len(all_candidates),
        "already_scraped_count": len(already_scraped),
        "unscraped_queue_count": len(new_unscraped),
        "unscraped_queue": new_unscraped
    }
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 80)
    print(f"📄 Laporan giliran disimpan di: {REPORT_PATH}")
    print("=" * 80)