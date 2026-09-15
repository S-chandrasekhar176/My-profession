# UltraBot — Live Sandbox Runbook for Gemini IDE

> Paste this entire file into your Gemini IDE as the task brief. It instructs the
> agent to run UltraBot end-to-end in the sandbox exactly as validated in prior
> sessions: real Fyers market data, paper execution, human-in-the-loop daily OAuth.

---

## MISSION

Run UltraBot live in this sandbox for one full trading session (09:15–15:30 IST):

1. Set up the environment and start the backend.
2. Perform the **Fyers daily OAuth flow** with the user in the loop:
   the agent builds the auth link → the USER opens it in their browser and
   completes login + 2FA → the USER pastes the final callback URL back →
   the agent exchanges the code and verifies the token.
3. Start the engine in **PAPER mode** on live Fyers market data.
4. Monitor health all session, then produce an EOD summary.

---

## NON-NEGOTIABLE RULES (violating any of these = abort)

1. **PAPER MODE ONLY.** Never call `/api/engine/start` with `"mode":"live"`.
   Live broker order placement is not authorized in any phase before P7.
2. **Secrets hygiene.** APP ID / secret key / tokens live in the chat or env
   vars ONLY. Never write them to files, logs, git, or shell history.
   The only place they are stored is the backend's encrypted credentials store
   (via the API). Redact any token that appears in command output.
3. **No restarts during market hours.** Do not restart the backend or engine
   between 09:00–15:35 IST. If something breaks mid-session, report it and wait
   for the user's go-ahead (or the 15:35 flat window).
4. **Database is append-only.** Never delete/wipe `ultrabot.db` or its WAL/SHM
   files. Forensic reads only.
5. **Do not bypass the broker rate limiter.** All REST calls already flow
   through the app's internal `RateLimiter`; never add parallel scraping.
6. **The auth step requires a HUMAN.** Do not attempt to automate the Fyers
   login/2FA, do not headless-open the auth URL, do not reuse old sessions.
   Login + 2FA is intentionally manual (Fyers tokens expire daily).

---

## PREREQUISITES

- Python 3.12 with venv support; git.
- Repo: `https://github.com/S-chandrasekhar176/My-profession`
- Branch to run: ask the user (default: `feature/phase-2-fno-data-foundation`;
  alternative: `main` for stable).
- The user will provide IN CHAT (never in files):
  - Fyers **APP ID** (format `XXXXXX-100`)
  - Fyers **secret_key**
  - Fyers **redirect_uri** — MUST exactly match what is registered in the
    Fyers developer dashboard. Two valid setups:
    - `http://127.0.0.1:8000/api/brokers/fyers/callback` (backend auto-catches
      the redirect — simplest in sandbox), or
    - the frontend Settings URL (then the user manually copies the
      `auth_code` from the final URL and gives it to the agent).
  - Backend admin username + password (JWT login).

---

## STEP-BY-STEP

### S0 — Environment setup
```bash
git clone https://github.com/S-chandrasekhar176/My-profession.git ultrabot_run
cd ultrabot_run && git checkout <BRANCH>
cd ultrabot-web/backend
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-fyers-extra.txt   # plain install, WITH deps
```
If `setup.sh` exists, prefer running its 3-step flow verbatim (it installs the
Fyers SDK with correct dependency ordering).

### S1 — Start backend (verify before market-critical steps)
```bash
python app.py            # binds APP_HOST:APP_PORT, default 127.0.0.1:8000
curl -s http://127.0.0.1:8000/api/health | python -m json.tool
```
Expect HTTP 200 and JSON keys including `status`, `loop_health`-related fields.

### S2 — API login (JWT)
```bash
curl -s -X POST http://127.0.0.1:8000/api/auth/login \
  -d "username=<ADMIN_USER>" -d "password=<ADMIN_PASS>"
```
Save the returned `access_token` in a shell VARIABLE for subsequent calls
(`AUTH="Authorization: Bearer <token>"`). Do not echo it into logs.

### S3 — Save Fyers credentials (encrypted at rest)
```bash
curl -s -X POST http://127.0.0.1:8000/api/brokers/fyers/credentials \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"app_id":"<APP_ID>","secret_key":"<SECRET>","redirect_uri":"<REDIRECT_URI>"}'
```
The callback endpoint READS these saved credentials — this step must happen
BEFORE authorize/callback.

