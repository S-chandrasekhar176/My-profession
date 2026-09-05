# Release Notes — v0.4.11

**Date:** Saturday, 2026-09-05 (built + tested same day, weekend implementation)
**Base:** v0.4.10 merged main @ `50f7a97` (PR #3)
**Theme:** G2 sector-attribution flow recovery + TMCV universe fix + the universal
shadow-outcome recorder — **the ML clock starts** (promotion-ladder Gate-2 dataset).

---

## 1. Dynamic sector map (G2 flow recovery) — headline fix

**The bug (found on Friday's live session):** the runtime F&O universe grew to
210 symbols via `fno_universe_generated.py`, but only ~50 carried sector
metadata — everything else resolved to `"Unknown"`. G2 (sector concentration)
lumped all Unknowns into ONE false bucket: TRENT BUY was blocked as a false
"same-sector" collision with COLPAL, while genuinely same-sector peers could
slip through ungrouped. 3 of Friday's 5 traded symbols (COLPAL, TRENT,
KALYANKJIL) had no real sector attribution.

**The fix — dynamic, sourced, dated; nothing hardcoded:**
- New builder `scripts/build_sector_map.py`: universe + lots from **Dhan's
  public broker master** (fresh fetch, FUTSTK underlyings, lot = mode across
  expiries), sectors + industries from **TradingView India scanner** (bulk,
  ~5 chunked calls), merged and emitted as:
  - `utils/fno_universe_generated.py` — 211 entries, each with
    symbol/name/sector/industry/lot_size (+ `cash_only` flags)
  - `config/sector_map.json` — dated manifest: `{generated_at, sources,
    counts, unknown_symbols, staleness_days_warn}`. The runtime refuses
    manifests without `generated_at`, warns past 45 days of age, and
    degrades gracefully to embedded metadata on any read error.
- **Coverage at build time: 209/211 symbols with real sector + industry;
  1 unknown (NAM — too new for TV), 0.5% residual, recorded in the manifest.**
- `utils/market_utils.py`: loads the override at import, exposes
  `get_stock_industry()`; G2 and G6 consume real sectors with zero gate-code
  changes.
- **Friday replay proof:** TRENT → Retail Trade, KALYANKJIL → Retail Trade
  (correctly grouped as real peers — G2 protection now works where it
  matters), COLPAL → Consumer Non-Durables (false collision gone).
- `M&M` (Dhan suffix-stripped `BAJAJ`→`BAJAJ_AUTO`) aliasing handled via the
  builder's `TV_ALIASES`; both re-derivable from live sources.

## 2. TMCV universe fix (cash-only tier) — 2 red tests → green

- TMCV (Tata Motors CV — NSE cash listing, **no F&O derivative series yet**)
  was dropped by every universe regeneration (Dhan FUTSTK has no TMCV row),
  breaking `test_tata_demerger_successors_present`.
- New first-class tier: `CASH_ONLY_UNIVERSE` in `market_utils.py` (source of
  truth), preserved by the builder AND re-added defensively at import even if
  a future regeneration forgets.
- New helpers: `is_fno_tradeable()` (real derivative series exists — use on
  any options/lot-math path; v0.5.0 gating) and `is_cash_only()`.
- The `_EXTRA_PICKS` overlap test now tests the RUNTIME filtered output
  (route already filters; the raw list is a curated superset by design) and
  renamed-symbol reachability checks core ∪ extras (ETERNAL legitimately
  entered the F&O core after the rename).

## 3. Universal shadow-outcome recorder — **the ML clock starts**

**The gap:** promotion-ladder Gate 2 needs ≥100 resolved shadow signals, but
until now only whole-strategy shadow mode (TRS) produced them. Friday's ~197
non-traded signals (gate-blocked, TTL-expired, skipped) evaporated with zero
learning value.

**Now every signal that never becomes a trade resolves hypothetically:**

| Source | Registry kind | Reason tag |
|---|---|---|
| Gate-blocked at scan (finally!) | `gate_blocked` | `GATE_BLOCKED` + failing gate names |
| TTL / adverse move / regime shift / session close | `never_traded` | invalidation code |
| User skip (Telegram reject included) | `never_traded` | `USER_SKIPPED` |
| Restart-orphaned pendings | `never_traded` | `ORPHAN_EXPIRED` |
| Whole-strategy shadow (TRS, existing) | `strategy_shadow` | — |

- New table `shadow_outcomes` (auto-created via `create_all`): full geometry,
  outcome (`SHADOW_TARGET/SHADOW_SL/SHADOW_EXPIRED`), **MFE/MAE** (LTP-basis,
  honestly documented as a lower bound), regime, VIX, blocking gates,
  registered/resolved timestamps.
- **Ladder rule enforced:** rows carry `feed_realtime_registered` /
  `feed_realtime_resolved` — only realtime-verified samples count toward the
  clock. Backup-feed and unverifiable samples are recorded but flagged out.
  No feed → flagged out, never blindly counted.
- Engine additions: `_register_shadow()` (single registry for all kinds,
  never raises, garbage geometry sanitized to 0.0 then dropped honestly at
  resolve), resolver writes the outcome row per cycle, ML clock aggregated
  in `Repository.get_shadow_clock()` and exposed in **`/status` →
  `shadow_clock`** (resolved_today / realtime_resolved / wins / losses /
  win_rate / per_strategy). Config kill-switch:
  `risk.shadow_recorder_enabled` (default true, mirrored in pristine snapshot).
- Existing behavior preserved: signal-row updates, broadcasts (enriched with
  kind/reason/MFE/MAE), per-strategy stats.

## 4. Test campaign

- **854 passed, 0 failed** (baseline 821 passed + 2 pre-existing failures —
  both now fixed). 31 new tests across:
  - `tests/test_sector_map.py` — coverage ≥98%, manifest integrity, Friday
    symbols, cash-only tier, builder/runtime tier consistency
  - `tests/test_shadow_recorder.py` — pure recorder logic (feed
    classification, gate extraction, outcome computation, excursion math),
    DB clock aggregation with realtime-only counting, engine hook behavior
    (kind tagging, kill-switch, never-raises, unique keys)
- G2 gate fixtures updated to derive sector names from the live taxonomy
  (previously hardcoded `"Energy"`).
- `test_fyers_candle_feed` failover test migrated from deprecated
  `asyncio.get_event_loop()` to `asyncio.run` (loop-state-safe under any
  test ordering).

## 5. Known limitations (honest)

- MFE/MAE are LTP-basis lower bounds (no intrabar high/low in the LTP feed).
- `NAM` has no sector (too new for the TV scan); manifest records it.
- Gate-blocked signals intentionally get no `signals`-table row — their
  outcomes live only in `shadow_outcomes` (keeps the blotter clean).
- Shadow clock counts **per calendar day**; weekly roll-ups are a v0.4.12
  analytics task.

## 6. Ops notes

- Fresh deploys: run `python scripts/build_sector_map.py` once (network with
  access to images.dhan.co + scanner.tradingview.com; falls back to the
  committed dated artifacts otherwise).
- Rollback: `risk.shadow_recorder_enabled: false` disables new registrations
  (existing rows stay); sector map auto-degrades if the artifact is removed.

## 7. Post-review hotfix — dependency resolution (v0.4.11.1, same branch)

**Found by the independent test round:** `pip install -r requirements.txt`
was **ResolutionImpossible** on a clean machine — every published
fyers-apiv3 version hard-pins `aiohttp==3.8.x/3.9.x`, while
requirements.txt demanded `aiohttp>=3.10.0` AND still listed
`fyers-apiv3>=0.3.5` itself. No version combination could ever satisfy
both; fresh clones, CI, and new machines died at install time. The repo
already contained the *designed* fix (two-step install: core requirements +
`requirements-fyers.txt` with `--no-deps`, performed by `setup.sh` steps
2–4) — but the stale `fyers-apiv3` line in requirements.txt deadlocked step
2 before the design could work.

**Fix (evidence-based, not guesswork):**
- `requirements.txt`: `fyers-apiv3>=0.3.5` **removed** (canonical home is
  `requirements-fyers.txt`, installed `--no-deps`); `aiohttp>=3.10.0` →
  **`aiohttp==3.9.3`** — the exact version the pinned SDK
  (`fyers-apiv3==3.1.16`) requires and the combination the entire backend
  suite is verified green on (`news/news_engine.py` imports aiohttp
  directly, so it stays a declared core dependency).
- `requirements-fyers.txt` header rewritten to current truth (no more stale
  claim that start.sh performs the install — it's `setup.sh` steps 3–4).
- **Proven fixed**: `pip install --dry-run --ignore-installed -r
  requirements.txt` now resolves cleanly against real PyPI (exit 0, no
  ResolutionImpossible) — the exact scenario that previously failed.
- **Regression guards**: `tests/test_requirements_consistency.py` (5 static
  tests) locks the contract: no fyers-apiv3 in core requirements, aiohttp
  pin must exactly match the SDK's required version (version-map checked),
  fyers files keep their `--no-deps` contract, setup.sh keeps the two-step
  flow, direct aiohttp consumers stay declared.
- **Test totals: 859 passed / 0 failed** (854 + 5 new).

**Universe-hygiene probes from the same test round — status update:**
the two flagged checks measured the RAW list, and the invariants moved in
v0.4.11 by design:
- `TMCV present: False` in `FNO_UNIVERSE` is now **intentional**: TMCV has
  no F&O derivative series, so it lives in the new `CASH_ONLY_UNIVERSE`
  tier with full metadata (`is_cash_only('TMCV') == True`,
  `is_fno_tradeable('TMCV') == False`) and is preserved across
  regenerations. The 2 red hygiene tests from earlier rounds are green.
- `extras overlap 34/45` is now measured **post-filter**:
  `test_extra_picks_do_not_overlap_core` asserts the RUNTIME-filtered
  extras (core ∪ extras minus core duplicates) contain no overlap; the raw
  list is a curated superset by design.
- Updated probes for the next round: `FNO_UNIVERSE` size == 211,
  `is_cash_only('TMCV')` == True, sector map manifest present with
  `generated_at` (209/211 sectors resolved, NAM recorded as unknown).
