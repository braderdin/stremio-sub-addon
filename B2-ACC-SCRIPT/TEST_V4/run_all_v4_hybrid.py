import sys
import json
import time
import random
import importlib
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
ROOT_DIR = PARENT_DIR.parent
TEST_DIR = PARENT_DIR / "TEST"

for p in [str(CURRENT_DIR), str(PARENT_DIR), str(ROOT_DIR), str(TEST_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from browser_utils_v4 import logger
import step01_signup_v4
import browser_worker_v4

b2_config = importlib.import_module("00_b2_config")


def sync_env_file(output_path: Path, all_accounts_dict: dict):
    """Menyinkronkan dan menulis berkas b2_accounts.env secara terstruktur."""
    env_file = output_path / "b2_accounts.env"
    lines = []

    sorted_accs = sorted(all_accounts_dict.values(), key=lambda x: x.get("index", 0))

    for item in sorted_accs:
        acc_num = item.get("acc_num", str(item.get("index", "")))
        lines.append(f"# Account {acc_num} (0GB - 9.5GB)")
        lines.append(f"# {item.get('B2_BUCKET_NAME')}")
        lines.append(f"# {item.get('B2_KEY_NAME')}")
        lines.append(f"B2_ACC{acc_num}_KEY_ID={item.get('B2_KEY_ID')}")
        lines.append(f"B2_ACC{acc_num}_APP_KEY={item.get('B2_APP_KEY')}")
        lines.append(f"B2_ACC{acc_num}_BUCKET_NAME={item.get('B2_BUCKET_NAME')}")
        lines.append(f"B2_ACC{acc_num}_BUCKET_ID={item.get('B2_BUCKET_ID')}")
        lines.append(f"B2_ACC{acc_num}_S3_API_ENDPOINT={item.get('B2_S3_API_ENDPOINT')}")
        lines.append(f"B2_ACC{acc_num}_KEY_NAME={item.get('B2_KEY_NAME')}\n")

    with open(env_file, "w", encoding="utf-8") as ef:
        ef.write("\n".join(lines))
    logger.info(f"Berkas environment diperbarui: {env_file}")


def main():
    total_accounts = len(b2_config.ACCOUNTS_QUEUE)
    output_path = b2_config.OUTPUT_DIR
    output_path.mkdir(parents=True, exist_ok=True)
    consolidated_file = output_path / "b2_all_accounts.json"

    all_accounts_map = {}
    if consolidated_file.exists():
        try:
            with open(consolidated_file, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
                if isinstance(existing_data, list):
                    for entry in existing_data:
                        all_accounts_map[entry.get("acc_num")] = entry
                elif isinstance(existing_data, dict):
                    all_accounts_map = existing_data
        except Exception as e:
            logger.warning(f"Catatan pembacaan berkas JSON yang sudah ada: {e}")

    logger.info(f"Memulai Suite V4 Hybrid untuk {total_accounts} akun terdaftar.")

    for acc in b2_config.ACCOUNTS_QUEUE:
        idx = acc["index"]
        acc_num = acc["acc_num"]
        email = acc["email"]
        single_file = output_path / f"b2_account_{acc_num}.json"

        logger.info(f"--- Giliran Akun [{idx}/{total_accounts}]: {email} (Akun {acc_num}) ---")

        if single_file.exists():
            try:
                with open(single_file, "r", encoding="utf-8") as sf:
                    existing_acc = json.load(sf)
                    if existing_acc.get("B2_KEY_ID") and existing_acc.get("B2_APP_KEY"):
                        logger.info(f"Akun {acc_num} sudah selesai sebelumnya. Melanjutkan ke akun berikutnya...")
                        all_accounts_map[acc_num] = existing_acc
                        sync_env_file(output_path, all_accounts_map)
                        continue
            except Exception:
                pass

        try:
            warmup_seconds = random.randint(5, 8)
            logger.info(f"Jeda persiapan sistem selama {warmup_seconds} detik...")
            time.sleep(warmup_seconds)

            # Pelayar 1: Registrasi formulir pendaftaran
            step01_signup_v4.run_step01_signup(email)

            # Pelayar 2: Verifikasi aktivasi & konfigurasi bucket
            logger.info("Memulai alur lanjutan pendaftaran...")
            result = browser_worker_v4.process_b2_account(acc)
            result["acc_num"] = acc_num

            with open(single_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=4)
            logger.info(f"Berkas hasil disimpan: {single_file}")

            all_accounts_map[acc_num] = result
            sorted_results = sorted(all_accounts_map.values(), key=lambda x: x.get("index", 0))
            with open(consolidated_file, "w", encoding="utf-8") as cf:
                json.dump(sorted_results, cf, indent=4)

            sync_env_file(output_path, all_accounts_map)

            cooldown_seconds = 10.0
            logger.info(f"Jeda istirahat {int(cooldown_seconds)} detik sebelum akun berikutnya...")
            time.sleep(cooldown_seconds)

        except Exception as e:
            logger.critical(f"Proses terhenti pada Akun [{idx}] ({email}): {e}")
            sys.exit(1)

    logger.info("Semua akun dalam antrean berhasil diproses.")


if __name__ == "__main__":
    main()