import time
import random
import os
import tempfile
import urllib.request
from pathlib import Path

import pydub
import speech_recognition

from browser_utils_v5 import (
    logger,
    human_delay,
    get_recaptcha_token,
    apply_recaptcha_token_to_page,
    verify_token_injected,
    wait_for_form_submission,
)

# =============================================================================
# ENJIN PENGESANAN CAPTCHA V2/V3 PINTAR
# =============================================================================
def detect_recaptcha_version(page) -> str:
    """
    Mengesan versi reCAPTCHA yang digunakan:
    - 'v2_checkbox': Checkbox + audio challenge (traditional)
    - 'v2_invisible': Invisible reCAPTCHA v2
    - 'v3': Score-based reCAPTCHA v3 (no user interaction)
    """
    try:
        v3_script = page.evaluate("""() => {
            const scripts = Array.from(document.querySelectorAll('script'));
            for (const s of scripts) {
                if (s.src && s.src.includes('recaptcha') && s.src.includes('render=')) {
                    return 'v3';
                }
                if (s.innerHTML && s.innerHTML.includes('grecaptcha.execute')) {
                    return 'v3';
                }
            }
            return '';
        }""")
        if v3_script == 'v3':
            logger.info("[DETECT] reCAPTCHA v3 (score-based) dikesan")
            return 'v3'
    except Exception:
        pass
    
    try:
        invisible = page.evaluate("""() => {
            const el = document.querySelector('.grecaptcha-badge');
            return el !== null;
        }""")
        if invisible:
            logger.info("[DETECT] reCAPTCHA v2 invisible dikesan")
            return 'v2_invisible'
    except Exception:
        pass
    
    logger.info("[DETECT] reCAPTCHA v2 checkbox/audio dikesan")
    return 'v2_checkbox'


def is_recaptcha_v3_active(page) -> bool:
    """Semak jika reCAPTCHA v3 aktif (score-based, tiada UI)."""
    try:
        return page.evaluate("""() => {
            return typeof grecaptcha !== 'undefined' && typeof grecaptcha.execute === 'function';
        }""")
    except Exception:
        return False


def solve_recaptcha_v3(page, action: str = "signup") -> bool:
    """
    Menyelesaikan reCAPTCHA v3 (score-based) dengan memanggil grecaptcha.execute.
    Tidak memerlukan interaksi pengguna - hanya perlu execute dan dapatkan token.
    """
    logger.info(f"[*] [reCAPTCHA v3] Menjalankan grecaptcha.execute untuk action: {action}")
    
    try:
        token = page.evaluate("""(action) => {
            return new Promise((resolve) => {
                if (typeof grecaptcha !== 'undefined' && typeof grecaptcha.execute === 'function') {
                    grecaptcha.execute(action).then(resolve).catch(() => resolve(''));
                } else {
                    resolve('');
                }
            });
        }""", action)
        
        if token:
            logger.info(f"[OK] [reCAPTCHA v3] Token diperoleh: {token[:25]}...")
            apply_recaptcha_token_to_page(page, token)
            human_delay(2.0, 4.0, "captcha")
            return verify_token_injected(page)
        else:
            logger.warning("[!] [reCAPTCHA v3] Gagal mendapat token")
            return False
    except Exception as e:
        logger.error(f"[!] [reCAPTCHA v3] Ralat: {e}")
        return False


