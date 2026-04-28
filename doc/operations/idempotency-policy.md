# Idempotency policy for Stripe operations

Every Stripe API call with `idempotency_key=` must classify the key under one of three patterns. New keys must justify their classification in the PR description. Reviewers (including the orchestrator at audit time) reject PRs that violate this policy.

## Why this exists

Two billing-chassis bugs (0024.6 and 0024.10) came from defaulting to time-window idempotency without classifying the resource the call creates. Time-window keys are correct for some operations (idempotent side effects) and wrong for others (consumable resources). Without an explicit policy, the wrong default kept getting picked.

## The three patterns

### Content-based — keys derive from the request payload

**Required for:** state-changing operations where retries should be detected by content equivalence.

**Shape:** `f"{op}:{user_id}:{distinguishing_content}"`

**Example (chassis):** `subscribe` endpoint after 0024.6:
```python
idempotency_key = f"subscribe:{user.id}:{lookup_key}:{pm_id}"
```
- Same user, same tier, same PM → Stripe replay (correct: this is a duplicate of the same logical request)
- Same user, same tier, different PM → fresh request (correct: a legitimate retry attempt)

**Why content-based:** the key composition encodes "what makes this request unique." Replays correctly detect actual duplicates; legitimate retries with different params correctly bypass the cache.

### Per-request — key supplied by client OR omitted entirely

**Required for:** operations that create consumable resources where each retry intentionally creates a fresh resource.

**Shape:** either omit `idempotency_key=` entirely, or accept a client-supplied UUID:
```python
si = stripe.SetupIntent.create(
    customer=user.stripe_customer_id,
    payment_method_types=["card"],
    usage="off_session",
    # no idempotency_key — each call creates a fresh SetupIntent
)
```

**Example (chassis):** `setup-intent` endpoint after 0024.10 — idempotency_key removed entirely. Frontend submitting-state prevents user-driven double-clicks; backend rate limits provide systemic backstop. Stripe rate limits are the final guardrail.

**Why per-request:** when the resource is consumable (succeeded after first use), replaying a cached creation response returns a now-consumed resource. The client mounts against a dead resource and breaks. See ticket 0024.10 for the surface this creates.

### Time-window — key bucketed by wall-clock interval

**Allowed only for:** idempotent side effects whose replay is harmless.

**Shape:** `f"{op}:{user_id}:{content}:{int(time.time() // window)}"`

**Example (chassis):** `topup` endpoint:
```python
idempotency_key = f"topup:{user.id}:{amount_micros}:{int(time.time() // 60)}"
```
- Replay returns the same charge object → no double-charge (the desired outcome)
- The side effect (charge → balance updated) is the intended outcome on first call; replay = no-op for the user

**Why time-window is OK here:** the resource (the charge) IS the desired side effect. Replaying it doesn't put the client in a broken state — the user's balance is already credited. Time-windowing only deduplicates rapid double-clicks, which is the original protective intent.

## Forbidden patterns

### Time-window keys for consumable resources

**Banned.** Consumable resources cannot be safely replayed because their Stripe-side state has changed since the cache was populated.

The replay returns a response object that says "SetupIntent in `requires_confirmation` state with client_secret=X" but the actual resource on Stripe is now `succeeded`. The client uses the stale response, mounts against the live resource, and breaks.

**Documented violations (now fixed):**
- `setup-intent` had `f"setup:{user.id}:{int(time.time() // 60)}"` — fixed in 0024.10
- `subscribe` originally had `f"subscribe:{user.id}:{lookup_key}"` — fixed in 0024.6 (added pm_id; key became content-based-only, no time-window)

### Per-resource-only keys without per-request salt

**Banned.** A key like `f"setup:{user.id}"` would deduplicate ALL retries forever within Stripe's 24h idempotency window. This is too aggressive for any chassis operation.

## Consumable resource catalog

| Stripe resource | Consumable? | Reasoning |
|---|---|---|
| `SetupIntent` | **yes** | Succeeded after first confirm; cannot be re-confirmed |
| `PaymentIntent` | **yes** | Succeeded after first confirm/charge; cannot be re-confirmed |
| `Subscription` | **yes** | Active after first invoice; `incomplete` is terminal-failure or transitional |
| `Invoice` | **yes** | Paid after first payment |
| `Customer` | **no** | Mutable; same Customer is reused indefinitely |
| `Price` / `Product` | **no** | Effectively immutable from the chassis perspective; read-only |
| `PaymentMethod` | **no** (mostly) | Once attached, stays attached; can be detached/swapped without consuming |

When introducing a new Stripe operation, classify the resource it creates against this table.

## When in doubt

**Default to per-request (omit `idempotency_key=`).** The frontend's `submitting`-state already prevents user-driven double-clicks. Backend rate limits prevent systemic abuse. Stripe's own rate limits prevent account-level abuse. Adding `idempotency_key` is a defensive choice that requires justification under the patterns above.

Speculative defense (idempotency-just-because) is a code smell. Past evidence: 0024.10 was caused by speculative minute-window idempotency on setup-intent that protected against a non-existent threat ("frontend bug spamming SetupIntents") at the cost of breaking legitimate retry flows.

## Reviewer checklist

For any PR adding or modifying `idempotency_key=`:

- [ ] What pattern does the new/changed key fall under (content-based / per-request / time-window)?
- [ ] If time-window: does the resource appear in the "Consumable resource catalog" as `yes`? If yes → reject.
- [ ] If content-based: does the content distinguish all legitimate retry scenarios? (e.g., includes payment_method_id when retries can swap PMs)
- [ ] Is there a regression test that would fail if the key shape regressed?

## Origin

Ticket 0024.10 retrospective, after the second time in the 0024.x arc that incorrect idempotency was the root cause (after 0024.6).
