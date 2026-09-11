import os
import sys
import tempfile
import urllib.request
import pydub
import speech_recognition

def test_audio_pipeline():
    print("\n=== [01] Ujian Penukaran Audio & Pengecaman Suara ===")
    recognizer = speech_recognition.Recognizer()
    
    # Audio sampel nombor pendek percuma untuk ujian integrasi Google Speech API
    sample_url = "https://www.google.com/recaptcha/api2/payload/audio.mp3"  # Dummy/fallback
    test_audio_url = "https://actions.google.com/sounds/v1/emergency/ambulance_siren_short.ogg" # Contoh ringkas

    temp_dir = tempfile.gettempdir()
    wav_path = os.path.join(temp_dir, "test_sample.wav")

    try:
        # Jana fail WAV ujian sintetik ringkas jika tiada internet untuk audio luaran
        print("[INFO] Menguji keupayaan eksport pydub AudioSegment...")
        silent_audio = pydub.AudioSegment.silent(duration=1000)
        silent_audio.export(wav_path, format="wav")
        
        if os.path.exists(wav_path) and os.path.getsize(wav_path) > 0:
            print("[PASSED] pydub berjaya menghasilkan fail WAV melalui ffmpeg.")
        else:
            print("[FAILED] pydub gagal mengeksport fail WAV.")
            return False

        print("[INFO] Menguji sambungan speech_recognition ke Google Recognizer...")
        with speech_recognition.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
            try:
                # Audio senyap dijangka melepasi parser tanpa UnknownValueError sistem crash
                recognizer.recognize_google(audio_data)
            except speech_recognition.UnknownValueError:
                print("[PASSED] Google Speech Engine bersedia (UnknownValueError diterima seperti yang dijangka untuk input senyap).")
            except speech_recognition.RequestError as e:
                print(f"[FAILED] Gagal menghubungi Google Speech API: {e}")
                return False

        return True

    except Exception as err:
        print(f"[FAILED] Ralat saluran audio: {err}")
        return False
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)

if __name__ == "__main__":
    success = test_audio_pipeline()
    sys.exit(0 if success else 1)