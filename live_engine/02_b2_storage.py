import sys
import importlib
from typing import Dict, Optional
from pathlib import Path
from b2sdk.v2 import B2Api, InMemoryAccountInfo

# Import dinamik modul 00_config (elak ralat nombor awalan modul)
_config = importlib.import_module("00_config")
B2_ACCOUNTS = _config.B2_ACCOUNTS
B2_MAX_BYTES_PER_ACCOUNT = _config.B2_MAX_BYTES_PER_ACCOUNT

_b2_api_instances = {}

def _get_b2_api(acc: dict) -> B2Api:
    """
    Menguruskan sesi auth B2 API dengan caching dalam memori.
    """
    acc_index = acc["index"]
    if acc_index not in _b2_api_instances:
        info = InMemoryAccountInfo()
        b2_api = B2Api(info)
        b2_api.authorize_account("production", acc["key_id"], acc["app_key"])
        _b2_api_instances[acc_index] = b2_api
    return _b2_api_instances[acc_index]

def check_bucket_used_bytes(acc: dict) -> int:
    """
    Mengira jumlah kapasiti data yang telah digunakan dalam bucket tertentu.
    """
    try:
        b2_api = _get_b2_api(acc)
        bucket = b2_api.get_bucket_by_name(acc["bucket_name"])
        total_bytes = 0
        for file_version, _ in bucket.ls(recursive=True):
            total_bytes += file_version.size
        return total_bytes
    except Exception as e:
        print(f"⚠️ Ralat semakan saiz bucket [{acc['bucket_name']}]: {e}")
        return 0

def upload_subtitle_to_b2(b2_path: str, srt_content: str) -> Dict:
    """
    Mencari akaun B2 yang belum penuh (<9.5GB) dan memuat naik fail .srt.
    Memulangkan URL awam (Cloudflare Alliance / S3 Endpoint) dan metadata muat naik.
    """
    srt_bytes = srt_content.encode("utf-8")
    file_size = len(srt_bytes)

    if not B2_ACCOUNTS:
        raise Exception("❌ Tiada senarai akaun B2 dijumpai dalam pembolehubah persekitaran!")

    selected_acc = None
    
    # Cari akaun B2 pertama yang masih mempunyai ruang kosong (<9.5GB)
    for acc in B2_ACCOUNTS:
        used_bytes = check_bucket_used_bytes(acc)
        if (used_bytes + file_size) < B2_MAX_BYTES_PER_ACCOUNT:
            selected_acc = acc
            break

    if not selected_acc:
        raise Exception("❌ Semua akaun B2 berdaftar telah mencapai had had 9.5GB!")

    b2_api = _get_b2_api(selected_acc)
    bucket = b2_api.get_bucket_by_name(selected_acc["bucket_name"])

    # Muat naik kandungan teks dari memori terus ke B2
    uploaded_file = bucket.upload_bytes(
        data_bytes=srt_bytes,
        file_name=b2_path,
        content_type="text/plain; charset=utf-8"
    )

    # Format pautan direct awam (Cloudflare / S3 Alliance endpoint)
    public_url = f"https://{selected_acc['bucket_name']}.{selected_acc['endpoint']}/{b2_path}"

    return {
        "url": public_url,
        "account_index": selected_acc["index"],
        "bucket_name": selected_acc["bucket_name"],
        "file_id": uploaded_file.id_,
        "b2_path": b2_path,
        "size_bytes": file_size
    }