import sys
import os
import importlib
from typing import Dict, Optional, List
from pathlib import Path
from b2sdk.v2 import B2Api, InMemoryAccountInfo

# Import dinamik modul 00_config
_config = importlib.import_module("00_config")
B2_ACCOUNTS = _config.B2_ACCOUNTS
B2_MAX_BYTES_PER_ACCOUNT = _config.B2_MAX_BYTES_PER_ACCOUNT

# Ambil pautan Worker B2 Storage dari config / persekitaran
CF_WORKER_B2_STORAGE = getattr(
    _config,
    "CF_WORKER_B2_STORAGE",
    os.getenv("CF_WORKER_B2_STORAGE", "https://b2-private-stremio-sub-addon.braderdin360.workers.dev")
)

_b2_api_instances = {}
_cached_bucket_bytes = {}
_exhausted_accounts = set()
_all_accounts_exhausted_flag = False

# Penunjuk giliran akaun global (Round-Robin Pointer)
_current_account_pointer = 0

class AllB2AccountsExhaustedException(Exception):
    """Exception khusus apabila kesemua akaun B2 mencapai limit transaksi atau storan."""
    pass

def is_all_b2_exhausted() -> bool:
    """Semak sama ada semua akaun B2 yang berdaftar telah kehabisan kuota transaksi harian / penuh."""
    global _all_accounts_exhausted_flag, _exhausted_accounts
    if not B2_ACCOUNTS:
        return True
    if len(_exhausted_accounts) >= len(B2_ACCOUNTS):
        _all_accounts_exhausted_flag = True
    return _all_accounts_exhausted_flag

def get_active_accounts_count() -> int:
    """Mengira baki akaun B2 yang masih boleh digunakan."""
    return max(0, len(B2_ACCOUNTS) - len(_exhausted_accounts))

def _get_b2_api(acc: dict) -> B2Api:
    """Menguruskan sesi auth B2 API dengan in-memory caching."""
    acc_index = acc["index"]
    if acc_index not in _b2_api_instances:
        info = InMemoryAccountInfo()
        b2_api = B2Api(info)
        b2_api.authorize_account("production", acc["key_id"], acc["app_key"])
        _b2_api_instances[acc_index] = b2_api
    return _b2_api_instances[acc_index]

def check_bucket_used_bytes(acc: dict, force_refresh: bool = False) -> int:
    """
    Mengira saiz storan bucket dengan perlindungan cache untuk jimatkan kuota transaksi Class C harian.
    """
    acc_index = acc["index"]
    if not force_refresh and acc_index in _cached_bucket_bytes:
        return _cached_bucket_bytes[acc_index]

    try:
        b2_api = _get_b2_api(acc)
        bucket = b2_api.get_bucket_by_name(acc["bucket_name"])
        total_bytes = 0
        for file_version, _ in bucket.ls(recursive=True):
            total_bytes += file_version.size
        _cached_bucket_bytes[acc_index] = total_bytes
        return total_bytes
    except Exception as e:
        err_msg = str(e).lower()
        if "cap exceeded" in err_msg or "transaction" in err_msg:
            _exhausted_accounts.add(acc_index)
            print(f"⚠️ [B2 Acc {acc_index}] Cap transaksi harian dicapai semasa semakan saiz.")
        else:
            print(f"⚠️ Ralat semakan saiz bucket [{acc['bucket_name']}]: {e}")
        return 0

def upload_subtitle_to_b2(b2_path: str, srt_content: str) -> Dict:
    """
    Memuat naik fail .srt ke B2 secara PURATA (Round-Robin Dynamic Rotation).
    Setiap muat naik baharu akan beralih ke akaun seterusnya (Acc 1 -> Acc 2 -> ... -> Acc N).
    Jika satu akaun mencecah had/penuh, sistem automatik melompat ke akaun aktif seterusnya.
    """
    global _all_accounts_exhausted_flag, _exhausted_accounts, _current_account_pointer

    if not B2_ACCOUNTS:
        _all_accounts_exhausted_flag = True
        raise AllB2AccountsExhaustedException("❌ Tiada senarai akaun B2 dijumpai dalam persekitaran!")

    if is_all_b2_exhausted():
        raise AllB2AccountsExhaustedException("❌ Kesemua akaun B2 telah mencapai had transaksi atau penuh!")

    srt_bytes = srt_content.encode("utf-8")
    file_size = len(srt_bytes)
    last_error = None
    total_accounts = len(B2_ACCOUNTS)

    # Putaran berkitar (Cyclic Round-Robin) bermula dari penunjuk semasa
    for attempt in range(total_accounts):
        acc_list_index = (_current_account_pointer + attempt) % total_accounts
        acc = B2_ACCOUNTS[acc_list_index]
        acc_idx = acc["index"]

        # Langkau akaun yang sudah mencapai had transaksi hari ini
        if acc_idx in _exhausted_accounts:
            continue

        # Semak saiz storan akaun (< 9.5 GB)
        used_bytes = check_bucket_used_bytes(acc)
        if (used_bytes + file_size) >= B2_MAX_BYTES_PER_ACCOUNT:
            print(f"ℹ️ [B2 Acc {acc_idx}] Bucket telah mencapai ambang 9.5GB. Beralih ke akaun seterusnya.")
            _exhausted_accounts.add(acc_idx)
            continue

        try:
            b2_api = _get_b2_api(acc)
            bucket = b2_api.get_bucket_by_name(acc["bucket_name"])

            # Muat naik fail terus dari memori
            uploaded_file = bucket.upload_bytes(
                data_bytes=srt_bytes,
                file_name=b2_path,
                content_type="text/plain; charset=utf-8"
            )

            # Kemas kini cache saiz tempatan
            if acc_idx in _cached_bucket_bytes:
                _cached_bucket_bytes[acc_idx] += file_size

            # Gerakkan penunjuk ke akaun seterusnya untuk muat naik berikutnya (Puratakan beban)
            _current_account_pointer = (acc_list_index + 1) % total_accounts

            # Bina URL melalui Cloudflare Worker Reverse Proxy
            proxy_host = CF_WORKER_B2_STORAGE.rstrip("/")
            public_url = f"{proxy_host}/{acc['bucket_name']}/{b2_path}"

            return {
                "url": public_url,
                "account_index": acc["index"],
                "bucket_name": acc["bucket_name"],
                "file_id": uploaded_file.id_,
                "b2_path": b2_path,
                "size_bytes": file_size
            }

        except Exception as e:
            err_str = str(e).lower()
            last_error = e

            # Kesan ralat had transaksi harian Free Tier Backblaze
            if "cap exceeded" in err_str or "transaction" in err_str or "limit" in err_str:
                _exhausted_accounts.add(acc_idx)
                baki_akaun = get_active_accounts_count()
                print(f"⚠️ [B2 Acc {acc_idx}] Cap harian dicapai! Beralih ke akaun sandaran (Baki: {baki_akaun} akaun aktif)...")
            else:
                print(f"⚠️ [B2 Acc {acc_idx}] Ralat semasa muat naik: {e}. Mencuba akaun seterusnya...")
                _exhausted_accounts.add(acc_idx)

            continue

    # Jika semua akaun telah dicuba dalam kitaran ini dan gagal
    _all_accounts_exhausted_flag = True
    raise AllB2AccountsExhaustedException(
        f"❌ Kesemua {total_accounts} akaun B2 tidak dapat digunakan atau telah mencapai transaction cap! (Ralat: {last_error})"
    )