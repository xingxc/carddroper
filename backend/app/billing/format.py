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
