# Release Notes — v0.4.12

**Date:** Saturday, 2026-09-05 (same-weekend build, follows merged v0.4.11)
**Base:** v0.4.11 merged main @ `04d0930` (PR #4)
**Theme:** Milestone-1 MEASUREMENT — the point-in-time feature snapshot (the
leakage-proof ML dataset), shadow analytics APIs, and the baseline report.
No ML yet, by design: measurement first.

---

## 1. Point-in-time feature snapshot — headline

Every signal now carries a **feature vector captured at SCAN time**, from the
exact candles the strategy saw, BEFORE the signal exists. It rides on the
signal dict, is copied into the shadow registry at registration, and is
written ONCE into `shadow_outcomes` at resolution — never mutated afterwards.

**The leakage guarantee (the point of the whole design):** a training row can
never contain information that did not exist at decision time. Friday proved
why this matters — the sector map changed over the weekend; features joined
later from current-state tables would silently poison the dataset. Snapshots
are immutable; `None` means "not observed", never zero.

**Features (schema_version "v1", all optional/honest):**

| Feature | Meaning | None when |
|---|---|---|
| `session_class` | IST bucket: OPENING_DRIVE / MORNING / LUNCH / AFTERNOON / POWER_CLOSE | outside market hours |
| `atr`, `atr_pct` | ATR(14) absolute + as % of close | < 15 bars |
| `vwap_distance_pct` | close vs session VWAP (typical price × volume) | no usable volume |
| `trend_strength` | (EMA9 − EMA21) / ATR14 — scale-free, sign = direction | < 22 bars |
| `htf_trend` | 15-min resampled close vs EMA20 → up/down/flat | < 5 resampled bars |
| `liquidity_ratio` | last bar volume / mean window volume | < 5 volume rows |
| `features_json` | full raw vector + `computed_at` + `n_candles` + `has_volume` | — |

New pure module `shadow/features.py` — engine-free, fully unit-tested, never
raises. Kill-switch: `risk.shadow_feature_snapshot_enabled` (default true,
mirrored in defaults.yaml + pristine snapshot).

## 2. Shadow analytics (repository + API)

New read-only reporting over `shadow_outcomes` — answers *"which strategies
work, under what conditions, with what risk?"* from real resolved samples:

- `Repository.get_shadow_analytics(group_by, days, realtime_only)` — buckets
  by strategy / symbol / regime / session / htf_trend / direction / kind;
  per-bucket resolved, wins/losses, win-rate, **avg MFE/MAE**, **avg R-mfe /
  R-mae** (normalized by risk geometry |entry − SL|), avg/sum PnL per share.
- `Repository.get_shadow_weekly(group_by, weeks)` — ISO-week roll-ups.
- `Repository.get_feature_coverage()` — rows with/without features, by kind.
- Engine `/status` now exposes **`shadow_features`** next to `shadow_clock`.

**Honest empty state enforced:** fewer than 10 realtime-resolved samples in
the window returns `status="insufficient_data"` with empty buckets —
percentages over tiny samples lie. The ladder rule (realtime-verified only)
is applied by default everywhere.

**API endpoints** (auth-guarded like the rest):
- `GET /api/analytics/shadow?group_by=strategy&days=30`
- `GET /api/analytics/shadow/weekly?group_by=strategy&weeks=8`
- `GET /api/analytics/shadow/features`

## 3. Baseline report generator

`scripts/build_baseline_report.py [--days 30] [--out report.md]` — markdown
baseline report: dataset-quality section, per-strategy / per-regime /
per-session / per-symbol tables, weekly roll-up, Gate-2 clock status.
Read-only; degrades honestly on an empty database.

## 4. Migration (old data preserved, proven)

`create_all` never alters existing tables, so v0.4.12 adds
`ensure_shadow_feature_columns()` — idempotent SQLite `ALTER TABLE ADD
COLUMN` for the nine new nullable feature columns, wired into `init_db()`.
Fresh databases get the full schema from create_all; existing v0.4.11
databases are migrated in place with **every legacy row intact** (test-proven:
row-level verification + idempotency + no-op on fresh schemas).

## 5. Test campaign

- **909 passed, 0 failed** (859 → +50 new).
- `tests/test_feature_snapshot.py` (28) — hand-computed ATR/VWAP/liquidity
  math, session buckets, trend signs, honesty contract (None ≠ 0), never-
  raises with garbage inputs, JSON stability, failure recording.
- `tests/test_shadow_analytics.py` (22) — bucket-stat math vs hand
  calculations, ok/insufficient/invalid states, realtime-only filtering,
  window cutoffs, ISO-week bucketing, coverage counts, migration
  preservation + idempotency + fresh-schema no-op, API contract (400 on
  bad group, honest empty states), engine feature-flow (signal_data →
  registry by reference, kill-switch, garbage-safe).

## 6. Known limitations (honest)

- MFE/MAE remain LTP-basis lower bounds (unchanged from v0.4.11).
- Feature coverage starts at 0% and climbs with new rows only — legacy rows
  are never backfilled (that would violate point-in-time discipline).
- Orphan-sweep registrations (engine restart) carry no features — the signal
  row predates the snapshot mechanism; kind-tagged and flagged in coverage.
- `session_class` buckets assume NSE hours (09:15–15:30 IST); `None` outside.
- Analytics read today's table state; there is no historical analytics
  snapshotting yet (v0.4.13+ concern if needed).

## 7. Ops notes

- No config changes required; `risk.shadow_feature_snapshot_enabled: true`
  is the default. Set `false` to stop feature capture (rows still resolve,
  without features) — independent of `shadow_recorder_enabled`.
- Baseline report after a few live days:
  `ultrabot-web/backend/.venv/bin/python scripts/build_baseline_report.py --days 7`
- Rollback: merge-revert; the added columns are nullable and harmless to
  older code paths.
