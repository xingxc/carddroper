"""Stripe object field extractors.

Centralizes "extract field X from a Stripe object Y" helpers so that Stripe
API version migrations (e.g., basil moving fields to nested locations) are
addressed in one place.

Each extractor:
- Tries the canonical (latest API version) path first
- Falls back to legacy paths for resilience across API version pinning
- Uses defensive dual-access (getattr OR dict.get) at each nesting layer
- Returns None if all paths fail (no exceptions on missing fields)
- Logs a warning at the call site if all paths fail (caller's responsibility)

Origin: ticket 0024.12 — basil API moved invoice.subscription to
parent.subscription_details.subscription, breaking handle_invoice_paid silently
because the test mocks reflected the old shape. This module is the discipline
anchor: future Stripe API migrations are single-file changes here.
"""


def _getattr_or_get(obj, key):
    """Defensive dual-access: attribute first, then dict-style get.

    Handles both MagicMock-style objects (attribute access) and real Stripe
    StripeObject instances (dict-backed; .get() is available).

    Returns None if the key is not found via either path.
    """
    val = getattr(obj, key, None)
    if val is None and hasattr(obj, "get"):
        val = obj.get(key)
    return val


def _is_valid_sub_id(val) -> bool:
    """Return True if val is a non-empty string that looks like a Stripe subscription ID.

    This guard prevents unrestricted MagicMock objects (used in tests that do NOT use
    spec=) from auto-vivifying nested attribute chains and returning a truthy MagicMock
    where a string is expected. Real Stripe subscription IDs are strings starting with
    'sub_'. We require at minimum that the value is a non-empty string.
    """
    return isinstance(val, str) and bool(val)


def extract_invoice_subscription_id(invoice) -> str | None:
    """Extract the subscription ID from a Stripe Invoice object.

    Tries four paths in order of preference (basil canonical first, legacy fallback):

    1. invoice.parent.subscription_details.subscription  (basil canonical — Stripe API 2025-03-31+)
    2. invoice.subscription                               (legacy top-level — older API versions)
    3. invoice.lines.data[0].parent.subscription_item_details.subscription  (per-line-item fallback)
    4. invoice.lines.data[0].parent.invoice_item_details.subscription       (less common line-item fallback)

    Uses defensive dual-access (getattr OR dict.get) at every nesting layer so this
    works with both attribute-style objects (MagicMock in tests) and real Stripe
    StripeObject instances (dict-backed at runtime).

    Only returns a value if it is a non-empty string — this prevents unrestricted
    MagicMock objects from auto-vivifying truthy non-string sentinels that would
    pass the truthiness check but fail as SQL parameters.

    Returns the subscription ID string if found, or None if all four paths miss.
    The caller is responsible for logging a warning when None is returned.

    This function is pure: no I/O, no logging, no side effects.
    """
    # Path 1: basil canonical — invoice.parent.subscription_details.subscription
    parent = _getattr_or_get(invoice, "parent")
    if parent is not None:
        sub_details = _getattr_or_get(parent, "subscription_details")
        if sub_details is not None:
            sub_id = _getattr_or_get(sub_details, "subscription")
            if _is_valid_sub_id(sub_id):
                return sub_id

    # Path 2: legacy top-level — invoice.subscription
    sub_id = _getattr_or_get(invoice, "subscription")
    if _is_valid_sub_id(sub_id):
        return sub_id

    # Paths 3 and 4: line-item fallbacks via invoice.lines.data[0].parent.*
    lines = _getattr_or_get(invoice, "lines")
    if lines is not None:
        lines_data = _getattr_or_get(lines, "data")
        if lines_data:
            try:
                first_line = lines_data[0]
                line_parent = _getattr_or_get(first_line, "parent")
                if line_parent is not None:
                    # Path 3: subscription_item_details.subscription
                    sub_item_details = _getattr_or_get(line_parent, "subscription_item_details")
                    if sub_item_details is not None:
                        sub_id = _getattr_or_get(sub_item_details, "subscription")
                        if _is_valid_sub_id(sub_id):
                            return sub_id

                    # Path 4: invoice_item_details.subscription
                    inv_item_details = _getattr_or_get(line_parent, "invoice_item_details")
                    if inv_item_details is not None:
                        sub_id = _getattr_or_get(inv_item_details, "subscription")
                        if _is_valid_sub_id(sub_id):
                            return sub_id
            except (IndexError, TypeError):
                pass

    return None
