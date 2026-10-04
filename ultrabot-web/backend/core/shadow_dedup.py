"""Shadow churn dedup (#19) — one setup, one sample.

2026-09-24 forensics showed the scan loop re-firing an identical setup every
cycle (~90s): 3x ASHOKLEY SELL @ 158.83 with identical entry/SL/target, all
resolved at the same second. Raw resolved counts were ~3x inflated, so the
verdict MIN_SAMPLE gate and win-rate confidence intervals were unreliable.

Two layers share the definitions in this module:

  * Creation layer (engine): while an unresolved SHADOW row with the same
    setup key exists today, skip recording a duplicate.
  * Statistics layer (repository): cluster already-recorded resolved rows
    into unique setups and report effective_n alongside resolved_raw.

No rows are deleted or rewritten — the ML feature store stays intact; dedup
affects statistics only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

GEOMETRY_DECIMALS = 2
CLUSTER_GAP_SECONDS = 300.0  # duplicates of one setup fire every scan cycle (~90s)

RESOLVED_STATUSES = ("SHADOW_TARGET", "SHADOW_SL", "SHADOW_EXPIRED")


def setup_key(
    strategy: Optional[str],
    symbol: Optional[str],
    direction: Optional[str],
    entry_price: Optional[float],
    stop_loss: Optional[float],
    target: Optional[float],
) -> Tuple[str, str, str, float, float, float]:
    """Identity of a shadow setup: who/where/which way, plus rounded geometry.

    Rounding to 2 decimals absorbs float jitter between scan cycles without
    merging genuinely different price levels.
    """
    def _f(v: Any) -> float:
        try:
            return round(float(v or 0.0), GEOMETRY_DECIMALS)
        except (TypeError, ValueError):
            return 0.0

    return (
        str(strategy or "").upper(),
        str(symbol or "").upper(),
        str(direction or "").upper(),
        _f(entry_price),
        _f(stop_loss),
        _f(target),
    )


def _parse_created_at(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def cluster_effective_setups(rows: Iterable[Any]) -> Dict[str, Dict[str, Any]]:
    """Cluster resolved shadow rows into unique setups, per strategy.

    Rows must expose: strategy, symbol, direction, entry_price, stop_loss,
    target, status, created_at (datetime or ISO string). Unresolved rows
    (status == "SHADOW") are ignored.

    A cluster is a run of rows with the same setup key (strategy included)
    sorted by created_at where consecutive rows are no more than
    CLUSTER_GAP_SECONDS apart. The cluster's outcome is its FIRST row's
    status — duplicates of one setup resolve against the same prices.

    Returns {strategy: {effective_n, effective_wins, effective_losses,
    effective_expired}}.
    """
    per_strategy: Dict[str, List[Tuple[datetime, Tuple, str]]] = {}
    for row in rows:
        status = str(getattr(row, "status", "") or "")
        if status not in RESOLVED_STATUSES:
            continue
        created = _parse_created_at(getattr(row, "created_at", None))
        if created is None:
            continue
        key = setup_key(
            getattr(row, "strategy", None),
            getattr(row, "symbol", None),
            getattr(row, "direction", None),
            getattr(row, "entry_price", None),
            getattr(row, "stop_loss", None),
            getattr(row, "target", None),
        )
        per_strategy.setdefault(key[0], []).append((created, key, status))

    out: Dict[str, Dict[str, Any]] = {}
    for strategy, items in per_strategy.items():
        items.sort(key=lambda t: t[0])
        effective_wins = effective_losses = effective_expired = 0
        clusters = 0
        prev_created: Optional[datetime] = None
        prev_key: Optional[Tuple] = None
        for created, key, status in items:
            # A cluster is a run of the SAME setup key within the time gap;
            # a different setup is always a new sample even at the same second.
            is_new_cluster = (
                prev_created is None
                or key != prev_key
                or (created - prev_created).total_seconds() > CLUSTER_GAP_SECONDS
            )
            if is_new_cluster:
                clusters += 1
                if status == "SHADOW_TARGET":
                    effective_wins += 1
                elif status == "SHADOW_SL":
                    effective_losses += 1
                else:
                    effective_expired += 1
            # same cluster: the first row's outcome already counted; duplicates
            # only extend the cluster window
            prev_created = created
            prev_key = key
        out[strategy] = {
            "effective_n": clusters,
            "effective_wins": effective_wins,
            "effective_losses": effective_losses,
            "effective_expired": effective_expired,
        }
    return out
