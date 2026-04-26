"""Unit tests for app.billing.format — format_balance and format_price.

Pure-function tests; no DB or Stripe involved.

Ticket 0024.1 Phase 0a — format_price coverage.
"""

import logging

from app.billing.format import format_balance, format_price


# ---------------------------------------------------------------------------
# format_balance (existing, regression coverage)
# ---------------------------------------------------------------------------


def test_format_balance_zero():
    assert format_balance(0) == "$0.00"


def test_format_balance_one_dollar():
    assert format_balance(1_000_000) == "$1.00"


def test_format_balance_sub_cent():
    assert format_balance(3_400) == "$0.0034"


def test_format_balance_ge_one_cent():
    assert format_balance(10_000) == "$0.01"


# ---------------------------------------------------------------------------
# format_price — core cases from ticket §Acceptance Phase 0a item 1
# ---------------------------------------------------------------------------


def test_format_price_sub_dollar_two_decimals():
    """999 cents = $9.99 → '$9.99/month'."""
    assert format_price(999, "usd", "month") == "$9.99/month"


def test_format_price_whole_dollars_no_decimal():
    """1000 cents = $10 → '$10/month' (no .00)."""
    assert format_price(1000, "usd", "month") == "$10/month"


def test_format_price_large_year():
    """99000 cents = $990 → '$990/year'."""
    assert format_price(99000, "usd", "year") == "$990/year"


def test_format_price_interval_count_gt_one():
    """1500 cents + interval_count=3 → '$15 every 3 months'."""
    assert format_price(1500, "usd", "month", 3) == "$15 every 3 months"


def test_format_price_sub_dollar_50_cents():
    """50 cents → '$0.50/month'."""
    assert format_price(50, "usd", "month") == "$0.50/month"


def test_format_price_non_usd_logs_warning_and_returns_dollar_prefix(caplog):
    """Non-USD currency logs warning + still returns '$' prefix (Design decision #6)."""
    with caplog.at_level(logging.WARNING, logger="app.billing.format"):
        result = format_price(999, "eur", "month")

    assert result == "$9.99/month"
    assert any("format_price_non_usd_currency" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# format_price — edge cases
# ---------------------------------------------------------------------------


def test_format_price_zero_amount():
    """0 cents → '$0/month'."""
    assert format_price(0, "usd", "month") == "$0/month"


def test_format_price_interval_count_zero_coerced_to_one(caplog):
    """interval_count=0 is invalid; coerce to 1 with a warning."""
    with caplog.at_level(logging.WARNING, logger="app.billing.format"):
        result = format_price(999, "usd", "month", 0)

    # Coerced to 1 → standard '/month' form
    assert result == "$9.99/month"
    assert any("format_price_invalid_interval_count" in r.message for r in caplog.records)


def test_format_price_day_interval():
    """Interval 'day' is supported."""
    assert format_price(100, "usd", "day") == "$1/day"


def test_format_price_week_interval():
    """Interval 'week' is supported."""
    assert format_price(500, "usd", "week") == "$5/week"


def test_format_price_interval_count_two_uses_plural_interval():
    """interval_count=2 → 'every 2 <interval>s' form."""
    assert format_price(2000, "usd", "week", 2) == "$20 every 2 weeks"
