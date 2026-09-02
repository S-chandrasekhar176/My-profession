"""P3 evidence 1 — Broker credentials single-source-of-truth round-trip.

Real HTTP against the LIVE uvicorn (:8000, DB_PATH=evidence/api_boot.db):
  GET /api/brokers            → status list (was 500-crashing pre-fix when rows exist)
  POST /api/brokers/zerodha/credentials → encrypted save (new frontend wiring)
  GET /api/brokers            → zerodha configured (has_credentials=true)
  DB raw check                → stored ciphertext ≠ plaintext
  DELETE /api/brokers/zerodha/credentials (NEW route) → row gone
  DELETE again                → honest success=false no-op
  GET /api/brokers            → clean

No mocks: production app, production encryption, production DB.
"""
import asyncio
import json
import sqlite3
import sys
import urllib.request

BASE = "http://127.0.0.1:8000"
DB = "/home/z/Awesome_DE/evidence/api_boot.db"


def http(method: str, path: str, token: str, body: dict | None = None) -> tuple[int, dict | list]:
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


async def main() -> None:
    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        print(f"{'PASS' if cond else 'FAIL'}  {name} {detail}")
        if not cond:
            failures.append(name)

    # login
    req = urllib.request.Request(
        BASE + "/api/auth/login",
        data=b"username=admin&password=admin",
        method="POST",
    )
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req) as r:
        token = json.loads(r.read().decode())["access_token"]
    check("login", bool(token))

    # clean slate
    http("DELETE", "/api/brokers/zerodha/credentials", token)

    # 1. status endpoint with zero rows
    code, body = http("GET", "/api/brokers", token)
    check("GET /api/brokers (empty)", code == 200 and body.get("brokers") == [], str(body))

    # 2. save zerodha creds (the wiring that was missing in the frontend)
    code, body = http("POST", "/api/brokers/zerodha/credentials", token, {
        "api_key": "real_test_api_key_123",
        "api_secret": "real_test_secret_456",
        "access_token": "real_test_token_789",
        "user_id": "AB1234",
        "account_type": "live",
    })
    check("POST zerodha credentials", code == 200 and "saved" in body.get("message", "").lower(), str(body))

    # 3. status now reports zerodha configured
    code, body = http("GET", "/api/brokers", token)
    rows = body.get("brokers", [])
    z = next((b for b in rows if b["broker"] == "zerodha"), None)
    check(
        "GET /api/brokers shows zerodha",
        code == 200 and z is not None
        and z["has_credentials"] is True
        and z["account_type"] == "live"
        and z["is_active"] in (True, False)
        and z["auth_status"] == "never_connected",
        str(z),
    )

    # 4. DB stores ciphertext, not plaintext
    con = sqlite3.connect(DB)
    row = con.execute(
        "SELECT encrypted_credentials FROM broker_credentials WHERE broker_name='zerodha'"
    ).fetchone()
    con.close()
    check(
        "DB stores encrypted (not plaintext)",
        row is not None
        and "real_test_secret_456" not in row[0]
        and "real_test_api_key_123" not in row[0]
        and len(row[0]) > 50,
        f"ciphertext_len={len(row[0]) if row else 0}",
    )

    # 5. DELETE (new route)
    code, body = http("DELETE", "/api/brokers/zerodha/credentials", token)
    check("DELETE zerodha credentials", code == 200 and body.get("success") is True, str(body))

    # 6. idempotent no-op delete
    code, body = http("DELETE", "/api/brokers/zerodha/credentials", token)
    check("DELETE again → honest success=false", code == 200 and body.get("success") is False, str(body))

    # 7. status clean again
    code, body = http("GET", "/api/brokers", token)
    check(
        "GET /api/brokers clean after delete",
        code == 200 and not any(b["broker"] == "zerodha" for b in body.get("brokers", [])),
        str(body),
    )

    # 8. invalid broker rejected
    code, body = http("DELETE", "/api/brokers/bogus_broker/credentials", token)
    check("DELETE invalid broker → 400", code == 400, str(code))

    print()
    if failures:
        print(f"RESULT: {len(failures)} FAILURES: {failures}")
        sys.exit(1)
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
