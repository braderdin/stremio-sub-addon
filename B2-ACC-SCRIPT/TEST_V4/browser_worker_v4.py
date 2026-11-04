import sys
import importlib
from pathlib import Path
from camoufox.sync_api import Camoufox

# -------------------------------------------------------------
# Penyelarasan Laluan Direktori (sys.path)
# -------------------------------------------------------------
CURRENT_DIR = Path(__file__).resolve().parent           # .../B2-ACC-SCRIPT/TEST_V4
PARENT_DIR = CURRENT_DIR.parent                         # .../B2-ACC-SCRIPT
ROOT_DIR = PARENT_DIR.parent                            # .../stremio-sub-addon
TEST_DIR = PARENT_DIR / "TEST"
V3_DIR = PARENT_DIR / "TEST_V3"                         # Laluan ke modul V3

for p in [str(CURRENT_DIR), str(PARENT_DIR), str(ROOT_DIR), str(TEST_DIR), str(V3_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from browser_utils_v4 import (
    human_delay,
    human_type,
    resolve_cloudflare_turnstile,
    dump_page_diagnostics,
    logger
)

# Konfigurasi & Dependensi Modul Eksternal
b2_config = importlib.import_module("00_b2_config")
B2_PASSWORD = b2_config.B2_PASSWORD
TEMP_DIR = b2_config.TEMP_DIR

try:
    email_handler = importlib.import_module("01_b2_email_handler_v2test")
except ModuleNotFoundError:
    email_handler = importlib.import_module("01_b2_email_handler")

step01 = importlib.import_module("02_b2_step01_signup_v3")
step02 = importlib.import_module("02_b2_step02_setup")
step03 = importlib.import_module("02_b2_step03_login_2fa")
step04 = importlib.import_module("02_b2_step04_bucket_cors")
step05 = importlib.import_module("02_b2_step05_app_keys")


def process_b2_account(acc_data: dict) -> dict:
    """Menyelesaikan konfigurasi lanjutan akun B2 (Aktivasi, 2FA, Bucket, API Key)."""
    target_email = acc_data["email"]
    bucket_name = acc_data["bucket_name"]
    key_name = acc_data["key_name"]
    acc_idx = acc_data["index"]

    logger.info(f"Memproses akun [{acc_idx}]: {target_email}")

    with Camoufox(headless=False, geoip=True) as browser:
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"
        )
        page = context.new_page()

        try:
            page.goto("https://secure.backblaze.com/user_signin.htm", wait_until="load", timeout=25000)
            human_delay(1.5, 4.0)

            email_field = page.locator('#email-field, input[name="email-field"]').first
            is_existing_account = False

            if acc_idx == 1 and email_field.is_visible():
                logger.info(f"Akaun 1 ({target_email}) terdeteksi siap masuk. Melewati langkah aktivasi emel.")
                is_existing_account = True

            if not is_existing_account:
                logger.info("[LANGKAH 1] Memantau tautan verifikasi aktivasi...")
                activation_link = step01.run_step01(
                    page, target_email, email_handler, human_delay, human_type, resolve_cloudflare_turnstile
                )

                logger.info("[LANGKAH 2] Konfigurasi kata sandi dan wilayah...")
                step02.run_step02(
                    page, activation_link, B2_PASSWORD, human_delay, human_type, resolve_cloudflare_turnstile
                )

            logger.info("[LANGKAH 3] Proses autentikasi 2FA...")
            step03.run_step03(
                page, target_email, B2_PASSWORD, email_handler, human_delay, human_type, resolve_cloudflare_turnstile
            )

            logger.info("[LANGKAH 4] Pembuatan bucket dan aturan CORS...")
            bucket_id, endpoint = step04.run_step04(
                page, bucket_name, human_delay, human_type
            )

            logger.info("[LANGKAH 5] Pembuatan kunci aplikasi (Application Keys)...")
            key_id, app_key = step05.run_step05(
                page, key_name, human_delay, human_type
            )

            logger.info(f"Akun [{acc_idx}] {target_email} berhasil diselesaikan 100%.")

            return {
                "index": acc_idx,
                "email": target_email,
                "B2_KEY_ID": key_id,
                "B2_APP_KEY": app_key,
                "B2_BUCKET_NAME": bucket_name,
                "B2_BUCKET_ID": bucket_id,
                "B2_S3_API_ENDPOINT": endpoint,
                "B2_KEY_NAME": key_name
            }

        except Exception as err:
            logger.error(f"Terjadi kegagalan pemrosesan pada akun [{acc_idx}]: {err}")
            err_img = TEMP_DIR / f"crash_acc_{acc_idx}.png"
            try:
                page.screenshot(path=str(err_img), full_page=True)
                logger.info(f"Tangkapan layar ralat disimpan di: {err_img}")
            except Exception:
                pass
            dump_page_diagnostics(page, TEMP_DIR, acc_idx)
            raise err

        finally:
            try:
                page.close()
                context.close()
            except Exception:
                pass