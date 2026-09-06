import sys
import json
import re
from pathlib import Path
from bs4 import BeautifulSoup
from curl_cffi import requests

print("=" * 75)
print("📦 EKSTRAKSI STRUKTUR LAMAN WEB (experiments/05_expV4_dump_structure.py)")
print("=" * 75)

TARGET_TESTS = {
    "search_rambo": "https://sub-scene.com/search?query=rambo",
    "search_imdb": "https://sub-scene.com/search?query=tt0241527",
    "title_page": "https://sub-scene.com/subscene/42661",
    "subtitle_page": "https://sub-scene.com/subtitle/2121914"
}

session = requests.Session(impersonate="chrome")
dump_data = {}

for key, url in TARGET_TESTS.items():
    print(f"\n[+] Memproses {key}: {url}")
    try:
        resp = session.get(url, allow_redirects=True, timeout=15)
        print(f"    ├ Status HTTP : {resp.status_code}")
        print(f"    └ Saiz HTML   : {len(resp.text)} bytes")

        soup = BeautifulSoup(resp.text, "lxml")

        # Ekstrak semua pautan <a>
        links = []
        for a in soup.find_all("a", href=True):
            links.append({
                "text": a.get_text().strip(),
                "href": a["href"]
            })

        # Ekstrak semua elemen borang <form>
        forms = []
        for form in soup.find_all("form"):
            forms.append({
                "action": form.get("action"),
                "method": form.get("method"),
                "inputs": [{"name": i.get("name"), "type": i.get("type"), "value": i.get("value")} for i in form.find_all("input")]
            })

        # Simpan struktur ke dalam kamus
        dump_data[key] = {
            "url": url,
            "final_url": resp.url,
            "status_code": resp.status_code,
            "page_title": soup.title.string.strip() if soup.title else None,
            "forms": forms,
            "total_links": len(links),
            "links_sample": links[:30],  # 30 pautan pertama
            "raw_html_snippet": resp.text[:1500]  # Snippet 1500 aksara pertama
        }

    except Exception as e:
        print(f"    └ ❌ Ralat: {e}")
        dump_data[key] = {"url": url, "error": str(e)}

# Simpan hasil akhir ke fail JSON
output_path = Path(__file__).resolve().parent / "sub_scene_structure.json"
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(dump_data, f, indent=2, ensure_ascii=False)

print("\n" + "=" * 75)
print(f"✅ HILANG KANVAS: Struktur berjaya disimpan dalam:")
print(f"   📄 {output_path}")
print("=" * 75)