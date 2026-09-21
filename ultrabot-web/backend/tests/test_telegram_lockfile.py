import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from notifications.telegram_interactive import InteractiveTelegramBot, TelegramPollLock
from notifications.telegram_bot import TelegramBot


@pytest.mark.asyncio
async def test_telegram_poll_lock_single_instance_ownership(tmp_path):
    lock_file = str(tmp_path / "test_poll.lock")
    lock1 = TelegramPollLock(lock_file)
    lock2 = TelegramPollLock(lock_file)

    acquired1, pid1 = lock1.acquire()
    assert acquired1 is True
    assert pid1 == os.getpid()

    # Second lock fails to acquire while lock1 is held
    acquired2, pid2 = lock2.acquire()
    assert acquired2 is False
    assert pid2 == os.getpid()

    # Release lock1
    lock1.release()

    # Now lock2 can acquire
    acquired2_after, pid2_after = lock2.acquire()
    assert acquired2_after is True
    assert pid2_after == os.getpid()
    lock2.release()


@pytest.mark.asyncio
async def test_two_interactive_poller_instances_second_yields(tmp_path, caplog):
    lock_file = str(tmp_path / "tg_exclusive.lock")
    cfg = {
        "telegram_interactive_enabled": True,
        "telegram_bot_token": "token-123",
        "telegram_chat_id": "456",
        "telegram_poll_timeout": 1,
        "telegram_poll_lockfile": lock_file,
    }

    bot1 = InteractiveTelegramBot(telegram_bot=MagicMock(), notif_config=cfg)
    bot2 = InteractiveTelegramBot(telegram_bot=MagicMock(), notif_config=cfg)

    tg1_calls = []
    async def fake_tg1(method, **kwargs):
        tg1_calls.append(method)
        if method == "deleteWebhook":
            return {"ok": True}
        await asyncio.sleep(0.3)
        return {"ok": True, "result": []}

    tg2_calls = []
    async def fake_tg2(method, **kwargs):
        tg2_calls.append(method)
        return {"ok": True, "result": []}

    bot1._tg = fake_tg1
    bot2._tg = fake_tg2

    # Start bot1 poller task
    task1 = asyncio.create_task(bot1.poll_loop())
    await asyncio.sleep(0.05)

    # Start bot2 poller task - should detect held lock, log warning and exit immediately
    with caplog.at_level("WARNING"):
        await bot2.poll_loop()

    assert bot2._poller_disabled is True
    assert "Another Telegram interactive poller is running" in caplog.text
    assert "Disabling this poller instance." in caplog.text
    assert tg2_calls == []  # Did not make any API calls (deleteWebhook or getUpdates)

    # Bot 1 owns poll and called deleteWebhook + getUpdates
    assert "deleteWebhook" in tg1_calls
    assert "getUpdates" in tg1_calls

    await bot1.stop()
    await asyncio.gather(task1, return_exceptions=True)


@pytest.mark.asyncio
async def test_poll_loop_409_conflict_backs_off(tmp_path, caplog):
    lock_file = str(tmp_path / "tg_409.lock")
    cfg = {
        "telegram_interactive_enabled": True,
        "telegram_bot_token": "token-123",
        "telegram_chat_id": "456",
        "telegram_poll_timeout": 1,
        "telegram_poll_lockfile": lock_file,
    }

    bot = InteractiveTelegramBot(telegram_bot=MagicMock(), notif_config=cfg)
    calls = []

    async def fake_tg(method, **kwargs):
        calls.append(method)
        if method == "deleteWebhook":
            return {"ok": True}
        if method == "getUpdates":
            if len(calls) == 2:
                # First getUpdates returns 409 conflict
                return {
                    "ok": False,
                    "error_code": 409,
                    "description": "Conflict: terminated by other getUpdates request",
                }
            bot._stopping = True
            return {"ok": True, "result": []}
        return None

    bot._tg = fake_tg

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with caplog.at_level("WARNING"):
            task = asyncio.create_task(bot.poll_loop())
            for _ in range(20):
                if bot._stopping:
                    break
                await asyncio.sleep(0.01)
            await task

    assert "Telegram poll conflict detected (another instance active?), backing off 5s" in caplog.text
    mock_sleep.assert_any_await(5)


@pytest.mark.asyncio
async def test_telegram_bot_delete_webhook():
    bot = TelegramBot(bot_token="test_tok", chat_id="123")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True, "result": True}
        mock_post.return_value = mock_resp

        res = await bot.delete_webhook(drop_pending_updates=True)
        assert res is True
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert "deleteWebhook" in args[0]
        assert kwargs.get("json") == {"drop_pending_updates": True}