# =============================================================================
# ENJIN PENYELESAI CAPTCHA V2 UTAMA (DENGAN VALIDASI TOKN & DELAY PINTAR)
# Menggabungkan logik terbaik V3 + V4 + penambahbaikan V5
# =============================================================================
def solve_captcha_target(page, target_mode: str = "signup", max_attempts: int = 4) -> bool:
    """
    Penyelesai Captcha V5 Hybrid (V3+V4+V5):
    1. Validasi token selepas inject (verify_token_injected) - V5
    2. Delay rawak 2-5 saat di setiap step kritikal - V5
    3. Sokongan reCAPTCHA v3 (score-based) - V5
    4. Logging terperinci untuk debugging - V5
    5. Fallback pintar jika audio challenge gagal - V3/V4 logic
    6. Pengekstrakan URL audio yang betul (.rc-audiochallenge-tdownload-link) - V3/V4
    7. Anchor frame klik checkbox sebelum bframe search - V3/V4
    """
    logger.info(f"[*] [PENYELESAI CAPTCHA V5 HYBRID] Menangani cabaran mod: {target_mode.upper()}...")

    # Check for reCAPTCHA v3 first
    if is_recaptcha_v3_active(page):
        logger.info("[*] [reCAPTCHA v3] Dikesan - menggunakan penyelesaian score-based")
        return solve_recaptcha_v3(page, action=target_mode)

    for attempt in range(1, max_attempts + 1):
        logger.info(f"[*] Percubaan penyelesaian #{attempt}/{max_attempts}...")

        if attempt > 1:
            inter_attempt_delay = random.uniform(2.0, 5.0)
            logger.info(f"[TIMER] Jeda antara percubaan {attempt-1} -> {attempt}: {inter_attempt_delay:.1f}s")
            time.sleep(inter_attempt_delay)

        # =============================================================
        # 1. KENAL PASTI ANCHOR FRAME & KLIK CHECKBOX (LOGIK V3/V4)
        # =============================================================
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
                        human_delay(2.5, 4.0, "captcha")

                    if chk.get_attribute("aria-checked") == "true":
                        logger.info(f"[OK] Kotak semak {target_mode} disahkan hijau!")
                        token = get_recaptcha_token(page)
                        if token:
                            apply_recaptcha_token_to_page(page, token)
                            human_delay(2.0, 4.0, "captcha")
                            if verify_token_injected(page):
                                return True
            except Exception as e:
                logger.debug(f"Anchor frame error: {e}")

        # Semak jika token diperoleh terus dari DOM
        token = get_recaptcha_token(page)
        if token:
            apply_recaptcha_token_to_page(page, token)
            human_delay(2.0, 4.0, "captcha")
            if verify_token_injected(page):
                return True

        # =============================================================
        # 3. CARI BFRAME CABARAN (IKON FON KEPALA / GAMBAR) - 8 KITARAN V3/V4
        # =============================================================
        bframe = None
        logger.info("[*] Mencari bframe reCAPTCHA (audio challenge)...")
        for cycle in range(8):
            for frame in page.frames:
                f_url = frame.url.lower()
                if "recaptcha" in f_url and "bframe" in f_url:
                    bframe = frame
                    break
            if bframe:
                logger.info(f"[OK] bframe ditemui pada kitaran {cycle+1}/8")
                break
            page.wait_for_timeout(3000)

        if not bframe:
            logger.warning("[!] bframe tidak ditemui selepas 8 kitaran")
            page.wait_for_timeout(3000)
            continue

        # =============================================================
        # 4. PINDAHAN AUDIO MENGIKUT FORMAT STABIL V3/V4
        # =============================================================
        try:
            audio_btn = bframe.locator("#recaptcha-audio-button").first
            if audio_btn.is_visible(timeout=7000):
                logger.info("[*] Menekan butang audio reCAPTCHA (ikon fon kepala)...")
                audio_btn.click(force=True)
                human_delay(2.5, 4.0, "captcha")

                # Semak DOScaptcha (had audio Google) - LOGIK V3/V4
                dos_err = bframe.locator(".rc-doscaptcha-header, .rc-audiochallenge-error-message").first
                if dos_err.is_visible(timeout=3000):
                    logger.warning("[!] Had audio Google dikesan (DOScaptcha). Memuat semula cabaran...")
                    reload_btn = bframe.locator("#recaptcha-reload-button").first
                    if reload_btn.is_visible(timeout=3000):
                        reload_btn.click(force=True)
                        human_delay(2.5, 4.0, "captcha")
                    continue

                # EKSTRAK URL AUDIO - LOGIK V3/V4 YANG BETUL
                audio_url = None
                # Prioriti 1: Link muat turun langsung (paling biasa)
                audio_el = bframe.locator(".rc-audiochallenge-tdownload-link, a[href*='payload/audio.mp3'], a[href*='audio']").first
                if audio_el.is_visible(timeout=4000):
                    audio_url = audio_el.get_attribute("href")
                    logger.info(f"[+] URL audio dijumpai via link muat turun: {audio_url[:50]}...")
                else:
                    # Prioriti 2: Elemen audio player
                    audio_src = bframe.locator("audio#player, #audio-source").first
                    if audio_src.count() > 0:
                        audio_url = audio_src.get_attribute("src")
                        logger.info(f"[+] URL audio dijumpai via audio player: {audio_url[:50]}...")

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
                        logger.info(f"[+] Audio dikonvert ke WAV: {wav_path}")

                        recognizer = speech_recognition.Recognizer()
                        with speech_recognition.AudioFile(str(wav_path)) as source:
                            audio_data = recognizer.record(source)

                        logger.info("[*] Menghuraikan audio dengan Google STT...")
                        transcribed_text = recognizer.recognize_google(audio_data)
                        logger.info(f"[OK] Teks audio berjaya dikesan: '{transcribed_text}'")

                        human_delay(1.5, 4.5, "captcha")

                        response_field = bframe.locator("#audio-response, #default-response").first
                        if response_field.is_visible(timeout=3000):
                            response_field.fill("")
                            time.sleep(0.3)
                            response_field.press_sequentially(transcribed_text.lower(), delay=random.randint(80, 150))
                            human_delay(1.5, 4.5, "captcha")

                            verify_btn = bframe.locator("#recaptcha-verify-button").first
                            if verify_btn.is_visible(timeout=5000):
                                logger.info("[*] Menekan butang Verify...")
                                verify_btn.click(force=True)
                                human_delay(1.5, 5.0, "captcha")

                            token = get_recaptcha_token(page)
                            if token:
                                apply_recaptcha_token_to_page(page, token)
                                human_delay(2.0, 4.0, "captcha")
                                if verify_token_injected(page):
                                    logger.info(f"[OK] CAPTCHA BERJAYA DISELESAIKAN pada percubaan #{attempt}")
                                    return True

        except speech_recognition.UnknownValueError:
            logger.warning("[!] Audio tidak boleh difahami (UnknownValueError)")
        except speech_recognition.RequestError as re:
            logger.error(f"[!] Ralat STT: {re}")
        except Exception as ae:
            logger.error(f"[!] Ralat pemprosesan audio: {ae}")

        # Semak token sekali lagi selepas cubaan audio
        token = get_recaptcha_token(page)
        if token:
            apply_recaptcha_token_to_page(page, token)
            human_delay(2.0, 4.0, "captcha")
            if verify_token_injected(page):
                return True

        # =============================================================
        # 5. MUAT SEMULA CABARAN JIKA BELUM BERJAYA (V3/V4)
        # =============================================================
        try:
            reload_btn = bframe.locator("#recaptcha-reload-button").first
            if reload_btn.is_visible(timeout=3000):
                logger.info("[*] Memuat semula cabaran reCAPTCHA...")
                reload_btn.click(force=True)
                human_delay(2.0, 4.0, "captcha")
        except Exception:
            pass

    logger.error(f"[X] CAPTCHA GAGAL diselesaikan selepas {max_attempts} percubaan")
    return False


