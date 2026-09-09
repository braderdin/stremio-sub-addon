import sys
import os
import json
import time
import importlib
from pathlib import Path
from curl_cffi import requests

print("=" * 80)
print("🧪 EKSPERIMEN V4: ADVANCED HARVESTING & EPISODE UNPACKER")
print("   Menguji: 1. Year-Based Discovery | 2. Streaming Catalogs | 3. Episode Unpacker")
print("=" * 80)

BASE_DIR = Path(__file__).resolve().parent.parent
ENGINE_DIR = BASE_DIR / "live_engine"
DATA_DIR = ENGINE_DIR / "data"
sys.path.append(str(ENGINE_DIR))

REPORT_PATH = Path(__file__).resolve().parent / "advanced_harvesting_report.json"

# ==============================================================================
# MODUL 1: UJIAN KATALOG BERASASKAN TAHUN (YEAR-BASED)
# ==============================================================================
def test_year_based_catalogs(years_to_test: list[int]) -> dict:
    print("\n" + "-" * 80)
    print("📅 [MODUL 1] MENGUJI PENAPIS TAHUN CINEMETA (YEAR-BASED)")
    print("-" * 80)
    
    year_results = {}
    
    for y in years_to_test:
        # Format parameter Cinemeta Stremio: genre=... atau year=...
        url = f"https://v3-cinemeta.strem.io/catalog/movie/top/year={y}.json"
        try:
            res = requests.get(url, timeout=8)
            if res.status_code == 200:
                metas = res.json().get("metas", [])
                valid_items = [
                    {"id": m.get("id"), "title": m.get("name"), "year": str(m.get("releaseInfo", "")).split("–")[0].strip()}
                    for m in metas if m.get("id", "").startswith("tt")
                ]
                year_results[str(y)] = {
                    "status": "SUCCESS",
                    "count": len(valid_items),
                    "sample": valid_items[:2]
                }
                print(f"   ✅ Tahun {y} -> Diterima: {len(valid_items):2d} tajuk (Contoh: {valid_items[0]['title'] if valid_items else 'N/A'})")
            else:
                year_results[str(y)] = {"status": f"HTTP_{res.status_code}", "count": 0}
                print(f"   ❌ Tahun {y} -> Gagal (HTTP {res.status_code})")
        except Exception as e:
            year_results[str(y)] = {"status": f"ERROR: {str(e)}", "count": 0}
            print(f"   ⚠️ Tahun {y} -> Ralat Sambungan: {e}")
            
        time.sleep(0.1)
        
    return year_results

# ==============================================================================
# MODUL 2: UJIAN KATALOG PLATFORM PENSTRIMAN (STREAMING PROVIDERS)
# ==============================================================================
def test_streaming_provider_catalogs() -> dict:
    print("\n" + "-" * 80)
    print("📺 [MODUL 2] MENGUJI KATALOG PLATFORM PENSTRIMAN (CYBERFLIX / TMDB STREMIO)")
    print("-" * 80)

    # Senarai cermin katalog platform penstriman popular yang mematuhi protokol Stremio v3
    test_endpoints = [
        {
            "provider": "CyberFlix / Netflix Movies",
            "url": "https://cyberflix.elfhosted.com/catalog/movie/cyberflix-netflix.json"
        },
        {
            "provider": "CyberFlix / Disney+ Movies",
            "url": "https://cyberflix.elfhosted.com/catalog/movie/cyberflix-disney.json"
        },
        {
            "provider": "CyberFlix / Apple TV+ Movies",
            "url": "https://cyberflix.elfhosted.com/catalog/movie/cyberflix-apple.json"
        },
        {
            "provider": "Stremio Default Public Domain Movies",
            "url": "https://v3-cinemeta.strem.io/catalog/movie/top/genre=History.json"
        }
    ]

    provider_results = {}

    for ep in test_endpoints:
        name = ep["provider"]
        url = ep["url"]
        try:
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                metas = res.json().get("metas", [])
                valid_items = [
                    {"id": m.get("id"), "title": m.get("name"), "year": str(m.get("releaseInfo", "")).split("–")[0].strip()}
                    for m in metas if m.get("id", "").startswith("tt")
                ]
                provider_results[name] = {
                    "status": "SUCCESS",
                    "count": len(valid_items),
                    "sample": valid_items[:2]
                }
                print(f"   ✅ {name:32} -> Entri: {len(valid_items):2d} tajuk unik.")
            else:
                provider_results[name] = {"status": f"HTTP_{res.status_code}", "count": 0}
                print(f"   ❌ {name:32} -> Gagal (HTTP {res.status_code})")
        except Exception as e:
            provider_results[name] = {"status": f"TIMEOUT / OFFLINE", "count": 0}
            print(f"   ⚠️ {name:32} -> Sambungan tergendala (dilangkau).")

        time.sleep(0.1)

    return provider_results

