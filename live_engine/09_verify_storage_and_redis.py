import sys
import os
import json
import argparse
import importlib
from pathlib import Path
from curl_cffi import requests

print("=" * 80)
print("🔍 DIAGNOSTIK INTEGRITI STORAGE B2 & UPSTASH REDIS (09_verify_storage_and_redis.py)")
print("=" * 80)

_config = importlib.import_module("00_config")
_redis = importlib.import_module("01_redis_db")
_b2 = importlib.import_module("02_b2_storage")

def inspect_redis(imdb_id: str):
    print(f"\n[1] Memeriksa Upstash Redis bagi IMDb ID: {imdb_id}")
    records = _redis.get_subtitle_records(imdb_id)
    
    if not records:
        print(f"  ❌ Tiada rekod dijumpai dalam Redis untuk kunci 'sub:{imdb_id}'.")
        # Semak jika lock sedang aktif
        lock_res = _redis._redis_request("GET", f"lock:{imdb_id}")
        if lock_res and lock_res.get("result"):
            print(f"  ⚠️ Status Kunci: 'lock:{imdb_id}' sedang AKTIF.")
        return []

    print(f"  ✅ Ditemui {len(records)} rekod sarikata dalam Redis!")
    print("\n  📋 Senarai 5 Rekod Teratas:")
    for idx, r in enumerate(records[:5]):
        print(f"      [{idx + 1}] ID: {r.get('id')} | Bahasa: {r.get('lang')} | Acc B2: {r.get('acc')}")
        print(f"          Pautan: {r.get('url')}")
        print(f"          Versi : {r.get('release')}")
    
    return records

def inspect_b2_storage(imdb_id: str, sample_records: list):
    print(f"\n[2] Memeriksa Status Storan Backblaze B2...")
    accounts = _config.B2_ACCOUNTS
    print(f"  └ Jumlah Akaun B2 Dikesan: {len(accounts)}")

    for acc in accounts:
        try:
            used_bytes = _b2.check_bucket_used_bytes(acc)
            used_mb = used_bytes / (1024 * 1024)
            print(f"      ├─ [Akaun {acc['index']}] Bucket: {acc['bucket_name']} | Penggunaan: {used_mb:.2f} MB")
        except Exception as e:
            print(f"      ├─ [Akaun {acc['index']}] Ralat membaca bucket: {e}")

    # Uji kebolehcapaian URL awam bagi sarikata pertama
    if sample_records:
        target_url = sample_records[0].get("url")
        print(f"\n[3] Menguji Kebolehcapaian URL Awam Sarikata...")
        print(f"  └ Menguji URL: {target_url}")
        try:
            res = requests.get(target_url, timeout=10)
            if res.status_code == 200 and len(res.text) > 50:
                print(f"  ✅ URL B2 Sah & Boleh Diakses! Saiz kandungan: {len(res.text)} aksara.")
                snippet = [line.strip() for line in res.text.splitlines() if line.strip()][:4]
                print(f"  📝 Pratonton Teks SRT: {' // '.join(snippet)}")
            else:
                print(f"  ❌ Gagal memuat turun dari URL B2. Status HTTP: {res.status_code}")
        except Exception as e:
            print(f"  ❌ Ralat menghubungi URL B2: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Semakan Integriti B2 & Redis")
    parser.add_argument("--imdb", type=str, default="tt2250912", help="IMDb ID untuk disemak (lalai: tt2250912)")
    args = parser.parse_args()

    # Jalankan pemeriksaan
    subs = inspect_redis(args.imdb)
    inspect_b2_storage(args.imdb, subs)

    print("\n" + "=" * 80)
    print("✨ Pemeriksaan selesai.")
    print("=" * 80)