---
id: 0012
title: Dockerfile hardening — non-root + multi-stage + HEALTHCHECK + public/ cleanup
status: open
priority: medium
found_by: ticket 0009 audit (backend F-5 medium, frontend F-3 nit, F-7 low)
---

## Context

Three audit findings clustered on image hygiene. Each is small; batching them into one ticket avoids three separate Dockerfile-touching dispatches.

- **Backend F-5 (medium, security + dep-hygiene):** `backend/Dockerfile` is single-stage, runs as root, has no `HEALTHCHECK`, and keeps `gcc` / `libpq-dev` in the final image. Cloud Run tolerates all of this but it fails standard container security baselines and bloats the image by ~250MB.
- **Frontend F-3 (nit, dead-code):** `frontend/public/` still contains the four default `create-next-app` SVGs (`file.svg`, `globe.svg`, `vercel.svg`, `window.svg`). `vercel.svg` ships Vercel branding; the other three are dead assets.
- **Frontend F-7 (low, design-smell):** `frontend/Dockerfile` runner stage does not set `ENV HOSTNAME=0.0.0.0`. Works today because Next.js standalone default is `0.0.0.0`, but the official deploy guide recommends explicit.

Priority: medium. Not a v0.1.0 blocker, but we want it landed before the prod project stands up so prod inherits hardened images from the first deploy. Ideal sequencing: after 0010 (since 0010 already touches `backend/Dockerfile` for the F-4 `pip install .` fix) and before the prod stand-up ticket.

## Pre-requisites

- Ticket 0010 resolved (it moves `backend/Dockerfile` off `requirements.txt`; this ticket picks up where that leaves off).

## Acceptance

### Phase 0: backend-builder — harden `backend/Dockerfile` (agent-executed)

Orchestrator dispatches **backend-builder**:

```
Task: Convert backend/Dockerfile to a hardened multi-stage build per ticket 0012.

Starting point: the state left by ticket 0010 (should already be `pip install .`
from pyproject.toml, with tenacity in the runtime dep list).

Required shape:

  Stage 1 — builder (python:3.11-slim):
    - Install build-time apt deps: libpq-dev, gcc, build-essential.
    - Copy pyproject.toml + README.md (if referenced by build).
    - Build a virtualenv at /opt/venv via `python -m venv /opt/venv`.
    - pip install . into that venv (no dev extras).

  Stage 2 — runtime (python:3.11-slim):
    - Install runtime apt deps only: libpq5 (NOT libpq-dev), ca-certificates,
      tini or dumb-init for PID 1 signal handling.
    - COPY --from=builder /opt/venv /opt/venv
    - COPY ./app /app/app, ./alembic /app/alembic, ./alembic.ini /app/alembic.ini
    - ENV PATH=/opt/venv/bin:$PATH
    - adduser --system --no-create-home --group appuser
    - USER appuser
    - WORKDIR /app
    - EXPOSE 8000
    - HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
        CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1
    - ENTRYPOINT ["tini", "--"]   (or dumb-init)
    - CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

Test locally:
  - `docker build -t carddroper-backend:hardened ./backend` — must succeed.
  - `docker run --rm -p 8000:8000 carddroper-backend:hardened` — starts, /health
    returns 200 (at least once container has access to a DB; for the smoke,
    running against docker-compose is fine).
  - `docker image inspect carddroper-backend:hardened --format '{{.Config.User}}'`
    → "appuser" (not "" / "root").
  - `docker image inspect carddroper-backend:hardened --format '{{.Config.Healthcheck}}'`
    → non-nil.
  - Final image size should be notably smaller than the current single-stage
    image. Report before/after via `docker images | grep carddroper-backend`.

Cloud Build compatibility:
  The migrate step in cloudbuild.yaml runs inside this backend image with
  `entrypoint: sh`. With a non-root user, the migrate step must be able to
  write to /tmp (where we download the cloud-sql-proxy). /tmp is world-writable
  by default, so this should still work. Verify by:
    - Running `docker run --rm -it carddroper-backend:hardened sh -c
        'touch /tmp/foo && echo ok'`.
  If /tmp is not writable (unlikely, but possible with hardened base images),
  adjust the migrate step to use a user-writable path like /home/appuser/tmp.

Do NOT:
  - Change what gets installed into the venv (the pyproject.toml dep list is
    ticket 0010's territory).
  - Add or remove runtime behaviour (handlers, routes, lifespan).
  - Modify cloudbuild.yaml — but DO verify the migrate step still works by
    mental walkthrough. Flag any concerns.

Report:
  - Dockerfile diff summary.
  - Local docker build + run smoke results.
  - Image size before / after.
  - Any Cloud Build concern.
```

