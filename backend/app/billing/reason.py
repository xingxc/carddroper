from enum import Enum


class Reason(str, Enum):
    """Chassis-closed vocabulary for balance_ledger.reason.

    Project layers do not add reason values. Project-specific debits are
    identified by ref_type + ref_id, not by reason.
    """

    TOPUP = "topup"
    SUBSCRIPTION_GRANT = "subscription_grant"
    SUBSCRIPTION_RESET = "subscription_reset"
    SIGNUP_BONUS = "signup_bonus"
    VERIFY_BONUS = "verify_bonus"
    DEBIT = "debit"
    REFUND = "refund"
    ADJUSTMENT = "adjustment"
