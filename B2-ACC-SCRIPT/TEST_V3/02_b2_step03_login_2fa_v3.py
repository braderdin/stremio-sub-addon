import time

def run_step03(page, target_email: str, b2_password: str, email_handler, human_delay, human_type, resolve_turnstile):
    """Langkah 3 V3: Log masuk pintar (menyokong mod Next mahupun mod paparan terus kedua-dua kotak)."""
    print("\n---> [LANGKAH 3] Log Masuk & Pengesahan Kod 2FA...")
    
    # Jika pelayar belum berada di user_signin.htm, navigasikan terus
    if "user_signin.htm" not in page.url.lower():
        page.goto("https://secure.backblaze.com/user_signin.htm", wait_until="load", timeout=60000)
        human_delay(1.5, 2.5)

    resolve_turnstile(page)

    # 1. Pastikan medan emel diisi
    email_field = page.locator('#email-field, input[name="email-field"]').first
    if email_field.is_visible(timeout=5000):
        val = email_field.input_value()
        if not val or target_email.lower() not in val.lower():
            human_type(email_field, target_email)
            human_delay(0.8, 1.5)

    # 2. Semak sama ada kotak kata laluan SUDAH terpapar di skrin
    pass_field = page.locator('input[type="password"]:not(.hidden-password-field), input#password-field, input[name*="password" i]').first
    
    # HANYA klik Next jika kotak kata laluan BELUM keluar
    if not pass_field.is_visible():
        next_btn = page.locator('button:has-text("Next"), input[value="Next"]').first
        if not next_btn.is_visible(timeout=3000):
            next_btn = page.locator('#submit-button').first
        if next_btn.is_visible(timeout=3000):
            next_btn.click()
            human_delay(1.5, 2.5)

    # 3. Masukkan Kata Laluan
    pass_field.wait_for(state="visible", timeout=15000)
    human_type(pass_field, b2_password)
    human_delay(0.8, 1.5)

    # 4. Klik Log Masuk (Sign In)
    signin_btn = page.locator('button:has-text("Sign in"), button:has-text("Sign In"), #submit-button').first
    signin_btn.click()
    page.wait_for_load_state("networkidle")
    human_delay(2.0, 4.5)

    # 5. Tunggu Halaman 2FA & Kod OTP
    print("[*] Menunggu kotak input kod 2FA di skrin...")
    code_field = page.locator('input[name="code"], input[id*="code" i]').first
    code_field.wait_for(state="visible", timeout=45000)
    human_delay(1.5, 2.5)

    # Ambil kod OTP dari Gmail
    otp_code = email_handler.wait_for_2fa_code(target_email, timeout_seconds=300, poll_interval=20)
    human_type(code_field, otp_code)
    human_delay(1.0, 3.0)

    try:
        trust_chk = page.locator('input[type="checkbox"], .toggle').first
        if trust_chk.is_visible(timeout=2000):
            trust_chk.check()
    except Exception:
        pass

    page.locator('button:has-text("Enter Code"), input[value="Enter Code"]').first.click()
    
    # Tunggu sehingga tiba di papan pemuka bucket
    page.wait_for_url("**/b2_buckets.htm*", timeout=60000)
    human_delay(3.0, 5.0)
    print("[✓] Berjaya melepasi 2FA dan tiba di Papan Pemuka Buckets!")