# Testing

How we prove a feature works, across three environments.

Three tiers, one rule. Every environment has a specific job; conflating them wastes the environment and hides bugs.

## Overview

| Tier | Where | What it catches | Who runs it | Gate |
|---|---|---|---|---|
| **local** | your laptop, docker-compose | logic, regressions, contract, flow behavior — everything deterministic | AI agents + you, every commit | `pytest` green + `ruff` clean |
| **staging** | `carddroper-staging` GCP project | infrastructure glue — IAM, secrets, env vars, real external APIs, DNS, TLS | you, after each `main` deploy | curated smoke script suite exits 0 |
| **prod** | `carddroper-prod` GCP project | nothing — observability only | no-one; Cloud Logging + uptime checks watch it | n/a |

Cross-ref: [environments.md](environments.md) defines the three environments themselves. This doc defines what each one is *for* with respect to correctness.

## The rule

> **If a bug can be caught locally, it must be caught locally.** Staging is the last line of defense, not the first. Prod is never a test target.

Corollary: the moment you catch a class of bug only in staging, you've identified a gap in the local suite. Backport a local test before resolving the ticket.

### Breaking-change audit checklist

A chassis API-shape change (response envelope, error-code split, endpoint rename, etc.) has **four** consumer layers, each of which can silently lag behind. When drafting a breaking-change ticket, the orchestrator audit must tick every box before dispatch:

1. **Local unit tests** (`backend/tests/` + frontend suite when it exists) — every assertion that reads the old shape migrated to the new shape.
2. **Frontend / client consumers** — every component, hook, and API-client parse site migrated; TypeScript types updated; `useQuery<T>` generics tightened.
3. **Staging smoke scripts** (`backend/scripts/smoke_*.py`) — every script that hits the changed endpoint re-checked. Easy to miss because smokes only run on deploy, not on PR; a missed migration manifests as a staging failure weeks after the breaking change.
4. **Documentation** — `doc/systems/*.md`, `doc/reference/backend-api.md`, chassis-contract entries if applicable. Adopter-facing docs must describe the new shape, not the old.

Origin: 0021 Phase 2 smoke battery on 2026-04-23 failed on `smoke_auth` + `smoke_verify_email` because ticket 0016.6's envelope change (`/auth/me` → `{user, expires_in}`) updated layers 1, 2, and 4 but missed layer 3. Retroactive fix landed in commit `59b0b04`. Future chassis-shape tickets must audit all four layers explicitly in their Acceptance section.

### Test isolation from env state

Tests must never rely on `.env` defaults. Any test whose behavior depends on a `Settings` value must explicitly control that value. Two patterns, selected by **how the tested code reads the setting**:

#### Pattern 1 — Runtime-path tests (explicit patch)

**When:** the tested code re-reads `settings.X` on each request (e.g., a FastAPI handler that checks `if settings.BILLING_ENABLED:` inside its body, or a dependency that evaluates per-request).

**How:** `patch.object(settings, "X", ...)` inside the test scope.

```python
async def test_register_skips_billing_hook_when_disabled(client):
    from app.config import settings
    with patch.object(settings, "BILLING_ENABLED", False):
        resp = await client.post("/auth/register", json={...})
    # assertions — the register handler re-reads settings.BILLING_ENABLED
    # per request, so the patch is effective for the duration of the call.
```

**Why this works:** the runtime code re-reads settings on every request. The patch is active for the duration of the `with` block, covering the request.

#### Pattern 2 — Feature-gated tests (skipif)

**When:** the tested code made its decision at module-import time (e.g., `main.py` conditionally mounting routes with `if settings.BILLING_ENABLED: app.include_router(...)`).

**How:** `pytest.mark.skipif` — module-level `pytestmark` for file-wide gating, per-test decorator for mixed files.

```python
# at the top of test_billing_topup.py — gates every test in the file
import pytest
from app.config import settings

pytestmark = pytest.mark.skipif(
    not settings.BILLING_ENABLED,
    reason="requires BILLING_ENABLED=true — feature-gated at app-init time",
)
```

**Why patching doesn't work here:** routes are baked into the FastAPI app instance when `app.include_router(...)` runs — at module-import time, once. Patching `settings.X` mid-test can't retroactively add or remove a route. Skipif declares honestly that this test exercises a feature and is meaningless without it.

#### How to decide which pattern

Ask: **when does the code consult the setting?**

- Per-request (inside a function body that runs per request, inside a dependency): **Pattern 1 (patch)**.
- At app-init / module-import / decorator arguments / class-level defaults: **Pattern 2 (skipif)**.

