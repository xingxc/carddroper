---
id: 0007
title: staging first deploy — cloudbuild.yaml, Cloud Build trigger, *.run.app verification
status: open
priority: high
found_by: orchestrator 2026-04-20
---

## Context

PLAN.md §10.4 (second ticket of three covering the staging push). Ticket 0006 created the GCP foundation (project, APIs, Cloud SQL, secrets). This ticket gets code running on it.

**Minimum-surface strategy:** we deploy exactly `/health` on the backend and the `<h1>Carddroper</h1>` homepage on the frontend. No auth routes exercised, no Stripe, no DB reads beyond migrations. The point is to prove the deployment pipeline — Cloud Build → Artifact Registry → alembic migration → Cloud Run with secrets + Cloud SQL socket → serving at `*.run.app`. Every failure mode that surfaces here is a cloud-specific one (IAM, Unix socket path, secret name, build SA permissions) rather than app logic. Once this is green, layering Stripe/email/auth on top in later tickets has a working foundation underneath.

**Out of 0007: custom domains.** Ticket 0008 handles `staging.carddroper.com` + `api.staging.carddroper.com` Cloudflare CNAMEs and Cloud Run domain mappings. The frontend in 0007 points at the backend's `*.run.app` URL directly.

**Execution model:** mixed.
- **Phase 0** is agent-executed (orchestrator dispatches backend-builder to write `cloudbuild.yaml` at repo root).
- **Phases 1-5** are user-executed: GitHub ↔ Cloud Build connection, IAM role grants, trigger creation, merge to `main`, and build verification.

Reference docs:
- `doc/operations/deployment.md` — GCP deployment plan + secret names already in Secret Manager.
- `doc/operations/environments.md` — staging sizing + scale-to-zero policy.
- `doc/PLAN.md` §10.4 — why staging push is early.

## Pre-requisites

All ticket 0006 deliverables resolved (project, Cloud SQL `RUNNABLE`, three secrets present, runtime service account created). Confirm with:

```bash
gcloud config get-value project                                                    # carddroper-staging
gcloud sql instances describe carddroper-staging-db --format="value(state)"        # RUNNABLE
gcloud secrets list --format="value(name)" | wc -l                                 # 3
```

Additional one-time checks:

1. **GitHub repo is pushed.** `main` exists on `github.com/xingxc/carddroper` and reflects the current scaffold.
2. **Project number** — needed for the Cloud Build service account email:
   ```bash
   PROJECT_NUMBER=$(gcloud projects describe carddroper-staging --format="value(projectNumber)")
   echo "Project number: $PROJECT_NUMBER"
   # Cloud Build SA will be: $PROJECT_NUMBER@cloudbuild.gserviceaccount.com
   ```

## Acceptance

### Phase 0: Write `cloudbuild.yaml` at repo root (agent-executed)

Orchestrator dispatches **backend-builder** with a brief to write `/Users/johnxing/mini/postapp/cloudbuild.yaml`. The file must implement this sequence, in order:

1. **Build backend image** — `docker build ./backend -t us-west1-docker.pkg.dev/$PROJECT_ID/carddroper-repo/backend:$SHORT_SHA`.
2. **Push backend image** to Artifact Registry.
3. **Run migrations** — `alembic upgrade head` using the just-built backend image. Connect to Cloud SQL via Auth Proxy sidecar (TCP, localhost:5432). The `MIGRATION_DATABASE_URL` comes from the `carddroper-migration-database-url` Secret Manager secret. Standard pattern: `gcr.io/google-appengine/exec-wrapper` with `-s $PROJECT_ID:us-west1:carddroper-staging-db`.
4. **Deploy backend to Cloud Run** — service name `carddroper-backend`, region `us-west1`:
   - `--image` = the pushed backend image.
   - `--service-account` = `carddroper-runtime@carddroper-staging.iam.gserviceaccount.com`.
   - `--add-cloudsql-instances` = `$PROJECT_ID:us-west1:carddroper-staging-db`.
   - `--set-secrets` = `DATABASE_URL=carddroper-database-url:latest,JWT_SECRET=carddroper-jwt-secret:latest`.
   - `--min-instances=0`, `--max-instances=3`, `--allow-unauthenticated`.
   - `--port=8000`.
