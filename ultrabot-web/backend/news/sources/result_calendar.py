import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from utils.market_utils import FNO_UNIVERSE, get_all_fno_symbols

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_NSE_RESULTS_URL = "https://www.nseindia.com/api/event-calendar"
_MONEYCONTROL_RESULTS_URL = "https://www.moneycontrol.com/markets/earnings/"

_FNO_SET: set = set(get_all_fno_symbols())
_FNO_NAME_MAP: Dict[str, str] = {}
for _s in FNO_UNIVERSE:
    _FNO_NAME_MAP[_s["name"].upper()] = _s["symbol"]
    _parts = _s["name"].upper().split()
    if len(_parts) > 1:
        _FNO_NAME_MAP[" ".join(_parts[-2:])] = _s["symbol"]


class ResultCalendarSource:
    """Fetch upcoming company results calendar.

    Scrapes result calendar pages for F&O stocks with
    upcoming earnings announcements today or this week.
    """

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml",
        }

    async def fetch(self) -> List[Dict[str, Any]]:
        """Fetch upcoming result dates.

        Returns:
            List of news items for stocks with upcoming results.
        """
        items = []

        try:
            items = await self._fetch_moneycontrol_results()
        except Exception as e:
            logger.warning("Failed to fetch results from Moneycontrol: %s", e)

        try:
            nse_items = await self._fetch_nse_results()
            items.extend(nse_items)
        except Exception as e:
            logger.warning("Failed to fetch results from NSE: %s", e)

        return items

    async def _fetch_moneycontrol_results(self) -> List[Dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self.timeout, headers=self._headers) as client:
            response = await client.get(_MONEYCONTROL_RESULTS_URL, follow_redirects=True)
            response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        items = []
        today = datetime.now(IST).date()
        week_end = today + timedelta(days=7)

        rows = soup.select("table tbody tr")
        for row in rows[:100]:
            cells = row.select("td")
            if len(cells) < 3:
                continue

            company_name = cells[0].get_text(strip=True)
            result_date_str = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            time_str = cells[2].get_text(strip=True) if len(cells) > 2 else ""

            symbol = self._name_to_symbol(company_name)
            if not symbol:
                continue

            # Parse date
            result_date = self._parse_date(result_date_str)
            if result_date is None:
                continue

            # Only include today and this week
            if result_date < today or result_date > week_end:
                continue

            days_away = (result_date - today).days
            if days_away == 0:
                impact = "high"
                headline_prefix = "Results TODAY"
            elif days_away <= 3:
                impact = "high"
                headline_prefix = f"Results in {days_away} days"
            else:
                impact = "medium"
                headline_prefix = f"Results this week ({result_date_str})"

            items.append({
                "headline": f"{headline_prefix}: {company_name} ({symbol})",
                "source": "result_calendar",
                "url": _MONEYCONTROL_RESULTS_URL,
                "category": "results",
                "sentiment": "neutral",
                "impact_level": impact,
                "symbols": [symbol],
                "timestamp": datetime.now(IST).isoformat(),
                "extra": {
                    "company": company_name,
                    "result_date": result_date_str,
                    "time": time_str,
                    "days_away": days_away,
                },
            })

        return items

    async def _fetch_nse_results(self) -> List[Dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self.timeout, headers=self._headers) as client:
            await client.get("https://www.nseindia.com/", follow_redirects=True)
            response = await client.get(_NSE_RESULTS_URL, follow_redirects=True)
            response.raise_for_status()
            data = response.json()

        items = []
        today = datetime.now(IST).date()

        for row in data[:150]:
            symbol = row.get("symbol", "").upper()
            purpose = row.get("purpose", "")
            event_date = row.get("date", "")

            if symbol not in _FNO_SET:
                continue

            if "result" not in purpose.lower():
                continue

            result_date = self._parse_date(event_date)
            if result_date is not None and result_date == today:
                items.append({
                    "headline": f"Results TODAY: {symbol} - {purpose}",
                    "source": "result_calendar",
                    "url": _NSE_RESULTS_URL,
                    "category": "results",
                    "sentiment": "neutral",
                    "impact_level": "high",
                    "symbols": [symbol],
                    "timestamp": datetime.now(IST).isoformat(),
                })

        return items

    def _name_to_symbol(self, name: str) -> str:
        name_upper = name.upper()
        if name_upper in _FNO_NAME_MAP:
            return _FNO_NAME_MAP[name_upper]
        if name_upper in _FNO_SET:
            return name_upper
        for full_name, sym in _FNO_NAME_MAP.items():
            if name_upper in full_name or full_name in name_upper:
                return sym
        return ""

    @staticmethod
    def _parse_date(date_str: str):
        """Parse various date formats."""
        if not date_str:
            return None
        from datetime import datetime as dt
        for fmt in ["%d %b %Y", "%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%b %d, %Y"]:
            try:
                return dt.strptime(date_str.strip(), fmt).date()
            except ValueError:
                continue
        return None
