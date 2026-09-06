import io
import zipfile
from typing import List, Dict

def decode_srt_content(raw_bytes: bytes) -> str:
    """
    Mengesan dan menukar pengekodan teks dari pelbagai format ke UTF-8 standard.
    """
    encodings = ["utf-8-sig", "utf-8", "latin-1", "windows-1252", "cp1256", "iso-8859-1"]
    for enc in encodings:
        try:
            text = raw_bytes.decode(enc)
            # Penyeragaman penanda baris baharu Windows (\r\n) ke Unix (\n)
            return text.replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue
    
    # Fallback jika berlaku kerosakan bait tertentu
    return raw_bytes.decode("utf-8", errors="ignore").replace("\r\n", "\n").replace("\r", "\n")

def extract_srt_from_zip(zip_bytes: bytes) -> List[Dict[str, str]]:
    """
    Mengekstrak semua fail sarikata (.srt / .vtt) dari bait ZIP dalam memori.
    Memulangkan senarai kamus mengandungi nama fail, kandungan teks, dan saiz.
    """
    extracted_items = []
    
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            for fname in z.namelist():
                clean_fname = fname.lower()
                # Tapis fail sarikata dan abaikan fail sistem macOS / sarikata tersembunyi
                if clean_fname.endswith((".srt", ".vtt")) and not clean_fname.startswith("__macosx"):
                    content_bytes = z.read(fname)
                    
                    # Abaikan fail rosak yang bersaiz kurang 50 bait
                    if len(content_bytes) < 50:
                        continue

                    decoded_text = decode_srt_content(content_bytes)
                    
                    extracted_items.append({
                        "filename": fname.split("/")[-1],
                        "content": decoded_text,
                        "size": len(content_bytes)
                    })

    except zipfile.BadZipFile:
        # Jika sumber muat turun bukan arkib ZIP tetapi terus fail teks SRT
        if len(zip_bytes) >= 50:
            decoded_text = decode_srt_content(zip_bytes)
            extracted_items.append({
                "filename": "extracted_sub.srt",
                "content": decoded_text,
                "size": len(zip_bytes)
            })

    return extracted_items