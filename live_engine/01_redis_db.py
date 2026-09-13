import json
import importlib
import re
from typing import Optional, List, Dict
from curl_cffi import requests

_config = importlib.import_module("00_config")
# [PERUBAHAN FUNGSI]: Memuatkan konfigurasi multi-akaun berserta konfigurasi asal sebagai fallback
UPSTASH_REDIS_REST_URL = getattr(_config, "UPSTASH_REDIS_REST_URL", "")
UPSTASH_REDIS_REST_TOKEN = getattr(_config, "UPSTASH_REDIS_REST_TOKEN", "")
REDIS_ACCOUNTS = getattr(_config, "REDIS_ACCOUNTS", [])

def get_target_redis_account(imdb_id: str) -> dict:
    """
    [PERUBAHAN FUNGSI]: Menentukan akaun Upstash Redis sasaran berasaskan Modulo Hashing.
    Mengagihkan beban penulisan secara seimbang (purata 10% setiap akaun) mengikut angka IMDb ID.
    """
    if not REDIS_ACCOUNTS:
        return {
            "index": 1,
            "url": UPSTASH_REDIS_REST_URL,
            "token": UPSTASH_REDIS_REST_TOKEN
        }

    clean_id = str(imdb_id or "").strip()
    match = re.search(r"tt(\d+)", clean_id)
    if match:
        num = int(match.group(1))
        shard_idx = num % len(REDIS_ACCOUNTS)
    else:
        # Sandaran jika format ID bukan corak 'tt' biasa
        shard_idx = sum(ord(c) for c in clean_id) % len(REDIS_ACCOUNTS)

    return REDIS_ACCOUNTS[shard_idx]

def get_primary_redis_account() -> dict:
    """
    [PERUBAHAN FUNGSI]: Mengambil akaun Redis asal (Index 1 / Akaun 00) khusus untuk semakan sandaran (Fallback) data lama.
    """
    if REDIS_ACCOUNTS:
        return REDIS_ACCOUNTS[0]
    return {
        "index": 1,
        "url": UPSTASH_REDIS_REST_URL,
        "token": UPSTASH_REDIS_REST_TOKEN
    }

def _redis_request(command: str, *args, target_account: Optional[dict] = None) -> Optional[dict]:
    """
    [PERUBAHAN FUNGSI]: Menghantar arahan ke Upstash Redis via REST API menggunakan POST Body.
    Menyokong parameter pilihan 'target_account' untuk operasi akaun spesifik (Sharding).
    Jika 'target_account' tidak dinyatakan, sistem lalai kepada akaun utama (Fallback selamat).
    """
    account = target_account if target_account else get_primary_redis_account()
    url = account.get("url", "").rstrip("/")
    token = account.get("token", "")

    if not url or not token:
        print("⚠️ Pembolehubah persekitaran Upstash Redis tidak lengkap.")
        return None

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    # Hantar arahan sebagai array JSON: ["COMMAND", "arg1", "arg2", ...]
    payload = [command] + [str(a) for a in args]

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
    """
    [PERUBAHAN FUNGSI]: Mengunci IMDb ID pada akaun sasaran bagi mengelakkan perlumbaan proses runner serentak.
    """
    norm_id = _clean_imdb_id(imdb_id)
    key = f"lock:{norm_id}"
    target = get_target_redis_account(norm_id)
    res = _redis_request("SET", key, "processing", "EX", ttl_seconds, "NX", target_account=target)
    return bool(res and res.get("result") == "OK")

def remove_processing_lock(imdb_id: str) -> bool:
    """
    [PERUBAHAN FUNGSI]: Memadam kunci pemprosesan pada akaun sasaran selepas proses selesai.
    """
    norm_id = _clean_imdb_id(imdb_id)
    key = f"lock:{norm_id}"
    target = get_target_redis_account(norm_id)
    res = _redis_request("DEL", key, target_account=target)
    return bool(res and res.get("result", 0) > 0)

def save_subtitle_record(imdb_id: str, record: dict) -> bool:
    """
    Menyimpan 1 rekod sarikata secara individu (Kekal disokong untuk keserasian belakang).
    """
    return save_subtitle_records_batch(imdb_id, [record])

