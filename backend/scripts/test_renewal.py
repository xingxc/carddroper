#!/usr/bin/env python3
"""Renewal cycle verification via Stripe Test Clocks.

Usage:
    python scripts/test_renewal.py                  # advance 31 days, run assertions (success path)
    python scripts/test_renewal.py --days=15        # custom advance duration
    python scripts/test_renewal.py --dry-run        # show pre-state, do not advance
    python scripts/test_renewal.py --simulate-decline  # swap PM to failing card; assert past_due
    python scripts/test_renewal.py --simulate-decline --no-restore-active  # leave sub in past_due

Reads fixture from backend/.test-clock-fixture.local. See
doc/operations/stripe-side-tests.md §Tier B for setup instructions.

Origin: ticket 0024.14 (success path). Extended by ticket 0024.15 (failure path).
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


def _assert_decline(
    pre_sub: dict,
    post_sub: dict,
    pre_ledger: list[dict],
    post_ledger: list[dict],
) -> list[str]:
    """
    Run all decline-path invariants. Returns list of failure messages (empty = all pass).

    Invariants checked (inverted from _assert_renewal):
      1. status changed from pre-state to 'past_due' (active → past_due specifically)
      2. current_period_start UNCHANGED (post == pre)
      3. current_period_end UNCHANGED (post == pre)
      4. grant_micros UNCHANGED (post == pre)
      5. NO new subscription_reset ledger entry posted
      6. NO new subscription_grant ledger entry posted
    """
    failures: list[str] = []

    pre_start = pre_sub.get("current_period_start")
    post_start = post_sub.get("current_period_start")
    pre_end = pre_sub.get("current_period_end")
    post_end = post_sub.get("current_period_end")
    pre_status = pre_sub.get("status")
    post_status = post_sub.get("status")
    pre_grant = pre_sub.get("grant_micros")
    post_grant = post_sub.get("grant_micros")

    # Invariant 1: status flipped to 'past_due'
    if post_status != "past_due":
        failures.append(
            f"FAIL [status]: post status={post_status!r}, expected 'past_due' "
            f"(pre status was {pre_status!r})"
        )

    # Invariant 2: current_period_start UNCHANGED
    if pre_start != post_start:
        failures.append(
            f"FAIL [period_start_unchanged]: pre={pre_start!r} != post={post_start!r} — "
            "period_start must not change on payment failure (Path B)"
        )

    # Invariant 3: current_period_end UNCHANGED
    if pre_end != post_end:
        failures.append(
            f"FAIL [period_end_unchanged]: pre={pre_end!r} != post={post_end!r} — "
            "period_end must not change on payment failure (Path B)"
        )

    # Invariant 4: grant_micros UNCHANGED
    if pre_grant != post_grant:
        failures.append(
            f"FAIL [grant_micros_unchanged]: pre={pre_grant!r} != post={post_grant!r} — "
            "grant_micros must not change on payment failure (Path B)"
        )

    # Find new ledger entries (entries in post but not in pre)
    pre_ids = {row["id"] for row in pre_ledger}
    new_entries = [row for row in post_ledger if row["id"] not in pre_ids]
    new_reset_entries = [e for e in new_entries if e["reason"] == "subscription_reset"]
    new_grant_entries = [e for e in new_entries if e["reason"] == "subscription_grant"]

    # Invariant 5: NO new subscription_reset entry
    if new_reset_entries:
        failures.append(
            f"FAIL [no_reset_entry]: found {len(new_reset_entries)} new subscription_reset "
            "ledger entries — payment failure must NOT post a reset grant"
        )

    # Invariant 6: NO new subscription_grant entry
    if new_grant_entries:
        failures.append(
            f"FAIL [no_grant_entry]: found {len(new_grant_entries)} new subscription_grant "
            "ledger entries — payment failure must NOT post a grant"
        )

    return failures


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def _run_success_path(
    args: argparse.Namespace,
    customer_id: str,
    clock_id: str,
    user_id: int,
    subscription_id: str,
    grants_flag: bool,
) -> int:
    """Execute the renewal success-path verification (0024.14 original logic)."""

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


async def _run_decline_path(
    args: argparse.Namespace,
    customer_id: str,
    clock_id: str,
    user_id: int,
    subscription_id: str,
) -> int:
    """Execute the renewal failure-path verification (0024.15).

    Flow:
      1. Read sub's current default_payment_method from Stripe → original_pm_id
      2. Attach pm_card_chargeCustomerFail to the test customer
      3. Modify sub's default_payment_method to pm_card_chargeCustomerFail
      4. Sleep 5s for the customer.subscription.updated webhook to settle
      5. Capture pre-state DB
      6. Advance test clock by args.days days
      7. Sleep args.wait seconds for webhook delivery
      8. Capture post-state DB
      9. Run six failure-path assertions
      finally: restore default_payment_method=original_pm_id (always)
      If args.restore_active: attempt Invoice.pay(failed_invoice_id) to recover sub
    """
    import stripe

    FAIL_PM = "pm_card_chargeCustomerFail"

    # ------------------------------------------------------------------
    # 1. Retrieve current sub to get original default_payment_method
    # ------------------------------------------------------------------
    print(f"\nRetrieving subscription {subscription_id} from Stripe...")
    try:
        sub = await asyncio.to_thread(
            stripe.Subscription.retrieve,
            subscription_id,
            expand=["default_payment_method"],
        )
    except Exception as exc:
        print(_red(f"ERROR retrieving subscription from Stripe: {exc}"), file=sys.stderr)
        return 1

    # default_payment_method may be a PaymentMethod object or a bare string ID
    dpm = sub.get("default_payment_method")
    if dpm is None:
        original_pm_id = None
    elif isinstance(dpm, str):
        original_pm_id = dpm
    else:
        # Expanded PaymentMethod object
        original_pm_id = dpm.get("id")

    print(f"  original default_payment_method: {original_pm_id!r}")

    # ------------------------------------------------------------------
    # 2. Swap to the failing test PM (with finally-restore)
    # ------------------------------------------------------------------
    failed_invoice_id: str | None = None

    try:
        # Attach the fail PM to the test customer
        print(f"\nAttaching {FAIL_PM} to customer {customer_id}...")
        try:
            await asyncio.to_thread(
                stripe.PaymentMethod.attach,
                FAIL_PM,
                customer=customer_id,
            )
            print(f"  Attached {FAIL_PM} successfully.")
        except Exception as exc:
            print(_red(f"ERROR attaching {FAIL_PM}: {exc}"), file=sys.stderr)
            return 1

        # Set the fail PM as the subscription's default
        print(f"\nSetting subscription default_payment_method to {FAIL_PM}...")
        try:
            await asyncio.to_thread(
                stripe.Subscription.modify,
                subscription_id,
                default_payment_method=FAIL_PM,
            )
            print(f"  Subscription default_payment_method → {FAIL_PM}")
        except Exception as exc:
            print(
                _red(f"ERROR modifying subscription default_payment_method: {exc}"),
                file=sys.stderr,
            )
            return 1

        # Wait for the customer.subscription.updated webhook from the PM swap to settle
        print("\nWaiting 5s for PM-swap webhook to settle...")
        await asyncio.sleep(5)

        # ------------------------------------------------------------------
        # 3. Capture pre-state DB (post-PM-swap, pre-clock-advance)
        # ------------------------------------------------------------------
        print("\nCapturing pre-state DB (post-PM-swap, pre-clock-advance)...")
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
        # 4. Advance the test clock
        # ------------------------------------------------------------------
        target_ts = _target_frozen_time(args.days)
        target_dt = datetime.fromtimestamp(target_ts, tz=timezone.utc)
        print(f"\nAdvancing test clock by {args.days} days (charge will fail)...")
        print(f"  Target time: {target_dt.isoformat()} (Unix: {target_ts})")

        try:
            await asyncio.to_thread(_advance_clock, clock_id, target_ts)
        except Exception as exc:
            print(_red(f"\nERROR advancing test clock: {exc}"), file=sys.stderr)
            return 1

        print("  Clock advance queued (Stripe processes this asynchronously).")

        # ------------------------------------------------------------------
        # 5. Wait for webhook delivery
        # ------------------------------------------------------------------
        wait_seconds = args.wait
        print(
            f"\nWaiting {wait_seconds}s for invoice.payment_failed webhook delivery...\n"
            f"  (Stripe fires invoice.payment_failed + customer.subscription.updated;\n"
            f"   {wait_seconds}s is conservative for local stripe listen forwarding.)"
        )
        await asyncio.sleep(wait_seconds)

        # ------------------------------------------------------------------
        # 6. Capture post-state DB
        # ------------------------------------------------------------------
        print("Capturing post-state DB...")
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
        # 7. Retrieve failed invoice ID for optional restore-active step
        # ------------------------------------------------------------------
        try:
            sub_refreshed = await asyncio.to_thread(
                stripe.Subscription.retrieve,
                subscription_id,
            )
            latest_invoice = sub_refreshed.get("latest_invoice")
            if isinstance(latest_invoice, str):
                failed_invoice_id = latest_invoice
            elif latest_invoice and hasattr(latest_invoice, "id"):
                failed_invoice_id = latest_invoice.id
        except Exception as exc:
            print(
                _yellow(f"\n[warn] Could not retrieve latest_invoice id: {exc}"),
            )

        # ------------------------------------------------------------------
        # 8. Run six failure-path assertions
        # ------------------------------------------------------------------
        print("\nRunning failure-path assertions...")
        failures = _assert_decline(pre_sub, post_sub, pre_ledger, post_ledger)

        if failures:
            print(_red(f"\n{'='*60}"))
            print(_red("DECLINE VERIFICATION FAILED"))
            print(_red(f"{'='*60}"))
            for f in failures:
                print(_red(f"  {f}"))
            print(
                _red(
                    "\n  Next step: file a follow-up ticket using the audit template.\n"
                    "  Check: docker-compose logs backend | grep 'invoice.payment_failed'"
                )
            )
            return 1
        else:
            print(_green(f"\n{'='*60}"))
            print(_green("DECLINE VERIFICATION PASSED"))
            print(_green(f"{'='*60}"))
            print(_green(f"  Status:         {pre_sub['status']} → {post_sub['status']}"))
            print(_green(f"  Period start:   {post_sub['current_period_start']} (UNCHANGED)"))
            print(_green(f"  Period end:     {post_sub['current_period_end']} (UNCHANGED)"))
            print(_green(f"  grant_micros:   {post_sub['grant_micros']} (UNCHANGED)"))
            print(_green("  Ledger:         no new subscription_reset or subscription_grant entries"))
            return 0

    finally:
        # ------------------------------------------------------------------
        # finally: Restore original PM (always runs, even if assertions fail)
        # ------------------------------------------------------------------
        if original_pm_id is not None:
            print(f"\n[finally] Restoring subscription default_payment_method → {original_pm_id}...")
            try:
                await asyncio.to_thread(
                    stripe.Subscription.modify,
                    subscription_id,
                    default_payment_method=original_pm_id,
                )
                print(f"[finally]   Restored default_payment_method to {original_pm_id}.")
            except Exception as exc:
                print(
                    _yellow(
                        f"[finally] WARNING: could not restore default_payment_method: {exc}\n"
                        "  The subscription's PM may still be set to the fail token.\n"
                        "  Manually restore via Stripe Dashboard or re-attach a working card."
                    )
                )
        else:
            print(
                _yellow(
                    "\n[finally] WARNING: original_pm_id was None — cannot restore PM.\n"
                    "  The subscription had no default_payment_method before the test.\n"
                    "  Manually set a working PM via Stripe Dashboard."
                )
            )

        # ------------------------------------------------------------------
        # Optional: restore sub to active by paying the failed invoice
        # ------------------------------------------------------------------
        if args.restore_active:
            if failed_invoice_id:
                print(f"\n[restore-active] Paying failed invoice {failed_invoice_id}...")
                try:
                    await asyncio.to_thread(
                        stripe.Invoice.pay,
                        failed_invoice_id,
                    )
                    print("[restore-active]   Invoice paid; subscription should return to active.")
                except Exception as exc:
                    print(
                        _yellow(
                            f"[restore-active] WARNING: could not pay failed invoice: {exc}\n"
                            "  Subscription may remain in past_due state.\n"
                            "  Use --no-restore-active to skip this step intentionally\n"
                            "  (e.g., when leaving past_due fixture for 0025 recovery-flow testing)."
                        )
                    )
            else:
                print(
                    _yellow(
                        "\n[restore-active] WARNING: failed_invoice_id not found — "
                        "skipping invoice re-pay.\n"
                        "  Subscription may remain in past_due."
                    )
                )
        else:
            print(
                _yellow(
                    "\n[restore-active] Skipped (--no-restore-active set).\n"
                    "  Subscription remains in past_due — useful as a starting fixture for\n"
                    "  0025 Customer Portal recovery-flow testing."
                )
            )


async def _run(args: argparse.Namespace) -> int:
    fixture = _load_fixture()
    customer_id: str = fixture["customer_id"]
    clock_id: str = fixture["clock_id"]
    user_id: int = int(fixture["user_id"])
    subscription_id: str = fixture["subscription_id"]

    _setup_stripe()
    grants_flag = _get_grants_flag()

    print(f"\n{'='*60}")
    if args.simulate_decline:
        print("Renewal FAILURE verification — Stripe Test Clocks (--simulate-decline)")
    else:
        print("Renewal cycle verification — Stripe Test Clocks")
    print(f"{'='*60}")
    print(f"  customer_id:     {customer_id}")
    print(f"  clock_id:        {clock_id}")
    print(f"  user_id:         {user_id}")
    print(f"  subscription:    {subscription_id}")
    print(f"  advance days:    {args.days}")
    print(f"  dry_run:         {args.dry_run}")
    if args.simulate_decline:
        print("  simulate_decline: True  (fail PM: pm_card_chargeCustomerFail)")
        print(f"  restore_active:  {args.restore_active}")
    else:
        print(f"  grants_flag:     BILLING_SUBSCRIPTION_GRANTS_TO_LEDGER={grants_flag}")
    print(f"{'='*60}\n")

    if args.simulate_decline:
        return await _run_decline_path(
            args,
            customer_id=customer_id,
            clock_id=clock_id,
            user_id=user_id,
            subscription_id=subscription_id,
        )
    else:
        return await _run_success_path(
            args,
            customer_id=customer_id,
            clock_id=clock_id,
            user_id=user_id,
            subscription_id=subscription_id,
            grants_flag=grants_flag,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the renewal cycle (subscription_cycle) branch of handle_invoice_paid\n"
            "by advancing a Stripe Test Clock and asserting DB state pre/post renewal.\n\n"
            "Default mode (no flags): success-path verification (0024.14).\n"
            "  --simulate-decline: failure-path verification (0024.15).\n\n"
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
    parser.add_argument(
        "--simulate-decline",
        action="store_true",
        default=False,
        help=(
            "Swap the subscription's default PM to pm_card_chargeCustomerFail before "
            "advancing the clock. Asserts past_due transition + Path B preservation "
            "(period + grant_micros unchanged, no phantom ledger writes). "
            "Restores the original PM in a finally block. "
            "Counterpart to the default success-path mode."
        ),
    )
    parser.add_argument(
        "--restore-active",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "After --simulate-decline assertions, attempt to pay the failed invoice "
            "to restore the subscription to active (default: True). "
            "Use --no-restore-active to leave the sub in past_due — useful as a "
            "starting fixture for 0025 Customer Portal recovery-flow testing."
        ),
    )
    args = parser.parse_args()

    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
