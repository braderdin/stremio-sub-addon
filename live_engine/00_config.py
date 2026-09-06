import os
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
    Mengesan dan mengumpul semua senarai akaun B2 dari B2_ACC1_* hingga B2_ACC20_*
    """
    accounts = []
    index = 1
    while True:
        key_id = os.getenv(f"B2_ACC{index}_KEY_ID")
        app_key = os.getenv(f"B2_ACC{index}_APP_KEY")
        bucket_name = os.getenv(f"B2_ACC{index}_BUCKET_NAME")
        bucket_id = os.getenv(f"B2_ACC{index}_BUCKET_ID")
        endpoint = os.getenv(f"B2_ACC{index}_S3_API_ENDPOINT", "s3.us-west-004.backblazeb2.com")

        if not key_id or not app_key or not bucket_name:
            break

        accounts.append({
            "index": index,
            "key_id": key_id,
            "app_key": app_key,
            "bucket_name": bucket_name,
            "bucket_id": bucket_id,
            "endpoint": endpoint
        })
        index += 1
    return accounts

B2_ACCOUNTS = get_b2_accounts()