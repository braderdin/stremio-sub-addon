import os
import sys
import time
import random
import tempfile
import urllib.request
from pathlib import Path

# Kebergantungan Pengecaman Audio Asal V2
import pydub
import speech_recognition

# Enjin Pelayar Stealth Camoufox
from camoufox.sync_api import Camoufox

# -------------------------------------------------------------
# Penyelarasan Jalur Direktori (sys.path)
# -------------------------------------------------------------
CURRENT_DIR = Path(__file__).resolve().parent           # .../B2-ACC-SCRIPT/TEST_V3
PARENT_DIR = CURRENT_DIR.parent                         # .../B2-ACC-SCRIPT
ROOT_DIR = PARENT_DIR.parent                            # .../stremio-sub-addon
TEST_DIR = PARENT_DIR / "TEST"

for p in [str(CURRENT_DIR), str(PARENT_DIR), str(ROOT_DIR), str(TEST_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)


# -------------------------------------------------------------
# Ritme Manusiawi & Pengendalian Input Emel Presisi
# -------------------------------------------------------------
def human_delay(min_s: float = 2.5, max_s: float = 4.5):
    time.sleep(random.uniform(min_s, max_s))


def robust_human_type(locator, page, text: str):
    """
    Menaip emel mengikut ritme manusiawi dan mencetuskan acara DOM penuh
    bagi menghapuskan ralat merah 'Please enter a valid email address'.
    """
    locator.scroll_into_view_if_needed()
    locator.click()
    human_delay(0.8, 1.5)

    # Kosongkan medan input terlebih dahulu
    locator.fill("")
    time.sleep(0.3)

    # Taip satu persatu aksara
    locator.press_sequentially(text, delay=random.randint(110, 200))
    human_delay(1.0, 1.8)

    # Semak sekiranya ada aksara tertinggal
    if locator.input_value() != text:
        print(f"[!] Emel tidak lengkap. Mengisi semula nilai tepat: {text}")
        locator.fill(text)
        human_delay(0.6, 1.0)

    # Cetuskan event agar validasi borang React Backblaze mengiktiraf emel
    try:
        locator.dispatch_event("input")
        locator.dispatch_event("change")
        locator.press("Tab")
    except Exception:
        pass
    human_delay(1.5, 2.5)

    # Semak jika amaran merah masih wujud
    err_notice = page.locator("text=/Please enter a valid email address/i").first
    try:
        if err_notice.is_visible(timeout=1000):
            print("[!] Ralat merah dikesan. Memaksa pengemaskinian status borang...")
            locator.fill(text)
            locator.dispatch_event("input")
            locator.dispatch_event("change")
            locator.press("Tab")
            human_delay(1.5, 2.0)
    except Exception:
        pass


# -------------------------------------------------------------
# Sistem Pengekstrakan & Suntikan Token reCAPTCHA V2
# -------------------------------------------------------------
def get_recaptcha_token(page) -> str:
    """Mengambil token g-recaptcha-response dari DOM."""
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
    """Menghantar token kepada sistem Backblaze mengikut jenis halangan."""
    if not token:
        return

    print(f"[✓] Token reCAPTCHA diperoleh: {token[:25]}...")
    page_title = page.title().lower()
    page_html = page.content().lower()

    if "security check" in page_title or "not so fast" in page_html:
        print("[✓] Menghantar token pengesahan onSubmit ke Backblaze...")
        page.evaluate("token => { if (typeof onSubmit === 'function') onSubmit(token); }", token)
        human_delay(3.0, 5.0)
    else:
        print("[✓] Menyuntik token ke dalam window.emailFormIframe...")
        page.evaluate("""token => {
            if (window.emailFormIframe && typeof window.emailFormIframe.updateProps === 'function') {
                window.emailFormIframe.updateProps({ fieldValues: { formCaptchaToken: token } });
            }
        }""", token)
        human_delay(3.0, 5.0)


def resolve_cloudflare_turnstile(page, max_retries: int = 15):
    """Pemeriksaan dan klik cabaran Cloudflare Turnstile."""
    for _ in range(max_retries):
        try:
            for frame in page.frames:
                if "challenges.cloudflare.com" in frame.url or "turnstile" in frame.url:
                    chk = frame.query_selector("input[type=checkbox], .ctp-checkbox-label, #challenge-stage")
                    if chk:
                        print("[*] Mengklik cabaran Cloudflare Turnstile...")
                        chk.click()
                        page.wait_for_timeout(2000)

            content = page.content().lower()
            if "just a moment" not in content and "attention required" not in content:
                break
        except Exception:
            pass
        page.wait_for_timeout(1000)


# -------------------------------------------------------------
# Enjin Automatik Pengecaman Suara & Fallback Captcha V2
# -------------------------------------------------------------
def solve_captcha_target(page, target_mode: str = "signup", max_attempts: int = 4) -> bool:
    """
    Penyelesai Khusus Mengikut Sasaran:
    - target_mode='gate'   -> Captcha pintu masuk (Security Check)
    - target_mode='signup' -> Captcha borang pendaftaran (#emailRecaptcha)
    """
    print(f"[*] [PENYELESAI CAPTCHA] Menangani cabaran mod: {target_mode.upper()}...")

    for attempt in range(1, max_attempts + 1):
        print(f"[*] Percubaan penyelesaian #{attempt}/{max_attempts}...")

        # 1. Kenal pasti anchor frame yang sepadan
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
                        print(f"[*] Mengklik kotak semak Captcha ({target_mode})...")
                        chk.click(force=True)
                        human_delay(2.5, 4.0)

                    if chk.get_attribute("aria-checked") == "true":
                        print(f"[✓] Kotak semak {target_mode} disahkan hijau!")
                        token = get_recaptcha_token(page)
                        apply_recaptcha_token_to_page(page, token)
                        return True
            except Exception:
                pass

        # Semak jika token diperoleh terus dari DOM
        token = get_recaptcha_token(page)
        if token:
            apply_recaptcha_token_to_page(page, token)
            return True

        # 3. Cari bframe cabaran (ikon fon kepala / gambar)
        bframe = None
        for _ in range(8):
            for frame in page.frames:
                f_url = frame.url.lower()
                if "recaptcha" in f_url and "bframe" in f_url:
                    bframe = frame
                    break
            if bframe:
                break
            page.wait_for_timeout(500)

        if not bframe:
            page.wait_for_timeout(1000)
            continue

        # 4. Percubaan Pintasan Audio
        try:
            audio_btn = bframe.locator("#recaptcha-audio-button").first
            if audio_btn.is_visible(timeout=3000):
                print("[*] Menekan butang audio reCAPTCHA (ikon fon kepala)...")
                audio_btn.click(force=True)
                human_delay(2.5, 4.0)

                dos_err = bframe.locator(".rc-doscaptcha-header, .rc-audiochallenge-error-message").first
                if dos_err.is_visible(timeout=1500):
                    print("[!] Google mengehadkan audio ('automated queries'). Memuat semula cabaran...")
                    reload_btn = bframe.locator("#recaptcha-reload-button").first
                    if reload_btn.is_visible(timeout=2000):
                        reload_btn.click(force=True)
                        human_delay(2.0, 3.0)
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
                    print("[+] Memuat turun audio MP3 dan menukar ke format WAV...")
                    temp_dir = tempfile.gettempdir()
                    mp3_file = os.path.join(temp_dir, f"cap_{random.randint(1000, 9999)}.mp3")
                    wav_file = os.path.join(temp_dir, f"cap_{random.randint(1000, 9999)}.wav")

                    try:
                        req = urllib.request.Request(
                            audio_url,
                            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                        )
                        with urllib.request.urlopen(req) as resp, open(mp3_file, "wb") as f_out:
                            f_out.write(resp.read())

                        sound = pydub.AudioSegment.from_mp3(mp3_file)
                        sound.export(wav_file, format="wav")

                        recognizer = speech_recognition.Recognizer()
                        with speech_recognition.AudioFile(wav_file) as src:
                            audio_data = recognizer.record(src)
                            transcribed_text = recognizer.recognize_google(audio_data)

                        print(f"[✓] Teks audio berjaya dikesan: '{transcribed_text}'")

                        resp_input = bframe.locator("#audio-response").first
                        if resp_input.is_visible(timeout=3000):
                            resp_input.fill(transcribed_text)
                            human_delay(1.5, 2.5)
                            bframe.locator("#recaptcha-verify-button").first.click(force=True)
                            human_delay(3.0, 5.0)

                            token = get_recaptcha_token(page)
                            if token:
                                apply_recaptcha_token_to_page(page, token)
                                return True
                    finally:
                        if os.path.exists(mp3_file):
                            os.remove(mp3_file)
                        if os.path.exists(wav_file):
                            os.remove(wav_file)
        except Exception as ae:
            print(f"[*] Info percubaan audio: {ae}")

        token = get_recaptcha_token(page)
        if token:
            apply_recaptcha_token_to_page(page, token)
            return True

        # Muat semula cabaran jika cubaan belum berjaya
        try:
            reload_btn = bframe.locator("#recaptcha-reload-button").first
            if reload_btn.is_visible(timeout=1500):
                reload_btn.click(force=True)
                human_delay(2.0, 3.0)
        except Exception:
            pass

    return False


# -------------------------------------------------------------
# Alur Pendaftaran Utama Pelayar 1 (Camoufox Stealth)
# -------------------------------------------------------------
def run_step01_chromium(target_email: str) -> bool:
    """
    Pelayar 1 (Enjin Camoufox Stealth):
    - Bersaiz kemas 1280x750 & tidak dikesan bot CDP.
    - Menangani Captcha Gerbang (Lapisan 1).
    - Mencari kotak emel & menangani Captcha Sebelum Taip (Lapisan 2).
    - Menaip emel & menangani Captcha Selepas Taip (Lapisan 3).
    - Klik 'Get Started Free' & memantau Captcha sehingga 'sign-up-thank-you' (Lapisan 4).
    - Jeda 10 saat sebelum menutup pelayar secara bersih.
    """
    print("\n" + "=" * 75)
    print("🦊 [PELAYAR 1: CAMOUFOX STEALTH] Memulakan Pendaftaran Awal")
    print(f"👉 Sasaran Emel: {target_email}")
    print("=" * 75)

    with Camoufox(headless=False, geoip=True) as browser:
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"
        )
        page = context.new_page()

        try:
            # -------------------------------------------------------------
            # [LAPISAN 1: PINTU MASUK & KESELAMATAN GERBANG]
            # -------------------------------------------------------------
            target_url = "https://www.backblaze.com/sign-up/cloud-storage?referrer=getstarted"
            print(f"[*] Membuka laman pendaftaran: {target_url}")
            page.goto(target_url, wait_until="load", timeout=60000)
            human_delay(2.5, 4.0)

            resolve_cloudflare_turnstile(page)

            page_title = page.title().lower()
            page_html = page.content().lower()
            if "security check" in page_title or "not so fast" in page_html or page.locator("#captcha-form").count() > 0:
                print("[!] Sekatan 'Security Check' dikesan di pintu masuk!")
                solve_captcha_target(page, target_mode="gate")
                print("[*] Memuat semula laman pendaftaran...")
                page.reload(wait_until="load")
                human_delay(2.5, 4.0)

            # Tutup banner cookie sekiranya muncul
            try:
                cookie_btn = page.locator("#onetrust-accept-btn-handler, button:has-text('Accept All Cookies')").first
                if cookie_btn.is_visible(timeout=3000):
                    print("[*] Menutup banner cookie...")
                    cookie_btn.click()
                    human_delay(1.5, 2.5)
            except Exception:
                pass

            # -------------------------------------------------------------
            # [LAPISAN 2: MENGESAN KOTAK EMEL & SEMAKAN CAPTCHA SEBELUM TAIP]
            # -------------------------------------------------------------
            email_locator = None
            target_frame = page
            selectors = [
                'input[type="email"]',
                'input[name="email"]',
                'input[id*="email" i]',
                'input[placeholder*="email" i]'
            ]

            print("[*] Mengesan kotak medan emel...")
            start_search = time.time()
            while time.time() - start_search < 35:
                for frame in page.frames:
                    for sel in selectors:
                        try:
                            loc = frame.locator(sel).first
                            if loc.is_visible():
                                email_locator = loc
                                target_frame = frame
                                break
                        except Exception:
                            continue
                    if email_locator:
                        break
                if email_locator:
                    break
                page.wait_for_timeout(1000)

            if not email_locator:
                raise TimeoutError("Kotak medan emel tidak ditemui pada borang pendaftaran.")

            # Periksa jika captcha sudah aktif sebelum menaip
            if page.locator("#emailRecaptcha").is_visible():
                print("[!] Captcha dikesan aktif sebelum menaip! Menyelesaikan sekarang...")
                solve_captcha_target(page, target_mode="signup")
                human_delay(2.0, 3.5)

            print(f"[✓] Kotak emel ditemui! Menaip sasaran: {target_email}")
            robust_human_type(email_locator, page, target_email)

            # -------------------------------------------------------------
            # [LAPISAN 3: SEMAKAN CAPTCHA SEBELUM MENEKAN BUTANG (PRE-SUBMIT)]
            # -------------------------------------------------------------
            if page.locator("#emailRecaptcha").is_visible():
                print("[!] Captcha muncul sebaik sahaja emel diisi! Menyelesaikan...")
                solve_captcha_target(page, target_mode="signup")
                human_delay(2.0, 3.5)

            # -------------------------------------------------------------
            # [LAPISAN 4: SERAHAN & GELUNG PEMANTAU SEHINGGA THANK-YOU]
            # -------------------------------------------------------------
            submit_btn = target_frame.locator('button:has-text("Get Started Free"), input[value="Get Started Free"], button#form-submit').first
            if not submit_btn.is_visible():
                submit_btn = page.locator('button:has-text("Get Started Free"), input[value="Get Started Free"]').first

            print("[*] Menekan butang 'Get Started Free'...")
            submit_btn.click()
            human_delay(2.5, 4.0)

            print("[*] Memantau proses pendaftaran sehingga tiba di 'sign-up-thank-you' (had 180 saat)...")
            start_watchdog = time.time()
            reached_thank_you = False

            while time.time() - start_watchdog < 180:
                curr_url = page.url.lower()
                if "sign-up-thank-you" in curr_url:
                    print(f"[✓] Pendaftaran BERJAYA! Tiba di halaman konfirmasi: {page.url}")
                    reached_thank_you = True
                    break

                # 1. Semak sekiranya #emailRecaptcha muncul selepas submit
                email_recaptcha_el = page.locator("#emailRecaptcha").first
                try:
                    if email_recaptcha_el.is_visible(timeout=1000):
                        print("[!] Captcha borang (#emailRecaptcha) aktif! Menyelesaikan...")
                        solve_captcha_target(page, target_mode="signup")
                        human_delay(2.0, 3.0)

                        # Tekan butang submit semula jika butang aktif kembali
                        for frame in page.frames:
                            sub = frame.locator('button#form-submit, button:has-text("Get Started Free")').first
                            if sub.is_visible(timeout=1000):
                                print("[*] Menekan semula butang 'Get Started Free' selepas token dihantar...")
                                sub.click()
                                human_delay(2.5, 4.0)
                                break
                except Exception:
                    pass

                # 2. Semak jika terlompat ke sekatan Security Check
                if "security check" in page.title().lower() or "not so fast" in page.content().lower():
                    print("[!] Sekatan Security Check dikesan! Menyelesaikan...")
                    solve_captcha_target(page, target_mode="gate")
                    human_delay(2.5, 4.0)

                # 3. Semak Turnstile
                resolve_cloudflare_turnstile(page, max_retries=1)

                page.wait_for_timeout(2000)

            if not reached_thank_you:
                raise TimeoutError("Pendaftaran gagal tiba di laman 'sign-up-thank-you' dalam tempoh 180 saat.")

            # -------------------------------------------------------------
            # [LAPISAN 5: JEDA 10 SAAT SEBELUM TUTUP PELAYAR]
            # -------------------------------------------------------------
            print("\n" + "=" * 65)
            print("⏳ [SELESAI] Halaman thank-you dicapai! Menunggu 10 saat sebelum menutup pelayar...")
            print("=" * 65)
            for s in range(10, 0, -1):
                print(f"   Pelayar Camoufox akan ditutup dalam masa {s} saat...", end="\r")
                time.sleep(1)
            print("\n[✓] Jeda 10 saat selesai. Menutup Pelayar 1 secara bersih.\n")
            return True

        finally:
            try:
                page.close()
                context.close()
            except Exception:
                pass


if __name__ == "__main__":
    test_email = "test@example.com"
    run_step01_chromium(test_email)