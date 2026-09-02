"""Build and merge watchlists from multiple sources.

Combines symbols from News Scanner, Technical Scanner, and Kronos Scanner
into a final deduplicated, regime-biased, and ranked Top 10 Watchlist.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional, Set
import pandas as pd

from utils.market_utils import (
    is_fno_stock,
    get_all_fno_symbols,
    get_stock_info,
    get_stock_sector,
    get_lot_size,
    get_last_candle_age_minutes,
)
from utils.indicators import calculate_rsi
from scanner.technical_scanner import TechnicalScanner
from scanner.kronos.kronos_scanner import KronosScanner

logger = logging.getLogger(__name__)

# Score multipliers per source
_SOURCE_MULTIPLIERS = {
    "news_high": 1.0,
    "news_medium": 0.7,
    "news_low": 0.4,
    "technical_high": 0.9,
    "technical_medium": 0.6,
    "technical_low": 0.3,
    "kronos": 1.0,
    "manual": 0.5,
}

# Minimum confidence to include
_MIN_CONFIDENCE = 0.25

# Freshness guard (Phase 5): a candidate symbol whose newest candle is older
# than this many CALENDAR days is treated as delisted/suspended and dropped.
# 7 days safely covers weekends and NSE festival-week closures while still
# catching delisted tickers (whose last bar is months/years old).
_STALE_CANDLE_MAX_AGE_DAYS = 7.0


class WatchlistBuilder:
    """Build watchlists from news, technical, and Kronos scanner results.

    Merges and deduplicates symbols, combines scores from multiple
    sources, applies regime-directional bias, and outputs a final ranked Top 10 list.
    """

    def __init__(
        self,
        max_watchlist_size: int = 40,
        min_confidence: float = _MIN_CONFIDENCE,
        technical_scanner: Optional[TechnicalScanner] = None,
        kronos_scanner: Optional[KronosScanner] = None,
    ):
        self.max_watchlist_size = max_watchlist_size
        self.min_confidence = min_confidence
        self.technical_scanner = technical_scanner or TechnicalScanner()
        self.kronos_scanner = kronos_scanner or KronosScanner()
        self._fno_set: Optional[Set[str]] = None

    def _get_fno_set(self) -> Set[str]:
        if self._fno_set is None:
            self._fno_set = set(get_all_fno_symbols())
        return self._fno_set

    def build_from_news(self, news_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extract and score symbols from news items."""
        results = []
        fno_set = self._get_fno_set()
        seen = set()

        for item in news_items:
            impact = item.get("impact_level", "low").lower()
            sentiment = item.get("sentiment", "neutral").lower()
            headline = item.get("headline", "")
            symbols = item.get("symbols", [])

            if isinstance(symbols, str):
                symbols = [symbols]

            for sym in symbols:
                sym_upper = sym.upper()
                if sym_upper in seen:
                    continue
                if fno_set and sym_upper not in fno_set:
                    continue
                seen.add(sym_upper)

                base_key = f"news_{impact}"
                base_score = _SOURCE_MULTIPLIERS.get(base_key, 0.3)
                if sentiment == "positive":
                    base_score *= 1.15
                elif sentiment == "negative":
                    base_score *= 0.85
                base_score = min(base_score, 1.0)

                if base_score < self.min_confidence:
                    continue

                results.append({
                    "symbol": sym_upper,
                    "source": "news",
                    "score": round(base_score, 3),
                    "reason": headline[:80],
                    "impact_level": impact,
                    "sentiment": sentiment,
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def build_from_technical(self, technical_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert technical scanner results to scored watchlist entries."""
        results = []
        seen = set()

        for item in technical_results:
            symbol = item.get("symbol", "").upper()
            if symbol in seen:
                continue
            seen.add(symbol)

            confidence = item.get("confidence", 0)
            setup_type = item.get("setup_type", "")
            details = item.get("details", {})

            if confidence < self.min_confidence:
                continue

            score = min(confidence, 1.0)

            reason = setup_type.replace("_", " ").title()
            if "distance_pct" in details:
                reason += f" ({details['distance_pct']:.1f}% away)"
            elif "volume_ratio" in details:
                reason += f" ({details['volume_ratio']:.1f}x vol)"

            results.append({
                "symbol": symbol,
                "source": "technical",
                "score": round(score, 3),
                "reason": reason,
                "setup_type": setup_type,
                "confidence": confidence,
                "details": details,
            })

        return results

    def merge_lists(
        self,
        news_list: Optional[List[Dict[str, Any]]] = None,
        technical_list: Optional[List[Dict[str, Any]]] = None,
        kronos_list: Optional[List[Dict[str, Any]]] = None,
        existing_watchlist: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Merge multiple source lists into a final scored watchlist with multi-source boosts."""
        news_list = news_list or []
        technical_list = technical_list or []
        kronos_list = kronos_list or []
        existing_watchlist = existing_watchlist or []

        symbol_scores: Dict[str, Dict[str, Any]] = {}

        # 1. Existing Watchlist
        for item in existing_watchlist:
            sym = item.get("symbol", "").upper()
            if sym:
                symbol_scores.setdefault(sym, {
                    "symbol": sym,
                    "score": 0.0,
                    "sources": [],
                    "reasons": [],
                    "is_existing": True,
                    "details": {},
                })
                symbol_scores[sym]["score"] += item.get("score", 0.3)
                symbol_scores[sym]["sources"].append("existing")
                reason = item.get("reason", "")
                if reason:
                    symbol_scores[sym]["reasons"].append(reason)

        # 2. News List
        for item in news_list:
            sym = item.get("symbol", "").upper()
            if not sym:
                continue
            symbol_scores.setdefault(sym, {
                "symbol": sym,
                "score": 0.0,
                "sources": [],
                "reasons": [],
                "is_existing": False,
                "details": {},
            })
            news_score = item.get("score", 0.5)
            if "news" not in symbol_scores[sym]["sources"]:
                symbol_scores[sym]["score"] += news_score
                symbol_scores[sym]["sources"].append("news")
                reason = item.get("reason", "")
                if reason:
                    symbol_scores[sym]["reasons"].append(f"[News] {reason}")
                sentiment = item.get("sentiment", "")
                if sentiment:
                    bias = "BUY" if sentiment == "positive" else ("SELL" if sentiment == "negative" else "")
                    symbol_scores[sym]["bias"] = bias

        # 3. Technical List
        for item in technical_list:
            sym = item.get("symbol", "").upper()
            if not sym:
                continue
            symbol_scores.setdefault(sym, {
                "symbol": sym,
                "score": 0.0,
                "sources": [],
                "reasons": [],
                "is_existing": False,
                "details": {},
            })
            tech_score = item.get("score", 0.5)
            if "technical" not in symbol_scores[sym]["sources"]:
                symbol_scores[sym]["score"] += tech_score
                symbol_scores[sym]["sources"].append("technical")
                reason = item.get("reason", "")
                if reason:
                    symbol_scores[sym]["reasons"].append(f"[Tech] {reason}")
                setup_type = item.get("setup_type", "")
                symbol_scores[sym]["setup_type"] = setup_type
                if item.get("details"):
                    symbol_scores[sym]["details"].update(item["details"])

        # 4. Kronos List
        for item in kronos_list:
            sym = item.get("symbol", "").upper()
            if not sym:
                continue
            symbol_scores.setdefault(sym, {
                "symbol": sym,
                "score": 0.0,
                "sources": [],
                "reasons": [],
                "is_existing": False,
                "details": {},
            })
            kronos_score = item.get("score", 0)
            if "kronos" not in symbol_scores[sym]["sources"]:
                symbol_scores[sym]["score"] += kronos_score * 0.6
                symbol_scores[sym]["sources"].append("kronos")
                reasons = item.get("reasons", [])
                for r in reasons:
                    symbol_scores[sym]["reasons"].append(f"[Kronos] {r}")
                if "change_pct" in item:
                    symbol_scores[sym]["details"]["price_change_pct"] = item["change_pct"]

        # 5. Multi-Source Boost & Final Ranking
        final_list = []
        for sym, data in symbol_scores.items():
            source_count = len([s for s in data["sources"] if s != "existing"])
            score = data["score"]

            if source_count >= 3:
                score *= 1.30
            elif source_count >= 2:
                score *= 1.15

            score = min(score, 1.0)

            if score < self.min_confidence:
                continue

            stock_info = get_stock_info(sym) or {}
            final_list.append({
                "symbol": sym,
                "name": stock_info.get("name", sym),
                "sector": stock_info.get("sector", get_stock_sector(sym)),
                "lot_size": stock_info.get("lot_size", get_lot_size(sym)),
                "is_fno": is_fno_stock(sym),
                "score": round(score, 3),
                "sources": data["sources"],
                "reasons": data["reasons"][:3],
                "source_count": source_count,
                "is_existing": data.get("is_existing", False),
                "bias": data.get("bias", ""),
                "setup_type": data.get("setup_type", ""),
                "details": data.get("details", {}),
            })

        final_list.sort(key=lambda x: (x["score"], x["source_count"]), reverse=True)
        return final_list[:self.max_watchlist_size]

    def apply_regime_bias(
        self,
        candidates: List[Dict[str, Any]],
        regime: str,
    ) -> List[Dict[str, Any]]:
        """Apply directional regime bias to candidate stocks.

        - Bull regime: Boosts relative strength / bullish setups / positive change (+25%).
        - Bear regime: Boosts relative weakness / bearish setups / negative change (+25%).
        - Sideways / Volatile: Neutral, merged multi-source score is unadjusted.
        """
        regime_norm = (regime or "Sideways").capitalize()
        biased_list = []

        for item in candidates:
            item_copy = dict(item)
            base_score = item_copy.get("score", 0.5)
            bias = item_copy.get("bias", "")
            setup_type = item_copy.get("setup_type", "").lower()
            details = item_copy.get("details", {})
            price_change = float(details.get("price_change_pct", 0.0) or 0.0)

            is_bullish = (
                bias == "BUY"
                or "bullish" in setup_type
                or "oversold" in setup_type
                or price_change > 0.3
            )
            is_bearish = (
                bias == "SELL"
                or "bearish" in setup_type
                or "overbought" in setup_type
                or price_change < -0.3
            )

            multiplier = 1.0
            if regime_norm == "Bull":
                if is_bullish:
                    multiplier = 1.25
                elif is_bearish:
                    multiplier = 0.75
            elif regime_norm == "Bear":
                if is_bearish:
                    multiplier = 1.25
                elif is_bullish:
                    multiplier = 0.75
            # Sideways / Volatile keep multiplier = 1.0

            final_score = min(1.0, max(0.1, base_score * multiplier))
            item_copy["score"] = round(final_score, 3)
            item_copy["regime_bias_applied"] = regime_norm
            item_copy["regime_multiplier"] = multiplier
            biased_list.append(item_copy)

        biased_list.sort(key=lambda x: (x["score"], x.get("source_count", 1)), reverse=True)
        return biased_list

    async def build_daily_watchlist(
        self,
        feed: Any = None,
        news_items: Optional[List[Dict[str, Any]]] = None,
        regime: str = "Sideways",
        candidate_symbols: Optional[List[str]] = None,
        top_candidates_count: int = 20,
        final_top_n: int = 10,
    ) -> List[Dict[str, Any]]:
        """Complete 4-step daily pre-market watchlist generation pipeline:
        
        1. Scan candidate universe across Technical, Kronos, and News.
        2. Merge and narrow to Top ~20 candidates.
        3. Apply Regime-Directional Bias (Bull: long bias, Bear: short bias, Sideways: neutral).
        4. Rank and select final Top 10 actively scanned watchlist.
        """
        symbols = candidate_symbols or get_all_fno_symbols()
        logger.info(
            "Starting Daily Watchlist Generation for %d candidate symbols (Regime=%s)...",
            len(symbols), regime,
        )

        # 1. Technical Scan
        tech_results = []
        market_data: Dict[str, Dict[str, Any]] = {}

        if feed is not None and hasattr(feed, "get_candles"):
            try:
                tech_results = await self.technical_scanner.scan(symbols, feed)
                logger.info("TechnicalScanner produced %d setup signals", len(tech_results))
            except Exception as tech_err:
                logger.warning("TechnicalScanner error: %s", tech_err)

            # Build market data for Kronos Scanner in parallel
            sem = asyncio.Semaphore(15)

            async def _fetch_sym(sym: str) -> None:
                async with sem:
                    try:
                        daily_candles = None
                        try:
                            daily_candles = await feed.get_candles(sym, timeframe="1d", count=3)
                        except Exception:
                            pass

                        candles = await feed.get_candles(sym, timeframe="15m", count=30)

                        # ── Freshness guard (Phase 5) ─────────────────────
                        # Delisted/suspended symbols can still serve OLD
                        # history through the feed; drop candidates whose
                        # newest bar is far in the past instead of letting
                        # them rank into the watchlist.
                        _age_min = get_last_candle_age_minutes(candles)
                        if _age_min is not None and _age_min > _STALE_CANDLE_MAX_AGE_DAYS * 1440.0:
                            logger.info(
                                "Dropping %s from watchlist candidates: newest candle is %.0f days old (delisted/suspended?)",
                                sym, _age_min / 1440.0,
                            )
                            return

                        if candles and len(candles) >= 10:
                            df = pd.DataFrame(candles)
                            for col in ["open", "high", "low", "close", "volume"]:
                                if col in df.columns:
                                    df[col] = pd.to_numeric(df[col], errors="coerce")
                            df = df.dropna(subset=["close"])
                            if len(df) >= 5:
                                ltp = float(df["close"].iloc[-1])
                                if daily_candles and len(daily_candles) >= 2:
                                    prev_close = float(daily_candles[-2].get("close", ltp))
                                else:
                                    prev_close = float(df["close"].iloc[0]) if len(df) > 1 else ltp
                                vol = int(df["volume"].iloc[-1]) if "volume" in df.columns else 1000
                                avg_vol = int(df["volume"].mean()) if "volume" in df.columns else vol
                                rsi_series = calculate_rsi(df["close"])
                                rsi = float(rsi_series.iloc[-1]) if not rsi_series.empty and pd.notna(rsi_series.iloc[-1]) else 50.0

                                market_data[sym] = {
                                    "ltp": ltp,
                                    "close": prev_close,
                                    "volume": vol,
                                    "avg_volume": avg_vol if avg_vol > 0 else vol,
                                    "high": float(df["high"].iloc[-1]),
                                    "low": float(df["low"].iloc[-1]),
                                    "open": float(df["open"].iloc[-1]),
                                    "rsi": rsi,
                                }
                    except Exception:
                        pass

            await asyncio.gather(*[_fetch_sym(s) for s in symbols], return_exceptions=True)

        # 2. Kronos Scan
        kronos_results = []
        if market_data:
            try:
                kronos_results = self.kronos_scanner.scan(
                    watchlist_symbols=list(market_data.keys()),
                    market_data=market_data,
                    news_items=news_items or [],
                )
                logger.info("KronosScanner produced %d scored results", len(kronos_results))
            except Exception as kronos_err:
                logger.warning("KronosScanner error: %s", kronos_err)

        # 3. News Scan
        news_results = []
        if news_items:
            try:
                news_results = self.build_from_news(news_items)
                logger.info("News scanner produced %d results", len(news_results))
            except Exception as news_err:
                logger.warning("News scan error: %s", news_err)

        # 4. Merge lists
        technical_entries = self.build_from_technical(tech_results)
        merged_candidates = self.merge_lists(
            news_list=news_results,
            technical_list=technical_entries,
            kronos_list=kronos_results,
        )

        # Fallback if feeds produced no results (e.g. offline/mock environment)
        if not merged_candidates:
            # Only promote candidates that yielded verifiable market data —
            # symbols dropped by the freshness guard above (delisted/suspended)
            # must not sneak back into the watchlist through this fallback.
            # When nothing is verifiable (total feed outage) fall back to the
            # full candidate list, preserving the offline degradation path.
            viable = [s for s in symbols if s in market_data]
            fallback_pool = viable if viable else symbols
            logger.info("No candidates from live feeds, generating base FNO universe ranking")
            for sym in fallback_pool[:top_candidates_count]:
                stock_info = get_stock_info(sym) or {}
                merged_candidates.append({
                    "symbol": sym,
                    "name": stock_info.get("name", sym),
                    "sector": stock_info.get("sector", get_stock_sector(sym)),
                    "lot_size": stock_info.get("lot_size", get_lot_size(sym)),
                    "is_fno": True,
                    "score": 0.50,
                    "sources": ["universe"],
                    "reasons": ["NSE F&O Core Liquid Stock"],
                    "source_count": 1,
                    "details": {},
                })

        # Narrow to top candidates
        top_candidates = merged_candidates[:top_candidates_count]

        # 5. Apply Regime-Directional Bias
        biased_candidates = self.apply_regime_bias(top_candidates, regime=regime)

        # 6. Final Top N selection
        final_watchlist = biased_candidates[:final_top_n]
        logger.info(
            "Daily Watchlist complete: Selected top %d stocks (Regime=%s): %s",
            len(final_watchlist), regime, [x["symbol"] for x in final_watchlist],
        )
        return final_watchlist