If unsure, check the actual call site. `settings.X` inside a function body → Pattern 1. `settings.X` at module top-level → Pattern 2. New test files for entirely feature-gated subsystems should start with a module-level `pytestmark` from the first line.

#### Two-state verification

A robust chassis passes the suite under **both** env states:

```bash
# With the feature flag enabled
BILLING_ENABLED=true in .env  → pytest: full suite green, 0 skipped
# With the feature flag disabled
BILLING_ENABLED=false in .env → pytest: 0 failed, feature-gated tests skipped
```

The invariant is **zero failures** in either state. Skipped tests (with clear `reason=...`) are acceptable and expected under Pattern 2.

This is a standard check for `doc/issues/0018-chassis-hardening-audit.md` sweeps — run the suite under both states and verify the invariant.

#### Origin

Pattern formalized in ticket 0023.1 during 0023 PAYG topup development (2026-04-23). A naive "force `BILLING_ENABLED=true` via session-scoped autouse fixture" pattern was considered and rejected: it lies about env state, requires global Stripe mocking, and has larger blast radius than declarative skipif. See `doc/issues/0023.1-test-isolation-fixes.md` for the full Kind-1 vs Kind-2 analysis.

---

## Local tier

The primary correctness environment. Fast, deterministic, no external side effects. AI agents iterate freely here.

### Backend — `backend/tests/`

Tooling: `pytest==8.3.4`, `pytest-asyncio==0.25.2`, `ruff==0.9.0`. All declared in `backend/pyproject.toml` `[dependency-groups] dev`.

```bash
cd backend
.venv/bin/pytest                 # full suite
.venv/bin/pytest -k auth         # one feature area
.venv/bin/ruff check .           # lint
.venv/bin/ruff format --check .  # format
```

Test categories (by location, not by filename suffix):

| Directory | Scope | External I/O allowed |
|---|---|---|
| `tests/services/` | service-layer functions in isolation | no — mock the client/DB |
| `tests/test_*_flow.py` | end-to-end HTTP flows through FastAPI | yes — real test DB, real internal routes |
| `tests/test_<topic>.py` | topic-focused behavior (jwt claims, exception handler, email service) | depends — document in the test |

Fixtures live in `tests/conftest.py`. Database is a throwaway Postgres — docker-compose brings it up; the test suite migrates it fresh.

External-service policy in the local tier:

- **SendGrid** — no-key fallback (`SENDGRID_API_KEY=""`). `send_email` logs `{event, template, to_hash, mock_message_id, dev_preview_url}` and returns a mock ID. No real mail sent.
- **Stripe** — test mode keys; webhooks exercised via `stripe listen --forward-to` or mocked signature verification in pytest.
- **HTTP to third parties** — always mocked. `httpx.AsyncClient` gets an injected transport in tests.

### Frontend — `frontend/`

Tooling: `eslint` + `tsc --noEmit` today. **No test runner configured.** This is a known gap; setup is a separate ticket (see §Open gaps).

```bash
cd frontend
npm run lint       # eslint
npm run typecheck  # tsc --noEmit
npm run build      # next build (type + build-time errors)
```

Until a runner lands, frontend correctness is gated on lint + typecheck + build succeeding, plus manual browser verification as the functional smoke. Not sufficient long-term.

### Gates

Before any commit, agents and humans must both show:

1. `pytest` green.
2. `ruff check` + `ruff format --check` clean.
3. Frontend: `npm run lint` + `npm run typecheck` + `npm run build` clean (when touching frontend).

Agents may not report a task done without showing these in their report.

### Test-data conventions

- **Local pytest tests** use `@example.com` addresses (e.g. `a@example.com`, `user@example.com`).
  This is the convention already in the codebase — do not rewrite existing fixtures. Note that
  `@example.com` is RFC 2606-reserved and will be rejected by pydantic-email / email-validator in
  production validation, but pytest tests bypass the HTTP layer or hit an internal test app that
  accepts the addresses directly, so this is not a problem in practice.
- **Staging smoke scripts** use `smoke+<slug>@carddroper.com` — our real domain. The delivery
  attempt will bounce harmlessly (no inbox for arbitrary `smoke+*` addresses), but the address
  passes email-validator. The `smoke+` prefix lets a future nightly sweep reap smoke-created users.
- Test tokens are minted with the helper in `tests/test_jwt_claims.py` (`_mint` function) — never hand-crafted in other files.
- Test DB state is isolated per test via fixtures; parallel execution must remain safe.
- `ruff format` runs before every commit; no hand-formatting arguments.

