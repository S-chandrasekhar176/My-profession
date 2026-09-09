# SIM ENGINE SPEC — SimFeed / SimBroker / injected clock (D-009)

Status: SPEC (build staged into P1/P2 waves). This is the harness component
that turns "thousands of scenarios" from a slogan into thousands of whole
sessions executed overnight at accelerated speed.

## Components

### 1. Clock injection (`core/sim/clock.py`)
- `SimClock(start_ist, speed)` implementing the same interface the engine uses
  for `now()`. All market-hours logic (ORB windows, TTLs, TIME_STOPs, session
  boundaries) must read the injected clock. Acceptance: zero direct
  `datetime.now()` in engine/scanner paths (grep-gated in harness).

### 2. SimFeed (`core/sim/feed.py`)
- Replays recorded sessions (existing EOD DBs, captured ticks, Yahoo/Fyers
  historical candles) AND generates synthetic books: gap-open, flash-crash,
  illiquid tape, stalled tick, VIX spike.
- Scripted fault injection: feed drop N minutes, stale window, wrong-source
  poison quote (must be REJECTED by source-truth checks), 429 storm replay.

### 3. SimBroker (`core/sim/broker.py`)
- Implements the broker interface PaperBroker uses; fills with configurable
  slippage/partial-fill/reject rates; margin errors; order-reject storms.

### 4. Scenario runner (`core/sim/runner.py` + `scripts/run_scenario.py`)
- Input: scenario YAML (day profile + fault schedule + assertions).
- Output: machine-readable result JSON (trades, exits, invariants held,
  expected-vs-actual diff) → `docs/agentic/scenarios/results/`.
- Assertion library: PnL invariant, exit-reason legality, no trade outside
  session, kill-switch takes effect ≤ 1 scan cycle, restart-resume parity.

## Scenario sources
1. Recorded real days (Sep 7–8 transcripts + EOD DBs) replayed as golden
   baselines — code changes must not silently alter baseline outcomes.
2. Table-driven fault matrix (fault × market regime × position state).
3. Property-based (future): random walks with injected extremes; invariants
   must never break regardless of path.

## Cadence
- Per-PR: affected-module scenarios only (fast subset).
- Phase gate: full matrix + multi-day replay + ≥2 h soak at ≥50× speed.

## Build order
1. Clock injection + grep gate (P1 wave) — enables deterministic tests.
2. SimFeed replay of one recorded day + golden baseline (P1/P2 wave).
3. SimBroker + fault injection (P2 wave, alongside F&O poller soak).
4. Matrix runner + phase-gate reports (P2 exit criteria input).
