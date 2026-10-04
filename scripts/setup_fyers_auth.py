import os
import sys
import json

sys.path.insert(0, os.path.abspath("."))
from scripts.ub_auth_client import UBAuthClient

client = UBAuthClient()

# 1. Save credentials
cred_payload = {
    "app_id": "2W6YIA7ZAB-100",
    "secret_key": "1BKKX4VQKU",
    "redirect_uri": "http://127.0.0.1:8000/api/brokers/fyers/callback",
    "account_type": "live"
}

print("Saving Fyers credentials...")
code_save, res_save = client.authed_json("POST", "/api/brokers/fyers/credentials", body=cred_payload)
print(f"Save credentials response ({code_save}):", res_save)

# 2. Get authorize URL
print("Fetching Fyers authorize URL...")
code_auth, res_auth = client.authed_json("GET", "/api/brokers/fyers/authorize")
print(f"Authorize response ({code_auth}):", res_auth)
if isinstance(res_auth, dict) and "auth_url" in res_auth:
    print("\nAUTH_URL_START")
    print(res_auth["auth_url"])
    print("AUTH_URL_END")
