import os
import sys
import time
import random
import tempfile
import urllib.request
from pathlib import Path

import pydub
import speech_recognition
from camoufox.sync_api import Camoufox

# -------------------------------------------------------------
# Penyelarasan Jalur Direktori (sys.path)
# -------------------------------------------------------------
CURRENT_DIR = Path(__file__).resolve().parent           # .../B2-ACC-SCRIPT/TEST_V4
PARENT_DIR = CURRENT_DIR.parent                         # .../B2-ACC-SCRIPT
ROOT_DIR = PARENT_DIR.parent                            # .../stremio-sub-addon
TEST_DIR = PARENT_DIR / "TEST"
V3_DIR = PARENT_DIR / "TEST_V3"                         # Modul rujukan V3

for p in [str(CURRENT_DIR), str(PARENT_DIR), str(ROOT_DIR), str(TEST_DIR), str(V3_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from browser_utils_v4 import (
    robust_human_type,
    resolve_cloudflare_turnstile,
    logger
)


# -------------------------------------------------------------
# Jeda Manusiawi Persis V3 (Lebih Relaks & Menghindari Sekatan Bot)
# -------------------------------------------------------------
def human_delay(min_s: float = 2.0, max_s: float = 4.5):
    """Jeda rawak beritma manusia untuk kestabilan interaksi DOM."""
    time.sleep(random.uniform(min_s, max_s))


def get_recaptcha_token(page) -> str:
    """Mengekstrak token g-recaptcha-response dari DOM."""
    try:
        return page.evaluate("""() => {
            const el = document.querySelector('#emailRecaptcha textarea[name="g-recaptcha-response"]')
                       || document.querySelector('textarea[name="g-recaptcha-response"]')
                       || document.getElementById('g-recaptcha-response');
            return el ? el.value : '';
        }""")
    except Exception:
        return ""


def apply_recaptcha_token_to_page(page, token: str):
    """Menyuntik token verifikasi reCAPTCHA dengan jeda penstabilan V3."""
    if not token:
        return

    logger.info(f"[✓] Token reCAPTCHA diperoleh: {token[:25]}...")
    page_title = page.title().lower()
    page_html = page.content().lower()

    if "security check" in page_title or "not so fast" in page_html:
        logger.info("[✓] Menghantar token pengesahan onSubmit ke Backblaze...")
        page.evaluate("token => { if (typeof onSubmit === 'function') onSubmit(token); }", token)
        human_delay(3.0, 5.0)
    else:
        logger.info("[✓] Menyuntik token ke dalam window.emailFormIframe...")
        page.evaluate("""token => {
            if (window.emailFormIframe && typeof window.emailFormIframe.updateProps === 'function') {
                window.emailFormIframe.updateProps({ fieldValues: { formCaptchaToken: token } });
            }
        }""", token)
        human_delay(3.0, 5.0)


# -------------------------------------------------------------
# Enjin Pengesanan & Penyelesaian Captcha V3+ (Dengan Jeda Penuh)
# -------------------------------------------------------------
def solve_captcha_target(page, target_mode: str = "signup", max_attempts: int = 4) -> bool:
    """
    Penyelesai Captcha Beritma V3:
    - 8 kitaran carian bframe dengan jeda 3000ms.
    - Jeda penuh 2.5s - 5.0s semasa interaksi audio dan token.
    - Sokongan pembersihan fail audio sementara yang selamat.
    """
    logger.info(f"[*] [PENYELESAI CAPTCHA] Menangani cabaran mod: {target_mode.upper()}...")

    for attempt in range(1, max_attempts + 1):
        logger.info(f"[*] Percubaan penyelesaian #{attempt}/{max_attempts}...")

        # 1. Kenal pasti anchor frame
        anchor_frame = None
        for frame in page.frames:
            f_url = frame.url.lower()
            if "recaptcha" in f_url and "anchor" in f_url and "size=invisible" not in f_url:
                if target_mode == "signup" and "sa=signup" in f_url:
                    anchor_frame = frame
                    break
                elif target_mode == "gate" and ("sa=login" in f_url or "sa=signup" not in f_url):
                    anchor_frame = frame
                    break

        if not anchor_frame:
            for frame in page.frames:
                f_url = frame.url.lower()
                if "recaptcha" in f_url and "anchor" in f_url and "size=invisible" not in f_url:
                    anchor_frame = frame
                    break

        # 2. Klik kotak semakan anchor jika belum hijau
        if anchor_frame:
            try:
                chk = anchor_frame.locator("#recaptcha-anchor, .recaptcha-checkbox").first
                if chk.is_visible(timeout=3000):
                    if chk.get_attribute("aria-checked") != "true":
                        logger.info(f"[*] Mengklik kotak semak Captcha ({target_mode})...")
                        chk.click(force=True)
                        human_delay(2.5, 4.0)

                    if chk.get_attribute("aria-checked") == "true":
                        logger.info(f"[✓] Kotak semak {target_mode} disahkan hijau!")
                        token = get_recaptcha_token(page)
                        apply_recaptcha_token_to_page(page, token)
                        return True
            except Exception:
                pass

        token = get_recaptcha_token(page)
        if token:
            apply_recaptcha_token_to_page(page, token)
            return True

        # 3. Cari bframe cabaran (Ikon Audio / Imej) - 8 Kitaran persis V3
        bframe = None
        for _ in range(8):
            for frame in page.frames:
                f_url = frame.url.lower()
                if "recaptcha" in f_url and "bframe" in f_url:
                    bframe = frame
                    break
            if bframe:
                break
            page.wait_for_timeout(3000)

        if not bframe:
            page.wait_for_timeout(3000)
            continue

        # 4. Pintasan Audio Mengikut Format Stabil V3
        try:
            audio_btn = bframe.locator("#recaptcha-audio-button").first
            if audio_btn.is_visible(timeout=7000):
                logger.info("[*] Menekan butang audio reCAPTCHA (ikon fon kepala)...")
                audio_btn.click(force=True)
                human_delay(2.5, 4.0)

                dos_err = bframe.locator(".rc-doscaptcha-header, .rc-audiochallenge-error-message").first
                if dos_err.is_visible(timeout=3000):
                    logger.warning("[!] Sekatan audio Google dikesan. Memuat semula cabaran...")
                    reload_btn = bframe.locator("#recaptcha-reload-button").first
                    if reload_btn.is_visible(timeout=3000):
                        reload_btn.click(force=True)
                        human_delay(2.5, 4.0)
                    continue

                audio_url = None
                audio_el = bframe.locator(".rc-audiochallenge-tdownload-link, a[href*='payload/audio.mp3'], a[href*='audio']").first
                if audio_el.is_visible(timeout=4000):
                    audio_url = audio_el.get_attribute("href")
                else:
                    audio_src = bframe.locator("audio#player, #audio-source").first
                    if audio_src.count() > 0:
                        audio_url = audio_src.get_attribute("src")

                if audio_url:
                    logger.info("[+] Memuat turun audio MP3 dan menukar ke format WAV...")
                    with tempfile.TemporaryDirectory() as td:
                        mp3_path = Path(td) / f"cap_{random.randint(1000, 9999)}.mp3"
                        wav_path = Path(td) / f"cap_{random.randint(1000, 9999)}.wav"

                        req = urllib.request.Request(
                            audio_url,
                            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                        )
                        with urllib.request.urlopen(req) as resp, open(mp3_path, "wb") as f_out:
                            f_out.write(resp.read())

                        sound = pydub.AudioSegment.from_mp3(str(mp3_path))
                        sound.export(str(wav_path), format="wav")

                        recognizer = speech_recognition.Recognizer()
                        with speech_recognition.AudioFile(str(wav_path)) as src:
                            audio_data = recognizer.record(src)
                            transcribed_text = recognizer.recognize_google(audio_data)

                        logger.info(f"[✓] Teks audio berjaya dikesan: '{transcribed_text}'")

                        resp_input = bframe.locator("#audio-response").first
                        if resp_input.is_visible(timeout=3000):
                            resp_input.fill(transcribed_text)
                            human_delay(1.5, 4.5)
                            bframe.locator("#recaptcha-verify-button").first.click(force=True)
                            human_delay(1.5, 5.0)

                            token = get_recaptcha_token(page)
                            if token:
                                apply_recaptcha_token_to_page(page, token)
                                return True
        except Exception as ae:
            logger.debug(f"[*] Info percubaan audio: {ae}")

        token = get_recaptcha_token(page)
        if token:
            apply_recaptcha_token_to_page(page, token)
            return True

        # Muat semula cabaran sekiranya belum selesai
        try:
            reload_btn = bframe.locator("#recaptcha-reload-button").first
            if reload_btn.is_visible(timeout=3000):
                reload_btn.click(force=True)
                human_delay(2.0, 4.0)
        except Exception:
            pass

    return False


def is_any_captcha_active(page) -> tuple[bool, str]:
    """
    Mengesan sebarang jenis Captcha secara aktif (Gate, Form, BFrame, atau Anchor).
    """
    title = page.title().lower()
    content = ""
    try:
        content = page.content().lower()
    except Exception:
        pass

    if "security check" in title or "not so fast" in content or page.locator("#captcha-form").count() > 0:
        return True, "gate"

    try:
        if page.locator("#emailRecaptcha").is_visible(timeout=1000):
            return True, "signup"
    except Exception:
        pass

    for frame in page.frames:
        f_url = frame.url.lower()
        if "recaptcha" in f_url:
            if "bframe" in f_url:
                return True, "signup"
            if "anchor" in f_url:
                try:
                    chk = frame.locator("#recaptcha-anchor, .recaptcha-checkbox").first
                    if chk.is_visible(timeout=1000) and chk.get_attribute("aria-checked") != "true":
                        return True, "signup"
                except Exception:
                    pass

    return False, ""


def find_and_click_submit(page) -> bool:
    """Mencari dan menekan butang hantar di semua peringkat frame."""
    submit_selectors = [
        'button#form-submit',
        'button:has-text("Get Started Free")',
        'input[value="Get Started Free"]',
        'button[type="submit"]'
    ]
    for frame in page.frames:
        for sel in submit_selectors:
            try:
                btn = frame.locator(sel).first
                if btn.is_visible(timeout=1000):
                    btn.scroll_into_view_if_needed()
                    btn.click(force=True)
                    human_delay(2.0, 3.5)
                    return True
            except Exception:
                continue
    return False


def smart_reload_and_resubmit(page, target_email: str) -> bool:
    """
    Logik Refresh Pintar: Memuat semula laman, mengendalikan Turnstile/Gate,
    mengisi semula emel, menyelesaikan Captcha jika muncul, dan menghantar semula borang.
    """
    logger.warning("🔄 [SMART REFRESH] Laman tidak bertindak balas. Memuat semula dan memulakan semula borang...")
    page.reload(wait_until="load", timeout=45000)
    human_delay(3.0, 5.0)

    resolve_cloudflare_turnstile(page)

    captcha_active, mode = is_any_captcha_active(page)
    if captcha_active:
        solve_captcha_target(page, target_mode=mode)
        human_delay(2.0, 4.0)

    selectors = [
        'input[type="email"]',
        'input[name="email"]',
        'input[id*="email" i]',
        'input[placeholder*="email" i]'
    ]
    email_locator = None
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

    if not email_locator:
        logger.error("[!] Medan input emel tidak ditemui selepas muat semula.")
        return False

    logger.info(f"[*] Mengisi semula emel sasaran: {target_email}")
    robust_human_type(email_locator, page, target_email)
    human_delay(1.5, 3.0)

    captcha_active, mode = is_any_captcha_active(page)
    if captcha_active:
        solve_captcha_target(page, target_mode=mode)
        human_delay(2.0, 4.0)

    logger.info("[*] Menekan butang hantar selepas Smart Refresh...")
    find_and_click_submit(page)
    return True


# -------------------------------------------------------------
# Aliran Utama Pendaftaran Pelayar 1 (Camoufox Stealth)
# -------------------------------------------------------------
def run_step01_signup(target_email: str) -> bool:
    logger.info(f"🦊 [PELAYAR 1: V4 HYBRID] Memulakan Pendaftaran Awal: {target_email}")

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
            human_delay(2.5, 4.0)

            resolve_cloudflare_turnstile(page)

            # Semakan sekatan pintu masuk
            if "security check" in page.title().lower() or "not so fast" in page.content().lower() or page.locator("#captcha-form").count() > 0:
                logger.warning("[!] Sekatan 'Security Check' dikesan di pintu masuk!")
                solve_captcha_target(page, target_mode="gate")
                page.reload(wait_until="load")
                human_delay(2.5, 4.0)

            try:
                cookie_btn = page.locator("#onetrust-accept-btn-handler, button:has-text('Accept All Cookies')").first
                if cookie_btn.is_visible(timeout=2000):
                    cookie_btn.click()
                    human_delay(1.5, 3.0)
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
                human_delay(2.0, 4.0)

            logger.info(f"[✓] Kotak emel ditemui! Menaip sasaran: {target_email}")
            robust_human_type(email_locator, page, target_email)

            # Captcha pasca-isi
            if page.locator("#emailRecaptcha").is_visible():
                logger.info("[!] Captcha muncul sebaik sahaja emel diisi.")
                solve_captcha_target(page, target_mode="signup")
                human_delay(2.0, 4.0)

            logger.info("[*] Menekan butang 'Get Started Free'...")
            find_and_click_submit(page)
            human_delay(3.0, 5.0)

            # -------------------------------------------------------------
            # GELUNG PEMANTAU PINTAR DENGAN SMART REFRESH
            # -------------------------------------------------------------
            logger.info("⏳ Memulakan pemantau pintar pengalihan laman (Had: 180 saat)...")
            start_watchdog = time.time()
            last_activity_time = time.time()
            last_logged_interval = 0
            refresh_count = 0
            reached_thank_you = False

            while time.time() - start_watchdog < 180:
                elapsed = int(time.time() - start_watchdog)
                curr_url = page.url.lower()

                # 1. Semak kejayaan pendaftaran
                if "sign-up-thank-you" in curr_url or "thank-you" in curr_url:
                    logger.info(f"[✓] Pendaftaran BERJAYA! Tiba di halaman konfirmasi: {page.url}")
                    reached_thank_you = True
                    break

                body_text = ""
                try:
                    body_text = page.locator("body").inner_text().lower()
                except Exception:
                    pass

                if "check your email" in body_text or "we sent a confirmation link" in body_text:
                    logger.info("[✓] Pendaftaran BERJAYA! Mesej konfirmasi ditemui dalam badan laman.")
                    reached_thank_you = True
                    break

                # 2. Semak ralat fatal akaun
                if any(err in body_text for err in ["already in use", "already registered", "account with this email already exists"]):
                    raise RuntimeError(f"Emel {target_email} telah pun didaftarkan sebelum ini.")

                # 3. Heartbeat berkala setiap 10 saat
                if elapsed // 10 > last_logged_interval:
                    last_logged_interval = elapsed // 10
                    logger.info(f"[Pemantau {elapsed}s] URL: {page.url[:45]}... | Menunggu maklum balas...")

                # 4. Fallback Pintar: Pengesanan Captcha berterusan di setiap kitaran
                captcha_detected, mode = is_any_captcha_active(page)
                if captcha_detected:
                    logger.warning(f"[!] Captcha dikesan aktif semasa pemantauan ({mode.upper()}). Memulakan penyelesaian...")
                    solve_captcha_target(page, target_mode=mode)
                    last_activity_time = time.time()
                    human_delay(2.5, 4.5)

                    logger.info("[*] Menekan semula butang hantar selepas penyelesaian Captcha...")
                    find_and_click_submit(page)
                    human_delay(3.0, 5.0)

                # 5. Semak Turnstile
                resolve_cloudflare_turnstile(page, max_retries=1)

                # 6. LOGIK REFRESH PINTAR: Jika laman statik melebihi 30 saat tanpa perubahan
                stuck_seconds = time.time() - last_activity_time
                if stuck_seconds >= 30 and refresh_count < 3:
                    refresh_count += 1
                    logger.warning(f"[⚠️] Tiada perubahan dikesan selama {int(stuck_seconds)}s. Melancarkan Smart Refresh #{refresh_count}...")
                    smart_reload_and_resubmit(page, target_email)
                    last_activity_time = time.time()
                    human_delay(3.0, 5.0)

                page.wait_for_timeout(3000)

            if not reached_thank_you:
                raise TimeoutError("Pendaftaran gagal tiba di laman 'sign-up-thank-you' dalam tempoh 180 saat.")

            # Jeda penutup 10 saat persis V3
            logger.info("⏳ Halaman thank-you dicapai! Menunggu 10 saat sebelum menutup pelayar...")
            time.sleep(10)
            return True

        finally:
            try:
                page.close()
                context.close()
            except Exception:
                pass


if __name__ == "__main__":
    test_email = "test@example.com"
    run_step01_signup(test_email)