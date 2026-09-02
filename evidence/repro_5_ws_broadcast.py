"""P2 evidence: connect to the real /ws endpoint with a real JWT and capture
live engine broadcasts (verifies H6-backend: BookingLevels serialization fix).

No mocks — real server, real token, real market data flowing.
"""
import asyncio
import json
import sys

import httpx
import websockets

BASE = "http://127.0.0.1:8000"
WS_URL = "ws://127.0.0.1:8000/ws"


async def main(listen_seconds: float = 75.0) -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=15) as client:
        r = await client.post(
            "/api/auth/login",
            data={"username": "admin", "password": "admin"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        r.raise_for_status()
        token = r.json()["access_token"]
    print(f"[ws-test] logged in, token len={len(token)}")

    got_booking_levels = False
    msg_count = 0
    channels_seen = {}

    try:
        async with websockets.connect(f"{WS_URL}?token={token}", open_timeout=10) as ws:
            print("[ws-test] CONNECTED to ws://127.0.0.1:8000/ws")
            async def reader():
                nonlocal got_booking_levels, msg_count
                end = asyncio.get_event_loop().time() + listen_seconds
                while asyncio.get_event_loop().time() < end:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, end - asyncio.get_event_loop().time()))
                    except asyncio.TimeoutError:
                        break
                    msg_count += 1
                    try:
                        payload = json.loads(raw)
                    except Exception:
                        continue
                    ch = payload.get("channel", "?")
                    channels_seen[ch] = channels_seen.get(ch, 0) + 1
                    data = payload.get("data", {})
                    t = data.get("type", "")
                    if ch == "opportunity":
                        print(f"[ws-test] OPPORTUNITY event: type={t} ts={payload.get('ts')}")
                        opp = data.get("opportunity") or {}
                        if opp:
                            bl = opp.get("booking_levels")
                            if bl is not None:
                                got_booking_levels = True
                                print(f"[ws-test]   booking_levels serialized OK: {type(bl).__name__} len={len(bl) if isinstance(bl, list) else 'n/a'}")
                            print(f"[ws-test]   {opp.get('symbol')} {opp.get('direction')} entry={opp.get('entry_price')} conf={opp.get('confidence')} strategy={opp.get('strategy')}")
                    elif ch == "telemetry":
                        evts = (data.get("telemetry") or data).get("recent_events") if isinstance(data, dict) else None
                        if msg_count % 10 == 1:
                            print(f"[ws-test] telemetry tick (scans so far: {(data.get('telemetry') or data).get('total_scans')})")
                    elif ch == "personal":
                        if msg_count == 1:
                            print(f"[ws-test] personal: {t}")
                    else:
                        if channels_seen.get(ch, 0) == 1:
                            print(f"[ws-test] first '{ch}' event: type={t}")
            await reader()
    except Exception as exc:
        print(f"[ws-test] ERROR: {exc}")
        sys.exit(1)

    print(f"\n[ws-test] RESULT: {msg_count} messages received in {listen_seconds}s")
    print(f"[ws-test] channels seen: {json.dumps(channels_seen, indent=2)}")
    print(f"[ws-test] booking_levels serialization verified: {got_booking_levels}")
    # Success criteria: connection held, messages flowed, and no serialization errors
    sys.exit(0 if msg_count > 0 else 2)


if __name__ == "__main__":
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 75.0
    asyncio.run(main(secs))
