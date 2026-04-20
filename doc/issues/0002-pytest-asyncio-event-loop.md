---
id: 0002
title: pytest-asyncio event_loop fixture deprecated
status: resolved
priority: medium
found_by: backend-builder audit 2026-04-19
resolved_at: 2026-04-19
---

## Context

`tests/conftest.py` defines a custom session-scoped `event_loop` fixture. pytest-asyncio deprecates this; current runs emit a `DeprecationWarning` and a future release will raise.

## Acceptance

1. Remove the custom `event_loop` fixture from `tests/conftest.py`.
2. Make the minimum config change needed to keep tests green — e.g., set `asyncio_mode = "auto"` in `pyproject.toml` under `[tool.pytest.ini_options]` if not already set. Do not rewrite unrelated test infrastructure.
3. `.venv/bin/pytest tests/` passes with zero `event_loop` deprecation warnings. Unrelated warnings (e.g., passlib / crypt — tracked separately in 0003) are fine.

## Resolution

Custom `event_loop` fixture removed from `tests/conftest.py`. `asyncio_mode = "auto"` was already set in `pyproject.toml` — no config change needed. Tests 10/10 green, `event_loop` deprecation warning gone. Agent flagged a separate `asyncio_default_fixture_loop_scope` warning as out-of-scope; worth a future ticket if it starts mattering.
