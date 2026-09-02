"""Convert news items into watchlist additions.

Rules:
- High impact items  -> add immediately with news-driven reason.
- Medium impact items -> add if sentiment is positive (BUY bias) or
  there is a technical setup hint.
- Positive sentiment -> BUY bias.
- Negative sentiment -> SHORT/SELL bias.
"""
import logging
from typing import Any, Dict, List

from utils.market_utils import get_stock_sector, is_fno_stock, get_stock_info

logger = logging.getLogger(__name__)

# Fields considered as "technical setup hints" in news extra data
_TECHNICAL_HINTS = {"breakout", "support", "resistance", "breakdown", "crossover"}


class NewsToWatchlist:
    """Convert classified news items into watchlist addition entries.

    Each output entry has:
        - symbol: str
        - reason: str  (why it was added)
        - bias: str    ('BUY', 'SELL', or 'NEUTRAL')
        - source: str  (e.g. 'news_engine')
        - impact_level: str
        - headline: str (original headline for reference)
    """

    def convert(self, news_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert a list of classified news items to watchlist additions.

        Args:
            news_items: List of dicts from NewsAnalyzer.classify_news.

        Returns:
            Deduplicated list of watchlist addition dicts.
        """
        additions: List[Dict[str, Any]] = []
        seen_symbols: set = set()

        # Sort by impact level so high-impact items are processed first
        _impact_order = {"high": 0, "medium": 1, "low": 2}
        sorted_items = sorted(
            news_items,
            key=lambda x: _impact_order.get(x.get("impact_level", "low"), 2),
        )

        for item in sorted_items:
            impact = item.get("impact_level", "low")
            sentiment = item.get("sentiment", "neutral")
            symbols = item.get("relevant_symbols", [])
            headline = item.get("headline", "")
            category = item.get("category", "")
            extra = item.get("extra", {}) or {}

            for symbol in symbols:
                if symbol in seen_symbols:
                    continue

                # Only F&O stocks
                if not is_fno_stock(symbol):
                    continue

                should_add = False
                bias = "NEUTRAL"
                reason = ""

                if impact == "high":
                    # High impact -> always add
                    should_add = True
                    if sentiment == "positive":
                        bias = "BUY"
                        reason = f"High-impact positive news: {headline[:80]}"
                    elif sentiment == "negative":
                        bias = "SELL"
                        reason = f"High-impact negative news: {headline[:80]}"
                    else:
                        bias = "BUY" if category == "earnings" else "NEUTRAL"
                        reason = f"High-impact news ({category}): {headline[:80]}"

                elif impact == "medium":
                    # Medium impact -> add if positive sentiment OR technical hint
                    has_technical_hint = any(
                        hint in str(extra).lower()
                        for hint in _TECHNICAL_HINTS
                    )
                    has_technical_hint = has_technical_hint or any(
                        hint in headline.lower()
                        for hint in _TECHNICAL_HINTS
                    )

                    if sentiment == "positive" or has_technical_hint:
                        should_add = True
                        bias = "BUY"
                        reason = f"Medium-impact news: {headline[:80]}"
                        if has_technical_hint:
                            reason += " (technical setup)"
                    elif sentiment == "negative":
                        should_add = True
                        bias = "SELL"
                        reason = f"Medium-impact negative news: {headline[:80]}"

                if should_add:
                    seen_symbols.add(symbol)
                    stock_info = get_stock_info(symbol) or {}
                    additions.append({
                        "symbol": symbol,
                        "reason": reason,
                        "bias": bias,
                        "source": "news_engine",
                        "impact_level": impact,
                        "headline": headline,
                        "sector": stock_info.get("sector", "Unknown"),
                    })

        return additions
