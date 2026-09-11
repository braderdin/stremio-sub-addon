import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

TEST_FILES = [
    "00_check_env.py",
    "01_test_audio_recognition.py",
    "02_test_recaptcha_v2_demo.py"
]

def main():
    print("==================================================")
    print("   MEMULAKAN UJIAN SUITE RECAPTCHA BYPASS         ")
    print("==================================================")
    
    summary = {}

    for test_file in TEST_FILES:
        target_path = BASE_DIR / test_file
        if not target_path.exists():
            print(f"[SKIP] Fail {test_file} tidak dijumpai.")
            summary[test_file] = "NOT_FOUND"
            continue

        res = subprocess.run([sys.executable, str(target_path)])
        if res.returncode == 0:
            summary[test_file] = "PASS"
        else:
            summary[test_file] = "FAIL"
            print(f"\n[STOP] Ujian terhenti pada {test_file} kerana kegagalan.")
            break

    print("\n==================================================")
    print("   RUMUSAN KEPUTUSAN UJIAN                        ")
    print("==================================================")
    for script, status in summary.items():
        print(f"{script:<35} : {status}")

    if all(status == "PASS" for status in summary.values()) and len(summary) == len(TEST_FILES):
        print("\nKesemua komponen solver sedia untuk penilaian peringkat seterusnya.")
    else:
        print("\nSila periksa mesej ralat di atas sebelum meneruskan.")

if __name__ == "__main__":
    main()