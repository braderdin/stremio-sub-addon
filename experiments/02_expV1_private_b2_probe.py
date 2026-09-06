import sys
import os
import importlib
from pathlib import Path
from curl_cffi import requests

print("=" * 80)
print("🧪 UJIAN END-TO-END PRIVATE B2 & CLOUDFLARE PROXY (02_expV1_private_b2_probe.py)")
print("   Menguji: Raw S3 (401) -> CF Worker Proxy (200 OK) -> live_engine/02_b2_storage")
print("=" * 80)

# Tambah folder live_engine ke dalam modul path
ENGINE_DIR = Path(__file__).resolve().parent.parent / "live_engine"
sys.path.append(str(ENGINE_DIR))

_config = importlib.import_module("00_config")
_b2 = importlib.import_module("02_b2_storage")

B2_ACCOUNTS = _config.B2_ACCOUNTS

# Pastikan URL tepat dengan domain '-private-'
CORRECT_PROXY_URL = "https://b2-private-stremio-sub-addon.braderdin360.workers.dev"

# Kemas kini pembolehubah dalam modul konfigurasi dan b2 secara dinamik
_config.CF_WORKER_B2_STORAGE = os.getenv("CF_WORKER_B2_STORAGE", CORRECT_PROXY_URL).rstrip("/")
if "-private-" not in _config.CF_WORKER_B2_STORAGE:
    _config.CF_WORKER_B2_STORAGE = CORRECT_PROXY_URL

_b2.CF_WORKER_B2_STORAGE = _config.CF_WORKER_B2_STORAGE
CF_WORKER_B2_STORAGE = _config.CF_WORKER_B2_STORAGE

if not B2_ACCOUNTS:
    print("❌ Tiada senarai akaun B2 ditemui.")
    sys.exit(1)

acc1 = B2_ACCOUNTS[0]
BUCKET_NAME = acc1["bucket_name"]
ENDPOINT = acc1["endpoint"]
TEST_FILE_PATH = "subs/tt2250912/id_1606034_0.srt"

print(f"📦 Konfigurasi:")
print(f"   ├─ Bucket Sasaran    : {BUCKET_NAME}")
print(f"   ├─ Endpoint S3 B2    : {ENDPOINT}")
print(f"   ├─ Worker B2 Proxy   : {CF_WORKER_B2_STORAGE}")
print(f"   └─ Fail Ujian Sedia  : {TEST_FILE_PATH}")

# ------------------------------------------------------------------
# UJIAN 1: PENGESAHAN KEKUNCIAN PRIVATE B2 (RAW S3 URL)
# ------------------------------------------------------------------
print(f"\n[Ujian 1] Membuka URL Terus S3 (Tanpa Auth)...")
raw_s3_url = f"https://{BUCKET_NAME}.{ENDPOINT}/{TEST_FILE_PATH}"
print(f"   └ URL: {raw_s3_url}")

try:
    r_raw = requests.get(raw_s3_url, timeout=10)
    print(f"   └ Status HTTP: {r_raw.status_code}")
    if r_raw.status_code == 401:
        print("   └ 🔒 SAH: Bucket berstatus Private (Akses terus awam disekat dengan HTTP 401).")
    elif r_raw.status_code == 200:
        print("   └ ℹ️ Bucket boleh diakses secara terus awam.")
except Exception as e:
    print(f"   └ ❌ Ralat rangkaian Ujian 1: {e}")

# ------------------------------------------------------------------
# UJIAN 2: UJIAN AKSES FAIL SEDIA ADA MELALUI CLOUDFLARE PROXY
# ------------------------------------------------------------------
print(f"\n[Ujian 2] Membuka Fail Sedia Ada Melalui Cloudflare Worker Reverse Proxy...")
proxy_file_url = f"{CF_WORKER_B2_STORAGE}/{BUCKET_NAME}/{TEST_FILE_PATH}"
print(f"   └ URL Proxy: {proxy_file_url}")

try:
    r_proxy = requests.get(proxy_file_url, timeout=15)
    print(f"   └ Status HTTP: {r_proxy.status_code}")
    if r_proxy.status_code == 200 and len(r_proxy.text) > 50:
        print(f"   ├─ 🎉 BERJAYA! Cloudflare Worker berjaya menyedut fail Private B2 (200 OK).")
        print(f"   ├─ Saiz Data Diterima: {len(r_proxy.text)} aksara")
        snippet = [line.strip() for line in r_proxy.text.splitlines() if line.strip()][:4]
        print(f"   └─ Cuplikan Teks SRT: {' // '.join(snippet)}")
    else:
        print(f"   └ ❌ Gagal melepasi Worker Proxy. Respons: {r_proxy.text[:120]}")
except Exception as e:
    print(f"   └ ❌ Ralat rangkaian Ujian 2: {e}")

# ------------------------------------------------------------------
# UJIAN 3: INTEGRASI MODUL live_engine/02_b2_storage.py (UPLOAD & FETCH)
# ------------------------------------------------------------------
print(f"\n[Ujian 3] Menguji Fungsi upload_subtitle_to_b2() daripada live_engine...")
dummy_srt_path = "subs/probe_test/ping_check.srt"
dummy_srt_content = """1
00:00:01,000 --> 00:00:04,000
Ujian sambungan Cloudflare Worker B2 Proxy berjaya!

2
00:00:05,000 --> 00:00:08,000
Sedia untuk digabungkan ke Stremio Subtitles Engine.
"""

try:
    print(f"   ├─ Memuat naik fail ujian ke: {dummy_srt_path}")
    res = _b2.upload_subtitle_to_b2(dummy_srt_path, dummy_srt_content)
    returned_url = res.get("url", "")
    print(f"   ├─ ✅ Muat Naik Siap! URL Dijana: {returned_url}")

    if not returned_url.startswith(CF_WORKER_B2_STORAGE):
        print(f"   ⚠️ AMARAN: URL yang dipulangkan tidak menggunakan {CF_WORKER_B2_STORAGE}!")
    else:
        print(f"   ├─ 🎯 URL sah diselaraskan ke Cloudflare Worker Proxy.")

    # Uji muat turun fail baharu
    print(f"   ├─ Menguji muat turun fail baharu melalui URL yang dipulangkan...")
    r_check = requests.get(returned_url, timeout=15)
    print(f"   ├─ Status HTTP: {r_check.status_code}")

    if r_check.status_code == 200 and "Ujian sambungan" in r_check.text:
        print(f"   └─ 🏆 INTEGRASI SEMPURNA! Fail baharu boleh dimuat turun terus tanpa ralat 401.")
    else:
        print(f"   └─ ❌ Fail baharu gagal dimuat turun. Respons: {r_check.text[:120]}")

except Exception as e:
    print(f"   └ ❌ Ralat semasa Ujian 3: {e}")

print("\n" + "=" * 80)
print("✨ Ujian selesai. Jika ketiga-tiga status hijau, sistem sudah sedia untuk push ke GitHub!")
print("=" * 80)