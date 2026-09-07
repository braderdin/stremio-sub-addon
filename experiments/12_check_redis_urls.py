import json
import os
import time
from pathlib import Path
from dotenv import load_dotenv
import requests

# 1. Cari fail .env.local atau .env di direktori utama projek
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_LOCAL_PATH = BASE_DIR / ".env.local"
ENV_PATH = BASE_DIR / ".env"

if ENV_LOCAL_PATH.exists():
    load_dotenv(dotenv_path=ENV_LOCAL_PATH)
elif ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH)
else:
    load_dotenv()

UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")

if not UPSTASH_URL or not UPSTASH_TOKEN:
    print("❌ Ralat: UPSTASH_REDIS_REST_URL atau UPSTASH_REDIS_REST_TOKEN tidak dijumpai dalam .env.local / .env.")
    exit(1)

HEADERS = {"Authorization": f"Bearer {UPSTASH_TOKEN}"}

# 2. Tetapkan laluan output JSON
OUTPUT_DIR = Path(__file__).resolve().parent / "output_json"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = OUTPUT_DIR / "diagnostic_results.json"


def save_json_realtime(data):
    """Fungsi auto-save terus ke fail JSON di disk secara real-time."""
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_all_sub_keys():
    """Mengambil semua kunci 'sub:*' dari Upstash Redis."""
    url = f"{UPSTASH_URL}/keys/sub:*"
    res = requests.get(url, headers=HEADERS)
    if res.status_code == 200:
        return res.json().get("result", [])
    print(f"❌ Gagal mengambil kunci dari Redis. HTTP {res.status_code}: {res.text}")
    return []


def check_redis_key(key):
    """Memeriksa kandungan JSON dan URL di dalam kunci Redis."""
    url = f"{UPSTASH_URL}/get/{key}"
    res = requests.get(url, headers=HEADERS)

    key_result = {
        "key": key,
        "status": "ok",
        "error": None,
        "total_entries": 0,
        "items": []
    }

    if res.status_code != 200:
        msg = f"Gagal dibaca dari Redis (HTTP {res.status_code})"
        print(f"❌ [{key}] {msg}", flush=True)
        key_result["status"] = "error"
        key_result["error"] = msg
        return key_result

    data_raw = res.json().get("result")
    if not data_raw:
        msg = "Kunci wujud tetapi nilainya kosong (None/Null)"
        print(f"⚠️ [{key}] {msg}", flush=True)
        key_result["status"] = "empty"
        key_result["error"] = msg
        return key_result

    try:
        records = json.loads(data_raw) if isinstance(data_raw, str) else data_raw
    except json.JSONDecodeError as e:
        msg = f"JSON ROSAK/INVALID: {e}"
        print(f"🚨 [{key}] {msg}", flush=True)
        key_result["status"] = "json_error"
        key_result["error"] = msg
        key_result["raw_sample"] = str(data_raw)[:100]
        return key_result

    if not isinstance(records, list):
        records = [records]

    key_result["total_entries"] = len(records)
    print(f"\n🔍 Semakan Kunci: {key} ({len(records)} entri sarikata dijumpai)", flush=True)

    for idx, item in enumerate(records):
        sub_id = item.get("id", f"index_{idx}") if isinstance(item, dict) else f"index_{idx}"
        lang = item.get("lang", "unk") if isinstance(item, dict) else "unk"
        sub_url = item.get("url", "") if isinstance(item, dict) else ""

        item_data = {
            "sub_id": sub_id,
            "lang": lang,
            "url": sub_url,
            "format_issues": [],
            "http_status": {"get": None, "head": None},
            "status_ok": False,
            "bytes_size": None,
            "error_message": None
        }

        print(f"  ├─ [{sub_id}] Lang: {lang}", flush=True)
        print(f"  │  URL: {sub_url}", flush=True)

        if not sub_url:
            item_data["format_issues"].append("URL Kosong")
        if "https://" not in sub_url and "http://" not in sub_url:
            item_data["format_issues"].append("Protokol HTTP/HTTPS Hilang")
        if "//subs/" not in sub_url and "/subs/" not in sub_url:
            item_data["format_issues"].append("Laluan '/subs/' Hilang")
        if sub_url.count("https://") > 1:
            item_data["format_issues"].append("URL Bertindih (Double https://)")
        if "///" in sub_url:
            item_data["format_issues"].append("Triple Slash (///) dikesan")

        if item_data["format_issues"]:
            print(f"  │  🚨 Kerosakan Format Dikesan: {', '.join(item_data['format_issues'])}", flush=True)
            key_result["items"].append(item_data)
            continue

        try:
            get_res = requests.get(sub_url, timeout=5)
            head_res = requests.head(sub_url, timeout=5)

            item_data["http_status"]["get"] = get_res.status_code
            item_data["http_status"]["head"] = head_res.status_code

            status_str = f"GET: {get_res.status_code} | HEAD: {head_res.status_code}"

            if get_res.status_code == 200 and head_res.status_code == 200:
                item_data["status_ok"] = True
                item_data["bytes_size"] = len(get_res.text)
                print(f"  │  ✅ Status OK ({status_str}) - Saiz: {len(get_res.text)} bytes", flush=True)
            else:
                item_data["error_message"] = get_res.text[:120]
                print(f"  │  ❌ Ralat HTTP ({status_str})", flush=True)

        except Exception as e:
            item_data["error_message"] = str(e)
            print(f"  │  💥 Kegagalan Sambungan HTTP: {e}", flush=True)

        key_result["items"].append(item_data)

    return key_result


def main():
    print("=" * 60, flush=True)
    print("  UPSTASH REDIS SUBTITLE URL DIAGNOSTIC TOOL  ", flush=True)
    print("=" * 60, flush=True)

    keys = get_all_sub_keys()
    print(f"Jumlah kunci 'sub:*' dijumpai: {len(keys)}", flush=True)

    diagnostic_summary = {
        "last_updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_keys": len(keys),
        "processed_keys": 0,
        "results": []
    }

    # Cipta fail JSON awal di disk
    save_json_realtime(diagnostic_summary)

    if not keys:
        print("Tiada rekod sarikata ditemui di Redis.", flush=True)
        return

    for idx, key in enumerate(keys, start=1):
        key_data = check_redis_key(key)
        if key_data:
            diagnostic_summary["results"].append(key_data)

        # ⚡ Auto-save fail JSON terus setiap kali siap 1 key
        diagnostic_summary["processed_keys"] = idx
        diagnostic_summary["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_json_realtime(diagnostic_summary)

    print("\n" + "=" * 60, flush=True)
    print(f"✅ Selesai semakan! Fail JSON dikemas kini sepenuhnya di:\n   {OUTPUT_FILE}")


if __name__ == "__main__":
    main()