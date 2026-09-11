import os
import sys
import time
from pathlib import Path

# Masukkan laluan induk Recaptcha-Bypass ke dalam modul carian
parent_dir = str(Path(__file__).resolve().parent.parent)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from DrissionPage import ChromiumPage, ChromiumOptions
from RecaptchaSolver import RecaptchaSolver

def test_recaptcha_demo():
    print("\n=== [02] Ujian Pintasan reCAPTCHA Demo (Live) ===")
    
    options = ChromiumOptions()
    chrome_args = [
        "-no-first-run",
        "-force-color-profile=srgb",
        "-metrics-recording-only",
        "-password-store=basic",
        "-use-mock-keychain",
        "-export-tagged-pdf",
        "-no-default-browser-check",
        "-disable-background-mode",
        "-enable-features=NetworkService,NetworkServiceInProcess",
        "-disable-features=FlashDeprecationWarning",
        "-deny-permission-prompts",
        "-disable-gpu",
        "-accept-lang=en-US",
        "--disable-usage-stats",
        "--disable-crash-reporter",
        "--no-sandbox"
    ]
    for arg in chrome_args:
        options.set_argument(arg)

    driver = None
    try:
        print("[INFO] Melancarkan pelayar DrissionPage...")
        driver = ChromiumPage(addr_or_opts=options)
        solver = RecaptchaSolver(driver)

        target_url = "https://www.google.com/recaptcha/api2/demo"
        print(f"[INFO] Menavigasi ke laman sasaran: {target_url}")
        driver.get(target_url)

        t0 = time.time()
        print("[INFO] Memulakan proses pintasan captcha...")
        solver.solveCaptcha()
        elapsed = time.time() - t0
        print(f"[PASSED] reCAPTCHA berjaya diselesaikan dalam masa {elapsed:.2f} saat.")

        submit_btn = driver.ele("#recaptcha-demo-submit")
        if submit_btn:
            submit_btn.click()
            print("[PASSED] Borang pengesahan berjaya dihantar.")
            time.sleep(2)

        return True

    except Exception as err:
        print(f"[FAILED] Kegagalan pintasan: {err}")
        return False
    finally:
        if driver:
            driver.close()
            print("[INFO] Sesi pelayar ditutup.")

if __name__ == "__main__":
    success = test_recaptcha_demo()
    sys.exit(0 if success else 1)