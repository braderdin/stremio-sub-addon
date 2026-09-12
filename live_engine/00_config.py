import os
import json
from pathlib import Path
from dotenv import load_dotenv

# Laluan Folder Utama Projek
LIVE_ENGINE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = LIVE_ENGINE_DIR.parent
DATA_DIR = LIVE_ENGINE_DIR / "data"
TEMP_DIR = LIVE_ENGINE_DIR / "temp"
OUTPUT_DIR = LIVE_ENGINE_DIR / "output"

# Pastikan folder fizikal wujud
for folder in [DATA_DIR, TEMP_DIR, OUTPUT_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

# Muat turun pembolehubah dari .env.local jika wujud (Local Run)
ENV_LOCAL_PATH = PROJECT_ROOT / ".env.local"
if ENV_LOCAL_PATH.exists():
    load_dotenv(ENV_LOCAL_PATH)
else:
    load_dotenv()

# Tetapan Keselamatan & GitHub API
ADDON_SECRET_TOKEN = os.getenv("ADDON_SECRET_TOKEN", "")
CF_WORKER_URL = os.getenv("CF_WORKER_URL", "")
CF_WORKER_B2_STORAGE = os.getenv("CF_WORKER_B2_STORAGE", "https://b2-private-stremio-sub-addon.braderdin360.workers.dev")
GH_PAT = os.getenv("GH_PAT", "")
GH_OWNER = os.getenv("GH_OWNER", "braderdin")
GH_REPO = os.getenv("GH_REPO", "stremio-sub-addon")

# Tetapan Upstash Redis & Search
UPSTASH_REDIS_REST_URL = os.getenv("UPSTASH_REDIS_REST_URL", "")
UPSTASH_REDIS_REST_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")
REDIS_TCP_URL = os.getenv("REDIS_TCP_URL", "")

# Limit Storan B2 (9.5 GB dalam Bytes untuk elak caj overage Freetier)
B2_MAX_BYTES_PER_ACCOUNT = int(9.5 * 1024 * 1024 * 1024)

def get_b2_accounts() -> list:
    """
    Mengesan dan mengumpul senarai akaun B2.
    Keutamaan 1: Membaca format JSON tunggal daripada B2_MULTI_ACCOUNT_JSON.
    Keutamaan 2 (Fallback): Mengimbas format B2_ACC1_* hingga B2_ACC50_* (.env.local / legacy env).
    """
    accounts = []

    # 1. Semak jika pembolehubah JSON wujud (GitHub Actions / Cloudflare Secret)
    multi_json_raw = os.getenv("B2_MULTI_ACCOUNT_JSON", "").strip()
    if multi_json_raw:
        try:
            parsed = json.loads(multi_json_raw)
            if isinstance(parsed, list) and len(parsed) > 0:
                for idx, item in enumerate(parsed, 1):
                    if isinstance(item, dict):
                        key_id = str(item.get("key_id", "")).strip()
                        app_key = str(item.get("app_key", "")).strip()
                        bucket_name = str(item.get("bucket_name", "")).strip()

                        if key_id and app_key and bucket_name:
                            accounts.append({
                                "index": item.get("index", idx),
                                "key_id": key_id,
                                "app_key": app_key,
                                "bucket_name": bucket_name,
                                "bucket_id": str(item.get("bucket_id", "")).strip(),
                                "endpoint": str(item.get("endpoint", "s3.us-west-004.backblazeb2.com")).strip()
                            })

                if accounts:
                    return accounts
        except Exception as e:
            print(f"⚠️ Ralat membaca B2_MULTI_ACCOUNT_JSON: {e}. Beralih ke format B2_ACC...")

    # 2. Fallback: Baca format lama B2_ACC1_* hingga B2_ACC50_*
    for index in range(1, 51):
        key_id = os.getenv(f"B2_ACC{index}_KEY_ID")
        app_key = os.getenv(f"B2_ACC{index}_APP_KEY")
        bucket_name = os.getenv(f"B2_ACC{index}_BUCKET_NAME")
        bucket_id = os.getenv(f"B2_ACC{index}_BUCKET_ID", "")
        endpoint = os.getenv(f"B2_ACC{index}_S3_API_ENDPOINT", "s3.us-west-004.backblazeb2.com")

        if key_id and app_key and bucket_name:
            accounts.append({
                "index": index,
                "key_id": key_id.strip(),
                "app_key": app_key.strip(),
                "bucket_name": bucket_name.strip(),
                "bucket_id": bucket_id.strip() if bucket_id else "",
                "endpoint": endpoint.strip()
            })

    return accounts

B2_ACCOUNTS = get_b2_accounts()