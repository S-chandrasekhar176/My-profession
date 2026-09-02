import logging
import re
from datetime import datetime
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import feedparser

from utils.market_utils import FNO_UNIVERSE, get_all_fno_symbols

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# LiveMint market news RSS
_LIVEMINT_RSS_URLS = [
    "https://www.livemint.com/rss/markets",
    "https://www.livemint.com/rss/money",
    "https://www.livemint.com/rss/industry",
]

_FNO_NAME_LOOKUP: Dict[str, str] = {}
for _s in FNO_UNIVERSE:
    _name_parts = _s["name"].upper().split()
    _FNO_NAME_LOOKUP[_s["symbol"]] = _s["symbol"]
    for part in _name_parts:
        if len(part) > 3:
            _FNO_NAME_LOOKUP[part] = _s["symbol"]

_FNO_SET: set = set(get_all_fno_symbols())


class LiveMintSource:
    """Fetch news from LiveMint RSS feeds.

    Uses feedparser to parse RSS and extracts F&O stock names.
    """

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout
        self.rss_urls = _LIVEMINT_RSS_URLS

    async def fetch(self) -> List[Dict[str, Any]]:
        import asyncio
        items = []
        for url in self.rss_urls:
            try:
                loop = asyncio.get_event_loop()
                feed = await loop.run_in_executor(None, feedparser.parse, url)
                for entry in feed.entries[:15]:
                    headline = entry.get("title", "")
                    link = entry.get("link", "")
                    published = entry.get("published", "")
                    summary = entry.get("summary", "")

                    if not headline:
                        continue

                    # Clean HTML from summary
                    summary = re.sub(r"<[^>]+>", "", summary).strip()

                    symbols = self._extract_symbols(headline + " " + summary)

                    items.append({
                        "headline": headline,
                        "source": "livemint",
                        "url": link,
                        "category": "market_news",
                        "sentiment": "neutral",
                        "impact_level": "low",
                        "symbols": symbols,
                        "timestamp": published or datetime.now(IST).isoformat(),
                        "summary": summary[:200],
                    })
            except Exception as e:
                logger.error("Failed to fetch LiveMint RSS from %s: %s", url, e)
                continue

        return items

    def _extract_symbols(self, text: str) -> List[str]:
        found = []
        text_upper = text.upper()

        for sym in _FNO_SET:
            if sym in text_upper and sym not in found:
                found.append(sym)

        if len(found) < 3:
            for fragment, sym in _FNO_NAME_LOOKUP.items():
                if fragment in text_upper and sym not in found:
                    if len(fragment) > 5:
                        found.append(sym)
                if len(found) >= 5:
                    break

        return found
