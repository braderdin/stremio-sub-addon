import sys
import time
import argparse
import importlib

# Import modul secara dinamik
_config = importlib.import_module("00_config")
_redis = importlib.import_module("01_redis_db")
_b2 = importlib.import_module("02_b2_storage")
_history = importlib.import_module("04_history_tracker")
_scraper = importlib.import_module("05_subtitle_scraper")
_ondemand = importlib.import_module("06_ondemand_runner")

# Senarai benih filem popular dengan IMDb ID rasmi bagi mengelakkan carian tersasar
DEFAULT_SEED_LIST = [
    {"imdb_id": "tt0145487", "title": "Spider-Man", "year": "2002"},
    {"imdb_id": "tt0499549", "title": "Avatar", "year": "2009"},
    {"imdb_id": "tt0848228", "title": "The Avengers", "year": "2012"},
    {"imdb_id": "tt0372784", "title": "Batman Begins", "year": "2005"},
    {"imdb_id": "tt0241527", "title": "Harry Potter and the Sorcerer's Stone", "year": "2001"},
    {"imdb_id": "tt0232500", "title": "The Fast and the Furious", "year": "2001"},
    {"imdb_id": "tt2911666", "title": "John Wick", "year": "2014"},
    {"imdb_id": "tt0418279", "title": "Transformers", "year": "2007"},
    {"imdb_id": "tt0369610", "title": "Jurassic World", "year": "2015"},
    {"imdb_id": "tt0133093", "title": "The Matrix", "year": "1999"},
    {"imdb_id": "tt5952138", "title": "Munafik", "year": "2016"},
    {"imdb_id": "tt0466342", "title": "Dukun", "year": "2018"},
    {"imdb_id": "tt11032374", "title": "Ejen Ali: The Movie", "year": "2019"},
    {"imdb_id": "tt15398776", "title": "Oppenheimer", "year": "2023"},
    {"imdb_id": "tt1517268", "title": "Barbie", "year": "2023"},
    {"imdb_id": "tt1160419", "title": "Dune", "year": "2021"},
    {"imdb_id": "tt1431045", "title": "Deadpool", "year": "2016"},
    {"imdb_id": "tt0172495", "title": "Gladiator", "year": "2000"},
    {"imdb_id": "tt0816692", "title": "Interstellar", "year": "2014"},
    {"imdb_id": "tt6263850", "title": "Deadpool & Wolverine", "year": "2024"}
]

def run_batch_cron_scrape(target_limit: int = 50, delay_sec: float = 1.0):
    """
    Melaksanakan pengikisan kelompok berjadual secara modular melalui on-demand runner.
    """
    print(f"🔄 Mula tugas Cron Batch Scraper (Had Sasaran: {target_limit} tajuk)")
    processed_count = 0

    for item in DEFAULT_SEED_LIST:
        if processed_count >= target_limit:
            print(f"🎯 Had sasaran kelompok ({target_limit} tajuk) telah dicapai.")
            break

        imdb_id = item["imdb_id"]
        movie_title = item["title"]
        movie_year = item.get("year", "")

        # Semak rekod tempatan, abaikan jika sudah pernah dikikis
        if _history.is_imdb_processed(imdb_id):
            continue

        print(f"\n📦 [Cron Queue] Memproses: {movie_title} ({movie_year}) -> {imdb_id}")
        
        # Panggil terus enjin on-demand yang lengkap dengan Cinemeta & Smart Scorer
        success = _ondemand.run_ondemand_scrape(imdb_id, custom_query=movie_title)
        
        if success:
            processed_count += 1

        time.sleep(delay_sec)

    print(f"\n✨ Selesai tugasan Cron Batch. Tajuk baharu berjaya diproses: {processed_count}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cron Batch Subtitle Scraper Runner")
    parser.add_argument("--limit", type=int, default=50, help="Jumlah had tajuk diproses per pusingan")
    parser.add_argument("--delay", type=float, default=1.0, help="Sela masa permintaan antara filem (saat)")
    
    args = parser.parse_args()
    run_batch_cron_scrape(target_limit=args.limit, delay_sec=args.delay)