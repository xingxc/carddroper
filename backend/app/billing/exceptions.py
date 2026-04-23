class InsufficientBalanceError(Exception):
    """Raised by billing.debit() when the user's balance is below the requested amount.

    Never an AppError — the chassis primitive raises a typed exception;
    the HTTP layer translates to an appropriate HTTP response if needed.
    """

    def __init__(self, user_id: int, balance_micros: int, requested_micros: int):
        self.user_id = user_id
        self.balance_micros = balance_micros
        self.requested_micros = requested_micros
        super().__init__(
            f"Insufficient balance for user {user_id}: "
            f"has {balance_micros} micros, requested {requested_micros} micros."
        )
