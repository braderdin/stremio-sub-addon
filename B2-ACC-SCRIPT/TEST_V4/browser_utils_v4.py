import time
import random
import json
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("B2_V4")


def human_delay(min_s: float = 1.5, max_s: float = 4.5):
    """Memberikan jeda acak menyerupai pola penjelajahan manusia."""
    time.sleep(random.uniform(min_s, max_s))


def human_type(locator, text: str, min_delay: int = 110, max_delay: int = 220):
    """Mengetik teks secara berurutan dengan jeda realistis."""
    locator.scroll_into_view_if_needed()
    locator.click()
    human_delay(0.8, 1.8)
    locator.press_sequentially(text, delay=random.randint(min_delay, max_delay))
    human_delay(1.0, 2.5)


def robust_human_type(locator, page, text: str):
    """
    Mengetik teks emel dan memicu event DOM agar validasi form React terkonfirmasi.
    """
    locator.scroll_into_view_if_needed()
    locator.click()
    human_delay(0.8, 2.0)

    locator.fill("")
    time.sleep(0.4)

    locator.press_sequentially(text, delay=random.randint(110, 190))
    human_delay(1.0, 2.5)

    if locator.input_value() != text:
        logger.warning("Nilai input belum lengkap. Mengisi ulang secara presisi.")
        locator.fill(text)
        human_delay(1.0, 2.0)

    try:
        locator.dispatch_event("input")
        locator.dispatch_event("change")
        locator.press("Tab")
    except Exception as e:
        logger.debug(f"Event dispatch error: {e}")
    human_delay(1.0, 2.5)

    err_notice = page.locator("text=/Please enter a valid email address/i").first
    try:
        if err_notice.is_visible(timeout=1000):
            logger.warning("Peringatan format emel aktif. Memperbarui status form.")
            locator.fill(text)
            locator.dispatch_event("input")
            locator.dispatch_event("change")
            locator.press("Tab")
            human_delay(1.0, 2.5)
    except Exception:
        pass


def resolve_cloudflare_turnstile(page, max_retries: int = 15):
    """Memeriksa dan menyelesaikan rintangan Cloudflare Turnstile pada iframe."""
    for _ in range(max_retries):
        try:
            for frame in page.frames:
                if "challenges.cloudflare.com" in frame.url or "turnstile" in frame.url:
                    chk = frame.query_selector("input[type=checkbox], .ctp-checkbox-label, #challenge-stage")
                    if chk:
                        logger.info("Mengklik kotak verifikasi Cloudflare Turnstile...")
                        chk.click()
                        page.wait_for_timeout(2500)

            content = page.content().lower()
            if "just a moment" not in content and "attention required" not in content:
                break
        except Exception:
            pass
        page.wait_for_timeout(2500)


def dump_page_diagnostics(page, temp_dir: Path, acc_idx: int):
    """Menyimpan berkas HTML dan struktur DOM untuk analisis ralat."""
    temp_dir.mkdir(parents=True, exist_ok=True)

    html_file = temp_dir / f"page_dump_acc_{acc_idx}.html"
    try:
        html_file.write_text(page.content(), encoding="utf-8")
        logger.info(f"Diagnostik HTML disimpan: {html_file}")
    except Exception as e:
        logger.error(f"Gagal menyimpan dump HTML: {e}")

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
        logger.info(f"Diagnostik struktur DOM disimpan: {json_file}")
    except Exception as e:
        logger.error(f"Gagal menyimpan dump JSON DOM: {e}")