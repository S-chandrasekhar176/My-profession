import os
import sys
import json
import time

sys.path.insert(0, os.path.abspath("."))
from scripts.ub_auth_client import UBAuthClient

client = UBAuthClient()

# 1. Test connection
print("Testing Fyers connection...")
code_test, res_test = client.authed_json("POST", "/api/brokers/fyers/test")
print(f"Test Result ({code_test}):", res_test)

# 2. Start engine in PAPER mode on fyers
print("\nStarting engine in PAPER mode on fyers...")
code_start, res_start = client.authed_json("POST", "/api/engine/start", body={"mode": "paper", "broker": "fyers"})
print(f"Start Result ({code_start}):", res_start)

# 3. Wait 3 seconds
time.sleep(3)

# 4. Check engine status
code_status, status = client.authed_json("GET", "/api/engine/status")
print(f"\nEngine Status ({code_status}):")
print(json.dumps(status, indent=2))
