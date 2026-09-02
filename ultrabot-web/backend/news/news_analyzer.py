"""News analyser – classifies raw news items into category, sentiment,
impact level, and relevant NSE F&O symbols.

Uses a keyword-based classification approach that maps news text to
F&O stocks from the market_utils universe.
"""
import logging
import re
from typing import Any, Dict, List, Set

from utils.market_utils import FNO_UNIVERSE, get_all_fno_symbols, _SYMBOL_MAP

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Sentiment keyword banks
# ------------------------------------------------------------------

_POSITIVE_WORDS = {
    "surge", "jump", "rally", "soar", "boom", "bullish", "upbeat",
    "strong", "record", "beat", "exceeds", "outperform", "upgrade",
    "buy", "upgrade", "acquire", "expansion", "growth", "profit",
    "dividend", "bonus", "buyback", "order win", "contract", "deal",
    "partnership", "launch", "innovation", "patent", "approval",
    "recommend", "overweight", "positive", "optimistic", "robust",
    "higher", "gains", "upside", "breakthrough", "recovery",
    "upward", "support", "healthy", "demand",
}

_NEGATIVE_WORDS = {
    "crash", "plunge", "drop", "fall", "slump", "bearish", "weak",
    "miss", "disappoint", "underperform", "downgrade", "sell",
    "fraud", "scam", "loss", "debt", "default", "bankruptcy",
    "liquidation", "probe", "investigation", "fine", "penalty",
    "cancellation", "delay", "recall", "warning", "risk",
    "concern", "fear", "panic", "sell-off", "selloff", "pressure",
    "lower", "decline", "downside", "cut", "reduce", "negative",
    "pessimistic", "volatile", "uncertainty", "headwind", "crisis",
    "lowered", "slashed", "revised down", "weakness",
}

# ------------------------------------------------------------------
# Category keywords
# ------------------------------------------------------------------

_CATEGORY_KEYWORDS = {
    "earnings": ["result", "earnings", "q1", "q2", "q3", "q4", "quarterly",
                 "profit after tax", "pat", "ebitda", "revenue", "net profit",
                 "consensus", "estimate"],
    "corporate_action": ["bonus", "split", "dividend", "buyback", "rights issue",
                          "demerger", "merger", "acquisition", "delisting"],
    "regulatory": ["rbi", "sebi", "rbi policy", "gst", "tax", "regulation",
                    "compliance", "fema", "fdi", "government", "policy",
                    "budget", "amendment"],
    "sector": ["it sector", "banking sector", "pharma", "auto", "fmcg",
                "energy", "metal", "infra", "reality", "cement",
                "telecom", "index", "nifty", "sensex"],
    "macro": ["gdp", "cpi", "wpi", "inflation", "iip", "pmi", "trade deficit",
              "current account", "fii", "dii", "fpi", "rupee", "dollar",
              "crude oil", "bond yield", "fed", "us fed"],
    "technical": ["breakout", "support", "resistance", "pattern", "moving average",
                   "rsi", "macd", "crossover", "trendline", "volume spike"],
}

# ------------------------------------------------------------------
# Impact escalation keywords
# ------------------------------------------------------------------

_HIGH_IMPACT_KEYWORDS = {
    "crash", "plunge", "surge", "record high", "record low",
    "bankruptcy", "fraud", "scam", "demerger", "merger",
    "acquisition", "buyback", "bonus issue", "stock split",
    "rbi policy", "rate cut", "rate hike", "budget",
    "results today", "earnings beat", "earnings miss",
    "downgrade", "upgrade", "target price",
}

_MEDIUM_IMPACT_KEYWORDS = {
    "dividend", "result", "quarterly", "order win",
    "contract", "deal", "partnership", "expansion",
    "sebi", "regulatory", "fii", "dii", "inflation",
    "gdp", "cpi", "breakout", "support", "resistance",
}