def save_subtitle_records_batch(imdb_id: str, new_records: List[Dict]) -> bool:
    """
    [PERUBAHAN FUNGSI]: Menyimpan atau mengemas kini senarai sarikata secara berkelompok ke akaun sasaran (Sharding).
    - Menyokong Fallback Automatik: Sekiranya rekod belum wujud di akaun sasaran baharu, sistem menyemak
      akaun asal (Index 1) agar rekod sedia ada digabungkan bersama dan tidak hilang.
    - Menjimatkan kuota panggilan Upstash Redis jika tiada perubahan dikesan.
    """
    if not new_records or not imdb_id:
        return True

    norm_id = _clean_imdb_id(imdb_id)
    key = f"sub:{norm_id}"
    target = get_target_redis_account(norm_id)
    primary = get_primary_redis_account()

    # 1. Semak rekod sedia ada di akaun Redis sasaran
    existing_res = _redis_request("GET", key, target_account=target)
    records = []
    
    if existing_res and existing_res.get("result"):
        try:
            val = existing_res["result"]
            records = json.loads(val) if isinstance(val, str) else val
            if not isinstance(records, list):
                records = []
        except Exception:
            records = []

    # 2. Fallback Pintar: Jika akaun sasaran kosong dan bukan akaun asal, ambil data lama daripada akaun asal (Index 1)
    if not records and target != primary:
        legacy_res = _redis_request("GET", key, target_account=primary)
        if legacy_res and legacy_res.get("result"):
            try:
                val = legacy_res["result"]
                legacy_records = json.loads(val) if isinstance(val, str) else val
                if isinstance(legacy_records, list):
                    records = legacy_records
            except Exception:
                pass

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

    # Simpan hasil gabungan ke akaun Redis sasaran
    json_str = json.dumps(records, ensure_ascii=False)
    res = _redis_request("SET", key, json_str, target_account=target)
    return bool(res and res.get("result") == "OK")

def get_subtitle_records(imdb_id: str) -> List[Dict]:
    """
    [PERUBAHAN FUNGSI]: Mengambil senarai sarikata bagi IMDb ID (Filem atau Siri Episod).
    - Membaca akaun sasaran hasil kiraan modulo.
    - Fallback 1: Jika rekod tiada di sasaran, semak akaun asal (Index 1 / Akaun 00) agar sarikata lama kekal terbaca.
    - Fallback 2: Menyokong tapisan automatik bagi siri episod (cth: tt19049680:1:1) daripada sarikata peringkat siri.
    """
    norm_id = _clean_imdb_id(imdb_id)
    key = f"sub:{norm_id}"
    target = get_target_redis_account(norm_id)
    primary = get_primary_redis_account()

    # 1. Semak di akaun sasaran
    res = _redis_request("GET", key, target_account=target)
    if res and res.get("result"):
        try:
            val = res["result"]
            records = json.loads(val) if isinstance(val, str) else val
            if isinstance(records, list) and records:
                return records
        except Exception:
            pass

    # 2. Fallback Pembacaan: Jika tiada di akaun sasaran, semak akaun asal (Index 1)
    if target != primary:
        res_legacy = _redis_request("GET", key, target_account=primary)
        if res_legacy and res_legacy.get("result"):
            try:
                val = res_legacy["result"]
                records = json.loads(val) if isinstance(val, str) else val
                if isinstance(records, list) and records:
                    return records
            except Exception:
                pass

    # 3. Fallback Pintar untuk Episod Bersiri
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
            base_target = get_target_redis_account(base_id)
            all_records = []

            # Semak peringkat siri di akaun sasaran siri
            base_res = _redis_request("GET", base_key, target_account=base_target)
            if base_res and base_res.get("result"):
                try:
                    val = base_res["result"]
                    records_candidate = json.loads(val) if isinstance(val, str) else val
                    if isinstance(records_candidate, list):
                        all_records = records_candidate
                except Exception:
                    pass

            # Fallback jika tiada di akaun sasaran siri: semak akaun asal
            if not all_records and base_target != primary:
                base_res_legacy = _redis_request("GET", base_key, target_account=primary)
                if base_res_legacy and base_res_legacy.get("result"):
                    try:
                        val = base_res_legacy["result"]
                        records_candidate = json.loads(val) if isinstance(val, str) else val
                        if isinstance(records_candidate, list):
                            all_records = records_candidate
                    except Exception:
                        pass

            if all_records:
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
    return []