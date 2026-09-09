# OPS TICKET — <short title>

- Date/session: 2026-MM-DD | Severity: S1(feed/engine down) / S2(degraded) / S3(cosmetic)
- Detected: <pre-open / in-session HH:MM IST / EOD-recon>
- Classified: <backend_death / workspace_suspend / hang / data_mismatch / UI>

## What happened (facts only, timestamps IST)
<timeline with evidence paths>

## Evidence bundle
- Death report: `persist/death_reports/death-<ts>.json` (if any)
- Logs: <paths + key lines>  | Screenshots: <paths>
- DB state: <row counts, positions, signal statuses>

## Containment taken (during session only)
- <kill switches flipped / restart / none>

## Suspected root cause (hypothesis, clearly marked)
<or "unknown — needs DEV investigation">

## Acceptance criteria for the fix
- <observable behavior that proves it fixed + regression test requirement>
