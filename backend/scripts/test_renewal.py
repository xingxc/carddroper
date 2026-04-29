#!/usr/bin/env python3
"""Renewal cycle verification via Stripe Test Clocks.

Usage:
    python scripts/test_renewal.py            # advance 31 days, run assertions
    python scripts/test_renewal.py --days=15  # custom advance duration
    python scripts/test_renewal.py --dry-run  # show pre-state, do not advance

Reads fixture from backend/.test-clock-fixture.local. See
doc/operations/stripe-side-tests.md §Tier B for setup instructions.

Origin: ticket 0024.14.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Make `app` importable when this script is run as `python scripts/test_renewal.py`
# from the backend/ directory. Without this, sys.path[0] is `backend/scripts/`, so
# `from app.config import settings` fails with ImportError. The original 0024.14
# implementation caught this with `except Exception: pass`, silently swallowing the
# error and showing a misleading "STRIPE_SECRET_KEY is not set" message even when
# backend/.env had the value. Fixed by inserting backend/ at the front of sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---------------------------------------------------------------------------
# ANSI color helpers
# ---------------------------------------------------------------------------
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def _green(msg: str) -> str:
    return f"{GREEN}{msg}{RESET}"


def _red(msg: str) -> str:
    return f"{RED}{msg}{RESET}"


def _yellow(msg: str) -> str:
    return f"{YELLOW}{msg}{RESET}"


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

FIXTURE_PATH = Path(__file__).resolve().parent.parent / ".test-clock-fixture.local"
FIXTURE_EXAMPLE_PATH = (
    Path(__file__).resolve().parent.parent / ".test-clock-fixture.local.example"
)

REQUIRED_FIXTURE_KEYS = ("customer_id", "clock_id", "user_id", "subscription_id")


def _load_fixture() -> dict:
    if not FIXTURE_PATH.exists():
        print(
            _red(
                f"ERROR: fixture file not found at {FIXTURE_PATH}\n"
                f"  Copy {FIXTURE_EXAMPLE_PATH} to {FIXTURE_PATH} and fill in the values.\n"
                f"  See doc/operations/stripe-side-tests.md §Tier B for setup instructions."
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        fixture = json.loads(FIXTURE_PATH.read_text())
    except json.JSONDecodeError as exc:
        print(_red(f"ERROR: fixture file is not valid JSON: {exc}"), file=sys.stderr)
        sys.exit(1)

    missing = [k for k in REQUIRED_FIXTURE_KEYS if not fixture.get(k)]
    if missing:
        print(
            _red(
                f"ERROR: fixture file is missing required keys: {missing}\n"
                f"  Update {FIXTURE_PATH} and replace all REPLACE_ME placeholders."
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    return fixture


# ---------------------------------------------------------------------------
# DB helpers (async SQLAlchemy — chassis pattern)
# ---------------------------------------------------------------------------


async def _fetch_pre_state(
    user_id: int,
    subscription_id: str,
) -> tuple[dict, list[dict]]:
    """Return (subscription_row_dict, ledger_entries_list) for the given user."""
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models.balance_ledger import BalanceLedger
    from app.models.subscription import Subscription

    async with AsyncSessionLocal() as db:
        # Subscription row
        result = await db.execute(
            select(Subscription).where(
                Subscription.stripe_subscription_id == subscription_id,
                Subscription.user_id == user_id,
            )
        )
        sub_row = result.scalar_one_or_none()
        if sub_row is None:
            return {}, []

        sub_snapshot = {
            "id": sub_row.id,
            "user_id": sub_row.user_id,
            "stripe_subscription_id": sub_row.stripe_subscription_id,
            "current_period_start": sub_row.current_period_start,
            "current_period_end": sub_row.current_period_end,
            "grant_micros": sub_row.grant_micros,
            "status": sub_row.status,
        }

        # Balance ledger entries for this user
        result = await db.execute(
            select(BalanceLedger)
            .where(BalanceLedger.user_id == user_id)
            .order_by(BalanceLedger.id.asc())
        )
        ledger_rows = result.scalars().all()
        ledger_snapshot = [
            {
                "id": row.id,
                "amount_micros": row.amount_micros,
                "reason": row.reason,
                "stripe_event_id": row.stripe_event_id,
                "created_at": row.created_at,
            }
            for row in ledger_rows
        ]

    return sub_snapshot, ledger_snapshot


# ---------------------------------------------------------------------------
# Stripe helpers
# ---------------------------------------------------------------------------


def _setup_stripe() -> None:
    """Configure stripe SDK from environment. Fails loudly if key is missing."""
    import stripe

    secret_key = os.environ.get("STRIPE_SECRET_KEY")
    if not secret_key:
        # Fall back to loading from app.config (picks up backend/.env via pydantic-settings).
        # Surface ImportError loudly — silent swallow was the original 0024.14 UX bug.
        try:
            from app.config import settings

            if settings.STRIPE_SECRET_KEY:
                # STRIPE_SECRET_KEY may be SecretStr (pydantic) or plain str depending
                # on how config.py is typed. Handle both — fall back to the raw value
                # if get_secret_value() isn't available.
                raw = settings.STRIPE_SECRET_KEY
                secret_key = raw.get_secret_value() if hasattr(raw, "get_secret_value") else raw
        except ImportError as e:
            print(
                _red(
                    f"ERROR: could not import app.config to read STRIPE_SECRET_KEY: {e}\n"
                    "  This usually means the script was run without backend/ on sys.path.\n"
                    "  Run from the backend/ directory: cd backend && .venv/bin/python scripts/test_renewal.py"
                ),
                file=sys.stderr,
            )
            sys.exit(1)

    if not secret_key:
        print(
            _red(
                "ERROR: STRIPE_SECRET_KEY is not set.\n"
                "  Export STRIPE_SECRET_KEY=sk_test_... or ensure backend/.env contains it."
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    stripe.api_key = secret_key


def _target_frozen_time(days: int) -> int:
    """Return Unix timestamp 'days' days from now (UTC)."""
    return int(time.time()) + days * 86400


def _advance_clock(clock_id: str, frozen_time: int) -> None:
    """Advance a Stripe test clock to the given Unix timestamp.

    SDK call: instance.advance(frozen_time=<int>).
    The SDK triggers a POST to /test_helpers/test_clocks/{id}/advance.
    Advancement is asynchronous on Stripe's side; the call returns when
    Stripe has queued the operation. Webhooks fire once advancement reaches
    the target time.
    """
    import stripe

    clock = stripe.test_helpers.TestClock.retrieve(clock_id)
    clock.advance(frozen_time=frozen_time)


def _get_grants_flag() -> bool:
    """Read BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER from env or app.config."""
    env_val = os.environ.get("BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER")
    if env_val is not None:
        return env_val.strip().lower() in ("1", "true", "yes")
    try:
        from app.config import settings

        return settings.BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER
    except ImportError as e:
        print(
            _red(
                f"ERROR: could not import app.config to read BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER: {e}\n"
                "  Run from backend/ directory or set BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER explicitly."
            ),
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------


def _assert_renewal(
    pre_sub: dict,
    post_sub: dict,
    pre_ledger: list[dict],
    post_ledger: list[dict],
    grants_flag: bool,
) -> list[str]:
    """
    Run all renewal invariants. Returns list of failure messages (empty = all pass).

    Invariants checked:
      1. current_period_start advanced (post > pre)
      2. current_period_end advanced (post > pre)
      3. When grants_flag=True: exactly ONE new subscription_reset ledger entry
      4. When grants_flag=True: new entry amount_micros == post row.grant_micros
      5. When grants_flag=True: NOT two new subscription_reset entries (idempotency)
      6. When grants_flag=False: no new subscription_reset or subscription_grant entries
    """
    failures: list[str] = []

    pre_start = pre_sub.get("current_period_start")
    post_start = post_sub.get("current_period_start")
    pre_end = pre_sub.get("current_period_end")
    post_end = post_sub.get("current_period_end")

    # Invariant 1: period_start advanced
    if pre_start is None or post_start is None:
        failures.append(
            f"FAIL [period_start]: pre={pre_start!r}, post={post_start!r} — "
            "expected both to be non-NULL after renewal"
        )
    elif post_start <= pre_start:
        failures.append(
            f"FAIL [period_start]: post ({post_start}) <= pre ({pre_start}) — "
            "period did not advance"
        )

    # Invariant 2: period_end advanced
    if pre_end is None or post_end is None:
        failures.append(
            f"FAIL [period_end]: pre={pre_end!r}, post={post_end!r} — "
            "expected both to be non-NULL after renewal"
        )
    elif post_end <= pre_end:
        failures.append(
            f"FAIL [period_end]: post ({post_end}) <= pre ({pre_end}) — "
            "period end did not advance"
        )

    # Find new ledger entries (entries that exist in post but not in pre)
    pre_ids = {row["id"] for row in pre_ledger}
    new_entries = [row for row in post_ledger if row["id"] not in pre_ids]
    new_reset_entries = [e for e in new_entries if e["reason"] == "subscription_reset"]
    new_grant_entries = [
        e for e in new_entries if e["reason"] in ("subscription_reset", "subscription_grant")
    ]

    if grants_flag:
        # Invariant 3: exactly one new subscription_reset entry
        if len(new_reset_entries) == 0:
            failures.append(
                "FAIL [ledger_reset]: grants_flag=True but no new subscription_reset ledger "
                "entry found after renewal"
            )
        elif len(new_reset_entries) > 1:
            # Invariant 5: idempotency — must not post two entries
            failures.append(
                f"FAIL [ledger_idempotency]: grants_flag=True but found "
                f"{len(new_reset_entries)} new subscription_reset entries — "
                f"expected exactly 1 (idempotency breach)"
            )
        else:
            # Invariant 4: amount_micros matches row.grant_micros
            grant_micros_post = post_sub.get("grant_micros")
            entry_amount = new_reset_entries[0]["amount_micros"]
            if grant_micros_post is None:
                failures.append(
                    "FAIL [grant_amount]: post subscription row has NULL grant_micros"
                )
            elif entry_amount != grant_micros_post:
                failures.append(
                    f"FAIL [grant_amount]: new subscription_reset entry amount_micros="
                    f"{entry_amount} != row.grant_micros={grant_micros_post}"
                )
    else:
        # Invariant 6: no new grant-type entries when flag=False
        if new_grant_entries:
            reasons = [e["reason"] for e in new_grant_entries]
            failures.append(
                f"FAIL [no_grant]: grants_flag=False but new ledger entries found with "
                f"reasons={reasons} — expected no grant activity"
            )

    return failures


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def _run(args: argparse.Namespace) -> int:
    fixture = _load_fixture()
    customer_id: str = fixture["customer_id"]
    clock_id: str = fixture["clock_id"]
    user_id: int = int(fixture["user_id"])
    subscription_id: str = fixture["subscription_id"]

    _setup_stripe()
    grants_flag = _get_grants_flag()

    print(f"\n{'='*60}")
    print("Renewal cycle verification — Stripe Test Clocks")
    print(f"{'='*60}")
    print(f"  customer_id:   {customer_id}")
    print(f"  clock_id:      {clock_id}")
    print(f"  user_id:       {user_id}")
    print(f"  subscription:  {subscription_id}")
    print(f"  advance days:  {args.days}")
    print(f"  dry_run:       {args.dry_run}")
    print(f"  grants_flag:   BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER={grants_flag}")
    print(f"{'='*60}\n")

    # ------------------------------------------------------------------
    # 1. Capture pre-renewal DB state
    # ------------------------------------------------------------------
    print("Capturing pre-renewal DB state...")
    pre_sub, pre_ledger = await _fetch_pre_state(user_id, subscription_id)

    if not pre_sub:
        print(
            _red(
                f"ERROR: no subscription row found in DB for "
                f"user_id={user_id}, subscription_id={subscription_id}\n"
                f"  Verify the fixture IDs match the DB and the subscription is active."
            ),
            file=sys.stderr,
        )
        return 1

    print(f"  current_period_start: {pre_sub['current_period_start']}")
    print(f"  current_period_end:   {pre_sub['current_period_end']}")
    print(f"  grant_micros:         {pre_sub['grant_micros']}")
    print(f"  status:               {pre_sub['status']}")
    print(f"  ledger entry count:   {len(pre_ledger)}")

    if pre_ledger:
        last = pre_ledger[-1]
        print(
            f"  last ledger entry:    id={last['id']} reason={last['reason']} "
            f"amount={last['amount_micros']}"
        )

    if args.dry_run:
        target_ts = _target_frozen_time(args.days)
        target_dt = datetime.fromtimestamp(target_ts, tz=timezone.utc)
        print(
            _yellow(
                f"\n[dry-run] Would advance clock {clock_id} to "
                f"{target_dt.isoformat()} (Unix: {target_ts})"
            )
        )
        print(_yellow("[dry-run] Skipping clock advance and assertions."))
        return 0

    # ------------------------------------------------------------------
    # 2. Advance the test clock
    # ------------------------------------------------------------------
    target_ts = _target_frozen_time(args.days)
    target_dt = datetime.fromtimestamp(target_ts, tz=timezone.utc)
    print(f"\nAdvancing test clock by {args.days} days...")
    print(f"  Target time: {target_dt.isoformat()} (Unix: {target_ts})")

    try:
        await asyncio.to_thread(_advance_clock, clock_id, target_ts)
    except Exception as exc:
        print(_red(f"\nERROR advancing test clock: {exc}"), file=sys.stderr)
        return 1

    print("  Clock advance queued (Stripe processes this asynchronously).")

    # ------------------------------------------------------------------
    # 3. Wait for webhook delivery
    # ------------------------------------------------------------------
    wait_seconds = args.wait
    print(
        f"\nWaiting {wait_seconds}s for webhook delivery...\n"
        f"  (Stripe event delivery via stripe listen typically takes 5–15s;\n"
        f"   {wait_seconds}s is conservative for local stripe listen forwarding.)"
    )
    await asyncio.sleep(wait_seconds)

    # ------------------------------------------------------------------
    # 4. Capture post-renewal DB state
    # ------------------------------------------------------------------
    print("Capturing post-renewal DB state...")
    post_sub, post_ledger = await _fetch_pre_state(user_id, subscription_id)

    if not post_sub:
        print(
            _red("ERROR: subscription row disappeared after clock advance."),
            file=sys.stderr,
        )
        return 1

    print(f"  current_period_start: {post_sub['current_period_start']}")
    print(f"  current_period_end:   {post_sub['current_period_end']}")
    print(f"  grant_micros:         {post_sub['grant_micros']}")
    print(f"  status:               {post_sub['status']}")
    print(f"  ledger entry count:   {len(post_ledger)}")

    if post_ledger:
        last = post_ledger[-1]
        print(
            f"  last ledger entry:    id={last['id']} reason={last['reason']} "
            f"amount={last['amount_micros']}"
        )

    # ------------------------------------------------------------------
    # 5. Assert renewal invariants
    # ------------------------------------------------------------------
    print("\nRunning assertions...")
    failures = _assert_renewal(pre_sub, post_sub, pre_ledger, post_ledger, grants_flag)

    if failures:
        print(_red(f"\n{'='*60}"))
        print(_red("RENEWAL VERIFICATION FAILED"))
        print(_red(f"{'='*60}"))
        for f in failures:
            print(_red(f"  {f}"))
        print(
            _red(
                "\n  Next step: file a follow-up ticket using the audit template.\n"
                "  Check: docker-compose logs backend | grep 'subscription_cycle'"
            )
        )
        return 1
    else:
        print(_green(f"\n{'='*60}"))
        print(_green("RENEWAL VERIFICATION PASSED"))
        print(_green(f"{'='*60}"))
        print(_green(f"  Period advanced: {pre_sub['current_period_start']} → {post_sub['current_period_start']}"))
        print(_green(f"  Period end:      {pre_sub['current_period_end']} → {post_sub['current_period_end']}"))
        if grants_flag:
            print(_green(f"  Ledger entries:  {len(pre_ledger)} → {len(post_ledger)} (+1 subscription_reset)"))
            pre_ids = {row["id"] for row in pre_ledger}
            new_entries = [row for row in post_ledger if row["id"] not in pre_ids]
            new_reset = [e for e in new_entries if e["reason"] == "subscription_reset"]
            if new_reset:
                print(_green(f"  Reset amount:    {new_reset[0]['amount_micros']} micros == grant_micros"))
        else:
            print(_green("  Ledger unchanged (grants_flag=False, correct)"))
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the renewal cycle (subscription_cycle) branch of handle_invoice_paid\n"
            "by advancing a Stripe Test Clock and asserting DB state pre/post renewal.\n\n"
            "Reads fixture from backend/.test-clock-fixture.local.\n"
            "See doc/operations/stripe-side-tests.md §Tier B for setup instructions."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--days",
        type=int,
        default=31,
        help="Number of days to advance the test clock (default: 31)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show pre-state and target time; do not advance the clock or run assertions",
    )
    parser.add_argument(
        "--wait",
        type=int,
        default=15,
        help="Seconds to wait after clock advance before capturing post-state (default: 15)",
    )
    args = parser.parse_args()

    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
