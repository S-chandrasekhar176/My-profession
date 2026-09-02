"""Test-suite database isolation.

WHY THIS FILE EXISTS (live-session-2 finding, 2026-08-28):
The suite previously ran against the REAL dev database
(``data/ultrabot.db``). At least two tests mutate it destructively:

* ``tests/test_feed_outage_degradation.py`` DELETES every active
  watchlist row ("Deterministic isolation: remove any watchlist rows
  left in the shared dev DB ... DELETE (not deactivate)").
* ``tests/test_vix_staleness_safety.py`` seeds a bare RELIANCE row into
  it.

Running pytest while the live engine was trading (as happened during the
2026-08-28 live session) silently WIPED the live session's
10-symbol pre-market watchlist — the engine scanned a single
test-seeded symbol (RELIANCE) for over two hours before anyone
noticed. This is the "tests write real data/ultrabot.db" hygiene item
from the P5 audit, now with demonstrated live damage.

THE FIX:
``db.database`` resolves the database file once, at import time, from
the ``DB_PATH`` environment variable (default: the production
``data/ultrabot.db``). pytest imports this conftest BEFORE any test
module, so setting ``DB_PATH`` here redirects every test — engine
fixtures, repositories, TestClient apps, init_db() calls — onto a
per-run temp database that is thrown away afterwards. The production
DB can no longer be touched by the test suite at all.
"""

import os
import tempfile
from pathlib import Path

_PROD_DB = Path(__file__).resolve().parent.parent / "data" / "ultrabot.db"

# Per-run temp directory (not pytest's tmp_path: this must happen at
# import time, before fixtures exist and before any test module
# imports db.database).
_TMP_DIR = Path(tempfile.mkdtemp(prefix="ultrabot_tests_"))
os.environ["DB_PATH"] = str(_TMP_DIR / "ultrabot_test.db")

# Import AFTER the env override so the module-level engine binds to the
# temp database.
import db.database as _db  # noqa: E402

# Hard guard: refuse to run the suite against the production DB even if
# someone force-sets DB_PATH from the outside.
if Path(_db.DB_PATH).resolve() == _PROD_DB.resolve():
    raise RuntimeError(
        f"Refusing to run tests against the production database ({_PROD_DB}). "
        "The test suite must stay isolated on a temp database."
    )

import pytest  # noqa: E402

import hashlib  # noqa: E402

import yaml  # noqa: E402


# ── Shipped-config tripwire v1 (v0.4.2) + v2 (v0.4.6) ────────────────────────
#
# v1 (during-run byte check): the shipped config/defaults.yaml must NEVER be
# mutated by the test run. This used to happen silently: the dual-Settings-
# singleton bug (see test_capital_resolver.py) let some tests' monkeypatched
# Settings.save miss the instance the routes actually held, so PUT payloads
# from tests were re-serialized onto the real config file — once even flipping
# the user's carry_forward_capital flag back off. The singleton is fixed at
# the source, and this session-level guard makes ANY future regression fail
# the run loudly instead of shipping a polluted config.
#
# v2 (v0.4.6, pristine-VALUES check): v1 had a blind spot — it compared md5
# BEFORE vs AFTER a run, so pollution baked into the tree BEFORE the baseline
# was captured shipped invisibly. That is exactly how v0.4.2 shipped
# hard_risk_pct 1.5 (pristine 1.0) and vix_threshold 22.0 (pristine 20): a
# test payload polluted the file, the cleanup pass only removed ALIAS keys
# (value drift on legitimate keys was invisible), and every later suite run
# reported "byte-stable". v2 pins the shipped VALUES against
# tests/pristine_config_snapshot.yaml at session START and END. Any value
# drift — regardless of when or how it got there — fails the run with a
# precise diff.
_DEFAULTS_YAML = Path(__file__).resolve().parent.parent / "config" / "defaults.yaml"
_SNAPSHOT_YAML = Path(__file__).resolve().parent / "pristine_config_snapshot.yaml"


def _md5_of_defaults() -> str:
    try:
        return hashlib.md5(_DEFAULTS_YAML.read_bytes()).hexdigest()
    except FileNotFoundError:
        return "<missing>"


