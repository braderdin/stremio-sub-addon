import os
import sys
import time
import json
import random
import importlib
from pathlib import Path

# -------------------------------------------------------------
# Penyelarasan Laluan Direktori (sys.path)
# -------------------------------------------------------------
CURRENT_DIR = Path(__file__).resolve().parent           # .../B2-ACC-SCRIPT/TEST
PARENT_DIR = CURRENT_DIR.parent                         # .../B2-ACC-SCRIPT
ROOT_DIR = PARENT_DIR.parent                            # .../stremio-sub-addon
BYPASS_DIR = ROOT_DIR / "Recaptcha-Bypass"

for p in [str(PARENT_DIR), str(CURRENT_DIR), str(ROOT_DIR), str(BYPASS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

# Pustaka Pelayar Asal
from camoufox.sync_api import Camoufox

# Import modul tetapan & emel asal
b2_config = importlib.import_module("00_b2_config")
B2_PASSWORD = b2_config.B2_PASSWORD
TEMP_DIR = b2_config.TEMP_DIR

email_handler = importlib.import_module("01_b2_email_handler_v2test")

# Import modular steps asal (Langkah 1 menggunakan versi klon v2test)
step01 = importlib.import_module("02_b2_step01_signup_v2test")
step02 = importlib.import_module("02_b2_step02_setup")
step03 = importlib.import_module("02_b2_step03_login_2fa")
step04 = importlib.import_module("02_b2_step04_bucket_cors")
step05 = importlib.import_module("02_b2_step05_app_keys")


# -------------------------------------------------------------
# Fungsi Bantuan Asal (Kekal 100%)
# -------------------------------------------------------------
def human_delay(min_s: float = 0.5, max_s: float = 1.5):
    time.sleep(random.uniform(min_s, max_s))

def human_type(locator, text: str):
    locator.click()
    human_delay(0.2, 0.4)
    locator.press_sequentially(text, delay=random.randint(45, 85))
    human_delay(0.3, 0.6)

def resolve_cloudflare_turnstile(page, max_retries: int = 20):
    for _ in range(max_retries):
        page.wait_for_timeout(1000)
        try:
            for frame in page.frames:
                if "challenges.cloudflare.com" in frame.url or "turnstile" in frame.url:
                    chk = frame.query_selector("input[type=checkbox], .ctp-checkbox-label, #challenge-stage")
                    if chk:
                        chk.click()
                        page.wait_for_timeout(1500)

            content = page.content().lower()
            if "just a moment" not in content and "attention required" not in content:
                break
        except Exception:
            continue

def dump_page_diagnostics(page, acc_idx: int):
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        html_file = TEMP_DIR / f"page_dump_acc_{acc_idx}.html"
        html_file.write_text(page.content(), encoding="utf-8")
        print(f"[🔍] Fail HTML penuh disimpan di: {html_file}")
    except Exception as e:
        print(f"[!] Gagal simpan HTML: {e}")

    try:
        dump_data = {"url": page.url, "title": page.title(), "frames": []}
        for idx, frame in enumerate(page.frames):
            try:
                elements = frame.evaluate("""() => {
                    return Array.from(document.querySelectorAll('input, button, form, iframe, a')).map(el => ({
                        tag: el.tagName.toLowerCase(),
                        type: el.getAttribute('type') || null,
                        name: el.getAttribute('name') || null,
                        id: el.getAttribute('id') || null,
                        placeholder: el.getAttribute('placeholder') || null,
                        text: (el.innerText || el.value || '').trim().slice(0, 100),
                        classes: el.className || null
                    }));
                }""")
                dump_data["frames"].append({"frame_index": idx, "frame_url": frame.url, "elements": elements})
            except Exception as fe:
                dump_data["frames"].append({"frame_index": idx, "error": str(fe)})

        json_file = TEMP_DIR / f"dom_structure_acc_{acc_idx}.json"
        with open(json_file, "w", encoding="utf-8") as jf:
            json.dump(dump_data, jf, indent=2, ensure_ascii=False)
        print(f"[🔍] Struktur DOM JSON disimpan di: {json_file}")
    except Exception as e:
        print(f"[!] Gagal simpan JSON diagnostik: {e}")


# -------------------------------------------------------------
# Aliran Utama Pengendali Akaun (process_b2_account)
# -------------------------------------------------------------
def process_b2_account(acc_data: dict) -> dict:
    target_email = acc_data["email"]
    bucket_name = acc_data["bucket_name"]
    key_name = acc_data["key_name"]
    acc_idx = acc_data["index"]

    with Camoufox(headless=True, geoip=True) as browser:
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"
        )
        page = context.new_page()

        try:
            # -------------------------------------------------------------
            # LOGIK RESUME PINTAR (Jika akaun sudah dicipta seperti Akaun 012)
            # -------------------------------------------------------------
            page.goto("https://secure.backblaze.com/user_signin.htm", wait_until="load", timeout=45000)
            human_delay(1.5, 2.5)

            email_field = page.locator('#email-field, input[name="email-field"]').first
            is_existing_account = False
            
            if acc_idx == 1 and email_field.is_visible():
                print(f"[✓] Mengesan Akaun 1 ({target_email}) telah dicipta. Melangkau Langkah 1 & 2...")
                is_existing_account = True

            if not is_existing_account:
                # LANGKAH 1: Signup & Pautan (Termasuk semakan dan penyelesaian captcha di dalamnya)
                activation_link = step01.run_step01(
                    page, target_email, email_handler, human_delay, human_type, resolve_cloudflare_turnstile
                )

                # LANGKAH 2: Kata Laluan, Region US West & Persetujuan Terma
                step02.run_step02(
                    page, activation_link, B2_PASSWORD, human_delay, human_type, resolve_cloudflare_turnstile
                )

            # LANGKAH 3: Log Masuk & Kod 2FA
            step03.run_step03(
                page, target_email, B2_PASSWORD, email_handler, human_delay, human_type, resolve_cloudflare_turnstile
            )

            # LANGKAH 4: Cipta Private Bucket & Tetapkan CORS
            bucket_id, endpoint = step04.run_step04(
                page, bucket_name, human_delay, human_type
            )

            # LANGKAH 5: Buat Kunci Aplikasi & Ekstrak Kredensial
            key_id, app_key = step05.run_step05(
                page, key_name, human_delay, human_type
            )

            print(f"[🎉] TAHNIAH! Akaun [{acc_idx}] {target_email} selesai 100%!")

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
            err_img = TEMP_DIR / f"crash_acc_{acc_idx}.png"
            try:
                page.screenshot(path=str(err_img), full_page=True)
                print(f"[!] Ralat dikesan. Paparan skrin disimpan di: {err_img}")
            except Exception:
                pass
            dump_page_diagnostics(page, acc_idx)
            raise err

        finally:
            try:
                page.close()
                context.close()
            except Exception:
                pass