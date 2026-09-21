"""Interactive Telegram control for UltraBot (v0.4.10).

Two-way Telegram layer on top of the existing one-way TelegramBot:

- Pushes pending-opportunity cards with inline buttons
  (Approve / Reject / Skip / Why) to the configured chat.
- Receives button taps via getUpdates long-polling and executes the
  decision against the engine using the SAME code path as the web
  dashboard (engine.confirm_opportunity / engine.skip_opportunity).
- Commands: /status /positions /pnl /pause /resume /help
- Canary: warns when the engine should be running but is not, during
  market hours (rate-limited).

Security:
- Only the configured telegram_chat_id is honored; every other sender
  is ignored and logged.
- Decisions are first-tap-wins; later taps answer "already decided".

Fail-safety:
- poll/push/canary loops never raise into the application; every cycle
  is wrapped and errors are logged + retried with backoff.
- If credentials are missing, start() is a no-op (same policy as the
  one-way TelegramBot).
"""
import asyncio
import html
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import httpx

try:
    import msvcrt
except ImportError:
    msvcrt = None

try:
    import fcntl
except ImportError:
    fcntl = None

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_TG_API = "https://api.telegram.org/bot{token}/{method}"

# callback_data budget is 64 bytes: "opp|" + 36-char uuid + "|approve" = 47.
_ACTION_APPROVE = "approve"
_ACTION_REJECT = "reject"
_ACTION_SKIP = "skip"
_ACTION_WHY = "why"

_CANARY_REPEAT_MINUTES = 45
_PUSH_INTERVAL_S = 5
_CANARY_INTERVAL_S = 120

# v0.4.21: the 11:16-IST silence — one hung command handler used to freeze
# the poll loop forever (dispatch is inline), and the fire-and-forget loop
# tasks had no supervision: a dead poll task meant a SILENTLY dead bot.
_HANDLER_TIMEOUT_S = 30.0     # per update-handler budget (commands hit DB+HTTP)
_TG_LOOP_RESPAWN_CAP = 5      # in-process respawns per loop per day
_TG_POLL_STALE_BASE_S = 120.0 # watchdog alert floor (long-poll can block ~poll_timeout+12)

# v0.4.13 canary false-positive fix (live 2026-09-07 10:50 & 11:58 IST:
# "engine is scanning" flagged as blind). States in which the bot is NOT
# blind during market hours:
#   running/starting — obviously healthy
#   scanning — the transient state DURING a scan tick (loop 1011 runs
#     while RUNNING/PAUSED/SCANNING and still calls _manage_all_positions)
#   paused — user-intentional no-NEW-entries state; the main loop still
#     enforces SLs/targets/time-stops on open positions, so exits are NOT
#     blind. (A user-paused botalerting "blind" every 45 min is noise.)
_CANARY_HEALTHY_STATES = ("running", "starting", "scanning", "paused")


def _esc(val: Any) -> str:
    if val is None:
        return ""
    return html.escape(str(val))


def _fmt_money(val: Any) -> str:
    try:
        v = float(val)
        sign = "+" if v >= 0 else "\u2212"
        return f"{sign}\u20b9{abs(v):,.2f}"
    except (TypeError, ValueError):
        return "\u20b9—"


