import sys
import time
import argparse
import importlib
from curl_cffi import requests

# Import modul secara dinamik bagi menyokong penamaan nombor awalan
_config = importlib.import_module("00_config")
_redis = importlib.import_module("01_redis_db")
_b2 = importlib.import_module("02_b2_storage")
_history = importlib.import_module("04_history_tracker")
_scraper = importlib.import_module("05_subtitle_scraper")

def resolve_cinemeta_metadata(imdb_id: str) -> tuple[str, str]:
    """
    Mendapatkan tajuk rasmi dan tahun keluaran filem daripada Cinemeta API.
    """
    url = f"https://v3-cinemeta.strem.io/meta/movie/{imdb_id}.json"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            meta = res.json().get("meta", {})
            return meta.get("name", ""), str(meta.get("year", ""))
    except Exception as e:
        print(f"⚠️ Ralat resolusi Cinemeta API ({imdb_id}): {e}")
    return "", ""

def run_ondemand_scrape(imdb_id: str, custom_query: str = None) -> bool:
    """
    Melaksanakan pengikisan terpantas bagi 1 IMDb ID spesifik yang dicetuskan oleh Worker.
    """
    print(f"🚀 Mula pengikisan On-Demand bagi IMDb ID: {imdb_id}")

    # Semak jika IMDb ID ini sudah wujud dalam rekod tempatan
    if _history.is_imdb_processed(imdb_id):
        print(f"ℹ️ IMDb ID {imdb_id} telah siap diproses sebelum ini. Pembatalan dilakukan.")
        return True

    # Kunci IMDb ID di Redis untuk mengelakkan proses bertindih
    if not _redis.set_processing_lock(imdb_id, ttl_seconds=600):
        print(f"⚠️ IMDb ID {imdb_id} sedang dikikis oleh runner lain. Menghentikan tugasan.")
        return False

    try:
        # Resolusi tajuk dan tahun filem melalui Cinemeta
        movie_title, movie_year = resolve_cinemeta_metadata(imdb_id)
        search_query = custom_query if custom_query else movie_title

        if not search_query:
            print(f"❌ Gagal mendapatkan tajuk bagi IMDb ID: {imdb_id}. Carian dibatalkan.")
            return False

        print(f"🔎 Carian Subscene: '{search_query}' (Tahun: {movie_year or 'N/A'})")

        # Carian selamat dengan Turnstile bypass & Smart Scoring
        movies, session = _scraper.search_subscene(search_query, year=movie_year, top_k=1)
        if not movies or not session:
            print(f"⚠️ Tiada padanan filem yang tepat dijumpai untuk: '{search_query}'")
            return False

        target_movie = movies[0]
        print(f"🎯 Filem Dipilih: {target_movie['title']} -> {target_movie['url']}")

        # Ambil senarai sarikata BM/ID
        subtitles = _scraper.get_movie_subtitles(session, target_movie["url"])
        print(f"  └ Menemui {len(subtitles)} sarikata Bahasa Melayu / Indonesia.")

        if not subtitles:
            print(f"⚠️ Tiada rekod sarikata BM/ID pada laman filem ini.")
            return False

        uploaded_records = []

        # Muat turun dan simpan fail sarikata
        for sub in subtitles:
            srt_files = _scraper.download_and_extract_subtitles(session, sub["detail_url"])
            
            for idx, srt_item in enumerate(srt_files):
                b2_filename = f"subs/{imdb_id}/{sub['lang']}_{sub['sub_id']}_{idx}.srt"
                
                try:
                    b2_res = _b2.upload_subtitle_to_b2(b2_filename, srt_item["content"])
                    
                    record = {
                        "id": f"{sub['sub_id']}_{idx}",
                        "lang": sub["lang"],
                        "url": b2_res["url"],
                        "release": sub["release"],
                        "source": "subscene",
                        "acc": b2_res["account_index"]
                    }
                    
                    # Simpan ke Upstash Redis
                    _redis.save_subtitle_record(imdb_id, record)
                    uploaded_records.append(record)
                    print(f"  ✔ [B2 Acc {b2_res['account_index']}] Muat naik berjaya: {b2_res['url']}")
                    
                except Exception as e:
                    print(f"  ❌ Ralat muat naik B2: {e}")

            time.sleep(0.3)

        # Simpan ke history tempatan sekiranya berjaya
        if uploaded_records:
            _history.add_processed_imdb(imdb_id, uploaded_records)
            print(f"✅ Berjaya memproses dan menyimpan {len(uploaded_records)} sarikata untuk {imdb_id}.")
            return True
        else:
            print(f"⚠️ Tiada fail sarikata berjaya diekstrak untuk {imdb_id}.")
            return False

    finally:
        # Buka semula kunci Redis selepas selesai proses
        _redis.remove_processing_lock(imdb_id)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="On-Demand Subtitle Scraper Runner")
    parser.add_argument("--imdb", type=str, required=True, help="Target IMDb ID (cth: tt0145487)")
    parser.add_argument("--query", type=str, default="", help="Kata kunci carian tajuk filem (pilihan)")
    
    args = parser.parse_args()
    run_ondemand_scrape(args.imdb, args.query)