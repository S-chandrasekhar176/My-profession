import locale
from typing import Optional


# Try to set Indian locale; fall back to default if not available
try:
    locale.setlocale(locale.LC_ALL, 'en_IN.UTF-8')
except locale.Error:
    try:
        locale.setlocale(locale.LC_ALL, 'en_IN')
    except locale.Error:
        pass


def format_currency(value: float, show_sign: bool = False) -> str:
    """Format a number as Indian currency (₹1,23,456.78).

    Uses the Indian numbering system (lakhs, crores).
    If locale en_IN is not available, falls back to manual formatting.

    Args:
        value: The numeric value to format.
        show_sign: If True, prefix + for positive values.

    Returns:
        Formatted currency string like ₹1,23,456.78 or -₹1,23,456.78.
    """
    # HF-8 (v0.4.8): the previous implementation formatted the DECIMAL
    # FRACTION (0.70) with ".2f" and then spliced it back in with another
    # "." prefix, producing malformed strings like "₹39,900.0.70" for every
    # amount that had paise. Format the whole value once and split on the
    # "." so the decimal digits are always exactly the two characters after
    # the separator.
    sign = ""
    if value < 0:
        sign = "-"
        value = abs(value)
    elif show_sign and value > 0:
        sign = "+"

    whole = f"{value:.2f}"
    int_digits, _sep, dec_digits = whole.partition(".")

    # Format integer part in Indian system
    int_str = _format_indian_number(int(int_digits))

    # Add decimal part if non-zero
    if dec_digits.strip("0"):
        result = f"{int_str}.{dec_digits}"
    else:
        result = int_str

    return f"{sign}₹{result}"


def _format_indian_number(num: int) -> str:
    """Format an integer using the Indian numbering system.

    Indian grouping: first 3 digits from right, then groups of 2.
    Example: 12345678 -> 1,23,45,678
    """
    s = str(num)
    if len(s) <= 3:
        return s

    # Last 3 digits
    result = s[-3:]
    remaining = s[:-3]

    # Group remaining in pairs from right
    while len(remaining) > 2:
        result = remaining[-2:] + "," + result
        remaining = remaining[:-2]

    if remaining:
        result = remaining + "," + result

    return result


def format_pct(value: float, decimal_places: int = 2) -> str:
    """Format a percentage with sign.

    Args:
        value: Percentage value (e.g., 3.456 for 3.456%).
        decimal_places: Number of decimal places.

    Returns:
        Formatted string like +3.46% or -1.23%.
    """
    if value > 0:
        return f"+{value:.{decimal_places}f}%"
    elif value < 0:
        return f"{value:.{decimal_places}f}%"
    else:
        return f"0.{('0' * decimal_places)}%"


def format_duration(seconds: Optional[int]) -> str:
    """Format a duration in seconds to a human-readable string.

    Examples:
        45 -> "45s"
        150 -> "2m 30s"
        5400 -> "1h 30m"
        9365 -> "2h 36m 5s"

    Args:
        seconds: Duration in seconds. None returns "N/A".

    Returns:
        Formatted duration string.
    """
    if seconds is None:
        return "N/A"

    seconds = int(seconds)
    if seconds < 0:
        seconds = 0

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 or not parts:
        parts.append(f"{secs}s")

    return " ".join(parts)