def compute_pnl_view(
    pnl: Optional[Dict[str, Any]],
    open_positions: Optional[List[Any]],
    live_prices: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """Map today's P&L into {realized, unrealized, total} for the /pnl command.

    v0.4.12.1 hotfix (live 2026-09-07): the handler previously read
    ``realized_pnl`` / ``unrealized_pnl`` keys that ``get_todays_pnl()`` never
    returns (it returns ``net_pnl`` / ``gross_pnl`` / ...), so /pnl printed
    ₹0.00 / ₹0.00 all day regardless of trading activity.

    - realized   = net P&L of today's CLOSED trades (repo ``net_pnl``).
    - unrealized = direction-aware MTM of open positions from
      live_prices or ``current_price`` (same math as the dashboard stats endpoint),
      because ``positions.unrealized_pnl`` is not maintained by the engine.
    - total      = realized + unrealized.
    """
    realized = 0.0
    if pnl:
        for key in ("net_pnl", "realized_pnl", "realized"):
            val = pnl.get(key)
            if val is not None:
                try:
                    realized = float(val)
                except (TypeError, ValueError):
                    realized = 0.0
                break

    unrealized = 0.0
    for p in open_positions or []:
        try:
            if isinstance(p, dict):
                entry = float(p.get("entry_price", 0) or 0)
                sym = str(p.get("symbol", "") or "").upper()
                current = float(live_prices.get(sym, 0.0)) if (live_prices and sym in live_prices) else 0.0
                if current <= 0:
                    current = float(p.get("current_price", 0) or 0) or entry
                qty = float(p.get("quantity", p.get("qty", 0)) or 0)
                direction = str(p.get("direction", "")).upper()
            else:
                entry = float(getattr(p, "entry_price", 0) or 0)
                sym = str(getattr(p, "symbol", "") or "").upper()
                current = float(live_prices.get(sym, 0.0)) if (live_prices and sym in live_prices) else 0.0
                if current <= 0:
                    current = float(getattr(p, "current_price", 0) or 0) or entry
                qty = float(getattr(p, "quantity", getattr(p, "qty", 0)) or 0)
                direction = str(getattr(p, "direction", "")).upper()

            if entry <= 0 or qty <= 0:
                continue
            sign = 1 if direction in ("BUY", "LONG") else -1
            unrealized += (current - entry) * qty * sign
        except (TypeError, ValueError, AttributeError):
            continue

    return {
        "realized": round(realized, 2),
        "unrealized": round(unrealized, 2),
        "total": round(realized + unrealized, 2),
    }


class TelegramPollLock:
    """Process-level advisory lockfile guard for Telegram interactive polling.

    Prevents multiple processes (e.g. uvicorn reload workers or duplicate engines)
    from concurrently polling Telegram getUpdates, which causes HTTP 409 Conflict.
    """

    def __init__(self, lockfile_path: str = "data/telegram_poll.lock"):
        self.lockfile_path = lockfile_path
        self._fh = None
        self._locked = False

    def acquire(self) -> Tuple[bool, Optional[int]]:
        """Attempt to acquire exclusive lock non-blockingly.

        Returns (True, my_pid) if acquired, (False, holder_pid) if locked by another process.
        """
        try:
            lock_dir = os.path.dirname(os.path.abspath(self.lockfile_path))
            if lock_dir:
                os.makedirs(lock_dir, exist_ok=True)
            if not os.path.exists(self.lockfile_path):
                try:
                    with open(self.lockfile_path, "a") as f:
                        pass
                except Exception:
                    pass

            fh = open(self.lockfile_path, "r+b")
            self._fh = fh

            if msvcrt is not None:
                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    holder_pid = self._read_holder_pid(fh)
                    fh.close()
                    self._fh = None
                    return False, holder_pid
            elif fcntl is not None:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (BlockingIOError, OSError):
                    holder_pid = self._read_holder_pid(fh)
                    fh.close()
                    self._fh = None
                    return False, holder_pid

            # Successfully locked - write byte 0 dummy marker and byte 1+ PID
            fh.seek(0)
            pid_bytes = b"X" + str(os.getpid()).encode("utf-8")
            fh.write(pid_bytes)
            fh.truncate()
            fh.flush()
            self._locked = True
            return True, os.getpid()
        except Exception as exc:
            logger.warning("Failed to acquire telegram poll lock: %s", exc)
            if self._fh:
                try:
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None
            return False, None

    def _read_holder_pid(self, fh) -> Optional[int]:
        try:
            fh.seek(1)
            raw = fh.read().decode("utf-8", errors="ignore").strip()
            return int(raw) if raw.isdigit() else None
        except Exception:
            return None

    def release(self) -> None:
        """Release the advisory lock and close the file handle."""
        if not self._locked or not self._fh:
            if self._fh:
                try:
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None
            self._locked = False
            return
        try:
            if msvcrt is not None:
                try:
                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                except Exception:
                    pass
            elif fcntl is not None:
                try:
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
            self._fh.close()
        except Exception as exc:
            logger.debug("Error releasing telegram poll lock: %s", exc)
        finally:
            self._fh = None
            self._locked = False


class InteractiveTelegramBot:
    """Two-way Telegram bridge between the user's mobile and the engine."""

    def __init__(
        self,
        telegram_bot,
        engine=None,
        repo_getter=None,
        notif_config: Optional[Dict[str, Any]] = None,
    ):
        self.tg = telegram_bot  # one-way sender (reuse token/chat_id/timeout)
        self.engine = engine
        self.repo_getter = repo_getter
        cfg = notif_config or {}

        self.enabled = bool(cfg.get("telegram_interactive_enabled", False))
        self.canary_enabled = bool(cfg.get("telegram_canary_enabled", True))
        self._token = str(cfg.get("telegram_bot_token", "") or "").strip()
        self._chat_id = str(cfg.get("telegram_chat_id", "") or "").strip()
        self._poll_timeout = int(cfg.get("telegram_poll_timeout", 25))
        self._poll_lock = TelegramPollLock(
            lockfile_path=str(cfg.get("telegram_poll_lockfile", "data/telegram_poll.lock"))
        )
        self._poller_disabled = False

        self._offset = 0
        self._sent_cards: Dict[str, int] = {}      # opp_id -> telegram message_id
        self._decided: Dict[str, str] = {}         # opp_id -> action taken
        self._last_canary: Dict[str, float] = {}   # canary key -> ts
        self._tasks: List[asyncio.Task] = []
        self._stopping = False
        # v0.4.21 loop health: monotonic beat of the last completed poll
        # cycle + per-loop respawn counters (supervision, see start()).
        self._poll_beat: float = 0.0
        self._respawn_counts: Dict[str, int] = {}
        self._loop_deaths: Dict[str, str] = {}     # loop name -> death reason
        self.started_at = datetime.now(IST)

    # ── v0.4.21: loop supervision + health observability ─────────────

    def poll_stalled_seconds(self) -> Optional[float]:
        """Seconds since the poll loop last completed a getUpdates cycle.

        None — interactive bot not running (disabled/unconfigured/stopped).
        Note: one long-poll cycle can legitimately block ~poll_timeout+12s,
        so consumers must alert only above that floor (watchdog uses
        max(TG_POLL_STALE_BASE_S, poll_timeout + 60)).
        """
        if not self._tasks or self._stopping or self._poll_beat <= 0.0:
            return None
        return round(max(time.monotonic() - self._poll_beat, 0.0), 1)

    def _on_loop_task_done(self, task: "asyncio.Task", name: str, factory) -> None:
        """Done-callback for the telegram loop tasks (supervision).

        Never raises. Logs a critical on ANY exit while not stopping, records
        the reason for /api/health, and respawns the loop in-process up to
        _TG_LOOP_RESPAWN_CAP times per day — a silently dead poll loop was
        the 11:16-IST 'bot stopped responding' failure mode.
        """
        try:
            if self._stopping or getattr(self, "_poller_disabled", False):
                return
            reason = "returned unexpectedly"
            if task.cancelled():
                reason = "cancelled"
            else:
                exc = task.exception()
                if exc is not None:
                    reason = f"died: {exc!r}"
            self._loop_deaths[name] = reason
            logger.critical("Telegram loop '%s' %s while bot is active", name, reason)
            count = self._respawn_counts.get(name, 0)
            if count >= _TG_LOOP_RESPAWN_CAP:
                logger.critical(
                    "Telegram loop '%s' respawn cap reached (%d today) — NOT respawning; "
                    "bot is DEAF until manual restart",
                    name, count,
                )
                return
            self._respawn_counts[name] = count + 1
            new_task = asyncio.create_task(factory(), name=f"{name}-respawn{count + 1}")
            self._tasks = [t for t in self._tasks if t is not task] + [new_task]
            new_task.add_done_callback(
                lambda t, n=name, f=factory: self._on_loop_task_done(t, n, f)
            )
            logger.warning("Telegram loop '%s' respawned (attempt %d/%d)", name, count + 1, _TG_LOOP_RESPAWN_CAP)
        except Exception:
            logger.exception("telegram loop supervision callback failed")

    # ------------------------------------------------------------------
    # Telegram API helpers
    # ------------------------------------------------------------------

    async def _tg(self, method: str, **payload) -> Optional[Dict[str, Any]]:
        """Generic Telegram Bot API POST. Returns response JSON or None."""
        if not self._token:
            return None
        url = _TG_API.format(token=self._token, method=method)
        try:
            async with httpx.AsyncClient(timeout=self._poll_timeout + 12.0) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if not data.get("ok"):
                    # 400 "message is not modified" etc. are benign – log light.
                    logger.debug("Telegram %s not ok: %s", method, data.get("description"))
                    return data  # caller may inspect ok=False
                return data
        except Exception as exc:
            logger.warning("Telegram %s failed: %s", method, exc)
            return None

    def _authorized(self, chat_id: Any) -> bool:
        """Strict chat whitelist – only the owner's chat is honored."""
        return str(chat_id or "").strip() == self._chat_id and bool(self._chat_id)

    # ------------------------------------------------------------------
    # Opportunity card
    # ------------------------------------------------------------------

    @staticmethod
    def _pct(entry: float, level: float) -> str:
        try:
            if entry and entry > 0:
                return f"{(level - entry) / entry * 100:+.2f}%"
        except (TypeError, ValueError):
            pass
        return "—"

    def build_card(self, opp: Dict[str, Any]) -> Tuple[str, List[List[Dict[str, str]]]]:
        """Build HTML text + inline keyboard for a pending opportunity."""
        opp_id = str(opp.get("id", ""))
        symbol = _esc(opp.get("symbol", "?"))
        name = _esc(opp.get("name") or "")
        direction = str(opp.get("direction", "?")).upper()
        strategy = _esc(opp.get("strategy", ""))
        entry = float(opp.get("entry_price", 0) or 0)
        sl = float(opp.get("stop_loss", 0) or 0)
        target = float(opp.get("target", 0) or 0)
        qty = int(opp.get("quantity") or opp.get("qty") or 0)
        rr = opp.get("risk_reward")
        conf = opp.get("confidence")
        vix = opp.get("vix")
        regime = _esc(opp.get("regime") or "")
        capital = opp.get("capital_required") or (
            entry * qty if entry and qty else None
        )
        risk_amt = opp.get("risk_amount") or (
            abs(entry - sl) * qty if entry and sl and qty else None
        )
        seg = _esc(opp.get("segment") or "EQ")
        is_test = opp_id.upper().startswith("TEST") or bool(opp.get("_test_mode"))

        arrow = "🟢" if direction in ("BUY", "LONG") else "🔴"
        head = "🧪 <b>TEST OPPORTUNITY</b>" if is_test else "🤖 <b>Opportunity</b>"

        lines = [
            f"{head} <code>#{_esc(opp_id[-8:])}</code>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"{arrow} <b>{direction} {symbol}</b>"
            + (f" <i>({name})</i>" if name else ""),
            f"Strategy <b>{strategy}</b> · Segment {seg}",
            "",
            f"Entry <b>₹{entry:.2f}</b> · SL <b>₹{sl:.2f}</b> ({self._pct(entry, sl)})",
            f"Target <b>₹{target:.2f}</b> ({self._pct(entry, target)})"
            + (f" · R:R <b>1:{rr:.1f}</b>" if rr else ""),
            f"Qty <b>{qty}</b>"
            + (f" · Risk {_fmt_money(risk_amt)}" if risk_amt else "")
            + (f" · Capital {_fmt_money(capital)}" if capital else ""),
        ]
        ctx_bits = []
        if vix is not None:
            ctx_bits.append(f"VIX {vix}")
        if regime:
            ctx_bits.append(f"Regime {regime}")
        if conf is not None:
            try:
                ctx_bits.append(f"Conf {float(conf) * 100:.0f}%")
            except (TypeError, ValueError):
                pass
        if ctx_bits:
            lines.append("")
            lines.append("· ".join(ctx_bits))
        lines += [
            "",
            "⏳ Auto-expires ~120s after creation (momentum window)",
        ]

        keyboard = [
            [
                {"text": "✅ Approve", "callback_data": f"opp|{opp_id}|{_ACTION_APPROVE}"},
                {"text": "❌ Reject", "callback_data": f"opp|{opp_id}|{_ACTION_REJECT}"},
            ],
            [
                {"text": "⏭ Skip", "callback_data": f"opp|{opp_id}|{_ACTION_SKIP}"},
                {"text": "ℹ️ Why", "callback_data": f"opp|{opp_id}|{_ACTION_WHY}"},
            ],
        ]
        return "\n".join(lines), keyboard

    async def send_opportunity_card(self, opp: Dict[str, Any]) -> bool:
        """Push a pending-opportunity card with action buttons.

        Marks the opportunity as "being sent" BEFORE the await so a concurrent
        push_loop cycle can never double-send (race seen in live test).
        """
        if not self._token or not self._chat_id:
            return False
        opp_id = str(opp.get("id", ""))
        if opp_id in self._sent_cards:
            return True  # already sent / being sent
        self._sent_cards[opp_id] = 0  # optimistic claim
        text, keyboard = self.build_card(opp)
        data = await self._tg(
            "sendMessage",
            chat_id=self._chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup={"inline_keyboard": keyboard},
        )
        if data and data.get("ok"):
            self._sent_cards[opp_id] = data["result"]["message_id"]
            return True
        self._sent_cards.pop(opp_id, None)  # release claim so we can retry
        return False

    async def _edit_card(self, opp_id: str, text: str, keep_buttons: bool = False) -> None:
        msg_id = self._sent_cards.get(opp_id)
        if not msg_id:
            return
        markup = (
            {
                "inline_keyboard": [
                    [{"text": "ℹ️ Details", "callback_data": f"opp|{opp_id}|{_ACTION_WHY}"}]
                ]
            }
            if keep_buttons
            else {"inline_keyboard": []}
        )
        await self._tg(
            "editMessageText",
            chat_id=self._chat_id,
            message_id=msg_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup,
        )

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------

    async def _handle_callback(self, cb: Dict[str, Any]) -> None:
        try:
            query_id = cb.get("id", "")
            msg = cb.get("message") or {}
            chat_id = (msg.get("chat") or {}).get("id")
            from_id = cb.get("from", {}).get("id")
            data = cb.get("data", "")

            if not self._authorized(chat_id):
                logger.warning(
                    "Ignoring Telegram callback from unauthorized chat=%s from=%s",
                    chat_id,
                    from_id,
                )
                return

            parts = data.split("|")
            if len(parts) != 3 or parts[0] != "opp":
                await self._tg("answerCallbackQuery", callback_query_id=query_id, text="Unknown action")
                return
            opp_id, action = parts[1], parts[2]

            # First-tap-wins guard
            if opp_id in self._decided and action != _ACTION_WHY:
                await self._tg(
                    "answerCallbackQuery",
                    callback_query_id=query_id,
                    text=f"Already decided: {self._decided[opp_id]}",
                    show_alert=False,
                )
                return

            if action == _ACTION_WHY:
                why_text = self._build_why(opp_id)
                await self._tg("answerCallbackQuery", callback_query_id=query_id, text="Details")
                await self._tg(
                    "sendMessage", chat_id=self._chat_id, text=why_text,
                    parse_mode="HTML", disable_web_page_preview=True,
                )
                return

            await self._tg("answerCallbackQuery", callback_query_id=query_id, text="Working…")
            self._decided[opp_id] = action
            await self._execute_decision(opp_id, action)
        except Exception as exc:
            logger.error("Callback handling failed: %s", exc, exc_info=True)

    async def _execute_decision(self, opp_id: str, action: str) -> None:
        """Run the decision through the engine (same path as dashboard)."""
        stamp = datetime.now(IST).strftime("%H:%M:%S")
        source = "telegram"
        try:
            if self.engine is None:
                await self._edit_card(opp_id, f"⚠️ Engine unavailable — decision NOT executed.\n[{stamp}]")
                return

            if action == _ACTION_APPROVE:
                opp = self.engine.pending_opportunities.get(opp_id, {})
                segment = str(opp.get("segment") or "EQ")
                result = await self.engine.confirm_opportunity(opp_id, segment=segment)
                status = str(result.get("status", "")).lower()
                if status in ("error", "not_found"):
                    reason = result.get("error") or result.get("reason") or "unknown"
                    self._decided.pop(opp_id, None)  # allow retry on transient issues
                    await self._edit_card(
                        opp_id,
                        f"⚠️ <b>Could not execute</b>\n<code>{_esc(reason)}</code>\n[{stamp} · {source}]",
                        keep_buttons=True,
                    )
                elif status == "rejected":
                    reason = result.get("reason", "pre-execution check failed")
                    await self._edit_card(
                        opp_id,
                        f"🛑 <b>Execution rejected by engine</b>\n<code>{_esc(reason)}</code>\n[{stamp} · {source}]",
                    )
                else:
                    trade = result.get("trade") or result
                    fill_price = float(
                        trade.get("entry_price") or trade.get("filled_price") or opp.get("entry_price", 0) or 0
                    )
                    qty = int(trade.get("quantity") or trade.get("qty") or opp.get("quantity", 0) or 0)
                    sl = float(trade.get("stop_loss") or trade.get("sl") or opp.get("stop_loss", 0) or 0)
                    target = float(trade.get("target") or trade.get("target_price") or opp.get("target", 0) or 0)
                    sym = _esc(trade.get("symbol") or opp.get("symbol", ""))
                    await self._edit_card(
                        opp_id,
                        "✅ <b>FILLED</b>\n"
                        f"{_esc(trade.get('direction') or opp.get('direction', ''))} <b>{sym}</b>"
                        f" · {qty} qty @ ₹{fill_price:.2f}\n"
                        f"SL ₹{sl:.2f} · TGT ₹{target:.2f}\n"
                        f"[{stamp} · approved via {source}]",
                    )
            elif action in (_ACTION_REJECT, _ACTION_SKIP):
                reason = "Rejected via Telegram" if action == _ACTION_REJECT else "Skipped via Telegram"
                result = await self.engine.skip_opportunity(opp_id, reason=reason)
                status = str(result.get("status", "")).lower()
                if status == "not_found":
                    self._decided.pop(opp_id, None)
                    await self._edit_card(
                        opp_id,
                        f"⌛ Not available (expired or already processed)\n[{stamp} · {source}]",
                    )
                else:
                    emoji = "❌" if action == _ACTION_REJECT else "⏭"
                    await self._edit_card(
                        opp_id,
                        f"{emoji} <b>{'Rejected' if action == _ACTION_REJECT else 'Skipped'}</b>"
                        f" — logged ({source})\n[{stamp}]",
                    )
            else:
                logger.warning("Unknown action '%s' for opp %s", action, opp_id)
        except Exception as exc:
            logger.error("Decision execution failed for %s/%s: %s", opp_id, action, exc, exc_info=True)
            self._decided.pop(opp_id, None)
            await self._edit_card(opp_id, f"⚠️ Error executing decision: <code>{_esc(exc)}</code>\n[{stamp}]")

    def _build_why(self, opp_id: str) -> str:
        """Human-readable 'why this trade' breakdown from the opportunity dict."""
        opp = {}
        if self.engine is not None:
            opp = self.engine.pending_opportunities.get(opp_id, {}) or {}
        lines = [f"ℹ️ <b>Why this trade</b> <code>#{_esc(opp_id[-8:])}</code>", "━━━━━━━━━━━━━━━━━━━━"]
        if not opp:
            lines.append("Opportunity no longer pending (decided or expired).")
            return "\n".join(lines)
        gates = opp.get("risk_gates") or {}
        if gates:
            lines.append("<b>Risk gates</b>")
            for gname, gval in list(gates.items())[:16]:
                if isinstance(gval, dict):
                    ok = gval.get("passed", gval.get("is_valid", True))
                    detail = gval.get("reason") or gval.get("details") or ""
                else:
                    ok = bool(gval)
                    detail = ""
                mark = "✅" if ok else "⛔"
                line = f"{mark} {_esc(gname)}"
                if detail:
                    line += f" — {_esc(detail)[:80]}"
                lines.append(line)
        else:
            lines.append("Gate detail not attached to this opportunity.")
        meta_bits = []
        if opp.get("kronos_score") is not None:
            meta_bits.append(f"Kronos {_esc(opp.get('kronos_score'))}")
        if opp.get("win_rate") is not None:
            meta_bits.append(f"WinRate {float(opp['win_rate']) * 100:.0f}%")
        if opp.get("market_trend"):
            meta_bits.append(f"Trend {_esc(opp.get('market_trend'))}")
        if meta_bits:
            lines += ["", "· ".join(meta_bits)]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    async def _handle_command(self, text: str) -> None:
        cmd = text.strip().split("@", 1)[0].lower()
        try:
            if cmd in ("/start", "/help"):
                await self._tg(
                    "sendMessage", chat_id=self._chat_id,
                    text=(
                        "🤖 <b>UltraBot commands</b>\n"
                        "/status — engine + session snapshot\n"
                        "/positions — open positions\n"
                        "/pnl — today's P&amp;L breakdown\n"
                        "/fees — fee awareness (today, week, month, year, overall)\n"
                        "/pause — pause trading (no new entries)\n"
                        "/resume — resume trading\n"
                        "Opportunity cards arrive with Approve / Reject / Skip buttons."
                    ),
                    parse_mode="HTML",
                )
                return

            if self.engine is None:
                await self._tg("sendMessage", chat_id=self._chat_id, text="⚠️ Engine not available.")
                return

            if cmd == "/status":
                eng = self.engine
                state = getattr(getattr(eng, "state", None), "value", "?")
                scans = getattr(eng, "_scan_count", 0)
                signals = getattr(eng, "_signals_generated", 0)
                trades = getattr(eng, "_trades_executed", 0)
                pending = len(getattr(eng, "pending_opportunities", {}) or {})
                session_id = getattr(eng, "session_id", None) or "—"
                total_cap = float(getattr(eng, "initial_capital", 0.0) or 0.0)

                # Query open positions for capital usage
                open_pos = []
                pnl_summary = {}
                if self.repo_getter is not None:
                    repo = await self.repo_getter()
                    try:
                        todays_trades = await repo.get_trades_by_date(
                            datetime.now(IST).date().isoformat(), limit=500
                        )
                        trades = len(todays_trades or [])
                        open_pos = await repo.get_open_positions() or []
                        pnl_summary = await repo.get_todays_pnl() or {}
                    except Exception:
                        pass  # keep in-memory counter as fallback
                    finally:
                        close = getattr(repo, "close", None)
                        if close:
                            await close()
                if not open_pos and hasattr(eng, "positions") and eng.positions:
                    open_pos = list(eng.positions.values())

                used_amount = sum(
                    float(getattr(p, "invested_amount", 0.0) or (float(getattr(p, "entry_price", 0.0) or 0.0) * float(getattr(p, "quantity", getattr(p, "qty", 0.0)) or 0.0)))
                    for p in open_pos
                )
                remaining_cap = max(0.0, total_cap - used_amount)
                run_min = int((datetime.now(IST) - self.started_at).total_seconds() // 60)
                gross_s = float(pnl_summary.get("gross_pnl", 0.0) or 0.0)
                fees_s = float(pnl_summary.get("total_fees", 0.0) or 0.0)
                net_s = float(pnl_summary.get("net_pnl", gross_s - fees_s) or 0.0)
                wr_s = float(pnl_summary.get("win_rate", 0.0) or 0.0)

                await self._tg(
                    "sendMessage", chat_id=self._chat_id,
                    text=(
                        f"📊 <b>Status</b> · {datetime.now(IST).strftime('%H:%M:%S IST')}\n"
                        f"Engine: <b>{_esc(state)}</b> · up {run_min}m\n"
                        f"Session: <code>{_esc(session_id)}</code>\n"
                        f"Scans {scans} · Signals {signals} · Trades {trades}\n"
                        f"Today: Gross <b>{_fmt_money(gross_s)}</b> · Fees <b>{_fmt_money(-fees_s)}</b> · Net <b>{_fmt_money(net_s)}</b> (WR: {wr_s:.0f}%)\n"
                        f"Amount used for trades: ₹{used_amount:,.2f}\n"
                        f"Remaining capital: ₹{remaining_cap:,.2f}\n"
                        f"Pending opportunities: {pending}"
                    ),
                    parse_mode="HTML",
                )
            elif cmd == "/positions":
                from utils.market_utils import get_lot_size, is_fno_stock
                positions = []
                if self.repo_getter is not None:
                    repo = await self.repo_getter()
                    try:
                        positions = await repo.get_open_positions()
                    finally:
                        close = getattr(repo, "close", None)
                        if close:
                            await close()
                if not positions and hasattr(self.engine, "positions") and self.engine.positions:
                    positions = list(self.engine.positions.values())
                if not positions:
                    await self._tg("sendMessage", chat_id=self._chat_id, text="📭 No open positions.")
                    return

                lines = [f"📂 <b>OPEN POSITIONS ({len(positions)})</b>\n────────────────────────"]
                feed = getattr(self.engine, "feed", None) or getattr(self.engine, "feed_manager", None)
                for p in positions[:10]:
                    if isinstance(p, dict):
                        sym = str(p.get("symbol", "?"))
                        direction = str(p.get("direction", "?")).upper()
                        strategy = str(p.get("strategy", "") or "").upper()
                        qty = p.get("quantity", p.get("qty", "?"))
                        entry = float(p.get("entry_price", 0.0) or 0.0)
                        sl = float(p.get("stop_loss") or p.get("initial_sl") or 0.0)
                        tgt = float(p.get("target") or p.get("initial_target") or 0.0)
                        curr_px = float(p.get("current_price", 0.0) or 0.0)
                        ext = p.get("extra")
                        stages_fired = p.get("stages_fired")
                    else:
                        sym = str(getattr(p, "symbol", "?"))
                        direction = str(getattr(p, "direction", "?")).upper()
                        strategy = str(getattr(p, "strategy", "") or "").upper()
                        qty = getattr(p, "quantity", getattr(p, "qty", "?"))
                        entry = float(getattr(p, "entry_price", 0.0) or 0.0)
                        sl = float(getattr(p, "stop_loss", 0.0) or getattr(p, "initial_sl", 0.0) or 0.0)
                        tgt = float(getattr(p, "target", 0.0) or getattr(p, "initial_target", 0.0) or 0.0)
                        curr_px = float(getattr(p, "current_price", 0.0) or 0.0)
                        ext = getattr(p, "extra", None)
                        stages_fired = getattr(p, "stages_fired", None)

                    extra_dict = {}
                    if isinstance(ext, str) and ext:
                        try:
                            extra_dict = json.loads(ext)
                        except Exception:
                            extra_dict = {}
                    elif isinstance(ext, dict):
                        extra_dict = ext

                    if stages_fired is None:
                        stages_fired = extra_dict.get("stages_fired")
                    if not stages_fired:
                        stages_fired = []
                    elif not isinstance(stages_fired, list):
                        try:
                            stages_fired = list(stages_fired)
                        except Exception:
                            stages_fired = []

                    stage_details = extra_dict.get("stage_details") or {}
                    if not isinstance(stage_details, dict):
                        stage_details = {}

                    partial_realized = float(extra_dict.get("partial_realized_pnl", 0.0) or 0.0)

                    try:
                        lot = get_lot_size(sym) if is_fno_stock(sym) else 1
                    except Exception:
                        lot = 1

                    ltp = 0.0
                    if feed and hasattr(feed, "get_ltp") and sym and sym != "?":
                        try:
                            ltp = float(await feed.get_ltp(sym) or 0.0)
                        except Exception:
                            ltp = 0.0
                    if not ltp or ltp <= 0:
                        ltp = curr_px or entry

                    sign = 1 if direction in ("BUY", "LONG") else -1
                    strat_txt = f" ({strategy})" if strategy else ""
                    dir_emoji = "🟢" if direction in ("BUY", "LONG") else "🔴"

                    sl_txt = f"₹{sl:,.2f}" if sl > 0 else "—"
                    tgt_txt = f"₹{tgt:,.2f}" if tgt > 0 else "—"

                    if entry > 0 and ltp > 0:
                        pnl = (ltp - entry) * float(qty or 0) * sign
                        pnl_pct = ((ltp - entry) / entry) * 100.0 * sign
                        pnl_str = f"{_fmt_money(pnl)} ({pnl_pct:+.2f}%)"
                    else:
                        pnl_str = "—"

                    pnl_line = f"├ P&amp;L: <b>{pnl_str}</b>"
                    if partial_realized > 0:
                        pnl_line += f" · Realized: <b>{_fmt_money(partial_realized)}</b>"

                    # Format stage booking statuses
                    s1_status = "S1:✅ Lock" if 1 in stages_fired else "S1:⏳ Lock"

                    if 2 in stages_fired:
                        s2_pnl = stage_details.get("2", {}).get("pnl")
                        if s2_pnl is not None:
                            s2_status = f"S2:✅ 25% ({_fmt_money(s2_pnl)})"
                        elif partial_realized > 0 and 3 not in stages_fired:
                            s2_status = f"S2:✅ 25% ({_fmt_money(partial_realized)})"
                        else:
                            s2_status = "S2:✅ 25%"
                    else:
                        s2_status = "S2:⏳ 25%"

                    if 3 in stages_fired:
                        s3_pnl = stage_details.get("3", {}).get("pnl")
                        if s3_pnl is not None:
                            s3_status = f"S3:✅ 30% ({_fmt_money(s3_pnl)})"
                        else:
                            s3_status = "S3:✅ 30%"
                    else:
                        s3_status = "S3:⏳ 30%"

                    if 4 in stages_fired:
                        s4_pnl = stage_details.get("4", {}).get("pnl")
                        if s4_pnl is not None:
                            s4_status = f"S4:✅ Runner ({_fmt_money(s4_pnl)})"
                        else:
                            s4_status = "S4:✅ Runner"
                    else:
                        s4_status = "S4:⏳ Runner"

                    stages_line = f"└ Stages: [{s1_status} | {s2_status} | {s3_status} | {s4_status}]"

                    card = [
                        f"\n{dir_emoji} <b>{_esc(sym)}</b> · {direction}{strat_txt} · Qty: <b>{qty}</b> (Lot: {lot})",
                        f"├ Entry: ₹{entry:,.2f} ➔ Current Price: ₹{ltp:,.2f}",
                        f"├ SL: {sl_txt} ➔ TGT: {tgt_txt}",
                        pnl_line,
                        stages_line,
                    ]
                    lines.extend(card)

                await self._tg(
                    "sendMessage", chat_id=self._chat_id, text="\n".join(lines), parse_mode="HTML",
                )
            elif cmd == "/pnl":
                if self.repo_getter is None:
                    await self._tg("sendMessage", chat_id=self._chat_id, text="⚠️ DB not available.")
                    return
                repo = await self.repo_getter()
                try:
                    pnl = await repo.get_todays_pnl() or {}
                    open_positions = await repo.get_open_positions()
                finally:
                    close = getattr(repo, "close", None)
                    if close:
                        await close()
                if not open_positions and hasattr(self.engine, "positions") and self.engine.positions:
                    open_positions = list(self.engine.positions.values())

                feed = getattr(self.engine, "feed", None) or getattr(self.engine, "feed_manager", None)
                live_prices: Dict[str, float] = {}
                for p in open_positions or []:
                    sym = getattr(p, "symbol", "")
                    if sym and feed and hasattr(feed, "get_ltp"):
                        try:
                            px = await feed.get_ltp(sym)
                            if px and px > 0:
                                live_prices[str(sym).upper()] = float(px)
                        except Exception:
                            pass

                view = compute_pnl_view(pnl, open_positions, live_prices=live_prices)
                realized = view["realized"]
                unrealized = view["unrealized"]
                total = view["total"]
                gross = float(pnl.get("gross_pnl", 0.0) or pnl.get("pnl", realized)) if pnl else 0.0
                fees = float(pnl.get("total_fees", 0.0) or pnl.get("fees", 0.0)) if pnl else 0.0
                win_rate = float(pnl.get("win_rate", 0.0) or 0.0) if pnl else 0.0
                net_win_rate = float(pnl.get("net_win_rate", 0.0) or 0.0) if pnl else 0.0
                closed_count = pnl.get("closed_trades", pnl.get("total_trades", 0)) if pnl else 0
                closed_txt = f" ({closed_count} closed trades)" if closed_count else " (0 closed trades)"
                gross_emoji = "🟢" if gross >= 0 else "🔴"
                net_emoji = "🟢" if realized >= 0 else "🔴"
                tot_emoji = "🟢" if total >= 0 else "🔴"
                lines = [
                    f"💰 <b>Today's P&amp;L Breakdown</b> · {datetime.now(IST).strftime('%d %b %H:%M')}",
                    "━━━━━━━━━━━━━━━━━━━━",
                    f"📊 Gross P&amp;L: {gross_emoji} <b>{_fmt_money(gross)}</b> (Strategy WR: {win_rate:.0f}%)",
                    f"🧾 Total Fees: <b>{_fmt_money(-fees)}</b> <i>(Brokerage &amp; Taxes)</i>",
                    f"💵 Net Realized: {net_emoji} <b>{_fmt_money(realized)}</b>{closed_txt} (Net WR: {net_win_rate:.0f}%)",
                    f"📈 Unrealized MTM: <b>{_fmt_money(unrealized)}</b>",
                    "━━━━━━━━━━━━━━━━━━━━",
                    f"🏁 Net Total: {tot_emoji} <b>{_fmt_money(total)}</b>",
                ]
                await self._tg(
                    "sendMessage", chat_id=self._chat_id, text="\n".join(lines), parse_mode="HTML",
                )
            elif cmd in ("/fees", "/fees_summary"):
                if self.repo_getter is None:
                    await self._tg("sendMessage", chat_id=self._chat_id, text="⚠️ DB not available.")
                    return
                repo = await self.repo_getter()
                try:
                    summary = await repo.get_multi_timeframe_fee_summary() or {}
                finally:
                    close = getattr(repo, "close", None)
                    if close:
                        await close()

                lines = [
                    f"🧾 <b>Institutional Fee Awareness Audit</b> · {datetime.now(IST).strftime('%d %b %H:%M')}",
                    "<i>Brokerage, STT, GST, Exchange & Stamp Duty Breakdown</i>",
                    "━━━━━━━━━━━━━━━━━━━━━━━━",
                ]

                tf_labels = [
                    ("today", "📅 Today"),
                    ("week", "🗓 This Week"),
                    ("month", "📆 This Month"),
                    ("year", "📈 This Year"),
                    ("overall", "🌐 Overall (All-Time)"),
                ]

                for key, display_label in tf_labels:
                    b = summary.get(key, {})
                    cnt = b.get("total_trades", 0)
                    gross = b.get("gross_pnl", 0.0)
                    fees = b.get("total_fees", 0.0)
                    net = b.get("net_pnl", 0.0)
                    g_wr = b.get("gross_win_rate", 0.0)
                    n_wr = b.get("net_win_rate", 0.0)
                    drag = b.get("fee_drag_pct", 0.0)
                    avg_fee = b.get("avg_trade_fee", 0.0)

                    net_emoji = "🟢" if net >= 0 else "🔴"
                    gross_emoji = "🟢" if gross >= 0 else "🔴"

                    lines.append(f"<b>{display_label}</b> ({cnt} trades):")
                    lines.append(f"  ├ Gross: {gross_emoji} {_fmt_money(gross)} (Strat WR: {g_wr:.0f}%)")
                    lines.append(f"  ├ Fees: <b>{_fmt_money(-fees)}</b> (Avg/Trade: ₹{avg_fee:.1f})")
                    lines.append(f"  └ Net: {net_emoji} <b>{_fmt_money(net)}</b> (Net WR: {n_wr:.0f}% · Drag: {drag:.1f}%)")
                    lines.append("")

                lines.append("<i>Note: Strategy WR is gross price predictive accuracy decoupled from fee drag.</i>")
                await self._tg(
                    "sendMessage", chat_id=self._chat_id, text="\n".join(lines), parse_mode="HTML",
                )
            elif cmd == "/pause":
                result = await self.engine.pause()
                ok = str(result.get("status", result.get("state", ""))).lower() not in ("error",)
                await self._tg(
                    "sendMessage", chat_id=self._chat_id,
                    text=("⏸ Trading <b>paused</b> — no new entries." if ok
                          else f"⚠️ Pause failed: {_esc(result)}"),
                    parse_mode="HTML",
                )
            elif cmd == "/resume":
                result = await self.engine.resume()
                ok = str(result.get("status", result.get("state", ""))).lower() not in ("error",)
                await self._tg(
                    "sendMessage", chat_id=self._chat_id,
                    text=("▶️ Trading <b>resumed</b>." if ok
                          else f"⚠️ Resume failed: {_esc(result)}"),
                    parse_mode="HTML",
                )
            else:
                await self._tg(
                    "sendMessage", chat_id=self._chat_id,
                    text="Unknown command — try /help",
                )
        except Exception as exc:
            logger.error("Command '%s' failed: %s", cmd, exc, exc_info=True)

    # ------------------------------------------------------------------
    # Loops
    # ------------------------------------------------------------------

    async def push_loop(self) -> None:
        """Detect new pending opportunities and push action cards."""
        while not self._stopping:
            try:
                if self.engine is not None and self._token and self._chat_id:
                    pending = dict(getattr(self.engine, "pending_opportunities", {}) or {})
                    for opp_id, opp in pending.items():
                        if opp_id not in self._sent_cards and opp_id not in self._decided:
                            ok = await self.send_opportunity_card(opp)
                            if ok:
                                logger.info("Interactive card pushed for opportunity %s", opp_id)
                    # prune cards for opportunities that are gone
                    for gone in [oid for oid in self._sent_cards if oid not in pending]:
                        self._sent_cards.pop(gone, None)
                await asyncio.sleep(_PUSH_INTERVAL_S)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.error("push_loop cycle failed: %s", exc, exc_info=True)
                await asyncio.sleep(10)

    async def poll_loop(self) -> None:
        """Long-poll getUpdates and dispatch messages/callbacks. Never raises."""
        acquired, holder_pid = self._poll_lock.acquire()
        if not acquired:
            self._poller_disabled = True
            pid_str = str(holder_pid) if holder_pid else "unknown"
            logger.warning(
                "Another Telegram interactive poller is running (PID %s). Disabling this poller instance.",
                pid_str,
            )
            return

        try:
            # On startup (before first getUpdates), clear hanging poll sessions and conflicting webhooks
            try:
                await self._tg("deleteWebhook", drop_pending_updates=True)
            except Exception as exc:
                logger.warning("Telegram deleteWebhook on startup failed: %s", exc)

            consecutive_errors = 0
            while not self._stopping:
                # v0.4.21 heartbeat: refreshed at the TOP of every cycle (after
                # each long-poll return), surfaced via poll_stalled_seconds().
                self._poll_beat = time.monotonic()
                try:
                    data = await self._tg(
                        "getUpdates",
                        offset=self._offset,
                        timeout=self._poll_timeout,
                        allowed_updates=["message", "callback_query"],
                    )
                    if data is None:
                        consecutive_errors += 1
                        if consecutive_errors % 20 == 0:
                            logger.critical(
                                "poll_loop: %d consecutive failed getUpdates calls — "
                                "bot effectively deaf (token/network?)",
                                consecutive_errors,
                            )
                        await asyncio.sleep(min(5 * consecutive_errors, 30))
                        continue
                    consecutive_errors = 0
                    if not data.get("ok"):
                        if data.get("error_code") == 409 or "conflict" in str(data.get("description", "")).lower():
                            logger.warning("Telegram poll conflict detected (another instance active?), backing off 5s")
                            await asyncio.sleep(5)
                        else:
                            await asyncio.sleep(3)
                        continue
                    for update in data.get("result", []):
                        self._offset = max(self._offset, update.get("update_id", 0) + 1)
                        if "callback_query" in update:
                            # v0.4.21: bounded dispatch — a hung handler (DB/HTTP
                            # wedge) used to freeze ALL subsequent messages forever
                            # (the 11:16-IST silence). Timeout cancels the handler;
                            # Repository.close() is shielded so its session still
                            # returns to the pool cleanly.
                            try:
                                await asyncio.wait_for(
                                    self._handle_callback(update["callback_query"]),
                                    timeout=_HANDLER_TIMEOUT_S,
                                )
                            except asyncio.TimeoutError:
                                logger.error(
                                    "callback handler timed out after %ss — update skipped",
                                    _HANDLER_TIMEOUT_S,
                                )
                        elif "message" in update:
                            msg = update["message"]
                            if self._authorized((msg.get("chat") or {}).get("id")):
                                text = msg.get("text", "")
                                if text.startswith("/"):
                                    try:
                                        await asyncio.wait_for(
                                            self._handle_command(text),
                                            timeout=_HANDLER_TIMEOUT_S,
                                        )
                                    except asyncio.TimeoutError:
                                        logger.error(
                                            "command '%s' timed out after %ss",
                                            text.split()[0], _HANDLER_TIMEOUT_S,
                                        )
                except asyncio.CancelledError:
                    return
                except Exception as exc:
                    logger.error("poll_loop cycle failed: %s", exc, exc_info=True)
                    await asyncio.sleep(10)
        finally:
            self._poll_lock.release()

    async def canary_loop(self) -> None:
        """Blind-spot canary: engine down during market hours → Telegram alert."""
        from core.market_hours import MarketHours

        mh = MarketHours()
        while not self._stopping:
            try:
                if self.canary_enabled and self.engine is not None and self._token and self._chat_id:
                    now = datetime.now(IST)
                    is_open = False
                    try:
                        is_open = bool(mh.is_market_open(now))
                    except TypeError:
                        is_open = bool(mh.is_market_open())
                    state = getattr(getattr(self.engine, "state", None), "value", "unknown")
                    if is_open and state not in _CANARY_HEALTHY_STATES:
                        key = "engine_down"
                        last = self._last_canary.get(key, 0)
                        grace_over = now.time() >= datetime.strptime("09:35", "%H:%M").time()
                        if grace_over and (time.time() - last) > _CANARY_REPEAT_MINUTES * 60:
                            self._last_canary[key] = time.time()
                            await self._tg(
                                "sendMessage", chat_id=self._chat_id,
                                text=(
                                    "🚨 <b>CANARY</b> · Market is OPEN but engine is "
                                    f"<b>{_esc(state)}</b>\n"
                                    "Bot may be blind — check dashboard / restart."
                                ),
                                parse_mode="HTML",
                            )
                            logger.warning("Canary fired: market open but engine state=%s", state)
                await asyncio.sleep(_CANARY_INTERVAL_S)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.error("canary_loop cycle failed: %s", exc, exc_info=True)
                await asyncio.sleep(30)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start background loops (no-op when disabled or unconfigured)."""
        if not self.enabled:
            logger.info("Interactive Telegram disabled by config (telegram_interactive_enabled=false)")
            return
        if not self._token or not self._chat_id:
            logger.info("Interactive Telegram not started — credentials missing")
            return
        self._stopping = False
        self._respawn_counts.clear()
        self._loop_deaths.clear()
        self._tasks = [
            self._supervised_create("tg-interactive-push", self.push_loop),
            self._supervised_create("tg-interactive-poll", self.poll_loop),
        ]
        if self.canary_enabled:
            self._tasks.append(self._supervised_create("tg-canary", self.canary_loop))
        logger.info(
            "Interactive Telegram started (chat=%s, poll_timeout=%ss, canary=%s)",
            self._chat_id,
            self._poll_timeout,
            self.canary_enabled,
        )

    def _supervised_create(self, name: str, factory) -> "asyncio.Task":
        """Create a loop task with death-supervision + respawn (v0.4.21)."""
        task = asyncio.create_task(factory(), name=name)
        task.add_done_callback(
            lambda t, n=name, f=factory: self._on_loop_task_done(t, n, f)
        )
        return task

    async def stop(self) -> None:
        self._stopping = True
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        self._poll_lock.release()

    # ------------------------------------------------------------------
    # Evidence export (for v0.4.10 acceptance pack)
    # ------------------------------------------------------------------

    def evidence_snapshot(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "chat_id": self._chat_id,
            "started_at": self.started_at.isoformat(),
            "cards_sent": dict(self._sent_cards),
            "decisions": dict(self._decided),
        }

    def dump_evidence(self, path: str) -> None:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                json.dump(self.evidence_snapshot(), fh, indent=2, default=str)
        except Exception as exc:
            logger.warning("Evidence dump failed: %s", exc)