5. **Capture backend URL** into `/workspace/backend_url.txt` via `gcloud run services describe ... --format="value(status.url)"`.
6. **Build frontend image** — `docker build ./frontend --build-arg NEXT_PUBLIC_API_BASE_URL=$(cat /workspace/backend_url.txt) -t us-west1-docker.pkg.dev/$PROJECT_ID/carddroper-repo/frontend:$SHORT_SHA`.
7. **Push frontend image**.
8. **Deploy frontend to Cloud Run** — service name `carddroper-frontend`:
   - `--min-instances=0`, `--max-instances=3`, `--allow-unauthenticated`, `--port=3000`.
   - No secrets, no Cloud SQL attachment (frontend is a static/SSR container).

Also include at the end of the file:
```yaml
options:
  logging: CLOUD_LOGGING_ONLY
images:
  - us-west1-docker.pkg.dev/$PROJECT_ID/carddroper-repo/backend:$SHORT_SHA
  - us-west1-docker.pkg.dev/$PROJECT_ID/carddroper-repo/frontend:$SHORT_SHA
```

### Phase 1: Connect GitHub repo to Cloud Build (user, browser)

1. In GCP Console: **Cloud Build** → **Triggers** → **Manage repositories** (or "Connect Repository" button).
2. Region: `us-west1`. Source: **GitHub (Cloud Build GitHub App)**.
3. Authenticate to GitHub → install the **Google Cloud Build** GitHub App on the `xingxc/carddroper` repo (or the account-wide install, restricted to that repo).
4. Click through the consent screen; return to GCP Console.
5. Confirm the repo appears under "Connected repositories".

Verify: `gcloud builds repositories list --connection=<auto-created-connection-name> --region=us-west1` lists `carddroper`. (The connection name is usually something like `github-cloudbuild` or the GitHub org name — the console shows it.)

### Phase 2: Grant Cloud Build service account the IAM roles it needs (user, CLI)

The default Cloud Build SA is `$PROJECT_NUMBER@cloudbuild.gserviceaccount.com`. Grant four roles:

```bash
CB_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"

# Deploy services to Cloud Run
gcloud projects add-iam-policy-binding carddroper-staging \
    --member="serviceAccount:${CB_SA}" \
    --role="roles/run.admin"

# Act as the runtime SA when setting --service-account on Cloud Run
gcloud projects add-iam-policy-binding carddroper-staging \
    --member="serviceAccount:${CB_SA}" \
    --role="roles/iam.serviceAccountUser"

# Connect to Cloud SQL (for the migration step's Auth Proxy)
gcloud projects add-iam-policy-binding carddroper-staging \
    --member="serviceAccount:${CB_SA}" \
    --role="roles/cloudsql.client"

# Read the migration DATABASE_URL secret during the build
gcloud projects add-iam-policy-binding carddroper-staging \
    --member="serviceAccount:${CB_SA}" \
    --role="roles/secretmanager.secretAccessor"
```

`roles/artifactregistry.writer` is granted to the Cloud Build SA by default; no action needed for image push.

Verify:
```bash
gcloud projects get-iam-policy carddroper-staging \
    --flatten="bindings[].members" \
    --filter="bindings.members:serviceAccount:${CB_SA}" \
    --format="value(bindings.role)" | sort
# Should include: roles/cloudbuild.builds.builder, roles/cloudsql.client,
#                 roles/iam.serviceAccountUser, roles/run.admin,
#                 roles/secretmanager.secretAccessor
```

### Phase 3: Create the Cloud Build trigger (user, browser)

1. GCP Console: **Cloud Build** → **Triggers** → **Create Trigger**.
2. **Name**: `carddroper-staging-main`.
3. **Region**: `us-west1`.
4. **Event**: Push to a branch.
5. **Source**: 2nd gen. Repository: `xingxc/carddroper`. Branch: `^main$`.
6. **Configuration**: Cloud Build configuration file (yaml or json). Location: `cloudbuild.yaml` (repository).
7. **Service account**: leave as default (`$PROJECT_NUMBER@cloudbuild.gserviceaccount.com`) since we granted it all the needed roles in Phase 2.
8. **Create**.