### Phase 1: frontend-builder — harden `frontend/Dockerfile` + clean public/ (agent-executed)

Orchestrator dispatches **frontend-builder**:

```
Task: Two small frontend hygiene fixes per ticket 0012.

  1. frontend/Dockerfile runner stage — add `ENV HOSTNAME=0.0.0.0` alongside
     the existing `ENV NODE_ENV=production`. That's the entire change (one line).
     Current Next.js standalone default matches, but being explicit guards
     against future behaviour drift per the official deploy guide.

  2. Delete unused default Next.js template SVGs:
       frontend/public/file.svg
       frontend/public/globe.svg
       frontend/public/vercel.svg
       frontend/public/window.svg
     None are imported anywhere in app/** or components/**. Confirm via grep
     before deletion.

Tests:
  - `npm ci && npm run build` must succeed.
  - `npm run lint` zero issues.
  - `npx tsc --noEmit` zero errors.
  - `docker build ./frontend` must succeed.
  - `docker run --rm -p 3000:3000 -e NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 \
       <image>` starts and serves /.

Do NOT:
  - Touch any app/** source files.
  - Touch package.json or package-lock.json.
  - Rebuild the Dockerfile structure beyond the one-line ENV addition.
  - Add a new logo or replacement SVG in the same commit — that belongs in the
    first brand-assets ticket.

Report:
  - Files deleted (4 paths).
  - Dockerfile diff (should be 1 line).
  - Build / lint / type-check / docker-build smoke results.
```

### Phase 2: user — staging deploy + verify (CLI)

```bash
git checkout dev
git add -A
git commit -m "docker: harden backend + frontend images (0012)"
git push origin dev
git checkout main
git merge --ff-only dev
git push origin main

gcloud builds list --region=us-west1 --limit=1
# Wait for SUCCESS, then:
curl -sSf https://api.staging.carddroper.com/health
curl -sSf https://staging.carddroper.com | grep -o 'Carddroper</h1>'
```

Verify the Cloud Run container is now running as non-root:

```bash
# Exec into the running Cloud Run revision (second-gen Cloud Run supports this)
gcloud run services update-traffic carddroper-backend --region=us-west1 \
    --to-revisions=$(gcloud run revisions list --service=carddroper-backend \
        --region=us-west1 --limit=1 --format='value(metadata.name)')=100

# Or just read the logs — the user the container boots as appears in startup.
gcloud run services logs read carddroper-backend --region=us-west1 --limit=20
# Look for uvicorn startup line + absence of "running as root" warnings.
```

Confirm the frontend SSR still renders and the hardened runner still binds:

```bash
curl -sSf https://staging.carddroper.com | head -20
# Expected: HTML containing <h1 class="..."> Carddroper</h1>
```

## Verification

**Automated checks (agents, reported in Phases 0 and 1):**

- `docker build ./backend` succeeds.
- `docker build ./frontend` succeeds.
- Backend image `.Config.User = "appuser"`.
- Backend image `.Config.Healthcheck` non-nil.
- Backend image size (post-hardening) notably smaller than pre-hardening (agent reports before/after).
- Frontend `npm ci / build / lint / tsc --noEmit` all clean.

**Functional smoke (user, Phase 2):**

- Cloud Build `SUCCESS`.
- `curl https://api.staging.carddroper.com/health` → 200 JSON.
- `curl https://staging.carddroper.com` → renders heading.
- `gcloud run services logs read carddroper-backend --region=us-west1` shows no startup errors and no "running as root" advisory.
- Four template SVGs gone from `frontend/public/`.

## Out of scope

- Bundling a brand logo / favicon. Separate brand-assets ticket whenever we're ready to draw it.
- Trimming the frontend Dockerfile further (it's already three-stage; no work there).
- Adding `dumb-init` / `tini` to the frontend Dockerfile. Next.js standalone handles PID 1 correctly.
- Scanning images for CVEs (`trivy`, `grype`). Ops-layer work for later.
- Moving images to Distroless or Alpine. Slim is fine for v0.1; distroless blocks the Python runtime debugability we want in staging.
- Squashing layers / using BuildKit cache mounts. Cloud Build handles cache.

## Report

Backend-builder:
- Dockerfile diff, local-build + local-run results, image size before / after, any Cloud Build concern.

Frontend-builder:
- Four file deletions, one-line Dockerfile diff, build/lint/type-check + docker-build smoke results.

User:
- Deploy SUCCESS, `/health` 200, frontend renders, startup logs clean.

## Resolution

*(filled in by orchestrator on close)*
