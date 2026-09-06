import sys
import os
import json
from pathlib import Path

# Masukkan laluan live_engine & root ke sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LIVE_ENGINE_DIR = PROJECT_ROOT / "live_engine"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(LIVE_ENGINE_DIR))

import importlib

print("=" * 65)
print("🧪 UJIAN EKSPERIMEN PENGIKIS (experiments/05_exp_test_scraper.py)")
print("=" * 65)

# 1. Semakan Import Modul
print("\n[1] Memuatkan Modul '05_subtitle_scraper'...")
try:
    scraper = importlib.import_module("05_subtitle_scraper")
    print("    ✔ Modul berjaya diimport.")
except Exception as e:
    print(f"    ❌ Ralat import: {e}")
    sys.exit(1)

# 2. Ujian Sesi Impersonate / Bypass Cloudflare
print("\n[2] Menguji Sesi Stealth (Bypass Cloudflare)...")
session, ok = scraper.create_stealth_session()
print(f"    - Status Sesi: {'✔ HTTP 200 OK' if ok else '❌ GAGAL / SEKATAN CLOUDFLARE'}")

if not ok:
    print("    ❌ Tidak boleh meneruskan ujian tanpa sesi aktif.")
    sys.exit(1)

# 3. Ujian Carian Terperinci (Harry Potter - tt0241527)
test_imdb = "tt0241527"
print(f"\n[3] Menguji Carian Subscene untuk IMDb: {test_imdb}...")

# Ujian Respons Mentah HTTP
search_url = f"https://subscene.com/subtitles/searchbytitle?query={test_imdb}"
try:
    resp = session.get(search_url, timeout=15)
    print(f"    - Status HTTP Response: {resp.status_code}")
    print(f"    - Panjang Respons Body : {len(resp.text)} aksara")
    
    if "Cloudflare" in resp.text and "Just a moment..." in resp.text:
        print("    ⚠️ AMARAN: Terkena Cloudflare Challenge Page (Soft-Block)!")
except Exception as e:
    print(f"    ❌ Ralat permintaan HTTP: {e}")

# 4. Ujian Eksekusi Fungsi Dalam Modul
print("\n[4] Memanggil Fungsi Pengikis Terus...")
try:
    if hasattr(scraper, "search_subscene"):
        results = scraper.search_subscene(session, test_imdb)
        print(f"    - Hasil carian 'search_subscene': {len(results)} tajuk dijumpai.")
        for idx, item in enumerate(results, 1):
            print(f"      {idx}. {item}")
            
    if hasattr(scraper, "scrape_subtitles_for_imdb"):
        subs = scraper.scrape_subtitles_for_imdb(session, test_imdb)
        print(f"    - Jumlah sarikata dikikis: {len(subs)}")
        for idx, sub in enumerate(subs[:5], 1):
            print(f"      [{idx}] {sub.get('lang', 'N/A')} | {sub.get('title', 'N/A')} -> {sub.get('download_url', 'N/A')}")
    else:
        print("    ℹ️ Fungsi 'scrape_subtitles_for_imdb' tidak ditemui. Memeriksa fungsi alternatif...")
        
except Exception as e:
    print(f"    ❌ Ralat semasa eksekusi pengikis: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 65)
print("✨ UJIAN EKSPERIMEN SELESAI")
print("=" * 65)