---

## Staging tier

Staging's job is NOT to re-run the local test suite. Its job is to catch what local cannot see:

- IAM bindings (runtime SA has access to every secret it needs)
- `cloudbuild.yaml` env-var typos
- Secret Manager mount at the right path
- Real SendGrid sender authentication + deliverability
- Real Stripe test-mode webhook signatures + event routing
- Cloud SQL proxy + IAM auth to the DB
- Custom-domain DNS + Cloud Run managed SSL
- CORS at the edge
- Cloud Build: migration-before-deploy ordering

These are all *glue* failures. Local cannot reproduce any of them without reproducing GCP itself.

### The smoke script pattern — `backend/scripts/smoke_*.py`

One script per feature area. Each script:

- Is idempotent — running it twice is fine.
- Runs fast — <10s per script is the target.
- Cleans up what it creates (or uses data prefixed `smoke+` so a nightly sweep can reap it).
- Hits the staging URL (`https://api.staging.carddroper.com`), not localhost.
- Returns non-zero exit on any failed assertion, with a clear message.
- Requires only the staging public URL — does not require GCP CLI access to run.

```bash
cd backend
.venv/bin/python scripts/smoke_email.py      # exercises SendGrid wiring end-to-end
.venv/bin/python scripts/smoke_auth.py       # register → verify → login → refresh → logout
.venv/bin/python scripts/smoke_stripe.py     # (later) test-mode charge + webhook
```

Each smoke MUST print a one-line success marker (`SMOKE OK: <feature>`) on success. Scripts with silent success are a footgun.

### When to run staging smokes

- **After every `main` deploy.** Cloud Build finishes → run the full smoke suite. ~30s end-to-end. Don't promote to a prod tag until staging smokes are green.
- **After touching infrastructure.** Adding a secret, changing `cloudbuild.yaml`, changing Cloud Run flags — rerun relevant smokes.
- **Never as a substitute for local tests.** If you write a staging smoke because you couldn't be bothered to write the local test, the feature is under-tested.

### Who runs staging smokes

You, the human. AI agents never hit staging directly — they lack credentials, and the blast radius of a mistaken action against real infrastructure is higher than the value of agent autonomy here. Agents may *write* smoke scripts; they do not *run* them against staging.

---

## Prod tier

Production is not a test environment. Never run a test against prod.

### Observability

| Signal | Where | Purpose |
|---|---|---|
| Application logs | Cloud Logging | debugging, incident forensics |
| Error tracking | Sentry (future) | surfaced exceptions, sourcemaps |
| Uptime | Cloud Monitoring uptime checks on `/healthz` | pagerable availability signal |
| Latency | Cloud Run request metrics | p50/p95/p99 per route |
| Synthetic canary | Cloud Scheduler hitting a dedicated test account (future) | golden-path liveness |

### What's deferred

- Synthetic canaries — add when we have paying users.
- Sentry — add before launch.
- Real User Monitoring (RUM) — add if/when frontend perf becomes a product concern.

---

## Coverage matrix — what each tier is responsible for

| Concern | local | staging | prod |
|---|---|---|---|
| Function correctness | ✅ primary | ❌ do not re-test | ❌ |
| HTTP contract (request/response shape) | ✅ primary | ❌ | ❌ |
| Database query correctness + migrations | ✅ | ✅ verify migration on cloud SQL | ❌ |
| Auth flow behavior | ✅ primary | ✅ smoke end-to-end with real DNS | ❌ |
| IAM / Secret Manager mount | ❌ cannot | ✅ primary | 👁 monitor |
| External API wiring (SendGrid, Stripe) | ❌ mocked | ✅ primary | 👁 monitor |
| DNS + TLS | ❌ | ✅ primary | 👁 monitor |
| Load / concurrency | ❌ | ❌ | 👁 observed only; dedicated load-test env later |
| Real user behavior | ❌ | ❌ | 👁 observed only |

---

## Per-ticket coverage checklist

For every new feature ticket, the dispatch brief and Acceptance section must require:

