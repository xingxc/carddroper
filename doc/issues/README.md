# Issues

Lightweight tracking for bugs, tech debt, and deferred decisions. One file per ticket: `<id>-<slug>.md`, zero-padded IDs.

## Ticket frontmatter

```yaml
id: 0001
title: short headline
status: open | in_progress | resolved | wontfix
priority: low | medium | high
found_by: source (e.g., "backend-builder audit 2026-04-19")
```

## Workflow

1. Orchestrator creates the ticket with clear acceptance criteria.
2. Agent is dispatched with the ticket ID; it reads the full file and executes the acceptance.
3. Agent reports back. Agent does NOT modify the ticket file.
4. Orchestrator verifies, flips `status` to `resolved`, appends a Resolution note.

## Ticket sections (template)

Every ticket file should have these sections in order:

- **Context** — one paragraph: what problem this solves, what doc(s) it's grounded in, what's already done.
- **Acceptance** — numbered list of concrete deliverables. Each item is observable (file exists, function signature matches, test passes).
- **Verification** — required. Two sub-bullets:
  - **Automated checks:** the commands the agent runs to prove correctness (e.g., `pytest tests/ -k auth`, `tsc --noEmit`, `npm run lint`, `npm run build`).
  - **Functional smoke:** the end-to-end check that proves the feature *works*, not just that the code compiles. Examples: `curl localhost:8000/auth/me` returns 401 when unauthenticated; `curl localhost:3000` SSR HTML contains "Carddroper"; webhook signature verification rejects a tampered payload. If the smoke can only be run by the user (visual UI check, Stripe live mode), name it explicitly so the orchestrator surfaces it after dispatch.
- **Out of scope** — what the agent should NOT touch, even if tempting. Prevents scope creep.
- **Report** — what the agent's reply must include (files touched, deps added, deviations).
- **Resolution** — added by the orchestrator on close, not the agent.

The Verification section exists because "tsc passes" and "lint passes" are necessary but not sufficient. We learned this on ticket 0004 — the dev server started but we hadn't confirmed the page actually rendered. Bake the smoke into the ticket so no agent can return "done" without exercising the feature.

## Index

| ID | Title | Status | Priority |
|---|---|---|---|
| 0001 | JWT exp datetime convention exception | resolved | low |
| 0002 | pytest-asyncio event_loop deprecation | resolved | medium |
| 0003 | passlib / Python 3.13 crypt removal | resolved | medium |
| 0004 | frontend scaffold — Next.js 16 + TS strict + Tailwind v4 + React Query | resolved | high |
| 0005 | docker-compose — Postgres + backend + frontend, one command up | resolved | high |
| 0006 | staging GCP foundation — project, IAM, Cloud SQL, AR, Secret Manager | resolved | high |
| 0007 | staging first deploy — cloudbuild.yaml, trigger, *.run.app verification | resolved | high |
| 0008 | staging custom domains — Cloudflare CNAMEs + Cloud Run domain mappings | resolved | high |
| 0009 | scaffold code audit — backend + frontend ground-truth inventory before v0.1.0 features | resolved | high |
| 0010 | SendGrid infrastructure — hardened send_email() helper + staging secret wiring | open | high |
| 0011 | backend hardening — global 500 exception handler + JWT iss/aud claims | open | high |
| 0012 | Dockerfile hardening — non-root + multi-stage + HEALTHCHECK + public/ cleanup | open | medium |
