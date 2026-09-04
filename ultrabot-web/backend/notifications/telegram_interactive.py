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

        self._offset = 0
        self._sent_cards: Dict[str, int] = {}      # opp_id -> telegram message_id
        self._decided: Dict[str, str] = {}         # opp_id -> action taken
        self._last_canary: Dict[str, float] = {}   # canary key -> ts
        self._tasks: List[asyncio.Task] = []
        self._stopping = False
        self.started_at = datetime.now(IST)

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
                        "/pnl — today's P&amp;L\n"
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
                run_min = int((datetime.now(IST) - self.started_at).total_seconds() // 60)
                await self._tg(
                    "sendMessage", chat_id=self._chat_id,
                    text=(
                        f"📊 <b>Status</b> · {datetime.now(IST).strftime('%H:%M:%S IST')}\n"
                        f"Engine: <b>{_esc(state)}</b> · up {run_min}m\n"
                        f"Session: <code>{_esc(session_id)}</code>\n"
                        f"Scans {scans} · Signals {signals} · Trades {trades}\n"
                        f"Pending opportunities: {pending}"
                    ),
                    parse_mode="HTML",
                )
            elif cmd == "/positions":
                positions = []
                if self.repo_getter is not None:
                    repo = await self.repo_getter()
                    try:
                        positions = await repo.get_open_positions()
                    finally:
                        close = getattr(repo, "close", None)
                        if close:
                            await close()
                if not positions:
                    await self._tg("sendMessage", chat_id=self._chat_id, text="📭 No open positions.")
                    return
                lines = ["📂 <b>Open positions</b>"]
                for p in positions[:10]:
                    sym = getattr(p, "symbol", "?")
                    direction = getattr(p, "direction", "?")
                    qty = getattr(p, "quantity", getattr(p, "qty", "?"))
                    entry = getattr(p, "entry_price", 0)
                    pnl = getattr(p, "unrealized_pnl", getattr(p, "pnl", None))
                    pnl_txt = f" · {_fmt_money(pnl)}" if pnl is not None else ""
                    lines.append(f"• {_esc(sym)} {direction} {qty} @ ₹{entry}{pnl_txt}")
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
                finally:
                    close = getattr(repo, "close", None)
                    if close:
                        await close()
                realized = pnl.get("realized_pnl", pnl.get("realized", 0))
                unrealized = pnl.get("unrealized_pnl", pnl.get("unrealized", 0))
                total = pnl.get("total_pnl", pnl.get("total"))
                lines = [
                    f"💰 <b>Today's P&amp;L</b> · {datetime.now(IST).strftime('%d %b %H:%M')}",
                    f"Realized: <b>{_fmt_money(realized)}</b>",
                    f"Unrealized: <b>{_fmt_money(unrealized)}</b>",
                ]
                if total is not None:
                    lines.append(f"Total: <b>{_fmt_money(total)}</b>")
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
        consecutive_errors = 0
        while not self._stopping:
            try:
                data = await self._tg(
                    "getUpdates",
                    offset=self._offset,
                    timeout=self._poll_timeout,
                    allowed_updates=["message", "callback_query"],
                )
                if data is None:
                    consecutive_errors += 1
                    await asyncio.sleep(min(5 * consecutive_errors, 30))
                    continue
                consecutive_errors = 0
                if not data.get("ok"):
                    await asyncio.sleep(3)
                    continue
                for update in data.get("result", []):
                    self._offset = max(self._offset, update.get("update_id", 0) + 1)
                    if "callback_query" in update:
                        await self._handle_callback(update["callback_query"])
                    elif "message" in update:
                        msg = update["message"]
                        if self._authorized((msg.get("chat") or {}).get("id")):
                            text = msg.get("text", "")
                            if text.startswith("/"):
                                await self._handle_command(text)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.error("poll_loop cycle failed: %s", exc, exc_info=True)
                await asyncio.sleep(10)

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
                    if is_open and state not in ("running", "starting"):
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
        self._tasks = [
            asyncio.create_task(self.push_loop(), name="tg-interactive-push"),
            asyncio.create_task(self.poll_loop(), name="tg-interactive-poll"),
        ]
        if self.canary_enabled:
            self._tasks.append(asyncio.create_task(self.canary_loop(), name="tg-canary"))
        logger.info(
            "Interactive Telegram started (chat=%s, poll_timeout=%ss, canary=%s)",
            self._chat_id,
            self._poll_timeout,
            self.canary_enabled,
        )

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
