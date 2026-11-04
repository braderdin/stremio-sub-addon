import time
import random
import json
import logging
from pathlib import Path

# =============================================================================
# KONFIGURASI LOGGING TERPERINCI UNTUK DEBUGGING
# =============================================================================
logging.basicConfig(
    level=logging.DEBUG,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("B2_V5")
logger.setLevel(logging.DEBUG)

# =============================================================================
# JEDA MANUSIAWI DENGAN VARIASI PINTAR (2-5 SAAT UNTUK CAPTCHA)
# =============================================================================
def human_delay(min_s: float = 1.5, max_s: float = 4.5, context: str = "general"):
    """
    Jeda rawak menyerupai pola manusia.
    context: 'captcha' -> 2.0-5.0s, 'form' -> 1.5-3.5s, 'general' -> 1.5-4.5s
    """
    if context == "captcha":
        min_s, max_s = 2.0, 5.0
    elif context == "form":
        min_s, max_s = 1.5, 3.5
    elif context == "click":
        min_s, max_s = 0.8, 2.0
    elif context == "typing":
        min_s, max_s = 1.0, 2.5
    
    delay = random.uniform(min_s, max_s)
    logger.debug(f"[⏱️] Jeda {context}: {delay:.2f}s")
    time.sleep(delay)


def human_type(locator, text: str, min_delay: int = 110, max_delay: int = 190):
    """Mengetik teks secara berurutan dengan jeda realistis."""
    locator.scroll_into_view_if_needed()
    locator.click()
    human_delay(0.8, 1.8, "click")
    locator.press_sequentially(text, delay=random.randint(min_delay, max_delay))
    human_delay(1.0, 2.5, "typing")


def robust_human_type(locator, page, text: str):
    """
    Mengetik teks emel dan memicu event DOM agar validasi form React terkonfirmasi.
    """
    locator.scroll_into_view_if_needed()
    locator.click()
    human_delay(0.8, 2.0, "click")

    locator.fill("")
    time.sleep(0.4)

    locator.press_sequentially(text, delay=random.randint(110, 190))
    human_delay(1.0, 2.5, "typing")

    if locator.input_value() != text:
        logger.warning("[!] Nilai input belum lengkap. Mengisi ulang secara presisi.")
        locator.fill(text)
        human_delay(1.0, 2.0, "form")

    try:
        locator.dispatch_event("input")
        locator.dispatch_event("change")
        locator.press("Tab")
    except Exception as e:
        logger.debug(f"Event dispatch error: {e}")
    human_delay(1.0, 2.5, "form")

    # Semak peringatan format emel
    err_notice = page.locator("text=/Please enter a valid email address/i").first
    try:
        if err_notice.is_visible(timeout=1000):
            logger.warning("[!] Peringatan format emel aktif. Memperbarui status form.")
            locator.fill(text)
            locator.dispatch_event("input")
            locator.dispatch_event("change")
            locator.press("Tab")
            human_delay(1.0, 2.5, "form")
    except Exception:
        pass
# =============================================================================
# CLOUDFLARE TURNSTILE RESOLVER
# =============================================================================
def resolve_cloudflare_turnstile(page, max_retries: int = 15):
    """Memeriksa dan menyelesaikan rintangan Cloudflare Turnstile pada iframe."""
    logger.info("[☁️] Memeriksa Cloudflare Turnstile...")
    for attempt in range(max_retries):
        try:
            for frame in page.frames:
                if "challenges.cloudflare.com" in frame.url or "turnstile" in frame.url:
                    chk = frame.query_selector("input[type=checkbox], .ctp-checkbox-label, #challenge-stage")
                    if chk:
                        logger.info(f"[☁️] Mengklik kotak verifikasi Cloudflare Turnstile (percubaan {attempt+1})...")
                        chk.click()
                        page.wait_for_timeout(2500)

            content = page.content().lower()
            if "just a moment" not in content and "attention required" not in content:
                logger.info("[✓] Cloudflare Turnstile selesai.")
                break
        except Exception as e:
            logger.debug(f"Turnstile check error: {e}")
        page.wait_for_timeout(2500)


# =============================================================================
# DIAGNOSTIK HALAMAN UNTUK DEBUGGING
# =============================================================================
def dump_page_diagnostics(page, temp_dir: Path, acc_idx: int):
    """Menyimpan berkas HTML dan struktur DOM untuk analisis ralat."""
    temp_dir.mkdir(parents=True, exist_ok=True)

    html_file = temp_dir / f"page_dump_acc_{acc_idx}.html"
    try:
        html_file.write_text(page.content(), encoding="utf-8")
        logger.info(f"[🔍] Diagnostik HTML disimpan: {html_file}")
    except Exception as e:
        logger.error(f"[!] Gagal menyimpan dump HTML: {e}")

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

        json_file = temp_dir / f"dom_structure_acc_{acc_idx}.json"
        with open(json_file, "w", encoding="utf-8") as jf:
            json.dump(dump_data, jf, indent=2, ensure_ascii=False)
        logger.info(f"[🔍] Diagnostik struktur DOM disimpan: {json_file}")
    except Exception as e:
        logger.error(f"[!] Gagal menyimpan dump JSON DOM: {e}")
# =============================================================================
# UTILITI TAMBAHAN UNTUK V5
# =============================================================================
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
    """Menyuntik token verifikasi reCAPTCHA dengan jeda penstabilan."""
    if not token:
        return

    logger.info(f"[✓] Token reCAPTCHA diperoleh: {token[:25]}...")
    page_title = page.title().lower()
    page_html = page.content().lower()

    if "security check" in page_title or "not so fast" in page_html:
        logger.info("[✓] Menghantar token pengesahan onSubmit ke Backblaze...")
        page.evaluate("token => { if (typeof onSubmit === 'function') onSubmit(token); }", token)
        human_delay(3.0, 5.0, "captcha")
    else:
        logger.info("[✓] Menyuntik token ke dalam window.emailFormIframe...")
        page.evaluate("""token => {
            if (window.emailFormIframe && typeof window.emailFormIframe.updateProps === 'function') {
                window.emailFormIframe.updateProps({ fieldValues: { formCaptchaToken: token } });
            }
        }""", token)
        human_delay(3.0, 5.0, "captcha")


def verify_token_injected(page) -> bool:
    """Memastikan token benar-benar terisi di DOM selepas inject."""
    try:
        token_value = page.evaluate("""() => {
            const el = document.querySelector('#emailRecaptcha textarea[name="g-recaptcha-response"]')
                       || document.querySelector('textarea[name="g-recaptcha-response"]')
                       || document.getElementById('g-recaptcha-response');
            return el ? el.value.length > 0 : false;
        }""")
        if token_value:
            logger.info("[✓] Token DIVALIDASI - g-recaptcha-response terisi di DOM")
            return True
        else:
            logger.warning("[!] Token TIDAK terisi di DOM selepas inject")
            return False
    except Exception as e:
        logger.debug(f"Token verification error: {e}")
        return False


def wait_for_form_submission(page, timeout: int = 15000) -> bool:
    """
    Memantau network/redirect selepas klik submit untuk confirm form benar-benar dihantar.
    Returns True jika ada navigasi atau network activity.
    """
    logger.info("[📡] Memantau form submission (network/redirect)...")
    start_time = time.time()
    initial_url = page.url
    
    while time.time() - start_time < timeout / 1000:
        # Cek redirect
        if page.url != initial_url:
            logger.info(f"[✓] Redirect dikesan: {initial_url} -> {page.url}")
            return True
        
        # Cek loading state
        try:
            if page.locator("text=/check your email|we sent a confirmation|thank you/i").first.is_visible(timeout=500):
                logger.info("[✓] Mesej kejayaan dikesan di halaman")
                return True
        except Exception:
            pass
        
        # Cek network idle (form submitted)
        try:
            page.wait_for_load_state("networkidle", timeout=1000)
            logger.info("[✓] Network idle - form kemungkinan dihantar")
            return True
        except Exception:
            pass
        
        time.sleep(0.5)
    
    logger.warning("[!] Timeout menunggu form submission")
    return False


def exponential_backoff_delay(attempt: int, base: float = 30.0, max_delay: float = 120.0) -> float:
    """Exponential backoff untuk smart refresh: 30s, 60s, 90s..."""
    delay = min(base * (attempt + 1), max_delay)
    jitter = random.uniform(0.8, 1.2)
    return delay * jitter


def is_page_stuck(page, last_dom_hash: str) -> tuple[bool, str]:
    """Mengesan sama ada laman 'stuck' dengan membandingkan DOM hash."""
    try:
        current_hash = page.evaluate("""() => {
            return document.body.innerHTML.length + '|' + document.title;
        }""")
        if current_hash == last_dom_hash:
            return True, current_hash
        return False, current_hash
    except Exception:
        return False, last_dom_hash