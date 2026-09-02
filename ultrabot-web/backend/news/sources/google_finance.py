import logging
import re
from datetime import datetime
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import httpx

from utils.market_utils import FNO_UNIVERSE, get_all_fno_symbols

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_GOOGLE_FINANCE_URL = "https://www.google.com/finance/"
_GOOGLE_TRENDING_URL = "https://www.google.com/finance/quote/NIFTY_50:INDEXNSE"

_FNO_SET: set = set(get_all_fno_symbols())
_FNO_NAME_MAP: Dict[str, str] = {}
for _s in FNO_UNIVERSE:
    _FNO_NAME_MAP[_s["name"].upper()] = _s["symbol"]
    _parts = _s["name"].upper().split()
    if len(_parts) > 1:
        _FNO_NAME_MAP[" ".join(_parts[-2:])] = _s["symbol"]


class GoogleFinanceSource:
    """Fetch trending stocks and news from Google Finance.

    Scrapes Google Finance trending tickers page.
    """

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }

    async def fetch(self) -> List[Dict[str, Any]]:
        """Fetch trending stocks from Google Finance.

        Returns:
            List of news item dicts.
        """
        items = []
        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=self._headers) as client:
                response = await client.get(_GOOGLE_FINANCE_URL, follow_redirects=True)
                response.raise_for_status()

            # Extract trending tickers from the page
            trending = self._parse_trending(response.text)
            for symbol, change_pct in trending:
                direction = "positive" if change_pct >= 0 else "negative"
                items.append({
                    "headline": f"{symbol} trending on Google Finance ({change_pct:+.2f}%)",
                    "source": "google_finance",
                    "url": _GOOGLE_FINANCE_URL,
                    "category": "trending",
                    "sentiment": direction,
                    "impact_level": "medium" if abs(change_pct) > 2 else "low",
                    "symbols": [symbol],
                    "timestamp": datetime.now(IST).isoformat(),
                    "extra": {"name": symbol, "change_pct": change_pct},
                })
        except Exception as e:
            logger.error("Failed to fetch Google Finance trending: %s", e)

        return items

    def _parse_trending(self, html: str) -> List[tuple]:
        """Parse trending tickers from Google Finance HTML."""
        from bs4 import BeautifulSoup
        results = []
        soup = BeautifulSoup(html, "html.parser")

        # Find all links
        for a in soup.find_all("a"):
            href = a.get("href", "")
            if "/quote/" in href and ":NSE" in href:
                text = a.get_text(strip=True)
                if "%" in text:
                    # extract symbol from href, e.g. /quote/TCS:NSE
                    symbol = href.split("/quote/")[-1].split(":")[0]
                    # extract change percentage
                    change_match = re.search(r"([+-]?[\d.]+)%", text)
                    change_pct = float(change_match.group(1)) if change_match else 0.0
                    
                    if symbol:
                        results.append((symbol, change_pct))

        # Deduplicate
        seen = set()
        dedup_results = []
        for sym, pct in results:
            if sym not in seen:
                seen.add(sym)
                dedup_results.append((sym, pct))

        return dedup_results[:20]

    def _name_to_symbol(self, name: str) -> str:
        """Convert a stock name to NSE symbol."""
        name_upper = name.upper()
        if name_upper in _FNO_NAME_MAP:
            return _FNO_NAME_MAP[name_upper]
        if name_upper in _FNO_SET:
            return name_upper
        # Partial match
        for full_name, sym in _FNO_NAME_MAP.items():
            if name_upper in full_name or full_name in name_upper:
                return sym
        return ""