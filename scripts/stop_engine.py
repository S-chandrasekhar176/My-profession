import os
import sys

sys.path.insert(0, os.path.abspath("."))
from scripts.ub_auth_client import UBAuthClient

client = UBAuthClient()
print("Stopping engine gracefully...")
code, res = client.authed_json("POST", "/api/engine/stop")
print(f"Stop result ({code}):", res)

code_st, st = client.authed_json("GET", "/api/engine/status")
print("Engine final status:", st.get("status") or st.get("state"))
