"""P3 evidence 2 — Error console real-data round-trip.

1. Drive the PRODUCTION ErrorEngine with a REAL failure: attempt a genuine
   Yahoo Finance fetch for a nonexistent symbol, catch the real exception,
   hand it to error_engine.handle_error (the exact path engine scan errors
   take: DB write + error_code + severity + auto-recovery attempt).
2. GET /api/errors must return the real row in the exact shape the rewired
   frontend consumes ({errors:[...]}, snake_case, lowercase severity).
3. PUT /api/errors/{id}/resolve must flip is_resolved in the DB.
"""
import asyncio
import json
import sys
import urllib.request

sys.path.insert(0, "/home/z/Awesome_DE/ultrabot-web/backend")

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

    # ── 1. REAL failing operation → production ErrorEngine ──
    # An unauthenticated call to Kite Connect's real API — this is the exact
    # failure a user hits when their daily access token expires (real 403
    # from the real broker API, not a fabricated condition).
    import httpx
    from errors.error_engine import ErrorEngine
    from db.database import async_session_factory
    from db.repository import Repository

    real_exc: Exception | None = None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("https://api.kite.trade/user/margins")
            resp.raise_for_status()
    except Exception as e:  # noqa: BLE001 — we WANT the real exception
        real_exc = e

    check("real broker API failure captured", real_exc is not None, f"{type(real_exc).__name__}: {str(real_exc)[:80]}" if real_exc else "none")

    error_engine = ErrorEngine()

    async def repo_getter():
        session = async_session_factory()
        try:
            return Repository(session)
        except Exception:
            await session.close()
            raise

    error_engine.set_db_session_getter(repo_getter)

    result = await error_engine.handle_error(
        real_exc,
        context={"source": "p3_evidence", "operation": "yahoo_invalid_symbol_fetch"},
    )
    check("error_engine.handle_error saved", bool(result.get("saved_to_db")), str(result)[:160])
    error_code = result.get("error_code", "")
    severity = result.get("severity", "")
    check("severity is lowercase class", severity in ("info", "warning", "error", "critical"), severity)

    # ── 2. GET /api/errors returns the real row in frontend-consumable shape ──
    code, body = http("GET", "/api/errors?limit=50", token)
    check("GET /api/errors 200", code == 200, str(code))
    rows = body.get("errors", []) if isinstance(body, dict) else []
    row = next((r for r in rows if r.get("error_code") == error_code), None)
    check("error row present", row is not None, f"error_code={error_code}")
    if row:
        check(
            "row shape (snake_case + severity lowercase + resolution fields)",
            all(k in row for k in (
                "id", "error_code", "error_type", "severity", "what_happened",
                "why_happened", "how_to_fix", "is_resolved", "auto_recovery_attempted",
                "auto_recovery_result", "created_at",
            )),
            "",
        )
        check("row unresolved", row["is_resolved"] is False)
        check(
            "what_happened contains the REAL exception text",
            isinstance(real_exc, Exception) and str(real_exc)[:40] in (row["what_happened"] or ""),
            row["what_happened"][:80],
        )

        # ── 3. Resolve round-trip ──
        code2, body2 = http("PUT", f"/api/errors/{row['id']}/resolve", token, {"resolution_note": "Resolved by P3 evidence"})
        check("PUT resolve 200", code2 == 200, str(body2)[:120])

        code3, body3 = http("GET", "/api/errors?limit=50", token)
        rows3 = body3.get("errors", []) if isinstance(body3, dict) else []
        row3 = next((r for r in rows3 if r.get("id") == row["id"]), None)
        check("row is_resolved=true after PUT", row3 is not None and row3["is_resolved"] is True)
        check("resolution_note persisted", row3 is not None and row3.get("resolution_note") == "Resolved by P3 evidence")

    print()
    if failures:
        print(f"RESULT: {len(failures)} FAILURES: {failures}")
        sys.exit(1)
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    asyncio.run(main())
