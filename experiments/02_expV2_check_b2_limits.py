#!/usr/bin/env python3
import os
import base64
import json
import urllib.request
import urllib.error
from pathlib import Path
from dotenv import load_dotenv

# Baca fail .env.local dari root direktori projek
env_path = Path("/home/braderdin/stremio-sub-addon/.env.local")
if env_path.exists():
    load_dotenv(dotenv_path=env_path, override=True)

def find_b2_accounts():
    accounts = []
    # Mengesan format B2_ACC1_KEY_ID hingga B2_ACC10_KEY_ID
    for i in range(1, 11):
        key_id = os.getenv(f"B2_ACC{i}_KEY_ID")
        app_key = os.getenv(f"B2_ACC{i}_APP_KEY")
        bucket_id = os.getenv(f"B2_ACC{i}_BUCKET_ID")
        bucket_name = os.getenv(f"B2_ACC{i}_BUCKET_NAME")
        
        if key_id and app_key:
            accounts.append({
                "label": f"B2 Acc {i}",
                "key_id": key_id.strip(),
                "app_key": app_key.strip(),
                "bucket_id": bucket_id.strip() if bucket_id else None,
                "bucket_name": bucket_name.strip() if bucket_name else None
            })
    return accounts

def test_b2_account_limits(acc):
    print(f"\n==================================================")
    print(f"🔍 Memeriksa: {acc['label']} ({acc['bucket_name'] or 'Tanpa Nama Bucket'})")
    print(f"   Key ID: {acc['key_id'][:10]}...")
    print(f"==================================================")

    # 1. Ujian Class C Transaction (Authorization)
    auth_url = "https://api.backblazeb2.com/b2api/v2/b2_authorize_account"
    auth_header = base64.b64encode(f"{acc['key_id']}:{acc['app_key']}".encode("utf-8")).decode("utf-8")
    
    headers = {"Authorization": f"Basic {auth_header}"}
    req = urllib.request.Request(auth_url, headers=headers)
    
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            api_url = data["apiUrl"]
            auth_token = data["authorizationToken"]
            account_id = data["accountId"]
            print("  🟢 [Class C - Auth] OK (Kunci Sah & Akaun Aktif)")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        print(f"  🔴 [Class C - Auth FAIL] Status {e.code}: {err_body}")
        return
    except Exception as e:
        print(f"  🔴 [Auth Error] {str(e)}")
        return

    # 2. Ujian Class B Transaction (Read / Download Cap)
    list_buckets_url = f"{api_url}/b2api/v2/b2_list_buckets"
    payload_b = json.dumps({"accountId": account_id}).encode("utf-8")
    headers_b2 = {
        "Authorization": auth_token,
        "Content-Type": "application/json"
    }

    req_b = urllib.request.Request(list_buckets_url, data=payload_b, headers=headers_b2, method="POST")
    try:
        with urllib.request.urlopen(req_b) as resp:
            print("  🟢 [Class B - Read/Download Cap] OK (Muat turun / Baca Dibenarkan)")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        print(f"  🚨 [Class B LIMIT EXCEEDED] Status {e.code}")
        print(f"     Respons B2: {err_body}")

    # 3. Ujian Class A Transaction (Write / Upload Cap)
    target_bucket_id = acc["bucket_id"]
    if target_bucket_id:
        upload_url_endpoint = f"{api_url}/b2api/v2/b2_get_upload_url"
        payload_a = json.dumps({"bucketId": target_bucket_id}).encode("utf-8")
        
        req_a = urllib.request.Request(upload_url_endpoint, data=payload_a, headers=headers_b2, method="POST")
        try:
            with urllib.request.urlopen(req_a) as resp:
                print("  🟢 [Class A - Write/Upload Cap] OK (Muat naik Dibenarkan)")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            print(f"  🚨 [Class A LIMIT EXCEEDED] Status {e.code}")
            print(f"     Respons B2: {err_body}")
    else:
        print("  ⚠️ [Class A Ditinggalkan] B2_ACC_BUCKET_ID tidak ditemui.")

def main():
    accounts = find_b2_accounts()

    if not accounts:
        print("❌ Tiada akaun format B2_ACC1_KEY_ID ditemui!")
        return

    print(f"🚀 Memulakan semakan kuota untuk {len(accounts)} akaun B2...")
    for acc in accounts:
        test_b2_account_limits(acc)

if __name__ == "__main__":
    main()