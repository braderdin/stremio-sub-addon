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
# Penyelarasan Laluan Direktori
# -------------------------------------------------------------
CURRENT_DIR = Path(__file__).resolve().parent           # .../B2-ACC-SCRIPT/TEST
PARENT_DIR = CURRENT_DIR.parent                         # .../B2-ACC-SCRIPT
ROOT_DIR = PARENT_DIR.parent                            # .../stremio-sub-addon
BYPASS_DIR = ROOT_DIR / "Recaptcha-Bypass"

for p in [str(PARENT_DIR), str(CURRENT_DIR), str(ROOT_DIR), str(BYPASS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)


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


def apply_recaptcha_token_to_page(page, token: str, human_delay):
    """Menghantar token kepada sistem Backblaze mengikut jenis halangan."""
    if not token:
        return

    print(f"[✓] Token reCAPTCHA diperoleh: {token[:25]}...")
    page_title = page.title().lower()
    page_html = page.content().lower()

    if "security check" in page_title or "not so fast" in page_html:
        print("[✓] Menghantar token pengesahan onSubmit ke Backblaze...")
        page.evaluate("token => { if (typeof onSubmit === 'function') onSubmit(token); }", token)
        page.wait_for_load_state("networkidle", timeout=15000)
        human_delay(3.0, 5.0)
    else:
        print("[✓] Menyuntik token ke dalam window.emailFormIframe...")
        page.evaluate("""token => {
            if (window.emailFormIframe && typeof window.emailFormIframe.updateProps === 'function') {
                window.emailFormIframe.updateProps({ fieldValues: { formCaptchaToken: token } });
            }
        }""", token)
        human_delay(3.0, 5.0)


def solve_captcha_target(page, target_mode: str, human_delay) -> bool:
    """
    Penyelesai khusus mengikut sasaran:
    - target_mode='gate'   -> Captcha 1 (Pintu masuk / sa=LOGIN)
    - target_mode='signup' -> Captcha 2 (Borang emel / sa=signup)
    """
    print(f"[*] [Penyelesai Captcha] Memulakan proses untuk: {target_mode.upper()}...")

    # 1. Kenal pasti anchor frame yang betul mengikut mode
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

    # Fallback jika url query tidak spesifik
    if not anchor_frame:
        for frame in page.frames:
            f_url = frame.url.lower()
            if "recaptcha" in f_url and "anchor" in f_url and "size=invisible" not in f_url:
                anchor_frame = frame
                break

    # 2. Klik kotak semakan anchor
    if anchor_frame:
        chk = anchor_frame.locator("#recaptcha-anchor, .recaptcha-checkbox").first
        try:
            if chk.is_visible(timeout=4000):
                is_checked = chk.get_attribute("aria-checked") == "true"
                if not is_checked:
                    print(f"[*] Mengklik kotak semakan Captcha ({target_mode})...")
                    chk.click(force=True)
                    human_delay(3.0, 5.0)
        except Exception as ce:
            print(f"[*] Info klik anchor: {ce}")

    # Semak jika terus lulus (tanda hijau serta-merta)
    if anchor_frame:
        try:
            chk = anchor_frame.locator("#recaptcha-anchor, .recaptcha-checkbox").first
            if chk.count() > 0 and chk.get_attribute("aria-checked") == "true":
                print(f"[✓] Kotak semakan {target_mode} telah lulus (tanda hijau)!")
                token = get_recaptcha_token(page)
                apply_recaptcha_token_to_page(page, token, human_delay)
                return True
        except Exception:
            pass

    # 3. Cari bframe cabaran (ikon fon kepala / gambar)
    bframe = None
    for _ in range(12):
        for frame in page.frames:
            f_url = frame.url.lower()
            if "recaptcha" in f_url and "bframe" in f_url:
                bframe = frame
                break
        if bframe:
            break
        page.wait_for_timeout(500)

    # 4. Percubaan Pintasan Audio
    if bframe:
        audio_btn = bframe.locator("#recaptcha-audio-button").first
        try:
            if audio_btn.is_visible(timeout=3000):
                print("[*] Menekan butang audio reCAPTCHA (ikon fon kepala)...")
                audio_btn.click(force=True)
                human_delay(3.0, 5.0)

                dos_err = bframe.locator(".rc-doscaptcha-header, .rc-audiochallenge-error-message").first
                if dos_err.is_visible(timeout=1500):
                    print("[!] Google mengehadkan cabaran audio ('automated queries'). Beralih ke bantuan skrin.")
                else:
                    audio_url = None
                    audio_el = bframe.locator(".rc-audiochallenge-tdownload-link, a[href*='payload/audio.mp3'], a[href*='audio']").first
                    if audio_el.is_visible(timeout=5000):
                        audio_url = audio_el.get_attribute("href")
                    else:
                        audio_src = bframe.locator("audio#player, #audio-source").first
                        if audio_src.count() > 0:
                            audio_url = audio_src.get_attribute("src")

                    if audio_url:
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

                            print(f"[✓] Google Speech mengecam teks audio: '{transcribed_text}'")

                            resp_input = bframe.locator("#audio-response").first
                            if resp_input.is_visible(timeout=3000):
                                resp_input.fill(transcribed_text)
                                human_delay(2.5, 4.5)
                                bframe.locator("#recaptcha-verify-button").first.click(force=True)
                                human_delay(3.0, 5.0)
                        finally:
                            if os.path.exists(mp3_file):
                                os.remove(mp3_file)
                            if os.path.exists(wav_file):
                                os.remove(wav_file)
        except Exception as ae:
            print(f"[*] Info percubaan audio: {ae}")

    # Semak jika token diperoleh selepas cubaan audio
    token = get_recaptcha_token(page)
    if token:
        apply_recaptcha_token_to_page(page, token, human_delay)
        return True

    # 5. Laluan Bantuan Manual (Skrin Headed)
    print("\n" + "=" * 70)
    print(f"🚨 [TINDAKAN DIPERLUKAN] Sila selesaikan cabaran {target_mode.upper()} pada skrin pelayar.")
    print("👉 Klik petak gambar atau langkau cabaran pada tetingkap yang terbuka.")
    print("⏳ Skrip bersedia menunggu anda selesai (had masa 30 saat)...")
    print("=" * 70 + "\n")

    manual_start = time.time()
    while time.time() - manual_start < 30:
        token = get_recaptcha_token(page)
        if token:
            print(f"[✓] Token {target_mode} berjaya dikesan daripada penyelesaian pada skrin!")
            apply_recaptcha_token_to_page(page, token, human_delay)
            return True

        if "sign-up-thank-you" in page.url.lower():
            print("[✓] Halaman beralih ke sign-up-thank-you!")
            return True

        if anchor_frame:
            try:
                chk = anchor_frame.locator("#recaptcha-anchor, .recaptcha-checkbox").first
                if chk.count() > 0 and chk.get_attribute("aria-checked") == "true":
                    print(f"[✓] Kotak semak {target_mode} disahkan hijau!")
                    page.wait_for_timeout(1500)
                    token = get_recaptcha_token(page)
                    apply_recaptcha_token_to_page(page, token, human_delay)
                    return True
            except Exception:
                pass

        page.wait_for_timeout(1500)

    print(f"[!] Masa menunggu bantuan {target_mode} tamat.")
    return False


def run_step01(page, target_email: str, email_handler, human_delay, human_type, resolve_turnstile) -> str:
    """
    Langkah 1 Versi Ujian (V2TEST):
    - Selesaikan Captcha 1 (jika dihalang WAF di pintu masuk).
    - Masukkan emel terus ke borang rasmi & tekan submit.
    - Selesaikan Captcha 2 (#emailRecaptcha) sebaik sahaja ia muncul selepas submit.
    - Tunggu pautan pengesahan di Gmail.
    """
    print("\n---> [LANGKAH 1 - V2TEST] Pendaftaran Emel Awal...")
    target_url = "https://www.backblaze.com/sign-up/cloud-storage?referrer=getstarted"
    page.goto(target_url, wait_until="load", timeout=60000)
    human_delay(2.5, 4.5)
    resolve_turnstile(page)

    # =============================================================
    # [FASA 1: CAPTCHA 1 - HANYA JIKA DISEKAT DI PINTU MASUK]
    # =============================================================
    page_title = page.title().lower()
    page_html = page.content().lower()
    is_hard_gate = ("security check" in page_title) or ("not so fast" in page_html) or (page.locator("#captcha-form").count() > 0)

    if is_hard_gate:
        print("[!] Captcha 1 ('Security Check') dikesan di pintu masuk!")
        solve_captcha_target(page, target_mode="gate", human_delay=human_delay)
        print("[*] Memuat semula halaman untuk memaparkan borang rasmi...")
        page.reload(wait_until="load")
        human_delay(3.0, 5.0)

    # =============================================================
    # [FASA 2: PENGISIAN EMEL & SERAHAN AWAL]
    # =============================================================
    try:
        cookie_btn = page.locator("#onetrust-accept-btn-handler, button:has-text('Accept All Cookies')").first
        if cookie_btn.is_visible(timeout=3000):
            print("[*] Menutup banner cookies...")
            cookie_btn.click()
            human_delay(2.5, 4.5)
    except Exception:
        pass

    email_locator = None
    target_frame = None
    selectors = ['input[type="email"]', 'input[name="email"]', 'input[id*="email" i]', 'input[placeholder*="email" i]']

    print(f"[*] Mencari kotak medan emel untuk: {target_email}")
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

    print(f"[✓] Kotak medan emel ditemui! Menaip emel...")
    human_type(email_locator, target_email)
    human_delay(2.5, 4.5)

    submit_btn = target_frame.locator('button:has-text("Get Started Free"), input[value="Get Started Free"]').first
    if not submit_btn.is_visible():
        submit_btn = page.locator('button:has-text("Get Started Free"), input[value="Get Started Free"]').first

    print("[*] Menekan butang 'Get Started Free'...")
    submit_btn.click()
    page.wait_for_load_state("networkidle")
    human_delay(3.0, 5.0)

    # =============================================================
    # [FASA 3: CAPTCHA 2 - GELUNG PANTAU PASCA-SERAHAN]
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

        # Semak jika Captcha 2 (#emailRecaptcha) muncul selepas submit
        email_recaptcha_el = page.locator("#emailRecaptcha").first
        is_dynamic_visible = False
        try:
            is_dynamic_visible = email_recaptcha_el.is_visible(timeout=1000)
        except Exception:
            pass

        if is_dynamic_visible:
            print("[!] Captcha 2 (#emailRecaptcha) aktif! Menyelesaikan captcha kedua sekarang...")
            solve_captcha_target(page, target_mode="signup", human_delay=human_delay)
            human_delay(3.0, 5.0)

            # Tekan butang submit semula jika ia muncul kembali
            for frame in page.frames:
                sub = frame.locator('button#form-submit, button:has-text("Get Started Free")').first
                try:
                    if sub.is_visible(timeout=1500):
                        print("[*] Menekan semula butang 'Get Started Free' selepas token dihantar...")
                        sub.click()
                        human_delay(3.0, 5.0)
                        break
                except Exception:
                    pass

        # Semak jika terlompat ke Security Check
        if "security check" in page.title().lower() or "not so fast" in page.content().lower():
            print("[!] Sekatan 'Security Check' dikesan semasa menunggu pengesahan...")
            solve_captcha_target(page, target_mode="gate", human_delay=human_delay)
            human_delay(3.0, 5.0)

        page.wait_for_timeout(2000)

    if not reached_thank_you:
        raise TimeoutError("Borang pendaftaran gagal tiba di 'sign-up-thank-you' dalam masa 120 saat.")

    # =============================================================
    # [FASA 4: SEMAKAN EMEL PENGAKTIFAN DI GMAIL]
    # =============================================================
    print(f"[*] Menunggu pautan pengaktifan Backblaze untuk: {target_email}")
    activation_link = email_handler.wait_for_verification_link(target_email, timeout_seconds=600, poll_interval=30)
    return activation_link