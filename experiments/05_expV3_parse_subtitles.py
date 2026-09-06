import sys
import re
from bs4 import BeautifulSoup
from curl_cffi import requests

print("=" * 70)
print("🧪 UJIAN PARSER SARIKATA BM/ID (experiments/05_expV3_parse_subtitles.py)")
print("=" * 70)

BASE_URL = "https://sub-scene.com"
TEST_IMDB = "tt0241527" # Harry Potter

session = requests.Session(impersonate="chrome")

# 1. Carian Tajuk / IMDb
search_url = f"{BASE_URL}/subtitles/searchbytitle?query={TEST_IMDB}"
print(f"\n[1] Menghantar permintaan carian: {search_url}")

resp = session.get(search_url, allow_redirects=True, timeout=15)
print(f"    Status HTTP: {resp.status_code}")

soup = BeautifulSoup(resp.text, "lxml")

# 2. Cari pautan filem/siri dalam hasil carian
movie_links = []
for a in soup.find_all("a", href=True):
    href = a["href"]
    # Cari struktur pautan filem Subscene
    if "/subtitles/" in href and href != "/subtitles/searchbytitle":
        full_link = BASE_URL + href if href.startswith("/") else href
        if full_link not in movie_links:
            movie_links.append(full_link)

print(f"    ✔ Tajuk filem/siri dijumpai: {len(movie_links)} pautan")
for link in movie_links[:3]:
    print(f"      ├─ {link}")

# 3. Jika dijumpai, buka pautan filem pertama dan cari sarikata Malay & Indonesian
if movie_links:
    target_movie_url = movie_links[0]
    print(f"\n[2] Membuka laman filem: {target_movie_url}")
    
    m_resp = session.get(target_movie_url, allow_redirects=True, timeout=15)
    m_soup = BeautifulSoup(m_resp.text, "lxml")
    
    extracted_subs = []
    
    # Imbas jadual/senarai sarikata
    for tr in m_soup.find_all("tr"):
        text = tr.get_text().lower()
        if "malay" in text or "indonesian" in text:
            a_tag = tr.find("a", href=True)
            if a_tag:
                sub_page = BASE_URL + a_tag["href"] if a_tag["href"].startswith("/") else a_tag["href"]
                lang = "Malay" if "malay" in text else "Indonesian"
                title = a_tag.get_text().strip()
                extracted_subs.append({
                    "lang": lang,
                    "title": title,
                    "page_url": sub_page
                })
                
    print(f"    ✔ Jumlah sarikata BM/ID dijumpai: {len(extracted_subs)}")
    for idx, sub in enumerate(extracted_subs[:5], 1):
        print(f"      [{idx}] [{sub['lang']}] {sub['title']}")
        print(f"          └─ Page: {sub['page_url']}")

print("\n" + "=" * 70)
print("✨ UJIAN PARSER SELESAI")
print("=" * 70)