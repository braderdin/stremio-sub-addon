import json
import importlib
import re
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

def _clean_imdb_id(imdb_id: str) -> str:
    """
    Menyeragamkan IMDb ID untuk format Filem (ttXXXXXXX) mahupun Siri Episod (ttXXXXXXX:season:episode).
    Menukar corak seperti tt19049680:S01:E01 atau tt19049680:s1:e1 kepada standard Stremio tt19049680:1:1.
    """
    if not imdb_id:
        return ""
    clean = str(imdb_id).strip()
    parts = clean.split(":")
    if len(parts) >= 3:
        base_id = parts[0].strip()
        s_num = re.sub(r"\D", "", parts[1])
        e_num = re.sub(r"\D", "", parts[2])
        if s_num and e_num:
            return f"{base_id}:{int(s_num)}:{int(e_num)}"
    elif len(parts) == 2:
        base_id = parts[0].strip()
        s_num = re.sub(r"\D", "", parts[1])
        if s_num:
            return f"{base_id}:{int(s_num)}"
    return clean

def set_processing_lock(imdb_id: str, ttl_seconds: int = 600) -> bool:
    """Kunci IMDb ID bagi mengelakkan perlumbaan proses runner serentak."""
    norm_id = _clean_imdb_id(imdb_id)
    key = f"lock:{norm_id}"
    res = _redis_request("SET", key, "processing", "EX", ttl_seconds, "NX")
    return bool(res and res.get("result") == "OK")

def remove_processing_lock(imdb_id: str) -> bool:
    """Padam kunci pemprosesan selepas selesai."""
    norm_id = _clean_imdb_id(imdb_id)
    key = f"lock:{norm_id}"
    res = _redis_request("DEL", key)
    return bool(res and res.get("result", 0) > 0)

def save_subtitle_record(imdb_id: str, record: dict) -> bool:
    """
    Menyimpan 1 rekod sarikata secara individu (Kekal disokong untuk keserasian belakang).
    """
    return save_subtitle_records_batch(imdb_id, [record])

def save_subtitle_records_batch(imdb_id: str, new_records: List[Dict]) -> bool:
    """
    Menyimpan atau mengemas kini senarai sarikata secara berkelompok (Append & Update In-Place).
    - Jika rekod dengan ID sama sudah wujud, metadata dikemas kini tanpa memadam rekod lain.
    - Jika rekod belum wujud, rekod baharu ditambah ke dalam senarai.
    - Menjimatkan kuota panggilan Upstash Redis jika tiada perubahan dikesan.
    """
    if not new_records or not imdb_id:
        return True

    norm_id = _clean_imdb_id(imdb_id)
    key = f"sub:{norm_id}"
    existing_res = _redis_request("GET", key)
    records = []
    
    if existing_res and existing_res.get("result"):
        try:
            val = existing_res["result"]
            records = json.loads(val) if isinstance(val, str) else val
            if not isinstance(records, list):
                records = []
        except Exception:
            records = []

    # Petakan kedudukan indeks ID sedia ada untuk kemas kini tepat
    record_map = {}
    for idx, r in enumerate(records):
        if isinstance(r, dict) and "id" in r:
            record_map[str(r["id"])] = idx

    has_changes = False

    for rec in new_records:
        if not isinstance(rec, dict) or "id" not in rec:
            continue
        rec_id = str(rec["id"])
        
        if rec_id in record_map:
            # Kemas kini rekod sedia ada sekiranya data berubah
            curr_idx = record_map[rec_id]
            if records[curr_idx] != rec:
                records[curr_idx].update(rec)
                has_changes = True
        else:
            # Tambah rekod baharu
            records.append(rec)
            record_map[rec_id] = len(records) - 1
            has_changes = True

    if not has_changes and records:
        return True

    json_str = json.dumps(records, ensure_ascii=False)
    res = _redis_request("SET", key, json_str)
    return bool(res and res.get("result") == "OK")

def get_subtitle_records(imdb_id: str) -> List[Dict]:
    """
    Mengambil senarai sarikata bagi IMDb ID (Filem atau Siri Episod).
    Menyokong fallback automatik: Jika query meminta episod (cth: tt19049680:1:1) tetapi sarikata
    disimpan pada peringkat siri umum (sub:tt19049680), sistem automatik menapis episod berkenaan.
    """
    norm_id = _clean_imdb_id(imdb_id)
    key = f"sub:{norm_id}"
    res = _redis_request("GET", key)
    if res and res.get("result"):
        try:
            val = res["result"]
            records = json.loads(val) if isinstance(val, str) else val
            if isinstance(records, list) and records:
                return records
        except Exception:
            pass

    # Fallback Pintar untuk Episod Bersiri
    if ":" in norm_id:
        parts = norm_id.split(":")
        base_id = parts[0]
        try:
            target_season = int(parts[1]) if len(parts) > 1 else None
            target_episode = int(parts[2]) if len(parts) > 2 else None
        except ValueError:
            target_season, target_episode = None, None

        if base_id and target_season is not None and target_episode is not None:
            base_key = f"sub:{base_id}"
            base_res = _redis_request("GET", base_key)
            if base_res and base_res.get("result"):
                try:
                    val = base_res["result"]
                    all_records = json.loads(val) if isinstance(val, str) else val
                    if isinstance(all_records, list):
                        matched = []
                        for r in all_records:
                            r_s = r.get("season")
                            r_e = r.get("episode")
                            if r_s is not None and r_e is not None:
                                if int(r_s) == target_season and int(r_e) == target_episode:
                                    matched.append(r)
                                    continue
                            # Semakan regex sandaran melalui nama keluaran (Release tag)
                            rel = str(r.get("release", ""))
                            m = re.search(r"[sS](\d+)[eE](\d+)", rel) or re.search(r"\b(\d+)x(\d+)\b", rel)
                            if m:
                                if int(m.group(1)) == target_season and int(m.group(2)) == target_episode:
                                    matched.append(r)
                        if matched:
                            return matched
                except Exception:
                    pass
    return []