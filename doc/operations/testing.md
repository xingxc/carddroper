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

The agent's Report must explicitly state which of 1–4 it added and the `pytest` output.

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
