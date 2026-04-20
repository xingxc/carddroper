---
id: 0003
title: passlib depends on stdlib crypt removed in Python 3.13
status: resolved
priority: medium
found_by: backend-builder audit 2026-04-19
resolved_at: 2026-04-19
---

## Context

`passlib` (used for password hashing in `app/services/auth_service.py`) imports the stdlib `crypt` module, which is removed in Python 3.13. The project runs on 3.11 now; upgrading Python will silently break at import time.

## Decision

Replace `passlib` with direct use of the `bcrypt` library. `bcrypt` is actively maintained and is already a transitive dependency (passlib uses it as the backend). `passlib` is effectively in maintenance-only mode.

## Acceptance

1. Rewrite `hash_password` and `verify_password` in `app/services/auth_service.py` to use the `bcrypt` library directly. Keep the same function signatures.
2. Use bcrypt's default cost factor (12 rounds) unless there's a reason to pin it — match whatever passlib was doing.
3. Existing hashes must remain verifiable: bcrypt hashes produced by passlib and by the `bcrypt` library are format-compatible (`$2b$...`), so `verify_password` must accept both without conditional branching.
4. Remove `passlib` from `requirements.txt` and `pyproject.toml`. Keep `bcrypt` pinned to its current version.
5. `.venv/bin/pytest tests/` passes. All 10 auth tests green.

## Resolution

`hash_password` and `verify_password` rewritten in `app/services/auth_service.py` using the `bcrypt` library directly. `passlib[bcrypt]==1.7.4` removed from `requirements.txt` and `pyproject.toml`; `bcrypt==4.0.1` retained. Format-compatible (`$2b$...`) so existing hashes remain verifiable. `CryptContext`/`pwd_context` removed as dead code. Tests 10/10 green.