Verify: `gcloud builds triggers list --region=us-west1` shows `carddroper-staging-main` with filter `^main$`.

### Phase 4: Merge `dev` → `main` to fire the trigger (user, CLI)

```bash
cd /Users/johnxing/mini/postapp
git checkout main
git pull
git merge --ff-only dev   # fast-forward only; dev should already contain main's history
git push origin main
```

Cloud Build should fire within ~10 seconds. Watch:

```bash
gcloud builds list --region=us-west1 --limit=1
# Or in the browser: Cloud Build → History
```

Expected first-run duration: 8-15 minutes (first build has no layer cache; subsequent builds will be 3-6 minutes).

### Phase 5: Verify the deployed services

After the build completes with `SUCCESS`:

```bash
# Get the URLs
BACKEND_URL=$(gcloud run services describe carddroper-backend --region=us-west1 --format="value(status.url)")
FRONTEND_URL=$(gcloud run services describe carddroper-frontend --region=us-west1 --format="value(status.url)")
echo "Backend:  $BACKEND_URL"
echo "Frontend: $FRONTEND_URL"

# Health check
curl -sSf "${BACKEND_URL}/health"
# Expected: {"status":"ok"}

# Frontend SSR smoke
curl -sSf "$FRONTEND_URL" | grep -i "carddroper"
# Expected: a line containing "Carddroper" from the <h1>

# Browser smoke (user): open $FRONTEND_URL — see the styled <h1>Carddroper</h1>.
```

## Verification

**Automated checks** (run after Phase 5 is green):

```bash
# Build succeeded
gcloud builds list --region=us-west1 --limit=1 --format="value(status)"   # SUCCESS

# Both services deployed
gcloud run services list --region=us-west1 --format="value(metadata.name)" | sort
# Expected: carddroper-backend, carddroper-frontend

# Images in registry
gcloud artifacts docker images list us-west1-docker.pkg.dev/carddroper-staging/carddroper-repo --format="value(IMAGE)" | sort -u
# Expected: .../backend, .../frontend

# Migration ran (check alembic version table on Cloud SQL)
# (Optional — if you have gcloud sql connect set up, otherwise skip.)
```

**Functional smoke:**

- `curl $BACKEND_URL/health` returns `{"status":"ok"}` with HTTP 200.
- `curl $BACKEND_URL/auth/me` returns HTTP 401 (confirms auth middleware runs in production, not just `/health`).
- `curl $FRONTEND_URL` returns HTML containing "Carddroper" (confirms SSR works).
- User opens `$FRONTEND_URL` in browser → sees styled `<h1>Carddroper</h1>` (confirms Tailwind CSS was built into the image).
- Cloud Run logs for `carddroper-backend` show no errors on startup (`gcloud run services logs read carddroper-backend --region=us-west1 --limit=50`).

## Out of scope

- Custom domain mapping (ticket 0008).
- Cloudflare DNS records (ticket 0008).
- Stripe webhook configuration (deferred; webhook endpoint doesn't exist yet).
- SendGrid secrets (deferred).
- Prod GCP project setup.
- Adding more than one Cloud Build trigger (e.g., PR triggers, tag triggers for prod).
- Stopping/parking the Cloud SQL instance (covered in deployment.md ops section).
- Pre-commit hooks, branch protection, CI tests on PR (PLAN.md §11 "operational, to finalize before launch").

## Report

User pastes:
1. Output of the verification block from Phase 5.
2. The two `*.run.app` URLs.
3. A brief "browser opened, saw styled Carddroper" confirmation.

Orchestrator handles:
- Verifying each line.
- Updating `doc/operations/deployment.md` checkboxes:
  - `carddroper-staging` Cloud Build trigger wired to `main`.
  - `carddroper-staging` deployed (backend + frontend).
- Adding Resolution note + flipping ticket status.