### S4 — Build the auth link and HAND IT TO THE USER
```bash
curl -s -H "$AUTH" http://127.0.0.1:8000/api/brokers/fyers/authorize
# -> {"auth_url": "https://api-t1.fyers.in/api/v3/generate-authcode?client_id=..."}
```
Print `auth_url` to the user and STOP. Say exactly:
"Open this link, log in with your Fyers credentials, complete 2FA, approve the
app, then paste the FINAL URL of your browser back to me."

### S5 — WAIT for the user's callback URL
The user will paste something like:
`http://127.0.0.1:8000/api/brokers/fyers/callback?auth_code=eyJ0eXAi...&state=...`
- `auth_code` is SINGLE-USE and expires in minutes → continue immediately.
- If their redirect_uri points at the backend callback, the exchange already
  happened automatically (browser shows Settings redirect) → skip to S7.
- If not, extract the `auth_code` query param and continue to S6.

### S6 — Exchange the code (only if auto-callback did not fire)
```bash
curl -s "http://127.0.0.1:8000/api/brokers/fyers/callback?auth_code=<CODE>"
```
Success = redirect to `.../settings?broker=fyers&auth=success` (or equivalent
JSON success). The access token is now stored encrypted in the DB.

### S7 — Verify the token
```bash
curl -s -X POST http://127.0.0.1:8000/api/brokers/fyers/test -H "$AUTH"
```

### S8 — Start the engine in PAPER mode on live Fyers data
```bash
curl -s -X POST http://127.0.0.1:8000/api/engine/start \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"mode":"paper","broker":"fyers"}'
```
Then poll until state = RUNNING:
```bash
curl -s http://127.0.0.1:8000/api/engine/status -H "$AUTH"
curl -s http://127.0.0.1:8000/api/health | python -m json.tool
```

### S9 — Session monitoring loop (every 10–15 min, log to a file)
- `GET /api/health` — verify: no `loop_never_beat: true`, feed status HEALTHY
  (not FROZEN/DEGRADED/DOWN), Telegram poll heartbeat advancing.
- Backend log sweep: **zero** occurrences of
  `non-checked-in connection`, `RuntimeWarning: coroutine ... never awaited`,
  `NullPool`, `Thread-NNNN` storms. Any occurrence → report immediately.
- `GET /api/engine/status` — positions open/closed, trades executed.
- Optional: `GET /api/opportunities` and watchlist for signal flow.

### S10 — EOD (after 15:35 IST)
1. Confirm all positions closed (engine flat) via status/trades endpoints.
2. Confirm the 15:35 EOD backup exists under `persist/snapshots/`.
3. Stop is allowed now if the user asks: `POST /api/engine/stop`.
4. Write an EOD summary: trades taken, P&L, gate blocks observed, feed
   incidents, errors, and any anomalies. Save it as a file for the user.

---

## FAILURE PLAYBOOK

| Symptom | Action |
|---|---|
| `auth_code` invalid/expired | Codes are single-use & short-lived. Re-run S4, get a fresh link, retry S5–S6 promptly. |
| Redirect URI mismatch | The `redirect_uri` saved in S3 must EXACTLY match the Fyers dashboard registration (scheme/host/path). |
| Callback says `no_credentials` | S3 was skipped or failed — save credentials, then re-run authorize. |
| Backend won't start / DB locked | Check for stale `ultrabot.db-wal`/`-shm` with no live process; do NOT delete the main DB. Report to user. |
| Feed FROZEN / DOWN alerts | Expected handler exists (watchdog). Do not restart mid-market; record timestamps and report. |
| 429 / rate-limit errors | Never bypass the limiter; reduce external polling; report. |
| Telegram silent | Check `/api/health` `telegram_poll_*` heartbeat keys before assuming deafness. |

---

## SUCCESS CHECKLIST (what "tested OK" means)

- [ ] Backend healthy (`/api/health` 200, no never-beat sentinel)
- [ ] Fyers token exchanged & verified (S7 success)
- [ ] Engine RUNNING in paper mode on live Fyers feed
- [ ] Ticks flowing (watchlist symbols updating; no FROZEN status)
- [ ] Zero NullPool / never-awaited / Thread-storm log entries all session
- [ ] Positions managed (SL/target/time exits firing per design)
- [ ] EOD backup present; session summary produced

## WHAT NOT TO DO

- Never `"mode":"live"` — paper only.
- Never store or commit APP ID/secret/token in any file.
- Never restart backend/engine 09:00–15:35 IST.
- Never wipe or "fix" the database by deletion.
- Never automate or bypass the human 2FA step.
- Never edit strategy/risk-gate code mid-session.