class NewsAnalyzer:
    """Classify a raw news item.

    Produces a dict with:
        - category: str (one of CATEGORY_KEYWORDS keys or 'general')
        - sentiment: str ('positive', 'negative', 'neutral')
        - impact_level: str ('high', 'medium', 'low')
        - relevant_symbols: list of NSE F&O symbols
    """

    def __init__(self):
        # Build a name-fragment -> symbol lookup for fuzzy matching
        self._name_lookup: Dict[str, str] = {}
        for stock in FNO_UNIVERSE:
            self._name_lookup[stock["symbol"]] = stock["symbol"]
            name_parts = stock["name"].upper().split()
            for part in name_parts:
                if len(part) > 3:
                    self._name_lookup[part] = stock["symbol"]
            # Two-word suffix
            if len(name_parts) > 1:
                self._name_lookup[" ".join(name_parts[-2:])] = stock["symbol"]

        self._fno_set: Set[str] = set(get_all_fno_symbols())

    def classify_news(self, news_item: Dict[str, Any]) -> Dict[str, Any]:
        """Classify a single news item.

        Args:
            news_item: Dict with at least 'headline'. May have 'summary',
                'symbols', 'source', etc.

        Returns:
            Enriched dict with added/overridden keys: category, sentiment,
            impact_level, relevant_symbols.
        """
        headline = news_item.get("headline", "")
        summary = news_item.get("summary", "")
        text = f"{headline} {summary}".upper()

        # ---- Category ----
        category = self._classify_category(text)

        # ---- Sentiment ----
        sentiment = self._classify_sentiment(text)

        # ---- Impact ----
        impact_level = self._classify_impact(text, news_item.get("impact_level", "low"))

        # ---- Relevant symbols ----
        relevant_symbols = self._extract_symbols(text)
        # Merge with any pre-extracted symbols
        pre_symbols = news_item.get("symbols", [])
        if isinstance(pre_symbols, list):
            for s in pre_symbols:
                s_upper = s.upper()
                if s_upper in self._fno_set and s_upper not in relevant_symbols:
                    relevant_symbols.append(s_upper)

        # Build enriched item (don't mutate input)
        enriched = dict(news_item)
        enriched["category"] = category
        enriched["sentiment"] = sentiment
        enriched["impact_level"] = impact_level
        enriched["relevant_symbols"] = relevant_symbols

        return enriched

    # ------------------------------------------------------------------
    # Category classification
    # ------------------------------------------------------------------

    def _classify_category(self, text: str) -> str:
        best_category = "general"
        best_count = 0

        for category, keywords in _CATEGORY_KEYWORDS.items():
            count = sum(1 for kw in keywords if kw in text)
            if count > best_count:
                best_count = count
                best_category = category

        return best_category

    # ------------------------------------------------------------------
    # Sentiment classification
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_sentiment(text: str) -> str:
        """Count positive and negative keyword hits; return majority."""
        pos_count = 0
        neg_count = 0

        words = set(re.findall(r"\b\w+\b", text.lower()))
        for word in words:
            if word in _POSITIVE_WORDS:
                pos_count += 1
            if word in _NEGATIVE_WORDS:
                neg_count += 1

        if pos_count > neg_count + 1:
            return "positive"
        elif neg_count > pos_count + 1:
            return "negative"
        else:
            return "neutral"

    # ------------------------------------------------------------------
    # Impact classification
    # ------------------------------------------------------------------

    def _classify_impact(self, text: str, pre_assigned: str) -> str:
        """Determine impact level from keywords, respecting pre-assigned value."""
        # If the source already assigned 'high', trust it
        if pre_assigned == "high":
            return "high"

        for kw in _HIGH_IMPACT_KEYWORDS:
            if kw in text:
                return "high"

        if pre_assigned == "medium":
            return "medium"

        for kw in _MEDIUM_IMPACT_KEYWORDS:
            if kw in text:
                return "medium"

        return "low"

    # ------------------------------------------------------------------
    # Symbol extraction
    # ------------------------------------------------------------------

    def _extract_symbols(self, text: str) -> List[str]:
        """Extract F&O stock symbols from text."""
        found: List[str] = []

        # Direct symbol match
        for sym in self._fno_set:
            if sym in text and sym not in found:
                found.append(sym)

        # Name fragment match (only add if < 5 direct matches)
        if len(found) < 5:
            for fragment, sym in self._name_lookup.items():
                if len(fragment) <= 3:
                    continue
                if fragment in text and sym not in found:
                    found.append(sym)
                if len(found) >= 8:
                    break

        return found
