import json
import importlib
from typing import Optional, List, Dict
from curl_cffi import requests

# Import dinamik modul 00_config (elak SyntaxError nombor awalan)
_config = importlib.import_module("00_config")
UPSTASH_REDIS_REST_URL = _config.UPSTASH_REDIS_REST_URL
UPSTASH_REDIS_REST_TOKEN = _config.UPSTASH_REDIS_REST_TOKEN

def _redis_request(command: str, *args) -> Optional[dict]:
    """
    Menghantar arahan REST API ke Upstash Redis.
    """
    if not UPSTASH_REDIS_REST_URL or not UPSTASH_REDIS_REST_TOKEN:
        print("⚠️ Pembolehubah persekitaran Upstash Redis tidak lengkap.")
        return None

    headers = {
        "Authorization": f"Bearer {UPSTASH_REDIS_REST_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # Format URL REST Upstash: URL/command/arg1/arg2...
    endpoint_parts = [UPSTASH_REDIS_REST_URL.rstrip("/"), command] + [str(a) for a in args]
    url = "/".join(endpoint_parts)

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        print(f"⚠️ Redis REST Error ({resp.status_code}): {resp.text}")
    except Exception as e:
        print(f"❌ Ralat sambungan Redis: {e}")
    return None

def set_processing_lock(imdb_id: str, ttl_seconds: int = 600) -> bool:
    """
    Menetapkankan kunci (lock) sementara di Redis supaya runner lain tidak mengikis ID sama.
    """
    key = f"lock:{imdb_id}"
    res = _redis_request("set", key, "processing", "EX", ttl_seconds, "NX")
    return bool(res and res.get("result") == "OK")

def remove_processing_lock(imdb_id: str) -> bool:
    """
    Membuka semula kunci di Redis selepas proses selesai.
    """
    key = f"lock:{imdb_id}"
    res = _redis_request("del", key)
    return bool(res and res.get("result", 0) > 0)

def save_subtitle_record(imdb_id: str, record: dict) -> bool:
    """
    Menyimpan senarai rekod subtitle ke dalam kunci Redis 'sub:ttXXXXXX'.
    """
    key = f"sub:{imdb_id}"
    
    # Ambil data sedia ada
    existing_res = _redis_request("get", key)
    records = []
    
    if existing_res and existing_res.get("result"):
        try:
            records = json.loads(existing_res["result"])
        except Exception:
            records = []

    # Tambah rekod baharu jika ID belum wujud
    existing_ids = {r.get("id") for r in records}
    if record.get("id") not in existing_ids:
        records.append(record)

    # Simpan semula ke Redis (kunci tanpa tamat tempoh/permanent cache)
    json_str = json.dumps(records)
    res = _redis_request("set", key, json_str)
    return bool(res and res.get("result") == "OK")

def get_subtitle_records(imdb_id: str) -> List[Dict]:
    """
    Mendapatkan senarai rekod subtitle bagi IMDb ID dari Redis.
    """
    key = f"sub:{imdb_id}"
    res = _redis_request("get", key)
    if res and res.get("result"):
        try:
            return json.loads(res["result"])
        except Exception:
            pass
    return []