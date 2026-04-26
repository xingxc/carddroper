import logging

logger = logging.getLogger(__name__)


def format_balance(micros: int) -> str:
    """Format a microdollar balance as a USD string per the chassis display policy.

    Display policy:
    - >= $0.01 (>= 10_000 micros): 2 decimal places  -> "$1.23"
    - 0 < micros < $0.01 (< 10_000 micros): 4 decimal places -> "$0.0034"
    - = 0: "$0.00"

    Pure function; no I/O.
    """
    if micros == 0:
        return "$0.00"
    dollars = micros / 1_000_000
    if micros >= 10_000:
        return f"${dollars:.2f}"
    return f"${dollars:.4f}"


def format_price(amount_cents: int, currency: str, interval: str, interval_count: int = 1) -> str:
    """Display a Stripe Price as a human-readable string.

    Examples:
        (999, "usd", "month")      -> "$9.99/month"
        (1000, "usd", "month")     -> "$10/month"   (whole dollars, no decimal)
        (99000, "usd", "year")     -> "$990/year"
        (1500, "usd", "month", 3)  -> "$15 every 3 months"
        (50, "usd", "month")       -> "$0.50/month"

    USD-only per chassis BILLING_CURRENCY for v1. Non-USD currencies log a warning
    and fall back to the "$" prefix.

    interval_count must be >= 1. Values < 1 are coerced to 1 with a warning.
    """
    if currency.lower() != "usd":
        logger.warning(
            "format_price_non_usd_currency",
            extra={"currency": currency, "fallback": "usd_display"},
        )

    if interval_count < 1:
        logger.warning(
            "format_price_invalid_interval_count",
            extra={"interval_count": interval_count, "coerced_to": 1},
        )
        interval_count = 1

    dollars = amount_cents / 100
    # Whole-dollar amounts render without decimals; sub-dollar uses 2 decimal places.
    if amount_cents % 100 == 0:
        amount_str = f"${int(dollars)}"
    else:
        amount_str = f"${dollars:.2f}"

    if interval_count == 1:
        return f"{amount_str}/{interval}"
    else:
        return f"{amount_str} every {interval_count} {interval}s"
