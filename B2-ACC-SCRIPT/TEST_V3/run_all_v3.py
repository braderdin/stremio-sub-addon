import os
import sys
import json
import time
import importlib
from pathlib import Path

# -------------------------------------------------------------
# Penyelarasan Laluan Direktori (sys.path)
# -------------------------------------------------------------
CURRENT_DIR = Path(__file__).resolve().parent           # .../B2-ACC-SCRIPT/TEST_V3
PARENT_DIR = CURRENT_DIR.parent                         # .../B2-ACC-SCRIPT
ROOT_DIR = PARENT_DIR.parent                            # .../stremio-sub-addon
TEST_DIR = PARENT_DIR / "TEST"

for p in [str(CURRENT_DIR), str(PARENT_DIR), str(ROOT_DIR), str(TEST_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

# Import konfigurasi dan worker versi V3
b2_config = importlib.import_module("00_b2_config")
browser_worker = importlib.import_module("02_b2_browser_worker_v3")


def sync_env_file(output_path: Path, all_accounts_dict: dict):
    """Menulis fail .env yang tersusun bagi semua akaun yang berjaya disiapkan."""
    env_file = output_path / "b2_accounts.env"
    lines = []
    
    # Susun mengikut nombor indeks akaun
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
    print(f"[+] Fail persekitaran dikemas kini: {env_file}")


def main():
    total_accounts = len(b2_config.ACCOUNTS_QUEUE)
    output_path = b2_config.OUTPUT_DIR
    output_path.mkdir(parents=True, exist_ok=True)
    consolidated_file = output_path / "b2_all_accounts.json"

    # Muat semula data sedia ada jika fail b2_all_accounts.json telah wujud
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
            print(f"[!] Maklumat bacaan fail JSON sedia ada: {e}")

    print(f"\n=======================================================")
    print(f"⭐ [SUITE V3] Memulakan Automasi bagi {total_accounts} Akaun")
    print(f"=======================================================\n")

    for acc in b2_config.ACCOUNTS_QUEUE:
        idx = acc["index"]
        acc_num = acc["acc_num"]
        email = acc["email"]
        single_file = output_path / f"b2_account_{acc_num}.json"

        print(f"\n" + "=" * 60)
        print(f" Giliran Akaun [{idx}/{total_accounts}]: {email} (Akaun {acc_num})")
        print("=" * 60)

        # Semak jika akaun ini sudah pernah siap sepenuhnya
        if single_file.exists():
            try:
                with open(single_file, "r", encoding="utf-8") as sf:
                    existing_acc = json.load(sf)
                    if existing_acc.get("B2_KEY_ID") and existing_acc.get("B2_APP_KEY"):
                        print(f"[✓] Akaun {acc_num} dikesan telah selesai sebelum ini. Melangkau...")
                        all_accounts_map[acc_num] = existing_acc
                        sync_env_file(output_path, all_accounts_map)
                        continue
            except Exception:
                pass

        try:
            # Jalankan worker versi V3
            result = browser_worker.process_b2_account(acc)
            result["acc_num"] = acc_num

            # 1. Simpan rekod fail JSON individu
            with open(single_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=4)
            print(f"[+] Fail individu disimpan: {single_file}")

            # 2. Kemas kini fail JSON gabungan
            all_accounts_map[acc_num] = result
            sorted_results = sorted(all_accounts_map.values(), key=lambda x: x.get("index", 0))
            with open(consolidated_file, "w", encoding="utf-8") as cf:
                json.dump(sorted_results, cf, indent=4)
            print(f"[+] Fail gabungan b2_all_accounts.json dikemas kini.")

            # 3. Kemas kini fail .env
            sync_env_file(output_path, all_accounts_map)

            # Jeda masa rehat sebelum akaun seterusnya
            cooldown_seconds = 15.0
            print(f"[⏳] Berehat seketika selama {int(cooldown_seconds)} saat sebelum akaun berikutnya...")
            time.sleep(cooldown_seconds)

        except Exception as e:
            print("\n" + "!" * 80)
            print(f"🚨 HENTI KECEMASAN (Fail-Fast): Akaun [{idx}] ({email}) GAGAL diproses!")
            print(f"Punca Ralat: {e}")
            print(f"🛑 Skrip dihentikan untuk semakan.")
            print("!" * 80 + "\n")
            sys.exit(1)

    print(f"\n[🎉] SELESAI! Kesemua akaun telah berjaya diproses.")
    print(f"    ├─ JSON Gabungan : {consolidated_file}")
    print(f"    └─ Format .ENV   : {output_path / 'b2_accounts.env'}")


if __name__ == "__main__":
    main()