1. **Local unit tests** covering pure-logic branches.
2. **Local integration tests** covering any HTTP route, DB write, or service-layer function touching I/O.
3. **Staging smoke script** (or extension of an existing one) if the feature touches: a new secret, a new env var, a new external API, a new endpoint that participates in a golden path, or a new Cloud Run flag.
4. **Doc update** (backend-api.md or relevant systems doc) if the feature adds or changes a public contract.
5. **`docker build` must succeed locally** if the change touches `Dockerfile`, `pyproject.toml`, or `package.json`. `pytest` and `ruff` run against source in place; only `docker build` exercises packaging (e.g. setuptools package discovery). Surface check: `docker build -t carddroper-backend-test backend/` returns exit 0 before pushing.
6. **Dockerfile `COPY` dependencies.** Before deleting the last tracked file in a directory referenced by a Dockerfile `COPY` (e.g. `public/`, `alembic/`), confirm either (a) the directory still has other tracked content, (b) a `.gitkeep` is added, or (c) the `COPY` line is removed. Git does not track empty directories, so a `COPY` that was valid at author time can silently break at build time. See the 0014 Phase 1 postmortem (commit `a8f1915`).
7. **Runtime-image parity for infra-embedded code.** Probes, health checks, startup scripts, and any code embedded in `cloudbuild.yaml`, `Dockerfile` `HEALTHCHECK`, Cloud Run entrypoints, or similar infra-runtime contexts may only use tools present in the specific image being deployed. "Works on my host" is **not** validation — a probe that imports `psycopg2` will `ImportError` silently in a runtime image that only has `asyncpg`, even though the agent's dev machine happily runs it. Verify the image itself via `docker run --rm <image> <command>`, or restrict the probe to stdlib. If the probe does import a third-party library, grep `pyproject.toml` / `requirements.txt` / the runtime stage's `pip install` for that library before committing. See the 0014.5 post-resolution correction (commit `c0779f5`).
8. **Fail-loud probes.** A shell loop that silences stderr (`2>/dev/null`) makes a broken probe indistinguishable from a not-ready-yet probe. If you silence stderr, at least emit the first iteration's underlying error once before looping; or use a probe with no classifiable error modes (stdlib `socket.connect_ex` beats typed-exception matching). Prefer simple exit-code semantics over exception-message classification.
9. **YAML literal-block indentation for embedded scripts.** When embedding multi-line code in a YAML `|` block (cloudbuild steps, k8s manifests, GitHub Actions `run:` blocks), every content line must be indented at or beyond the block's base indent — a line at column 0 terminates the block early and YAML reinterprets the orphaned lines as new top-level structure, producing parse errors that point at unrelated lines. Prefer a one-line form (semicolons, `connect_ex`, inline conditionals) for embedded scripts wherever feasible; reserve multi-line blocks for cases where a single line would be genuinely unreadable. See 0014.5 commit `48faec7` for the indentation-recovery path we took before switching to a one-liner in `c0779f5`.

The agent's Report must explicitly state which of 1–4 it added and the `pytest` output. Items 5–9 are surface-level checks — the agent should confirm them inline when relevant rather than listing them by number.

Agents should raise a visible flag (not silently skip) if a ticket touches infra but does not yet have a smoke script — the orchestrator decides whether to add one in the same ticket or spin a follow-up.

---

## Backfill policy

Already-shipped features that lack documented coverage get audited and backfilled rather than left as implicit debt. See ticket 0013 for the specific plan.

New rule going forward: no ticket closes as `resolved` without satisfying the per-ticket checklist above. If the agent can't show it, the orchestrator does not flip status.

---

## Agent dispatch expectations

Every agent dispatch brief (backend-builder, frontend-builder) must include:

```
Testing requirements:
- Run `.venv/bin/pytest` (backend) or `npm run lint && npm run typecheck && npm run build`
  (frontend) before reporting done.
- Add local tests covering the new behavior per doc/operations/testing.md §Per-ticket checklist.
- If this feature touches infra (new secret/env var/external API/public URL), flag that a
  staging smoke is needed and state whether you added one or deferred.
- Report must include: pytest summary line, ruff summary, files added under tests/, any
  smoke script added under scripts/.
```

This block is added to the CLAUDE.md dispatch template so orchestrators don't re-type it.

---

## Open gaps

1. **Frontend test runner not installed.** Playwright (E2E) or Vitest + React Testing Library (unit/component) — decide when the first real UI lands (ticket 0011's verify/reset pages). Track as a separate ticket.
2. **No CI runs pytest.** Cloud Build builds and migrates; it does not run tests. Cheap win: add a test step to `cloudbuild.yaml` gated on backend/ changes. Future ticket.
3. **No coverage reporting.** `pytest --cov` with a ratcheting floor is a later investment — only useful once the suite is mature enough that untested code is a real risk.
4. **Smoke scripts are manually invoked.** Eventually a Cloud Run Job runs them post-deploy and posts to a Slack/email channel on failure. Future.

These are explicit investments deferred until they're worth paying for.
