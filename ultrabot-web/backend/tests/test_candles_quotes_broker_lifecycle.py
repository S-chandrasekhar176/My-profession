"""v0.4.21 (wave 3) — regression tests for the /api/live-quotes DB-session leak.

Root cause being guarded (production symptom 2026-09-10, ~13:45 IST):

    sqlalchemy.pool.impl.NullPool - ERROR - The garbage collector is trying to
    clean up non-checked-in connection <AdaptedConnection <Connection(Thread-2884,
    started daemon ...)>> ...
    candles.py:288: RuntimeWarning: coroutine 'Repository.close' was never awaited

`_get_fyers_quotes_broker()` cleaned its session up with:

    res = repo.close()
    if asyncio.iscoroutine(res):   # NameError — asyncio not imported in scope
        await res                  # never ran

The NameError was swallowed by ``except Exception: pass``, so on EVERY
``/api/live-quotes`` poll (the header banner polls every ~3s per open tab):

  1. the ``Repository.close`` coroutine was created but never awaited
     (RuntimeWarning),
  2. the ``AsyncSession`` was never closed, so with NullPool its aiosqlite
     connection was dropped by the GC (SAWarning / NullPool-ERROR),
  3. each dropped aiosqlite connection stranded its dedicated daemon thread —
     the Thread-28xx/43xx storm.

These tests pin the contract: whatever happens inside
``_get_fyers_quotes_broker()``, the session context manager must exit and the
repository close coroutine must be awaited — on the success path, the
no-credentials path, and the exception path.
"""

import asyncio
import os
import sys
import warnings
from types import SimpleNamespace

import pytest

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import api.routes.candles as candles_mod  # noqa: E402
import db.database as db_database  # noqa: E402
import db.repository as db_repository  # noqa: E402
import utils.encryption as utils_encryption  # noqa: E402
import brokers.fyers as brokers_fyers  # noqa: E402


class FakeSession:
    """Mimics AsyncSession's async-context-manager lifecycle."""

    def __init__(self, log):
        self._log = log

    async def __aenter__(self):
        self._log["sessions_opened"] += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._log["sessions_closed"] += 1
        return False


class FakeRepository:
    """Mimics Repository: async CM whose __aexit__ awaits close()."""

    def __init__(self, session, log, cred, cred_error):
        self._log = log
        self._cred = cred
        self._cred_error = cred_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
        return False

    async def get_broker_credentials(self, name):
        self._log["cred_calls"] += 1
        if self._cred_error is not None:
            raise self._cred_error
        return self._cred

    async def close(self):
        self._log["repo_close_awaited"] += 1


class FakeFyersBroker:
    instances = []

    def __init__(self, app_id="", access_token="", **kwargs):
        self.app_id = app_id
        self.access_token = access_token
        FakeFyersBroker.instances.append(self)


@pytest.fixture()
def leak_log(monkeypatch):
    """Wire the fakes into the call-time import surface of
    _get_fyers_quotes_broker() and reset the module caches."""
    log = {"sessions_opened": 0, "sessions_closed": 0, "repo_close_awaited": 0, "cred_calls": 0}
    state = {"cred": SimpleNamespace(encrypted_credentials="enc-blob"), "cred_error": None}

    def make_session():
        return FakeSession(log)

    def make_repo(session):
        return FakeRepository(session, log, state["cred"], state["cred_error"])

    monkeypatch.setattr(db_database, "async_session_factory", make_session, raising=True)
    monkeypatch.setattr(db_repository, "Repository", make_repo, raising=True)
    monkeypatch.setattr(
        utils_encryption,
        "decrypt_credentials",
        lambda blob: {"access_token": "tok-123", "app_id": "app-123"},
        raising=True,
    )
    monkeypatch.setattr(brokers_fyers, "FyersBroker", FakeFyersBroker, raising=True)

    # Fresh cache per test: {"broker": None, "tried": False, "token_sig": None}
    candles_mod._fyers_quotes_cache.update({"broker": None, "tried": False, "token_sig": None})
    FakeFyersBroker.instances = []
    return log, state


@pytest.mark.asyncio
async def test_session_and_repo_closed_on_success_and_cached_second_call(leak_log):
    """Happy path: two polls → two sessions, each closed, close awaited each
    time; the FyersBroker itself is built once and cache-hit on poll #2."""
    log, _state = leak_log

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        broker1 = await candles_mod._get_fyers_quotes_broker()
        broker2 = await candles_mod._get_fyers_quotes_broker()

    assert broker1 is not None
    assert broker2 is broker1, "second poll must cache-hit the shared broker"
    assert len(FakeFyersBroker.instances) == 1, "FyersBroker built exactly once"

    # THE leak assertions: both polls fully released their session + repo.
    assert log["sessions_opened"] == 2
    assert log["sessions_closed"] == 2, "every session must exit its context manager"
    assert log["repo_close_awaited"] == 2, "Repository.close() must be awaited once per poll"

    never_awaited = [
        w for w in caught if "never awaited" in str(w.message).lower()
    ]
    assert never_awaited == [], (
        "no coroutine-missed-await warnings allowed: "
        f"{[str(w.message) for w in never_awaited]}"
    )


@pytest.mark.asyncio
async def test_session_closed_when_no_credentials_stored(leak_log):
    """No stored Fyers credentials → returns None AND the session is still
    released (the leak fired on this path too, every poll, when Fyers wasn't
    connected)."""
    log, state = leak_log
    state["cred"] = None

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = await candles_mod._get_fyers_quotes_broker()

    assert result is None
    assert log["sessions_closed"] == 1
    assert log["repo_close_awaited"] == 1
    assert not [w for w in caught if "never awaited" in str(w.message).lower()]


@pytest.mark.asyncio
async def test_session_closed_when_credential_fetch_raises(leak_log):
    """DB error inside the context managers must not leak the session."""
    log, state = leak_log
    state["cred_error"] = RuntimeError("boom — simulated DB failure")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = await candles_mod._get_fyers_quotes_broker()

    assert result is None
    assert log["sessions_closed"] == 1
    assert log["repo_close_awaited"] == 1
    assert not [w for w in caught if "never awaited" in str(w.message).lower()]


@pytest.mark.asyncio
async def test_session_closed_when_credentials_present_but_empty_blob(leak_log):
    """Credential row exists but has no encrypted payload → None, closed."""
    log, state = leak_log
    state["cred"] = SimpleNamespace(encrypted_credentials=None)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = await candles_mod._get_fyers_quotes_broker()

    assert result is None
    assert log["sessions_closed"] == 1
    assert log["repo_close_awaited"] == 1
    assert not [w for w in caught if "never awaited" in str(w.message).lower()]


def test_module_imports_asyncio_at_top_level():
    """Tripwire for the exact bug class: _get_fyers_quotes_broker() lives at
    module scope and must not depend on another function's local
    `import asyncio` (which is what made the old cleanup a NameError)."""
    assert hasattr(candles_mod, "asyncio"), (
        "api.routes.candles must import asyncio at module scope"
    )
    assert candles_mod.asyncio is asyncio
