import os
import sys
import time
import random
import importlib
from pathlib import Path
from camoufox.sync_api import Camoufox

# -------------------------------------------------------------
# Penyelarasan Laluan Direktori (sys.path)
# -------------------------------------------------------------
CURRENT_DIR = Path(__file__).resolve().parent           # .../B2-ACC-SCRIPT/TEST_V5
PARENT_DIR = CURRENT_DIR.parent                         # .../B2-ACC-SCRIPT
ROOT_DIR = PARENT_DIR.parent                            # .../stremio-sub-addon
TEST_DIR = PARENT_DIR / "TEST"
V3_DIR = PARENT_DIR / "TEST_V3"                         # Modul rujukan V3

for p in [str(CURRENT_DIR), str(PARENT_DIR), str(ROOT_DIR), str(TEST_DIR), str(V3_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from browser_utils_v5 import (
    human_delay,
    robust_human_type,
    resolve_cloudflare_turnstile,
    dump_page_diagnostics,
    logger,
    wait_for_form_submission,
    is_page_stuck,
)

from captcha_solver_v5 import (
    solve_captcha_target,
    is_any_captcha_active,
    find_and_click_submit,
    smart_reload_and_resubmit,
)

# Import email handler
try:
    email_handler = importlib.import_module("01_b2_email_handler_v2test")
except ModuleNotFoundError:
    email_handler = importlib.import_module("01_b2_email_handler")
# -------------------------------------------------------------
# ALIRAN UTAMA PENDAFTARAN PELAYAR V5 (Camoufox Stealth + Headed Mode)
# -------------------------------------------------------------
def run_step01_signup_v5(target_email: str) -> bool:
    logger.info(f"[FOX] [PELAYAR V5] Memulakan Pendaftaran Awal: {target_email}")
    logger.info(f"[MODE] Headed mode (browser visible) untuk debugging")

    with Camoufox(headless=False, geoip=True) as browser:
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"
        )
        page = context.new_page()

        try:
            target_url = "https://www.backblaze.com/sign-up/cloud-storage?referrer=getstarted"
            logger.info(f"[*] Membuka laman pendaftaran: {target_url}")
            page.goto(target_url, wait_until="load", timeout=60000)
            human_delay(2.5, 4.0, "general")

            resolve_cloudflare_turnstile(page)

            # Semakan sekatan pintu masuk (Gate)
            if "security check" in page.title().lower() or "not so fast" in page.content().lower() or page.locator("#captcha-form").count() > 0:
                logger.warning("[!] Sekatan 'Security Check' dikesan di pintu masuk!")
                solve_captcha_target(page, target_mode="gate")
                page.reload(wait_until="load")
                human_delay(2.5, 4.0, "general")

            try:
                cookie_btn = page.locator("#onetrust-accept-btn-handler, button:has-text('Accept All Cookies')").first
                if cookie_btn.is_visible(timeout=2000):
                    cookie_btn.click()
                    human_delay(1.5, 3.0, "click")
            except Exception:
                pass

            # Mengesan kotak emel
            email_locator = None
            selectors = [
                'input[type="email"]',
                'input[name="email"]',
                'input[id*="email" i]',
                'input[placeholder*="email" i]'
            ]

            start_search = time.time()
            while time.time() - start_search < 35:
                for frame in page.frames:
                    for sel in selectors:
                        try:
                            loc = frame.locator(sel).first
                            if loc.is_visible():
                                email_locator = loc
                                break
                        except Exception:
                            continue
                    if email_locator:
                        break
                if email_locator:
                    break
                page.wait_for_timeout(2000)

            if not email_locator:
                raise TimeoutError("Kotak medan emel tidak ditemui.")

            # Captcha pra-isi
            if page.locator("#emailRecaptcha").is_visible():
                logger.info("[!] Captcha dikesan sebelum mengisi emel.")
                solve_captcha_target(page, target_mode="signup")
                human_delay(2.0, 4.0, "captcha")

            logger.info(f"[OK] Kotak emel ditemui! Menaip sasaran: {target_email}")
            robust_human_type(email_locator, page, target_email)

            # Captcha pasca-isi
            if page.locator("#emailRecaptcha").is_visible():
                logger.info("[!] Captcha muncul sebaik sahaja emel diisi.")
                solve_captcha_target(page, target_mode="signup")
                human_delay(2.0, 4.0, "captcha")

            logger.info("[*] Menekan butang 'Get Started Free'...")
            find_and_click_submit(page)
            human_delay(3.0, 5.0, "click")

            # =============================================================================
            # GELUNG PEMANTAU PINTAR V5 DENGAN SMART REFRESH DAN DOM CHANGE DETECTION
            # =============================================================================
            logger.info("[HOURGLASS] Memulakan pemantau pintar pengalihan laman (Had: 180 saat)...")
            start_watchdog = time.time()
            last_activity_time = time.time()
            last_logged_interval = 0
            refresh_count = 0
            reached_thank_you = False
            last_dom_hash = ""

            # Dapatkan hash DOM awal
            try:
                last_dom_hash = page.evaluate("""() => {
                    return document.body.innerHTML.length + '|' + document.title;
                }""")
            except Exception:
                pass

            while time.time() - start_watchdog < 180:
                elapsed = int(time.time() - start_watchdog)
                curr_url = page.url.lower()

                # 1. Semak kejayaan pendaftaran
                if "sign-up-thank-you" in curr_url or "thank-you" in curr_url:
                    logger.info(f"[OK] Pendaftaran BERJAYA! Tiba di halaman konfirmasi: {page.url}")
                    reached_thank_you = True
                    break

                body_text = ""
                try:
                    body_text = page.locator("body").inner_text().lower()
                except Exception:
                    pass

                if "check your email" in body_text or "we sent a confirmation link" in body_text:
                    logger.info("[OK] Pendaftaran BERJAYA! Mesej konfirmasi ditemui dalam badan laman.")
                    reached_thank_you = True
                    break

                # 2. Semak ralat fatal akaun
                if any(err in body_text for err in ["already in use", "already registered", "account with this email already exists"]):
                    raise RuntimeError(f"Emel {target_email} telah pun didaftarkan sebelum ini.")

                # 3. Heartbeat berkala setiap 10 saat
                if elapsed // 10 > last_logged_interval:
                    last_logged_interval = elapsed // 10
                    logger.info(f"[PEMANTAU {elapsed}s] URL: {page.url[:45]}... | Menunggu maklum balas...")

                # 4. DOM Change Detection - cek sama ada laman 'stuck'
                stuck, new_hash = is_page_stuck(page, last_dom_hash)
                if stuck:
                    logger.warning(f"[WARNING] Laman 'stuck' dikesan - tiada perubahan DOM selama {int(time.time() - last_activity_time)}s")
                last_dom_hash = new_hash

                # 5. Fallback Pintar: Pengesanan Captcha berterusan di setiap kitaran
                captcha_detected, mode = is_any_captcha_active(page)
                if captcha_detected:
                    logger.warning(f"[!] Captcha dikesan aktif semasa pemantauan ({mode.upper()}). Memulakan penyelesaian...")
                    solve_captcha_target(page, target_mode=mode)
                    last_activity_time = time.time()
                    human_delay(2.5, 4.5, "captcha")

                    logger.info("[*] Menekan semula butang hantar selepas penyelesaian Captcha...")
                    find_and_click_submit(page)
                    human_delay(3.0, 5.0, "click")

                # 6. Semak Turnstile
                resolve_cloudflare_turnstile(page, max_retries=1)
# 7. LOGIK REFRESH PINTAR V5: Exponential backoff + DOM stuck detection
                stuck_seconds = time.time() - last_activity_time
                if stuck_seconds >= 30 and refresh_count < 3:
                    refresh_count += 1
                    logger.warning(f"[WARNING] Tiada perubahan dikesan selama {int(stuck_seconds)}s. Melancarkan Smart Refresh V5 #{refresh_count}...")
                    smart_reload_and_resubmit(page, target_email, refresh_count)
                    last_activity_time = time.time()
                    human_delay(3.0, 5.0, "general")
                    
                    # Update DOM hash after refresh
                    try:
                        last_dom_hash = page.evaluate("""() => {
                            return document.body.innerHTML.length + '|' + document.title;
                        }""")
                    except Exception:
                        pass

                page.wait_for_timeout(3000)

            if not reached_thank_you:
                raise TimeoutError("Pendaftaran gagal tiba di laman 'sign-up-thank-you' dalam tempoh 180 saat.")

            # Jeda penutup 10 saat
            logger.info("[HOURGLASS] Halaman thank-you dicapai! Menunggu 10 saat sebelum menutup pelayar...")
            time.sleep(10)
            return True

        except Exception as err:
            logger.error(f"[X] RALAT KRITIKAL: {err}")
            raise err

        finally:
            try:
                page.close()
                context.close()
            except Exception:
                pass


if __name__ == "__main__":
    test_email = "test@example.com"
    run_step01_signup_v5(test_email)