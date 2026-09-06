import sys
import time
import argparse
import importlib

_config = importlib.import_module("00_config")
_redis = importlib.import_module("01_redis_db")
_b2 = importlib.import_module("02_b2_storage")
_history = importlib.import_module("04_history_tracker")
_scraper = importlib.import_module("05_subtitle_scraper")
_ondemand = importlib.import_module("06_ondemand_runner")

# Senarai benih tajuk popular/trending asas untuk pengikisan cron berjadual
DEFAULT_SEED_LIST = [
    "Avatar", "Avengers", "Spider-Man", "Batman", "Harry Potter",
    "Fast and Furious", "John Wick", "Transformers", "Jurassic World",
    "Resident Evil", "The Matrix", "Munafik", "Dukun", "Ejen Ali",
    "Oppenheimer", "Barbie", "Dune", "Deadpool", "Gladiator", "Interstellar"
]

def run_batch_cron_scrape(target_limit: int = 50, delay_sec: float = 1.0):
    """
    Melaksanakan pengikisan secara kelompok bagi senarai tajuk sasaran.
    """
    print(f"🔄 Mula tugas Cron Batch Scraper (Sasaran Limit: {target_limit} tajuk)")
    
    session, ok = _scraper.create_stealth_session()
    if not ok:
        print("❌ Gagal melepasi tapisan permulaan Cloudflare WAF. Cron dibatalkan.")
        return

    processed_count = 0

    for query in DEFAULT_SEED_LIST:
        if processed_count >= target_limit:
            print(f"🎯 Sasaran kelompok {target_limit} tajuk telah dicapai sepenuhnya.")
            break

        print(f"\n🔎 [Cron Search] Meneliti tajuk: {query}")
        movies = _scraper.search_subscene(session, query)
        
        if not movies:
            time.sleep(delay_sec)
            continue

        for movie in movies[:2]:
            if processed_count >= target_limit:
                break

            imdb_id_candidate = movie["id"]
            
            # Abaikan jika filem ini sudah wujud dalam history.json
            if _history.is_imdb_processed(imdb_id_candidate):
                continue

            print(f" 📦 Memproses kelompok untuk ID: {imdb_id_candidate} ({movie['title']})")
            success = _ondemand.run_ondemand_scrape(imdb_id_candidate, custom_query=movie['title'])
            
            if success:
                processed_count += 1

            time.sleep(delay_sec)

    print(f"\n✨ Selesai tugasan Cron Batch. Jumlah tajuk baharu berjaya diproses: {processed_count}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cron Batch Subtitle Scraper Runner")
    parser.add_argument("--limit", type=int, default=50, help="Jumlah had tajuk diproses per pusingan")
    parser.add_argument("--delay", type=float, default=1.0, help="Sela masa permintaan (saat)")
    
    args = parser.parse_args()
    run_batch_cron_scrape(target_limit=args.limit, delay_sec=args.delay)