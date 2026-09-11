import os
import sys
import time
import random
import tempfile
import urllib.request
from pathlib import Path

# Kebergantungan Pengecaman Audio
import pydub
import speech_recognition

# -------------------------------------------------------------
# Penyelarasan Laluan Direktori untuk RecaptchaSolver
# -------------------------------------------------------------
CURRENT_DIR = Path(__file__).resolve().parent           # .../B2-ACC-SCRIPT/TEST
PARENT_DIR = CURRENT_DIR.parent                         # .../B2-ACC-SCRIPT
ROOT_DIR = PARENT_DIR.parent                            # .../stremio-sub-addon
BYPASS_DIR = ROOT_DIR / "Recaptcha-Bypass"

for p in [str(PARENT_DIR), str(CURRENT_DIR), str(ROOT_DIR), str(BYPASS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)


def try_solve_with_drissionpage(target_url: str) -> bool:
    """Mencuba menyelesaikan reCAPTCHA menggunakan DrissionPage terlebih dahulu (Parameter Bersih)."""
    print("[*] [Percubaan 1] Menguji penyelesaian captcha menggunakan DrissionPage...")
    driver = None
    try:
        from DrissionPage import ChromiumPage, ChromiumOptions
        options = ChromiumOptions()
        chrome_args = [
            "-no-first-run",
            "-force-color-profile=srgb",
            "-metrics-recording-only",
            "-password-store=basic",
            "-use-mock-keychain",
            "-no-default-browser-check",
            "-disable-gpu",
            "-accept-lang=en-US",
            "--no-sandbox",
            "--disable-background-mode"
        ]
        for arg in chrome_args:
            options.set_argument(arg)
        options.headless(True)

        driver = ChromiumPage(addr_or_opts=options)
        driver.get(target_url)
        time.sleep(2.5)

        from RecaptchaSolver import RecaptchaSolver
        solver = RecaptchaSolver(driver)
        solver.solveCaptcha()
        time.sleep(2.0)

        if "security check" not in driver.title.lower() and "not so fast" not in driver.html.lower():
            print("[✓] [DrissionPage] reCAPTCHA berjaya diselesaikan!")
            return True
        return False
    except Exception as e:
        print(f"[!] [DrissionPage] Info semakan ({e}). Beralih ke fallback Camoufox...")
        return False
    finally:
        if driver:
            try:
                driver.close()
            except Exception:
                pass


def solve_audio_recaptcha_in_camoufox(page, human_delay) -> bool:
    """
    Fallback Camoufox Audio: Menyelesaikan reCAPTCHA Enterprise secara langsung
    Menyokong sekatan awal 'Security Check' dan cabaran borang '#emailRecaptcha'.
    """
    print("[*] [Fallback Camoufox] Mengaktifkan pemintasan audio reCAPTCHA di dalam sesi aktif...")
    try:
        # 1. Semak sama ada bframe cabaran sudah sedia terapung di skrin
        existing_bframe = None
        for frame in page.frames:
            f_url = frame.url.lower()
            if "recaptcha" in f_url and "bframe" in f_url:
                existing_bframe = frame
                break

        # Jika bframe BELUM wujud, baru kita cari dan klik kotak semak anchor
        if not existing_bframe:
            anchor_frame = None
            for frame in page.frames:
                f_url = frame.url.lower()
                if "recaptcha" in f_url and "anchor" in f_url and "size=invisible" not in f_url:
                    anchor_frame = frame
                    break

            if anchor_frame:
                chk = anchor_frame.locator("#recaptcha-anchor, .recaptcha-checkbox").first
                try:
                    is_checked = chk.get_attribute("aria-checked") == "true"
                    if not is_checked and chk.is_visible(timeout=3000):
                        chk.click(force=True, timeout=5000)
                        human_delay(1.5, 2.5)
                except Exception as ce:
                    print(f"[*] Info klik kotak semak: {ce}")

        # 2. Cari bingkai cabaran (bframe) - Kunci carian KETAT pada perkataan 'bframe'
        bframe = None
        for _ in range(15):
            for frame in page.frames:
                f_url = frame.url.lower()
                if "recaptcha" in f_url and "bframe" in f_url:
                    bframe = frame
                    break
            if bframe and (bframe.locator("#recaptcha-audio-button").count() > 0 or bframe.locator("#audio-response").count() > 0):
                break
            page.wait_for_timeout(500)

        if not bframe:
            print("[*] Tiada cabaran bframe aktif ditemui (mungkin semakan telah lulus).")
            return True

        # 3. Tekan butang audio cabaran (ikon fon kepala) dengan force=True
        audio_btn = bframe.locator("#recaptcha-audio-button").first
        try:
            if audio_btn.is_visible(timeout=3000):
                print("[*] Menekan butang cabaran audio (ikon fon kepala)...")
                audio_btn.click(force=True)
                human_delay(2.0, 3.0)
        except Exception as be:
            print(f"[*] Info butang audio: {be}")

        # Semak sekatan rate-limit audio dari Google
        dos_err = bframe.locator(".rc-doscaptcha-header, .rc-audiochallenge-error-message").first
        if dos_err.is_visible(timeout=2000):
            print("[!] Google mengehadkan cabaran audio ('automated queries').")
            return False

        # 4. Ambil pautan muat turun audio MP3
        audio_url = None
        audio_el = bframe.locator(".rc-audiochallenge-tdownload-link, a[href*='payload/audio.mp3'], a[href*='audio']").first
        if audio_el.is_visible(timeout=6000):
            audio_url = audio_el.get_attribute("href")
        else:
            # Semak elemen audio alternatif
            audio_src = bframe.locator("audio#player, #audio-source").first
            if audio_src.count() > 0:
                audio_url = audio_src.get_attribute("src")

        if not audio_url:
            print("[!] Pautan audio captcha tidak ditemui dalam bframe.")
            return False

        print("[+] Memuat turun fail audio MP3 dan menukar kepada WAV...")
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

            print(f"[✓] Google Speech berjaya mengenal pasti kod: '{transcribed_text}'")

            # Masukkan hasil transkripsi dan tekan sahkan
            resp_input = bframe.locator("#audio-response").first
            resp_input.fill(transcribed_text)
            human_delay(0.5, 0.8)

            verify_btn = bframe.locator("#recaptcha-verify-button").first
            verify_btn.click(force=True)
            human_delay(2.5, 3.5)

        finally:
            if os.path.exists(mp3_file):
                os.remove(mp3_file)
            if os.path.exists(wav_file):
                os.remove(wav_file)

        # 5. Dapatkan token pengesahan dan suntik ke sasaran yang betul
        token = page.evaluate("""() => {
            const el = document.querySelector('#emailRecaptcha textarea[name="g-recaptcha-response"]')
                       || document.querySelector('textarea[name="g-recaptcha-response"]')
                       || document.getElementById('g-recaptcha-response');
            return el ? el.value : '';
        }""")

        if token:
            print(f"[✓] Token reCAPTCHA diperoleh: {token[:25]}...")
            if "security check" in page.title().lower() or "not so fast" in page.content().lower():
                print("[✓] Menghantar token pengesahan onSubmit ke Backblaze...")
                page.evaluate("token => { if (typeof onSubmit === 'function') onSubmit(token); }", token)
                page.wait_for_load_state("networkidle", timeout=15000)
                human_delay(2.0, 3.0)
            else:
                print("[✓] Menyuntik token ke dalam window.emailFormIframe...")
                page.evaluate("""token => {
                    if (window.emailFormIframe && typeof window.emailFormIframe.updateProps === 'function') {
                        window.emailFormIframe.updateProps({ fieldValues: { formCaptchaToken: token } });
                    }
                }""", token)
                human_delay(2.0, 3.0)

        return True

    except Exception as err:
        print(f"[!] Kegagalan pemprosesan audio Camoufox: {err}")
        return False


def run_step01(page, target_email: str, email_handler, human_delay, human_type, resolve_turnstile) -> str:
    """
    Langkah 1 Versi Ujian (V2TEST):
    Menyemak captcha awal -> Isi emel -> Memantau pasca-serahan sehingga tiba di 'sign-up-thank-you' -> Ambil pautan emel.
    """
    print("\n---> [LANGKAH 1 - V2TEST] Pendaftaran Emel Awal...")
    target_url = "https://www.backblaze.com/sign-up/cloud-storage?referrer=getstarted"
    page.goto(target_url, wait_until="load", timeout=60000)
    human_delay(1.5, 2.5)
    resolve_turnstile(page)

    # =============================================================
    # [FASA 1: SEMAKAN CAPTCHA SEBELUM BORANG (PRE-SUBMIT)]
    # =============================================================
    page_title = page.title().lower()
    page_html = page.content().lower()

    has_interstitial = ("security check" in page_title) or ("not so fast" in page_html) or (page.locator("#captcha-form").count() > 0)
    has_dynamic = page.locator("#emailRecaptcha").is_visible() if page.locator("#emailRecaptcha").count() > 0 else False

    if has_interstitial or has_dynamic:
        print("[!] Captcha dikesan di pintu masuk! Memulakan proses pelepasan awal...")
        solved = try_solve_with_drissionpage(target_url)
        if not solved:
            solve_audio_recaptcha_in_camoufox(page, human_delay)

        if "security check" in page.title().lower() or "not so fast" in page.content().lower():
            print("[*] Memuat semula halaman untuk memuatkan borang rasmi...")
            page.reload(wait_until="load")
            human_delay(2.0, 3.0)
    else:
        print("[✓] Tiada sekatan awal. Meneruskan ke pengisian emel...")

    # =============================================================
    # [FASA 2: PENGISIAN EMEL & SERAHAN BORANG]
    # =============================================================
    try:
        cookie_btn = page.locator("#onetrust-accept-btn-handler, button:has-text('Accept All Cookies')").first
        if cookie_btn.is_visible(timeout=3000):
            cookie_btn.click()
            human_delay(0.5, 1.0)
    except Exception:
        pass

    email_locator = None
    target_frame = None
    selectors = ['input[type="email"]', 'input[name="email"]', 'input[id*="email" i]', 'input[placeholder*="email" i]']

    start_time = time.time()
    while time.time() - start_time < 35:
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
        raise TimeoutError("Medan emel tidak ditemui pada halaman pendaftaran.")

    human_type(email_locator, target_email)
    human_delay(0.8, 1.3)

    submit_btn = target_frame.locator('button:has-text("Get Started Free"), input[value="Get Started Free"]').first
    if not submit_btn.is_visible():
        submit_btn = page.locator('button:has-text("Get Started Free"), input[value="Get Started Free"]').first
    
    print("[*] Menekan butang 'Get Started Free'...")
    submit_btn.click()
    page.wait_for_load_state("networkidle")
    human_delay(2.0, 3.0)

    # =============================================================
    # [FASA 3: GELUNG PANTAU PASCA-SERAHAN (POST-SUBMIT WATCHDOG)]
    # Memastikan pendaftaran tiba di 'sign-up-thank-you' tanpa tergantung
    # =============================================================
    print("[*] Memulakan pemantauan pengesahan sehingga tiba di 'sign-up-thank-you'...")
    watchdog_start = time.time()
    reached_thank_you = False

    while time.time() - watchdog_start < 120:
        curr_url = page.url.lower()
        if "sign-up-thank-you" in curr_url:
            print(f"[✓] Berjaya tiba di halaman pengesahan: {page.url}")
            reached_thank_you = True
            break

        email_recaptcha_el = page.locator("#emailRecaptcha").first
        is_dynamic_visible = False
        try:
            is_dynamic_visible = email_recaptcha_el.is_visible(timeout=1000)
        except Exception:
            pass

        if is_dynamic_visible:
            print("[!] Captcha borang (#emailRecaptcha) muncul selepas tekan submit. Menyelesaikan captcha...")
            solve_audio_recaptcha_in_camoufox(page, human_delay)
            human_delay(2.0, 3.0)

            # Semak jika borang iframe createAccountForm sudah kembali dan boleh ditekan semula
            for frame in page.frames:
                sub = frame.locator('button#form-submit, button:has-text("Get Started Free")').first
                try:
                    if sub.is_visible(timeout=1500):
                        print("[*] Menekan semula butang 'Get Started Free' selepas suntikan token...")
                        sub.click()
                        human_delay(2.0, 3.0)
                        break
                except Exception:
                    pass

        if "security check" in page.title().lower() or "not so fast" in page.content().lower():
            print("[!] Sekatan 'Security Check' dikesan semasa menunggu pengesahan...")
            solve_audio_recaptcha_in_camoufox(page, human_delay)
            human_delay(2.0, 3.0)

        page.wait_for_timeout(2000)

    if not reached_thank_you:
        raise TimeoutError("Borang pendaftaran gagal tiba di 'sign-up-thank-you' dalam masa 120 saat.")

    # =============================================================
    # [FASA 4: SEMAKAN EMEL PENGAKTIFAN DI GMAIL]
    # =============================================================
    print(f"[*] Menunggu pautan pengaktifan Backblaze untuk: {target_email}")
    activation_link = email_handler.wait_for_verification_link(target_email, timeout_seconds=600, poll_interval=30)
    return activation_link