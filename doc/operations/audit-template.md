# Audit template for chassis tickets

Use this template for the audit step of every chassis ticket before scoping the implementation. Answer the six questions explicitly in the audit report. Free-form analysis after is welcome but the six are required.

## Why this exists

In the 0024.x billing-chassis arc, three bugs (0024.7, 0024.9, 0024.10) shipped despite passing audits. Each piece was correct in isolation; the composition broke. The audits had been searching for "is this code correct" rather than "does this composition hold". The questions below force tracing of compositions before any line of code is written.

## The six questions

### 1. What user-visible action triggers this code path?

Name the action **concretely**. Avoid "user subscribes" — too vague. Examples that work:

- "User clicks Subscribe on a tier card after entering a card that fails the first invoice."
- "User cancels an active subscription via the project-layer cancel button."
- "User retries subscribe within 30 seconds of a card decline."

If the change is invisible to users (e.g., a webhook handler only), name the Stripe event that triggers the code path AND the user action that produces that event upstream.

### 2. Trace the full request flow

List every endpoint, table write, and webhook touched by the action **in order**. Example for a decline-retry flow:

```
1. POST /billing/setup-intent          → creates Stripe SetupIntent (consumable)
2. frontend stripe.confirmSetup()      → consumes SetupIntent, attaches PM
3. POST /billing/subscribe              → creates Stripe Subscription
                                          upserts subscriptions row
4. (background) customer.subscription.created  → upserts subscriptions row
5. (background) invoice.payment_failed         → updates subscriptions.status
6. frontend handleInvoiceDeclined       → POST /billing/setup-intent (RETRY)
7. frontend stripe.confirmSetup() again → consumes new SetupIntent
8. POST /billing/subscribe again        → cleanup branch fires, creates new Sub
```

The narrative format is fine. Order matters because retry behavior depends on state laid down by earlier steps.

### 3. For each endpoint above, document four properties

| Property | What to record |
|---|---|
| Idempotency key shape | The literal key string template, or "none" if absent |
| State mutated (DB) | Which tables / columns are written |
| State mutated (Stripe) | Which Stripe resources are created or updated |
| Retry semantics | What happens on duplicate request: replay (returns prior result), fresh (creates new resource), conflict (raises error) |

### 3.5. If this ticket changes WHEN or WHETHER any writer writes a column, audit every other writer to that column for assumptions that may now be invalidated

When a ticket modifies a writer's behavior — e.g., reordering when an upsert happens, adding an early return that skips a write, changing a condition that gates a write — list every OTHER writer to the same column and check each writer's implicit or explicit assumptions about when it runs.

Common assumptions to look for:

- "I always run after writer X has populated this row, so my INSERT path is rare."
- "Writer X always handles the flag-gate, so I can use the unflagged metadata value."
- "Writer X always writes a non-NULL value before me, so I don't need a fallback."

If the ticket invalidates any such assumption, the affected writer needs to be updated in the same ticket OR explicitly carved into a follow-up.

Origin: ticket 0024.13. Tickets 0024.7 (webhook INSERT path assumed subscribe endpoint always upserted first) and 0024.9 (subscribe endpoint stopped upserting on terminal failure) composed into a chassis-correctness bug because no audit explicitly checked the cross-writer assumption.

### 4. Consumability check

For each Stripe resource the flow creates: is it **consumable** (single-use, terminal state after first use)? Reference `doc/operations/idempotency-policy.md` §"Consumable resource catalog" for the canonical list.

If any resource is consumable AND the idempotency key for the endpoint that creates it is **time-window-based**, the chassis idempotency policy is violated. Flag immediately — this is the 0024.10 failure mode.

### 5. Adversarial scenario

Write down ONE concrete user scenario where each piece works in isolation but the composition breaks. **If you cannot construct one, the audit may have missed something — keep probing.**

This is the single highest-value question on the template. The audit's job is to *try to break the design* before code is written.

Examples from past 0024.x audits, retrospectively:

- **0024.7:** subscribe endpoint correctly stores `grant_micros=0` when flag=false; webhook handler correctly extracts metadata to know whether to call `grant()`. Composition: webhook upsert UPDATE clause overwrites the 0 with metadata value.
- **0024.9:** subscribe-decline-retry resets the form via `setClientSecret(null)` then re-fetches setup-intent. Composition: the re-fetch within the same minute hits Stripe idempotency replay → returns the consumed SetupIntent → PaymentElement won't mount → IntegrationError.
- **0024.6:** subscribe idempotency key was `subscribe:{user.id}:{lookup_key}`. Composition: legitimate retry with different PM (3DS-cancel-retry, decline-retry) reuses the key but with different params → Stripe IdempotencyError.

### 6. Test coverage for the adversarial scenario

For the scenario in #5: does any existing test exercise it? If not, name the test that should be added in the same ticket. If yes but the bug shipped anyway, why didn't it catch the issue? (Maybe the test mocked the wrong layer, or asserted the wrong thing.)

This question prevents shipping a fix without a regression guard.

## Format

The audit response includes the six answers as numbered sections. Each answer should be specific enough that a reader unfamiliar with the codebase can verify it from the code.

After the six numbered sections, the audit may include free-form additional findings, related concerns, or PAUSE recommendations.

## When to skip

The template is required for:
- Any ticket touching a billing endpoint or webhook handler
- Any ticket introducing a new code path that interacts with Stripe state
- Any ticket changing idempotency keys
- Any ticket where a previous ticket in the same surface area shipped with a bug

The template is **optional but recommended** for:
- Pure refactors with no behavior change
- Isolated bug fixes in non-Stripe paths
- Documentation-only changes

When in doubt, fill it out — the cost is low and it forces precision.

## Origin

Ticket 0024.10 retrospective, after observing that three audits in the 0024.x arc passed despite missing composition bugs that the template's questions would have surfaced.
