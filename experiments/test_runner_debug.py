import sys
import os
import json
import time
import re
from urllib.parse import urljoin

# Tambah direktori induk ke sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../live_engine")))

BASE_URL = "https://sub-scene.com"

def run_experiment():
    print("==================================================")
    print("🔬 MEMULAKAN UJIAN SIMULASI STREMIO -> SUBSCENE")
    print("==================================================")

    payload_path = os.path.join(os.path.dirname(__file__), "mock_stremio_payload.json")
    with open(payload_path, "r") as f:
        mock_data = json.load(f)

    imdb_id = mock_data["worker_dispatch_payload"]["client_payload"]["imdb_id"]
    fallback_title = mock_data["worker_dispatch_payload"]["client_payload"]["fallback_title"]

    print(f"📌 [Stremio Payload] IMDb ID Ditarget: {imdb_id}")
    print(f"📌 [Stremio Payload] Tajuk Sandaran: {fallback_title}\n")

    debug_report = {
        "target_imdb": imdb_id,
        "attempts": [],
        "detected_error": None,
        "fix_applied": False,
        "found_subtitles_count": 0
    }

    # Ujian 1: Carian Menggunakan IMDb ID melalui curl-cffi
    search_url = f"{BASE_URL}/search?query={imdb_id}"
    print(f"🌐 [Ujian 1] Menghantar permintaan carian ke: {search_url}")

    try:
        from curl_cffi import requests
        session = requests.Session(impersonate="chrome")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
            "Referer": f"{BASE_URL}/"
        }
        
        res = session.get(search_url, headers=headers, allow_redirects=True, timeout=15)
        
        print(f"  └─ HTTP Status Code : {res.status_code}")
        print(f"  └─ Final Resolved URL : {res.url}")

        attempt_info = {
            "query": imdb_id,
            "status_code": res.status_code,
            "final_url": res.url,
            "is_redirected_to_movie": False
        }

        # Analisis Ralat Utama: Auto-Redirect
        if re.search(r"/subscene/\d+$", res.url):
            attempt_info["is_redirected_to_movie"] = True
            print("\n🚨 [RALAT DIKESAN] Subscene tidak memulangkan laman carian!")
            print(f"   Sebab: Subscene melencongkan (302 Redirect) terus ke laman filem: {res.url}")
            print("   Kesan: Pengikis carian standard gagal kerana mencari tag <a> carian yang tidak wujud di laman filem.\n")
            debug_report["detected_error"] = "HTTP_302_AUTO_REDIRECT_TO_MOVIE_PAGE"
        
        debug_report["attempts"].append(attempt_info)

        # Pelaksanaan Penyelesaian (Auto-Detect Movie Page)
        target_movie_url = None
        if attempt_info["is_redirected_to_movie"]:
            print("🛠️ [Ujian Solusi] Menggunakan URL lencongan terus sebagai URL filem sasaran...")
            target_movie_url = res.url
            debug_report["fix_applied"] = True
        else:
            # Fallback jika carian tajuk
            print("🛠️ [Ujian Solusi] Menguji carian menggunakan tajuk filem...")
            search_title_url = f"{BASE_URL}/search?query={fallback_title.replace(' ', '+')}"
            res_title = session.get(search_title_url, headers=headers, allow_redirects=True, timeout=15)
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(res_title.text, "html.parser")
            for a in soup.find_all("a", href=True):
                if re.match(r"^/subscene/\d+$", a["href"]):
                    target_movie_url = urljoin(BASE_URL, a["href"])
                    break

        # Semakan Ekstraksi Subtitle dari Laman Filem
        if target_movie_url:
            print(f"🎬 Membuka Laman Filem Sasaran: {target_movie_url}")
            movie_res = session.get(target_movie_url, headers=headers, timeout=15)
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(movie_res.text, "html.parser")
            
            sub_list = []
            for tr in soup.find_all("tr"):
                row_text = tr.get_text(" ", strip=True).lower()
                if "indonesian" in row_text or "melayu" in row_text or "malay" in row_text:
                    a_tag = tr.find("a", href=True)
                    if a_tag and re.match(r"^/subtitle/\d+$", a_tag["href"]):
                        sub_list.append(urljoin(BASE_URL, a_tag["href"]))

            print(f"✅ Berjaya menjumpai {len(sub_list)} sarikata Bahasa Melayu / Indonesia!")
            debug_report["found_subtitles_count"] = len(sub_list)

    except Exception as e:
        print(f"❌ [CRITICAL ERROR] Ralat tidak dijangka: {e}")
        debug_report["detected_error"] = str(e)

    # Simpan hasil analisis ke JSON untuk debugging
    output_json_path = os.path.join(os.path.dirname(__file__), "debug_output.json")
    with open(output_json_path, "w") as f:
        json.dump(debug_report, f, indent=2)

    print(f"\n💾 Laporan debug JSON disimpan di: {output_json_path}")
    print("==================================================")

if __name__ == "__main__":
    run_experiment()