import sys
import os
import json
import importlib
from pathlib import Path

print("=" * 60)
print("🔍 UJIAN DIAGNOSTIK KESELURUHAN ENJIN (08_test_all_components.py)")
print("=" * 60)

# 1. Ujian Import Modul Dinamik
print("\n[1/6] Menguji Import Modul Enjin...")
modules = {}
module_names = [
    "00_config",
    "01_redis_db",
    "02_b2_storage",
    "03_zip_extractor",
    "04_history_tracker",
    "05_subtitle_scraper",
    "06_ondemand_runner",
    "07_cron_batch_runner"
]

for name in module_names:
    try:
        modules[name] = importlib.import_module(name)
        print(f"  ✔ Modul '{name}' berjaya diimport.")
    except Exception as e:
        print(f"  ❌ GAGAL mengimport '{name}': {e}")
        sys.exit(1)

# 2. Ujian Pembolehubah Persekitaran (ENV / Secrets)
print("\n[2/6] Memeriksa Kunci Persekitaran (ENV / Secrets)...")
config = modules["00_config"]
print(f"  - ADDON_SECRET_TOKEN : {'OK' if config.ADDON_SECRET_TOKEN else '⚠️ KOSONG'}")
print(f"  - REDIS REST URL     : {'OK' if config.UPSTASH_REDIS_REST_URL else '⚠️ KOSONG'}")
print(f"  - Bilangan Akaun B2  : {len(config.B2_ACCOUNTS)} / 5 Akaun Dikesan")

if len(config.B2_ACCOUNTS) == 0:
    print("  ❌ GAGAL: Tiada akaun B2 dikesan dalam pembolehubah persekitaran!")

# 3. Ujian Sambungan Upstash Redis REST
print("\n[3/6] Menguji Sambungan Upstash Redis...")
redis_mod = modules["01_redis_db"]
test_imdb = "tt0000000_test"

try:
    lock_ok = redis_mod.set_processing_lock(test_imdb, ttl_seconds=10)
    print(f"  - Ujian Menetap Kunci Lock Redis : {'✔ BERJAYA' if lock_ok else '❌ GAGAL'}")
    
    unlock_ok = redis_mod.remove_processing_lock(test_imdb)
    print(f"  - Ujian Memadam Kunci Lock Redis: {'✔ BERJAYA' if unlock_ok else '❌ GAGAL'}")
except Exception as e:
    print(f"  ❌ Ralat Ujian Redis: {e}")

# 4. Ujian Authentication & Semakan Bucket Backblaze B2
print("\n[4/6] Menguji Sambungan 5 Akaun Backblaze B2...")
b2_mod = modules["02_b2_storage"]

for acc in config.B2_ACCOUNTS:
    try:
        used_bytes = b2_mod.check_bucket_used_bytes(acc)
        used_mb = used_bytes / (1024 * 1024)
        print(f"  ✔ [Acc {acc['index']}] {acc['bucket_name']} -> Penggunaan: {used_mb:.2f} MB / 9500 MB")
    except Exception as e:
        print(f"  ❌ [Acc {acc['index']}] GAGAL disambung: {e}")

# 5. Ujian Pengikis Subscene & Bypass Cloudflare
print("\n[5/6] Menguji Pengikis Subscene & Pintu Masuk Cloudflare...")
scraper_mod = modules["05_subtitle_scraper"]

session, stealth_ok = scraper_mod.create_stealth_session()
print(f"  - Sesi Pelayar Impersonate Chrome : {'✔ BERJAYA (HTTP 200)' if stealth_ok else '❌ DISEKAT CLOUDFLARE'}")

if stealth_ok:
    test_query = "tt0241527" # Harry Potter
    print(f"  - Menguji carian filem bagi ID: {test_query}...")
    results = scraper_mod.search_subscene(session, test_query)
    print(f"  ✔ Hasil carian dijumpai: {len(results)} tajuk filem")

# 6. Ujian Fail Sejarah Tempatan (scraped_history.json)
print("\n[6/6] Memeriksa Fail Sejarah Tempatan...")
history_mod = modules["04_history_tracker"]
hist_data = history_mod.load_history()
print(f"  ✔ Rekod Sejarah Sedia Ada: {hist_data.get('total_items', 0)} IMDb ID tersimpan.")

print("\n" + "=" * 60)
print("✨ UJIAN DIAGNOSTIK SELESAI!")
print("=" * 60)