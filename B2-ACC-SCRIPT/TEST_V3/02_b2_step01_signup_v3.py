import os
import sys
import time
import random
from pathlib import Path

# -------------------------------------------------------------
# Penyelarasan Laluan Direktori
# -------------------------------------------------------------
CURRENT_DIR = Path(__file__).resolve().parent           # .../B2-ACC-SCRIPT/TEST_V3
PARENT_DIR = CURRENT_DIR.parent                         # .../B2-ACC-SCRIPT
ROOT_DIR = PARENT_DIR.parent                            # .../stremio-sub-addon
TEST_DIR = PARENT_DIR / "TEST"

for p in [str(CURRENT_DIR), str(PARENT_DIR), str(ROOT_DIR), str(TEST_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)


def run_step01(page, target_email: str, email_handler, human_delay=None, human_type=None, resolve_turnstile=None) -> str:
    """
    Langkah 1 Versi V3:
    - Buka salah satu laman web tunggu (Github / Neocities).
    - Paparkan emel di terminal dengan format yang jelas untuk salin & tampal.
    - Anda selesaikan pendaftaran & captcha secara manual pada peranti / pelayar sendiri.
    - Skrip memantau peti masuk Gmail sehingga pautan pengesahan Backblaze tiba.
    """
    print("\n" + "=" * 75)
    print("🚀 [LANGKAH 1 - V3] PENDAFTARAN MANUAL & PEMANTAUAN EMEL")
    print("=" * 75)

    # 1. Pilih dan buka salah satu laman web tunggu
    standby_urls = [
        "https://braderdin.github.io/",
        "https://braderdin.neocities.org/"
    ]
    chosen_url = random.choice(standby_urls)

    print(f"[*] Membuka laman web sedia ada: {chosen_url}")
    try:
        page.goto(chosen_url, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        fallback_url = standby_urls[1] if chosen_url == standby_urls[0] else standby_urls[0]
        print(f"[!] Gagal memuatkan {chosen_url} ({e}). Membuka sandaran: {fallback_url}")
        page.goto(fallback_url, wait_until="domcontentloaded", timeout=45000)

    # 2. Cetak paparan emel secara tersusun di terminal
    print("\n" + "#" * 75)
    print("📋 SILA SALIN (COPY) EMEL INI UNTUK DAFTAR DI BACKBLAZE:")
    print("")
    print(f"       👉  {target_email}  👈")
    print("")
    print("Langkah Anda:")
    print("1. Buka laman https://www.backblaze.com/sign-up/cloud-storage pada pelayar anda.")
    print("2. Tampal emel di atas & selesaikan captcha.")
    print("3. Tekan 'Get Started Free'.")
    print("4. Bot sedang 'standby' memantau emel masuk secara automatik...")
    print("#" * 75 + "\n")

    # 3. Pantau emel sehingga pautan pengesahan tiba
    print(f"⏳ Menunggu pautan pengesahan Backblaze untuk: {target_email}")
    activation_link = email_handler.wait_for_verification_link(
        target_email, 
        timeout_seconds=600, 
        poll_interval=15
    )

    if not activation_link:
        raise TimeoutError(f"Pautan pengesahan untuk {target_email} tidak diterima dalam masa 600 saat.")

    print(f"\n[✓] Pautan pengesahan berjaya diterima!")
    print(f"🔗 URL: {activation_link}\n")
    return activation_link