def _normalize_config(obj):
    """Normalize parsed YAML for value comparison (int/float unified)."""
    if isinstance(obj, dict):
        return {str(k): _normalize_config(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_config(v) for v in obj]
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float)):
        return float(obj)
    return obj


def _flatten(obj, prefix=""):
    """Flatten nested dict/list into {'path.to.key': leaf} for precise diffs."""
    flat = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            flat.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            flat.update(_flatten(v, f"{prefix}[{i}]"))
    else:
        flat[prefix] = obj
    return flat


def _check_pristine_values(when: str) -> None:
    """Fail loudly if defaults.yaml VALUES drifted from the pristine snapshot.

    Bypass for intentional local drift (e.g. you changed settings through the
    app UI on your own machine and want to run the suite anyway):
    ULTRABOT_CONFIG_SNAPSHOT_BYPASS=1 python -m pytest ...
    The during-run mutation check (v1) is NEVER bypassable.
    """
    if os.environ.get("ULTRABOT_CONFIG_SNAPSHOT_BYPASS") == "1":
        return
    try:
        shipped = _normalize_config(yaml.safe_load(_DEFAULTS_YAML.read_text()))
        pristine = _normalize_config(yaml.safe_load(_SNAPSHOT_YAML.read_text()))
    except FileNotFoundError as exc:
        raise AssertionError(
            f"Config tripwire v2 cannot read its inputs ({exc}). The shipped "
            "config and tests/pristine_config_snapshot.yaml must both exist."
        ) from exc

    flat_shipped = _flatten(shipped)
    flat_pristine = _flatten(pristine)
    drift = []
    for key in sorted(set(flat_pristine) | set(flat_shipped)):
        want = flat_pristine.get(key, "<absent from snapshot>")
        got = flat_shipped.get(key, "<absent from shipped file>")
        if want != got:
            drift.append(f"  {key}: snapshot={want!r}  shipped={got!r}")
    if drift:
        raise AssertionError(
            f"CONFIG VALUE DRIFT DETECTED ({when}) — config/defaults.yaml "
            "no longer matches tests/pristine_config_snapshot.yaml:\n"
            + "\n".join(drift)
            + "\n"
            "\nThis is the failure mode that silently shipped hard_risk_pct "
            "1.5 and vix_threshold 22.0 in v0.4.2 (a test payload leaked into "
            "the shipped file before the baseline was captured). Either:\n"
            "  1. REVERT config/defaults.yaml to the pristine values, or\n"
            "  2. If the change is INTENTIONAL (conscious config change), "
            "review the diff above and refresh the snapshot:\n"
            "       cp config/defaults.yaml tests/pristine_config_snapshot.yaml\n"
            "     (commit the snapshot update together with the change), or\n"
            "  3. Set ULTRABOT_CONFIG_SNAPSHOT_BYPASS=1 for a one-off local "
            "run with intentionally drifted config."
        )


# Session-START check runs at conftest import — before any test can run, and
# before pollution could masquerade as the baseline.
_check_pristine_values("session start")


@pytest.fixture(scope="session", autouse=True)
def _defaults_yaml_must_not_change():
    before = _md5_of_defaults()
    yield
    # Session-END pristine re-check (value-level; catches pollution even if a
    # failing test restores bytes imperfectly).
    _check_pristine_values("session end")
    after = _md5_of_defaults()
    if before != after:
        # Restore immediately so a failed run doesn't ship the pollution.
        raise AssertionError(
            "TEST SUITE MUTATED config/defaults.yaml "
            f"(md5 {before} -> {after}). A test executed a real Settings.save() "
            "against the shipped config — patch the save path in that test "
            "(see tests/test_risk_limits_no_crosswrite.py for the "
            "route-instance-safe patching pattern)."
        )


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_db():
    """Best-effort cleanup of the temp database after the whole run."""
    yield
    for suffix in ("", "-wal", "-shm"):
        try:
            (_TMP_DIR / ("ultrabot_test.db" + suffix)).unlink(missing_ok=True)
        except Exception:
            pass
    try:
        _TMP_DIR.rmdir()
    except Exception:
        pass
