import pytest
import warnings
from db.database import async_session_factory, init_db
from db.repository import Repository


@pytest.mark.asyncio
async def test_repo_getter_50_iterations_no_leak():
    """Verify calling repo_getter in a loop with proper close/context-manager causes zero connection leaks."""
    await init_db()

    async def repo_getter():
        session = async_session_factory()
        return Repository(session)

    # Capture warnings during 50 rapid calls
    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always")

        for _ in range(50):
            async with (await repo_getter()) as repo:
                watchlist = await repo.get_active_watchlist()
                assert isinstance(watchlist, list)

        import gc
        gc.collect()

        # Confirm no SAWarning or ResourceWarning about non-checked-in DB connections
        conn_warnings = [
            w for w in recorded_warnings
            if "non-checked-in connection" in str(w.message).lower()
            or ("unclosed" in str(w.message).lower() and "database" in str(w.message).lower())
        ]
        assert len(conn_warnings) == 0, f"Detected DB connection warnings: {[str(w.message) for w in conn_warnings]}"
