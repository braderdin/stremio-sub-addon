import json
import time
import importlib
from pathlib import Path

# Import dinamik modul 00_config
_config = importlib.import_module("00_config")
DATA_DIR = _config.DATA_DIR
HISTORY_FILE = DATA_DIR / "scraped_history.json"

def load_history() -> dict:
    """
    Membaca rekod sejarah pengikisan tempatan dari data/scraped_history.json.
    """
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ Ralat membaca {HISTORY_FILE.name}: {e}")
            
    return {"processed_ids": {}, "total_items": 0}

def save_history(data: dict) -> bool:
    """
    Menyimpan rekod sejarah pengikisan terkini ke data/scraped_history.json.
    """
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"❌ Ralat menyimpan {HISTORY_FILE.name}: {e}")
        return False

def is_imdb_processed(imdb_id: str) -> bool:
    """
    Semakan pantas sama ada IMDb ID telah sedia wujud dalam rekod tempatan.
    """
    history = load_history()
    return imdb_id in history.get("processed_ids", {})

def add_processed_imdb(imdb_id: str, sub_records: list) -> bool:
    """
    Menambah atau mengemas kini rekod IMDb ID dan senarai subtitle tempatan.
    """
    history = load_history()
    if "processed_ids" not in history:
        history["processed_ids"] = {}

    history["processed_ids"][imdb_id] = {
        "updated_at": int(time.time()),
        "sub_count": len(sub_records),
        "subtitles": sub_records
    }
    history["total_items"] = len(history["processed_ids"])
    return save_history(history)