def is_any_captcha_active(page) -> tuple[bool, str]:
    """
    Mengesan sebarang jenis Captcha secara aktif (Gate, Form, BFrame, atau Anchor).
    Termasuk pengesanan reCAPTCHA v3.
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
            return True, "sign_up"
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

    if is_recaptcha_v3_active(page):
        return True, "signup"

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
                    human_delay(2.0, 3.5, "click")
                    return True
            except Exception:
                continue
    return False


def smart_reload_and_resubmit(page, target_email: str, refresh_count: int) -> bool:
    """
    Logik Refresh Pintar V5: Exponential backoff + DOM change detection.
    Memuat semula laman, mengendalikan Turnstile/Gate,
    mengisi semula emel, menyelesaikan Captcha jika muncul, dan menghantar semula borang.
    """
    logger.warning(f"[REFRESH] [SMART REFRESH V5] Laman tidak bertindak balas. Refresh #{refresh_count} (exponential backoff)...")
    
    backoff = exponential_backoff_delay(refresh_count - 1)
    logger.info(f"[TIMER] Menunggu {backoff:.1f}s sebelum refresh...")
    time.sleep(backoff)
    
    page.reload(wait_until="load", timeout=45000)
    human_delay(3.0, 5.0, "general")

    from browser_utils_v5 import resolve_cloudflare_turnstile
    resolve_cloudflare_turnstile(page)

    captcha_active, mode = is_any_captcha_active(page)
    if captcha_active:
        logger.info(f"[*] Captcha dikesan selepas refresh ({mode}), menyelesaikan...")
        solve_captcha_target(page, target_mode=mode)
        human_delay(2.0, 4.0, "captcha")

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
    from browser_utils_v5 import robust_human_type
    robust_human_type(email_locator, page, target_email)
    human_delay(1.5, 3.0, "form")

    captcha_active, mode = is_any_captcha_active(page)
    if captcha_active:
        logger.info(f"[*] Captcha muncul selepas isi emel ({mode}), menyelesaikan...")
        solve_captcha_target(page, target_mode=mode)
        human_delay(2.0, 4.0, "captcha")

    logger.info("[*] Menekan butang hantar selepas Smart Refresh...")
    find_and_click_submit(page)
    return True


def exponential_backoff_delay(attempt: int, base: float = 30.0, max_delay: float = 120.0) -> float:
    """Exponential backoff untuk smart refresh: 30s, 60s, 90s..."""
    delay = min(base * (attempt + 1), max_delay)
    jitter = random.uniform(0.8, 1.2)
    return delay * jitter
