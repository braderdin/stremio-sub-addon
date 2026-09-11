import sys
import shutil

def test_environment():
    print("=== [00] Ujian Persekitaran & Kebergantungan ===")
    all_passed = True

    # 1. Semak Python Packages
    packages = ["pydub", "speech_recognition", "DrissionPage"]
    for pkg in packages:
        try:
            __import__(pkg)
            print(f"[PASSED] Pustaka Python: {pkg}")
        except ImportError:
            print(f"[FAILED] Pustaka Python '{pkg}' tidak dijumpai. Jalankan: pip install {pkg}")
            all_passed = False

    # 2. Semak Binari ffmpeg dalam WSL
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        print(f"[PASSED] Binari sistem: ffmpeg ditemui di {ffmpeg_path}")
    else:
        print("[FAILED] ffmpeg TIDAK dijumpai! Sila jalankan: sudo apt install -y ffmpeg")
        all_passed = False

    return all_passed

if __name__ == "__main__":
    success = test_environment()
    sys.exit(0 if success else 1)