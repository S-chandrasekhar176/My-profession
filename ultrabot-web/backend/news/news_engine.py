"""News engine – orchestrates fetching, analyzing, and watchlist conversion.

Aggregates news from all configured sources using concurrent HTTP requests,
runs the NewsAnalyzer for classification, and optionally converts high-impact
items into watchlist additions.
"""
import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import aiohttp

from news.news_analyzer import NewsAnalyzer
from news.news_to_watchlist import NewsToWatchlist
from news.sources.economic_times import EconomicTimesSource
from news.sources.ndtv_profit import NDTVProfitSource
from news.sources.livemint import LiveMintSource
from news.sources.hindu_businessline import HinduBusinessLineSource
from news.sources.nse_corporate import NSECorporateSource
from news.sources.result_calendar import ResultCalendarSource

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# Max age for news items (48 hours) - reject stale RSS entries
_MAX_AGE_SECONDS = 48 * 3600

# All available source classes – instantiated lazily
_SOURCE_CLASSES = [
    EconomicTimesSource,
    NDTVProfitSource,
    LiveMintSource,
    HinduBusinessLineSource,
    NSECorporateSource,
    ResultCalendarSource,
]


# Timeout per source (seconds)
_DEFAULT_FETCH_TIMEOUT = 20.0


class NewsEngine:
    """Fetch news from multiple sources concurrently, analyze, and convert.

    Args:
        config: Application config dict (from Settings). Expected keys:
            - news.sources: list of source names to enable
            - news.fetch_timeout: float timeout per source
            - news.enabled: bool master switch
        repository: Optional Repository for persisting news items.
    """

    def __init__(self, config, repository=None):
        self.config = config or {}
        self.repository = repository
        self.analyzer = NewsAnalyzer()
        self.watchlist_converter = NewsToWatchlist()

        # Build enabled sources
        news_cfg = self.config.get("news", {})
        enabled_sources = news_cfg.get("sources", [])
        self.fetch_timeout = float(news_cfg.get("fetch_timeout", _DEFAULT_FETCH_TIMEOUT))

        self._sources: List[Any] = []
        _source_name_map = {
            "economic_times": EconomicTimesSource,
            "ndtv_profit": NDTVProfitSource,
            "livemint": LiveMintSource,
            "hindu_businessline": HinduBusinessLineSource,
            "nse_corporate": NSECorporateSource,
            "result_calendar": ResultCalendarSource,
        }

        if enabled_sources:
            for name in enabled_sources:
                cls = _source_name_map.get(name)
                if cls:
                    self._sources.append(cls(timeout=self.fetch_timeout))
                else:
                    logger.warning("Unknown news source: %s", name)
        else:
            # Default: all sources
            for cls in _SOURCE_CLASSES:
                self._sources.append(cls(timeout=self.fetch_timeout))

        logger.info("NewsEngine initialised with %d sources", len(self._sources))

    async def run_full_scan(self) -> List[Dict[str, Any]]:
        """Fetch from all sources concurrently and return analysed items.

        Returns:
            Deduplicated list of classified news items.
        """
        if not self.config.get("news", {}).get("enabled", True):
            logger.info("News engine disabled in config.")
            return []

        # Fetch all sources concurrently
        tasks = [source.fetch() for source in self._sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_items: List[Dict[str, Any]] = []
        for source, result in zip(self._sources, results):
            if isinstance(result, Exception):
                logger.error("Source %s failed: %s", type(source).__name__, result)
                continue
            all_items.extend(result)

        logger.info("Fetched %d raw news items", len(all_items))

        # Filter out stale news (older than 48 hours)
        now = datetime.now(IST)
        fresh_items = []
        for item in all_items:
            ts_str = item.get("timestamp") or item.get("published_at") or ""
            if ts_str:
                try:
                    dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=IST)
                    age = (now - dt.astimezone(IST)).total_seconds()
                    if age > _MAX_AGE_SECONDS:
                        continue  # Skip stale items
                except Exception:
                    try:
                        from email.utils import parsedate_to_datetime
                        dt = parsedate_to_datetime(ts_str)
                        age = (now - dt.astimezone(IST)).total_seconds()
                        if age > _MAX_AGE_SECONDS:
                            continue
                    except Exception:
                        pass  # Keep items with unparseable timestamps
            fresh_items.append(item)

        logger.info("After staleness filter: %d fresh items (from %d raw)", len(fresh_items), len(all_items))

        # Deduplicate by headline
        seen_headlines: set = set()
        deduped = []
        for item in fresh_items:
            headline = item.get("headline", "")
            if headline in seen_headlines:
                continue
            seen_headlines.add(headline)
            deduped.append(item)

        # Classify each item
        analysed = []
        for item in deduped:
            classified = self.analyzer.classify_news(item)
            analysed.append(classified)

        # Single multi-key sort: impact level asc (high=0, med=1, low=2), then timestamp desc (newest first)
        _impact_rank = {"high": 0, "medium": 1, "low": 2}
        def _sort_key(item: dict):
            rank = _impact_rank.get(str(item.get("impact_level", "low")).lower(), 2)
            ts_str = item.get("timestamp") or item.get("published_at") or ""
            ts_val = 0.0
            if ts_str:
                try:
                    ts_val = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00")).timestamp()
                except Exception:
                    ts_val = 0.0
            return (rank, -ts_val)

        analysed.sort(key=_sort_key)

        logger.info("Analysed %d unique news items", len(analysed))
        return analysed

    async def run_morning_briefing(self) -> List[Dict[str, Any]]:
        """Run a full scan and return items suitable for the morning watchlist.

        Returns:
            List of watchlist additions derived from high/medium impact news.
        """
        items = await self.run_full_scan()
        watchlist_additions = self.watchlist_converter.convert(items)
        logger.info(
            "Morning briefing: %d news items → %d watchlist additions",
            len(items),
            len(watchlist_additions),
        )
        return watchlist_additions
