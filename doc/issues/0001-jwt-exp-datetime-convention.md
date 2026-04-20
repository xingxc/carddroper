---
id: 0001
title: JWT exp tz-aware datetime is undocumented exception to naive-UTC convention
status: resolved
priority: low
found_by: backend-builder audit 2026-04-19
resolved_at: 2026-04-19
---

## Context

Project convention: "datetime columns are naive UTC; write `datetime.now(timezone.utc).replace(tzinfo=None)`." JWT `exp` claims in `app/services/auth_service.py` are the one place this is violated — `python-jose` expects tz-aware datetimes. It works today but is undocumented, which risks a future "normalization" silently breaking token encoding.

## Acceptance

1. Grep `app/` for `datetime.now(timezone.utc)` without an accompanying `.replace(tzinfo=None)`. Confirm the only hits are JWT `exp` construction in `app/services/auth_service.py`. If any other site is found, call it out in your report instead of fixing — scope is documentation only.
2. At each JWT `exp` site, add a single-line comment: `# tz-aware: python-jose expects aware datetimes for exp. See doc/issues/0001.`
3. `.venv/bin/pytest tests/` passes.

## Out of scope

The backend-builder system prompt under `.claude/` must not be modified — that's orchestrator work and will be handled separately.

## Resolution

Grep confirmed JWT `exp` in `app/services/auth_service.py:35` and `:43` are the only tz-aware datetime sites. Documented with inline comments at both. Tests 10/10 green. Agent system prompt still needs to name this exception — tracked as orchestrator follow-up.
