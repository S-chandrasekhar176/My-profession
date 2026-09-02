import logging
import re
from datetime import datetime
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import feedparser

from utils.market_utils import FNO_UNIVERSE, get_all_fno_symbols

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_MONEYCONTROL_RSS_URL = "https://www.moneycontrol.com/rss/latestnews.xml"

# Build name fragment to symbol lookup
_FNO_NAME_LOOKUP: Dict[str, str] = {}
for _s in FNO_UNIVERSE:
    _name_parts = _s["name"].upper().split()
    _FNO_NAME_LOOKUP[_s["symbol"]] = _s["symbol"]
    # Map common name variations
    for part in _name_parts:
        if len(part) > 3:
            _FNO_NAME_LOOKUP[part] = _s["symbol"]

_FNO_SET: set = set(get_all_fno_symbols())


class MoneycontrolSource:
    """Fetch news from Moneycontrol RSS feed.

    Uses feedparser to parse RSS and extracts F&O stock names
    from headlines.
    """

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout
        self.rss_url = _MONEYCONTROL_RSS_URL

    async def fetch(self) -> List[Dict[str, Any]]:
        """Fetch top news from Moneycontrol RSS.

        Returns:
            List of news item dicts.
        """
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            feed = await loop.run_in_executor(None, feedparser.parse, self.rss_url)
        except Exception as e:
            logger.error("Failed to fetch Moneycontrol RSS: %s", e)
            return []

        items = []
        for entry in feed.entries[:25]:
            headline = entry.get("title", "")
            link = entry.get("link", "")
            published = entry.get("published", "")
            summary = entry.get("summary", "")

            if not headline:
                continue

            symbols = self._extract_symbols(headline + " " + summary)

            items.append({
                "headline": headline,
                "source": "moneycontrol",
                "url": link,
                "category": "market_news",
                "sentiment": "neutral",  # Will be classified by NewsAnalyzer
                "impact_level": "low",
                "symbols": symbols,
                "timestamp": published or datetime.now(IST).isoformat(),
                "summary": summary[:200],
            })

        return items

    def _extract_symbols(self, text: str) -> List[str]:
        """Extract F&O stock names/symbols from text."""
        found = []
        text_upper = text.upper()

        # Direct symbol match
        for sym in _FNO_SET:
            if sym in text_upper and sym not in found:
                found.append(sym)

        # Name fragment match (only if fewer than 3 direct matches)
        if len(found) < 3:
            for fragment, sym in _FNO_NAME_LOOKUP.items():
                if fragment in text_upper and sym not in found:
                    # Avoid false positives for common words
                    if len(fragment) > 5:
                        found.append(sym)
                if len(found) >= 5:
                    break

        return found
