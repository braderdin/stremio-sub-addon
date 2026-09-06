import sys
import time
import argparse
import importlib

# Import modul secara dinamik bagi menyokong penamaan nombor awalan
_config = importlib.import_module("00_config")
_redis = importlib.import_module("01_redis_db")
_b2 = importlib.import_module("02_b2_storage")
_history = importlib.import_module("04_history_tracker")
_scraper = importlib.import_module("05_subtitle_scraper")

def run_ondemand_scrape(imdb_id: str, custom_query: str = None) -> bool:
    """
    Melaksanakan pengikisan terpantas bagi 1 IMDb ID spesifik dari pencetus Worker.
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
        session, ok = _scraper.create_stealth_session()
        if not ok:
            print("❌ Gagal melepasi tapisan permulaan Cloudflare WAF.")
            return False

        search_target = custom_query if custom_query else imdb_id
        movies = _scraper.search_subscene(session, search_target)
        if not movies:
            print(f"⚠️ Tiada padanan filem dijumpai di Subscene bagi query: {search_target}")
            return False

        uploaded_records = []

        # Meneliti 2 carian teratas yang paling relevan
        for movie in movies[:2]:
            subtitles = _scraper.get_movie_subtitles(session, movie["url"])
            
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

                time.sleep(0.5)

        # Kemas kini rekod history tempatan jika terdapat subtitle berjaya dimuat naik
        if uploaded_records:
            _history.add_processed_imdb(imdb_id, uploaded_records)
            print(f"✅ Berjaya memproses {len(uploaded_records)} subtitle untuk {imdb_id}.")
            return True
        else:
            print(f"⚠️ Tiada subtitle BM/ID ditemui untuk {imdb_id}.")
            return False

    finally:
        # Buka semula kunci di Redis selepas selesai
        _redis.remove_processing_lock(imdb_id)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="On-Demand Subtitle Scraper Runner")
    parser.add_argument("--imdb", type=str, required=True, help="Target IMDb ID (cth: tt7984734)")
    parser.add_argument("--query", type=str, default="", help="Kata kunci carian tajuk filem (pilihan)")
    
    args = parser.parse_args()
    run_ondemand_scrape(args.imdb, args.query)