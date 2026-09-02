"""Black-Scholes Greeks calculator for European-style options.

Provides Delta, Gamma, Theta, Vega, and Implied Volatility calculations
for NSE index and stock options.
"""
import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

# Standard trading year parameters
_DAYS_PER_YEAR = 365.0
_TRADING_DAYS_PER_YEAR = 252.0

# Small epsilon to avoid log(0) or division by zero
_EPS = 1e-10


class GreeksCalculator:
    """Black-Scholes option Greeks calculator.

    All methods accept standard Black-Scholes inputs and return
    float values. Works for both CE and PE.
    """

    def __init__(
        self,
        risk_free_rate: float = 0.07,  # ~7% RBI repo rate
        trading_days: bool = True,
    ):
        self.r = risk_free_rate
        self.days_per_year = _TRADING_DAYS_PER_YEAR if trading_days else _DAYS_PER_YEAR

    # ------------------------------------------------------------------
    # Core Black-Scholes helpers
    # ------------------------------------------------------------------

    def _d1(self, S: float, K: float, T: float, sigma: float) -> float:
        """Compute d1 component.

        Args:
            S: Spot price.
            K: Strike price.
            T: Time to expiry in years.
            sigma: Implied volatility (annualised, e.g. 0.15 for 15%).
        """
        if sigma < _EPS or T < _EPS:
            return 0.0
        numerator = math.log(S / K) + (self.r + sigma ** 2 / 2.0) * T
        denominator = sigma * math.sqrt(T)
        return numerator / denominator

    def _d2(self, d1: float, T: float, sigma: float) -> float:
        """Compute d2 = d1 - sigma * sqrt(T)."""
        if sigma < _EPS or T < _EPS:
            return 0.0
        return d1 - sigma * math.sqrt(T)

    @staticmethod
    def _normal_pdf(x: float) -> float:
        """Standard normal probability density function."""
        return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)

    @staticmethod
    def _normal_cdf(x: float) -> float:
        """Standard normal cumulative distribution function.

        Uses the Abramowitz & Stegun approximation (max error < 1.5e-7).
        """
        if x < -8.0:
            return 0.0
        if x > 8.0:
            return 1.0

        # Coefficients
        a1 = 0.254829592
        a2 = -0.284496736
        a3 = 1.421413741
        a4 = -1.453152027
        a5 = 1.061405429
        p = 0.3275911

        sign = 1.0 if x >= 0 else -1.0
        x_abs = abs(x)
        t = 1.0 / (1.0 + p * x_abs)
        y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x_abs * x_abs / 2.0)

        return 0.5 * (1.0 + sign * y)

    # ------------------------------------------------------------------
    # Public Greeks methods
    # ------------------------------------------------------------------

    def calculate_delta(self, S: float, K: float, T: float, sigma: float, option_type: str = "CE") -> float:
        """Calculate option delta.

        Args:
            S: Spot price.
            K: Strike price.
            T: Time to expiry in years.
            sigma: Implied volatility (annualised).
            option_type: 'CE' for call, 'PE' for put.

        Returns:
            Delta value between -1.0 and 1.0.
        """
        d1 = self._d1(S, K, T, sigma)
        if option_type.upper() == "PE":
            return round(self._normal_cdf(d1) - 1.0, 6)
        return round(self._normal_cdf(d1), 6)

    def calculate_gamma(self, S: float, K: float, T: float, sigma: float) -> float:
        """Calculate option gamma.

        Gamma is the same for calls and puts.

        Returns:
            Gamma value (per 1 rupee move in underlying).
        """
        if S < _EPS:
            return 0.0
        d1 = self._d1(S, K, T, sigma)
        numerator = self._normal_pdf(d1)
        denominator = S * sigma * math.sqrt(T)
        if denominator < _EPS:
            return 0.0
        return round(numerator / denominator, 6)

    def calculate_theta(self, S: float, K: float, T: float, sigma: float, option_type: str = "CE") -> float:
        """Calculate option theta (per day).

        Returns:
            Theta value (premium decay per day, typically negative).
        """
        if T < _EPS:
            return 0.0

        d1 = self._d1(S, K, T, sigma)
        d2 = self._d2(d1, T, sigma)
        sqrt_t = math.sqrt(T)

        term1 = -(S * self._normal_pdf(d1) * sigma) / (2.0 * sqrt_t)

        if option_type.upper() == "PE":
            term2 = self.r * K * math.exp(-self.r * T) * (self._normal_cdf(d1) - 1.0)
        else:
            term2 = self.r * K * math.exp(-self.r * T) * self._normal_cdf(d2)

        theta_annual = term1 + term2
        theta_daily = theta_annual / self.days_per_year
        return round(theta_daily, 6)

    def calculate_vega(self, S: float, K: float, T: float, sigma: float) -> float:
        """Calculate option vega (per 1% change in IV).

        Vega is the same for calls and puts.

        Returns:
            Vega value per 1% IV change (i.e. multiply by 0.01 for
            premium change per 1 vol point).
        """
        if T < _EPS:
            return 0.0
        d1 = self._d1(S, K, T, sigma)
        vega = S * self._normal_pdf(d1) * math.sqrt(T)
        # Scale to per-1%-IV-change
        return round(vega * 0.01, 6)

    def calculate_iv(
        self,
        market_price: float,
        S: float,
        K: float,
        T: float,
        option_type: str = "CE",
        max_iterations: int = 100,
        tolerance: float = 1e-6,
    ) -> float:
        """Calculate implied volatility using Newton-Raphson.

        Args:
            market_price: Observed option premium in the market.
            S: Spot price.
            K: Strike price.
            T: Time to expiry in years.
            option_type: 'CE' or 'PE'.
            max_iterations: Maximum Newton-Raphson iterations.
            tolerance: Convergence tolerance.

        Returns:
            Implied volatility (annualised, e.g. 0.18 for 18% IV).
            Returns 0.0 if convergence fails.
        """
        if market_price <= 0 or S <= 0 or K <= 0 or T <= 0:
            return 0.0

        # Initial guess: use 20% IV
        sigma = 0.20

        for _ in range(max_iterations):
            # Theoretical price using current sigma
            theoretical = self._theoretical_price(S, K, T, sigma, option_type)

            # Vega for denominator
            vega = S * self._normal_pdf(self._d1(S, K, T, sigma)) * math.sqrt(T)
            if vega < _EPS:
                break

            # Price difference
            diff = theoretical - market_price

            # Check convergence
            if abs(diff) < tolerance:
                return round(sigma, 6)

            # Newton step
            sigma = sigma - diff / vega
            sigma = max(0.001, min(sigma, 5.0))  # Clamp to sane range

        # If not converged, return the best estimate
        logger.debug("IV calculation did not converge for K=%s S=%s", K, S)
        return round(sigma, 6)

    # ------------------------------------------------------------------
    # Theoretical price (for IV calculation)
    # ------------------------------------------------------------------

    def _theoretical_price(self, S: float, K: float, T: float, sigma: float, option_type: str = "CE") -> float:
        """Calculate Black-Scholes theoretical option price."""
        if T < _EPS or sigma < _EPS:
            # At expiry: intrinsic value only
            if option_type.upper() == "PE":
                return max(K - S, 0.0)
            return max(S - K, 0.0)

        d1 = self._d1(S, K, T, sigma)
        d2 = self._d2(d1, T, sigma)

        if option_type.upper() == "PE":
            price = K * math.exp(-self.r * T) * self._normal_cdf(-d2) - S * self._normal_cdf(-d1)
        else:
            price = S * self._normal_cdf(d1) - K * math.exp(-self.r * T) * self._normal_cdf(d2)

        return max(price, 0.0)

    # ------------------------------------------------------------------
    # Convenience: all greeks at once
    # ------------------------------------------------------------------

    def all_greeks(
        self,
        S: float,
        K: float,
        T: float,
        sigma: float,
        option_type: str = "CE",
    ) -> dict:
        """Return a dict of all five greeks + theoretical price.

        Returns:
            {delta, gamma, theta, vega, iv, theoretical_price}
        """
        return {
            "delta": self.calculate_delta(S, K, T, sigma, option_type),
            "gamma": self.calculate_gamma(S, K, T, sigma),
            "theta": self.calculate_theta(S, K, T, sigma, option_type),
            "vega": self.calculate_vega(S, K, T, sigma),
            "theoretical_price": round(self._theoretical_price(S, K, T, sigma, option_type), 2),
        }