# ==============================================================================
# MODUL 3: PENYAHKOD EPISOD SIRI TV (EPISODE UNPACKER)
# ==============================================================================
def test_series_episode_unpacker(series_imdb_list: list[dict]) -> dict:
    print("\n" + "-" * 80)
    print("🎬 [MODUL 3] MENGUJI PENYAHKOD EPISOD SIRI TV (tt...:season:episode)")
    print("-" * 80)

    unpacked_results = {}

    for s in series_imdb_list:
        imdb_id = s["imdb_id"]
        title = s["title"]
        meta_url = f"https://v3-cinemeta.strem.io/meta/series/{imdb_id}.json"

        try:
            res = requests.get(meta_url, timeout=10)
            if res.status_code == 200:
                meta = res.json().get("meta", {})
                videos = meta.get("videos", [])
                
                episodes = []
                for v in videos:
                    ep_id = v.get("id") # Format: "tt0903747:1:1"
                    season = v.get("season")
                    episode = v.get("episode")
                    ep_name = v.get("name", f"Episode {episode}")
                    
                    if ep_id and season is not None and episode is not None:
                        episodes.append({
                            "episode_id": ep_id,
                            "season": season,
                            "episode": episode,
                            "name": ep_name
                        })

                unpacked_results[imdb_id] = {
                    "title": title,
                    "total_episodes": len(episodes),
                    "sample_ids": [e["episode_id"] for e in episodes[:5]]
                }

                print(f"   📺 {title} ({imdb_id})")
                print(f"      ├─ Jumlah Episod Terbongkar : {len(episodes)} episod")
                print(f"      └─ Format ID Stremio        : {', '.join([e['episode_id'] for e in episodes[:3]])} ...")
            else:
                print(f"   ❌ Gagal memuat turun meta bagi {title} (HTTP {res.status_code})")
        except Exception as e:
            print(f"   ⚠️ Ralat menyedut meta {title}: {e}")

        time.sleep(0.1)

    return unpacked_results

# ==============================================================================
# PELAKSANAAN KESELURUHAN & PENJANAAN LAPORAN
# ==============================================================================
if __name__ == "__main__":
    # 1. Uji tahun ke belakang
    test_years = [2024, 2023, 2022, 2021, 2020, 2018, 2015, 2010, 2005, 2000, 1995]
    year_data = test_year_based_catalogs(test_years)

    # 2. Uji platform penstriman
    provider_data = test_streaming_provider_catalogs()

    # 3. Uji pembongkar episod bagi siri TV terkemuka
    test_series = [
        {"imdb_id": "tt0903747", "title": "Breaking Bad"},
        {"imdb_id": "tt0944947", "title": "Game of Thrones"},
        {"imdb_id": "tt14688458", "title": "Silo"}
    ]
    unpacker_data = test_series_episode_unpacker(test_series)

    # Kumpul dan simpan laporan
    full_report = {
        "year_based_discovery": year_data,
        "streaming_provider_catalogs": provider_data,
        "tv_series_episode_unpacker": unpacker_data
    }

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(full_report, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 80)
    print("✨ UJIAN EKSPERIMEN V4 SELESAI")
    print(f"📄 Laporan struktur data penuh disimpan di: {REPORT_PATH}")
    print("=" * 80)