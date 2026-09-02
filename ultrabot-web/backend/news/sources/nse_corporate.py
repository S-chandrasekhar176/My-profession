import logging
import re
from datetime import datetime
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from utils.market_utils import FNO_UNIVERSE, get_all_fno_symbols

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_NSE_CORPORATE_URL = "https://www.nseindia.com/api/corporate-announcements?index=equities"
_NSE_CORP_ACTIONS_URL = "https://www.nseindia.com/api/corporates-corporateActions?index=equities"

# Build a quick lookup: name fragment -> symbol
_NAME_TO_SYMBOL: Dict[str, str] = {}
for _stock in FNO_UNIVERSE:
    _name = _stock["name"].upper()
    _NAME_TO_SYMBOL[_name] = _stock["symbol"]
    # Also add last name word for companies like "Tata Consultancy"
    _words = _name.split()
    if len(_words) > 1:
        _NAME_TO_SYMBOL[" ".join(_words[-2:])] = _stock["symbol"]

_FNO_SET: set = set(get_all_fno_symbols())


class NSECorporateSource:
    """Fetch corporate action news from NSE website.

    Uses httpx + BeautifulSoup to scrape NSE's corporate filings/actions page.
    Returns structured news items with symbol extraction.
    """

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout
        self._session_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        }

    async def fetch(self) -> List[Dict[str, Any]]:
        """Fetch corporate action news from NSE.

        Returns:
            List of news item dicts.
        """
        items = []
        try:
            # Create a single client session to handle cookies properly for NSE
            async with httpx.AsyncClient(timeout=self.timeout, headers=self._session_headers) as client:
                # 1. Fetch homepage to set cookies
                await client.get("https://www.nseindia.com/", follow_redirects=True)

                # 2. Fetch actions & filings using the same client
                items.extend(await self._fetch_corporate_actions(client))
                items.extend(await self._fetch_corporate_filings(client))
        except Exception as e:
            logger.warning("Failed to fetch NSE corporate data: %s", e)

        return items

    async def _fetch_corporate_actions(self, client: httpx.AsyncClient) -> List[Dict[str, Any]]:
        try:
            response = await client.get(_NSE_CORP_ACTIONS_URL, follow_redirects=True)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.warning("Failed to parse corporate actions JSON: %s", e)
            return []

        items = []
        for row in data[:50]:
            symbol = row.get("symbol", "").upper()
            company = row.get("comp", "")
            purpose = row.get("subject", "")
            ex_date = row.get("exDate", "")
            record_date = row.get("recDate", "")

            # Only include F&O stocks
            if symbol not in _FNO_SET:
                continue

            impact = self._assess_corporate_impact(purpose)
            sentiment = self._assess_corporate_sentiment(purpose)

            items.append({
                "headline": f"{company}: {purpose}",
                "source": "nse_corporate",
                "url": _NSE_CORP_ACTIONS_URL,
                "category": "corporate_action",
                "sentiment": sentiment,
                "impact_level": impact,
                "symbols": [symbol],
                "timestamp": datetime.now(IST).isoformat(),
                "extra": {
                    "ex_date": ex_date,
                    "record_date": record_date,
                    "purpose": purpose,
                    "company": company,
                },
            })

        return items

    async def _fetch_corporate_filings(self, client: httpx.AsyncClient) -> List[Dict[str, Any]]:
        try:
            response = await client.get(_NSE_CORPORATE_URL, follow_redirects=True)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.warning("Failed to parse corporate announcements JSON: %s", e)
            return []

        items = []
        for row in data[:30]:
            symbol = row.get("symbol", "").upper()
            description = row.get("desc", "")
            filing_date = row.get("an_dt", "")

            if symbol not in _FNO_SET:
                continue

            items.append({
                "headline": description[:120],
                "source": "nse_corporate",
                "url": _NSE_CORPORATE_URL,
                "category": "corporate_filing",
                "sentiment": "neutral",
                "impact_level": "low",
                "symbols": [symbol],
                "timestamp": datetime.now(IST).isoformat(),
                "extra": {"filing_date": filing_date},
            })

        return items

    @staticmethod
    def _assess_corporate_impact(purpose: str) -> str:
        p = purpose.upper()
        high_keywords = ["SPLIT", "BONUS", "BUYBACK", "DEMERGER", "MERGER", "DIVIDEND >"]
        medium_keywords = ["DIVIDEND", "RIGHTS ISSUE", "AGM", "EGM"]
        for kw in high_keywords:
            if kw in p:
                return "high"
        for kw in medium_keywords:
            if kw in p:
                return "medium"
        return "low"

    @staticmethod
    def _assess_corporate_sentiment(purpose: str) -> str:
        p = purpose.upper()
        positive = ["BONUS", "SPLIT", "BUYBACK", "DIVIDEND"]
        negative = ["DELISTING", "BANKRUPTCY", "LIQUIDATION"]
        for kw in positive:
            if kw in p:
                return "positive"
        for kw in negative:
            if kw in p:
                return "negative"
        return "neutral"
