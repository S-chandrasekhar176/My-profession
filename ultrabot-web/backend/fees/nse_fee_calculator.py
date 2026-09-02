"""NSE fee calculator for equity intraday, delivery, and options trading.

All charges follow SEBI-regulated NSE fee structures.
"""
from __future__ import annotations

from typing import Dict


class NSEFeeCalculator:
    """Calculates all NSE regulatory charges for trades."""

    # NSE statutory rates
    STT_INTRADAY_SELL_PCT = 0.00025  # 0.025% on sell side only
    STT_DELIVERY_PCT = 0.001  # 0.1% on both sides
    STT_OPTIONS_SELL_PCT = 0.000625  # 0.0625% on sell premium
    EXCHANGE_TXN_PCT = 0.0000345  # 0.00345% on turnover
    EXCHANGE_TXN_OPTIONS_PCT = 0.0005  # 0.05% on premium (buy & sell)
    GST_PCT = 0.18  # 18% on (brokerage + exchange txn)
    SEBI_FEE_PCT = 0.000001  # 0.0001% on turnover
    STAMP_DUTY_BUY_PCT = 0.00003  # 0.003% on buy side equity
    STAMP_DUTY_SELL_PCT = 0.00003  # 0.003% on sell side (delivery)

    def __init__(self, brokerage_per_order: float = 20.0):
        self.brokerage_per_order = brokerage_per_order

    @staticmethod
    def _r(value: float) -> float:
        """Round to 2 decimal places."""
        return round(value, 2)

    def calculate_equity_intraday(
        self,
        buy_price: float,
        sell_price: float,
        quantity: int,
        brokerage_per_order: float = 20.0,
    ) -> Dict[str, float]:
        """Calculate all fees for equity intraday trade.

        Buy side:  brokerage flat, STT=0, Exchange=buy*qty*0.0000345,
                   GST=18%*(brokerage+exchange), SEBI=buy*qty*0.000001,
                   Stamp=buy*qty*0.00003
        Sell side: brokerage flat, STT=sell*qty*0.00025,
                   Exchange=sell*qty*0.0000345, GST=18%*(brokerage+exchange),
                   SEBI=sell*qty*0.000001, Stamp=0
        """
        buy_turnover = buy_price * quantity
        sell_turnover = sell_price * quantity

        # Buy side charges
        buy_brokerage = brokerage_per_order
        buy_exchange = buy_turnover * self.EXCHANGE_TXN_PCT
        buy_gst = (buy_brokerage + buy_exchange) * self.GST_PCT
        buy_sebi = buy_turnover * self.SEBI_FEE_PCT
        buy_stamp = buy_turnover * self.STAMP_DUTY_BUY_PCT

        # Sell side charges
        sell_brokerage = brokerage_per_order
        sell_stt = sell_turnover * self.STT_INTRADAY_SELL_PCT
        sell_exchange = sell_turnover * self.EXCHANGE_TXN_PCT
        sell_gst = (sell_brokerage + sell_exchange) * self.GST_PCT
        sell_sebi = sell_turnover * self.SEBI_FEE_PCT
        sell_stamp = 0.0

        total_brokerage = buy_brokerage + sell_brokerage
        total_stt = sell_stt
        total_exchange = buy_exchange + sell_exchange
        total_gst = buy_gst + sell_gst
        total_sebi = buy_sebi + sell_sebi
        total_stamp = buy_stamp + sell_stamp
        total = total_brokerage + total_stt + total_exchange + total_gst + total_sebi + total_stamp

        return {
            "brokerage": self._r(total_brokerage),
            "stt": self._r(total_stt),
            "exchange_txn": self._r(total_exchange),
            "gst": self._r(total_gst),
            "sebi": self._r(total_sebi),
            "stamp_duty": self._r(total_stamp),
            "total": self._r(total),
        }

    def calculate_equity_delivery(
        self,
        buy_price: float,
        sell_price: float,
        quantity: int,
        brokerage_per_order: float = 0.0,
    ) -> Dict[str, float]:
        """Calculate all fees for equity delivery trade.

        STT 0.1% on both buy and sell sides.
        """
        buy_turnover = buy_price * quantity
        sell_turnover = sell_price * quantity

        # Buy side charges
        buy_brokerage = brokerage_per_order
        buy_stt = buy_turnover * self.STT_DELIVERY_PCT
        buy_exchange = buy_turnover * self.EXCHANGE_TXN_PCT
        buy_gst = (buy_brokerage + buy_exchange) * self.GST_PCT
        buy_sebi = buy_turnover * self.SEBI_FEE_PCT
        buy_stamp = buy_turnover * self.STAMP_DUTY_BUY_PCT

        # Sell side charges
        sell_brokerage = brokerage_per_order
        sell_stt = sell_turnover * self.STT_DELIVERY_PCT
        sell_exchange = sell_turnover * self.EXCHANGE_TXN_PCT
        sell_gst = (sell_brokerage + sell_exchange) * self.GST_PCT
        sell_sebi = sell_turnover * self.SEBI_FEE_PCT
        sell_stamp = sell_turnover * self.STAMP_DUTY_SELL_PCT

        total_brokerage = buy_brokerage + sell_brokerage
        total_stt = buy_stt + sell_stt
        total_exchange = buy_exchange + sell_exchange
        total_gst = buy_gst + sell_gst
        total_sebi = buy_sebi + sell_sebi
        total_stamp = buy_stamp + sell_stamp
        total = total_brokerage + total_stt + total_exchange + total_gst + total_sebi + total_stamp

        return {
            "brokerage": self._r(total_brokerage),
            "stt": self._r(total_stt),
            "exchange_txn": self._r(total_exchange),
            "gst": self._r(total_gst),
            "sebi": self._r(total_sebi),
            "stamp_duty": self._r(total_stamp),
            "total": self._r(total),
        }

    def calculate_options(
        self,
        buy_premium: float,
        sell_premium: float,
        quantity: int,
        lot_size: int,
        brokerage_per_order: float = 20.0,
    ) -> Dict[str, float]:
        """Calculate all fees for options trade.

        STT 0.0625% on sell premium only.
        Exchange 0.05% on both sides of premium.
        """
        total_qty = quantity * lot_size
        buy_turnover = buy_premium * total_qty
        sell_turnover = sell_premium * total_qty

        # Buy side charges
        buy_brokerage = brokerage_per_order
        buy_exchange = buy_turnover * self.EXCHANGE_TXN_OPTIONS_PCT
        buy_gst = (buy_brokerage + buy_exchange) * self.GST_PCT
        buy_sebi = buy_turnover * self.SEBI_FEE_PCT
        buy_stamp = 0.0

        # Sell side charges
        sell_brokerage = brokerage_per_order
        sell_stt = sell_turnover * self.STT_OPTIONS_SELL_PCT
        sell_exchange = sell_turnover * self.EXCHANGE_TXN_OPTIONS_PCT
        sell_gst = (sell_brokerage + sell_exchange) * self.GST_PCT
        sell_sebi = sell_turnover * self.SEBI_FEE_PCT
        sell_stamp = 0.0

        total_brokerage = buy_brokerage + sell_brokerage
        total_stt = sell_stt
        total_exchange = buy_exchange + sell_exchange
        total_gst = buy_gst + sell_gst
        total_sebi = buy_sebi + sell_sebi
        total_stamp = 0.0
        total = total_brokerage + total_stt + total_exchange + total_gst + total_sebi + total_stamp

        return {
            "brokerage": self._r(total_brokerage),
            "stt": self._r(total_stt),
            "exchange_txn": self._r(total_exchange),
            "gst": self._r(total_gst),
            "sebi": self._r(total_sebi),
            "stamp_duty": self._r(total_stamp),
            "total": self._r(total),
        }

    def calculate_net_pnl(
        self,
        entry_price: float,
        exit_price: float,
        quantity: int,
        direction: str,
        segment: str = "EQ",
        mode: str = "intraday",
        lot_size: int = 1,
    ) -> Dict[str, float]:
        """Calculate net P&L after all fees.

        Args:
            entry_price: Price at which position was entered.
            exit_price: Price at which position was exited.
            quantity: Number of shares/lots.
            direction: 'LONG' or 'SHORT'.
            segment: 'EQ' for equity, 'OPT' for options.
            mode: 'intraday' or 'delivery'.
            lot_size: Lot size for options.

        Returns:
            Dict with gross_pnl, fees (total), and net_pnl.
        """
        if direction.upper() == "LONG":
            buy_price = entry_price
            sell_price = exit_price
        else:
            buy_price = exit_price
            sell_price = entry_price

        if segment.upper() == "OPT":
            fee_result = self.calculate_options(
                buy_premium=buy_price,
                sell_premium=sell_price,
                quantity=quantity,
                lot_size=lot_size,
                brokerage_per_order=self.brokerage_per_order,
            )
            gross_pnl = (sell_price - buy_price) * quantity * lot_size
        elif mode.lower() == "delivery":
            fee_result = self.calculate_equity_delivery(
                buy_price=buy_price,
                sell_price=sell_price,
                quantity=quantity,
                brokerage_per_order=0.0,
            )
            gross_pnl = (sell_price - buy_price) * quantity
        else:
            fee_result = self.calculate_equity_intraday(
                buy_price=buy_price,
                sell_price=sell_price,
                quantity=quantity,
                brokerage_per_order=self.brokerage_per_order,
            )
            gross_pnl = (sell_price - buy_price) * quantity

        fees = fee_result["total"]
        net_pnl = gross_pnl - fees

        return {
            "gross_pnl": self._r(gross_pnl),
            "fees": self._r(fees),
            "net_pnl": self._r(net_pnl),
            "fee_breakdown": fee_result,
        }
