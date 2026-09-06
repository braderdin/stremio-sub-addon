import json
from typing import List, Dict, Optional
from upstash_redis import Redis
from 00_config import UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN

# Inisialisasi Klien Redis
if UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN:
    redis_client = Redis(url=UPSTASH_REDIS_REST_URL, token=UPSTASH_REDIS_REST_TOKEN)
else:
    redis_client = None

def save_subtitle_record(imdb_id: str, record: Dict) -> bool:
    """
    Menyimpan atau menambah metadata subtitle ke senarai Redis mengikut IMDb ID.
    Format Kunci: sub:{imdb_id} (Contoh: sub:tt0120338)
    """
    if not redis_client:
        return False

    key = f"sub:{imdb_id}"
    try:
        existing_raw = redis_client.get(key)
        records = []
        if existing_raw:
            if isinstance(existing_raw, str):
                records = json.loads(existing_raw)
            elif isinstance(existing_raw, list):
                records = existing_raw

        # Elak duplikasi fail srt yang sama mengikut ID rekod
        existing_ids = {r.get("id") for r in records if isinstance(r, dict)}
        if record.get("id") not in existing_ids:
            records.append(record)
            redis_client.set(key, json.dumps(records))
        return True
    except Exception as e:
        print(f"❌ Ralat Redis Save [{key}]: {e}")
        return False

def get_subtitle_records(imdb_id: str) -> List[Dict]:
    """
    Mengambil senarai pautan dan metadata subtitle bagi sesuatu IMDb ID.
    """
    if not redis_client:
        return []

    key = f"sub:{imdb_id}"
    try:
        raw_data = redis_client.get(key)
        if not raw_data:
            return []
        if isinstance(raw_data, str):
            return json.loads(raw_data)
        if isinstance(raw_data, list):
            return raw_data
    except Exception as e:
        print(f"❌ Ralat Redis Fetch [{key}]: {e}")
    return []

def set_processing_lock(imdb_id: str, ttl_seconds: int = 600) -> bool:
    """
    Mengunci IMDb ID sementara (default 10 minit) semasa proses pengikisan berjalan.
    """
    if not redis_client:
        return False
    lock_key = f"lock:{imdb_id}"
    try:
        return bool(redis_client.set(lock_key, "processing", ex=ttl_seconds, nx=True))
    except Exception:
        return False

def remove_processing_lock(imdb_id: str) -> bool:
    """
    Membuka kunci IMDb ID selepas pengikisan selesai.
    """
    if not redis_client:
        return False
    lock_key = f"lock:{imdb_id}"
    try:
        redis_client.delete(lock_key)
        return True
    except Exception:
        return False