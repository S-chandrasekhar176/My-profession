import os
import sys
import json

sys.path.insert(0, os.path.abspath("."))
from scripts.ub_auth_client import UBAuthClient

client = UBAuthClient()
code, health = client.authed_json("GET", "/api/health")
print("HEALTH:", code)
print(json.dumps(health, indent=2))
