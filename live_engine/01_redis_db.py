import json
import importlib
from typing import Optional, List, Dict
from curl_cffi import requests

_config = importlib.import_module("00_config")
UPSTASH_REDIS_REST_URL = _config.UPSTASH_REDIS_REST_URL
UPSTASH_REDIS_REST_TOKEN = _config.UPSTASH_REDIS_REST_TOKEN

def _redis_request(command: str, *args) -> Optional[dict]:
    """
    Menghantar arahan ke Upstash Redis via REST API menggunakan POST Body.
    Kebal terhadap ralat aksara palang '/', petikan JSON, atau teks panjang.
    """
    if not UPSTASH_REDIS_REST_URL or not UPSTASH_REDIS_REST_TOKEN:
        print("⚠️ Pembolehubah persekitaran Upstash Redis tidak lengkap.")
        return None

    headers = {
        "Authorization": f"Bearer {UPSTASH_REDIS_REST_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # Hantar arahan sebagai array JSON: ["COMMAND", "arg1", "arg2", ...]
    payload = [command] + [str(a) for a in args]
    url = UPSTASH_REDIS_REST_URL.rstrip("/")

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        print(f"⚠️ Redis REST Error ({resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"❌ Ralat sambungan Redis: {e}")
    return None

def set_processing_lock(imdb_id: str, ttl_seconds: int = 600) -> bool:
    """Kunci IMDb ID bagi mengelakkan perlumbaan proses runner serentak."""
    key = f"lock:{imdb_id}"
    res = _redis_request("SET", key, "processing", "EX", ttl_seconds, "NX")
    return bool(res and res.get("result") == "OK")

def remove_processing_lock(imdb_id: str) -> bool:
    """Padam kunci pemprosesan selepas selesai."""
    key = f"lock:{imdb_id}"
    res = _redis_request("DEL", key)
    return bool(res and res.get("result", 0) > 0)

def save_subtitle_record(imdb_id: str, record: dict) -> bool:
    """
    Menyimpan 1 rekod sarikata secara individu (Kekal disokong untuk keserasian belakang).
    """
    return save_subtitle_records_batch(imdb_id, [record])

def save_subtitle_records_batch(imdb_id: str, new_records: List[Dict]) -> bool:
    """
    Menyimpan senarai sarikata secara berkelompok (Batch).
    Menjimatkan kuota panggilan Upstash Redis dengan menggabungkan operasi ke dalam 1 GET & 1 SET.
    """
    if not new_records:
        return True

    key = f"sub:{imdb_id}"
    existing_res = _redis_request("GET", key)
    records = []
    
    if existing_res and existing_res.get("result"):
        try:
            val = existing_res["result"]
            records = json.loads(val) if isinstance(val, str) else val
        except Exception:
            records = []

    existing_ids = {r.get("id") for r in records}
    added = False

    for rec in new_records:
        if rec.get("id") not in existing_ids:
            records.append(rec)
            existing_ids.add(rec.get("id"))
            added = True

    if not added:
        return True

    json_str = json.dumps(records, ensure_ascii=False)
    res = _redis_request("SET", key, json_str)
    return bool(res and res.get("result") == "OK")

def get_subtitle_records(imdb_id: str) -> List[Dict]:
    """Mengambil senarai sarikata yang disimpan bagi IMDb ID."""
    key = f"sub:{imdb_id}"
    res = _redis_request("GET", key)
    if res and res.get("result"):
        try:
            val = res["result"]
            return json.loads(val) if isinstance(val, str) else val
        except Exception:
            pass
    return []