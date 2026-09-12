import os
import sys
from pathlib import Path
import dns.resolver
import requests
from dotenv import load_dotenv

# 1. Tetapan & Maklumat Domain
DOMAIN = "braderdin-sub.eu.org"
TARGET_NS = ["bob.ns.cloudflare.com", "gene.ns.cloudflare.com"]
REQ_TICKET = "20260912185241-arf-35926"

# 2. Muat Kunci Telegram (.env.local jika jalan lokal, os.environ jika di GitHub Action)
ENV_LOCAL_PATH = Path("/home/braderdin/stremio-sub-addon/.env.local")
if ENV_LOCAL_PATH.exists():
    load_dotenv(dotenv_path=ENV_LOCAL_PATH)
else:
    load_dotenv()

BOT_TOKEN = os.getenv("X_TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("X_TELEGRAM_CHAT_ID")

if not BOT_TOKEN or not CHAT_ID:
    print("❌ Ralat: X_TELEGRAM_BOT_TOKEN atau X_TELEGRAM_CHAT_ID tidak dijumpai!")
    sys.exit(1)

def check_nameservers(domain: str) -> list:
    """Semak rekod NS secara terus ke DNS Cloudflare & Google untuk elak DNS caching."""
    resolver = dns.resolver.Resolver()
    resolver.nameservers = ["1.1.1.1", "8.8.8.8"]
    resolver.timeout = 5.0
    resolver.lifetime = 5.0

    try:
        answers = resolver.resolve(domain, "NS")
        return [str(r.target).rstrip(".").lower() for r in answers]
    except Exception:
        return []

def send_telegram_alert(message: str):
    """Hantar notifikasi kemas kini ke Telegram."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            print("✅ Notifikasi Telegram berjaya dihantar!")
        else:
            print(f"⚠️ Gagal hantar Telegram: {resp.text}")
    except Exception as e:
        print(f"❌ Ralat sambungan Telegram: {e}")

def main():
    detected_ns = check_nameservers(DOMAIN)
    is_active = any(ns in detected_ns for ns in TARGET_NS)

    if is_active:
        ns_text = "\n".join([f"  🔹 <code>{ns}</code>" for ns in detected_ns])
        msg = (
            "🎉 <b>DOMAIN SUDAH LULUS & AKTIF!</b> 🎉\n\n"
            f"🌐 <b>Domain:</b> <code>{DOMAIN}</code>\n"
            f"🎫 <b>No. Permohonan:</b> <code>{REQ_TICKET}</code>\n\n"
            "✅ <b>Nameserver Dikesan:</b>\n"
            f"{ns_text}\n\n"
            "🚀 <b>Tindakan Seterusnya:</b>\n"
            "1. Buka Cloudflare Dashboard (Status dah Active).\n"
            "2. Aktifkan <b>Smart Tiered Cache</b>.\n"
            "3. Pautkan ke Worker Addon Stremio di menu <i>Custom Domains</i>!"
        )
    else:
        msg = (
            "⏳ <b>STATUS DOMAIN: MASIH DALAM PROSES</b> ⏳\n\n"
            f"🌐 <b>Domain:</b> <code>{DOMAIN}</code>\n"
            f"🎫 <b>No. Tiket NIC:</b> <code>{REQ_TICKET}</code>\n"
            "📌 <b>Status:</b> Belum dipropagasi / Masih dalam giliran kelulusan nic.eu.org.\n\n"
            "🎯 <i>Bot akan terus semak secara automatik setiap 2 hari.</i>"
        )

    print(f"Hasil semakan untuk {DOMAIN}: {detected_ns if detected_ns else 'Tiada NS'}")
    send_telegram_alert(msg)

if __name__ == "__main__":
    main()