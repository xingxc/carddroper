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
2. **Project number** (occasionally needed for other SA references):
   ```bash
   PROJECT_NUMBER=$(gcloud projects describe carddroper-staging --format="value(projectNumber)")
   echo "Project number: $PROJECT_NUMBER"
   ```
   Carddroper's build SA is project-scoped and user-managed (`carddroper-build@<project-id>.iam.gserviceaccount.com`), so we don't need `$PROJECT_NUMBER` for it — but you may see it referenced in IAM audit logs and the default compute SA's email.

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

### Phase 2: Create a dedicated build service account and grant it IAM roles (user, CLI)

**Important — Google's 2024+ policy:** Cloud Build triggers must use a **user-managed** service account. The legacy `<PROJECT_NUMBER>@cloudbuild.gserviceaccount.com` SA is Google-managed and is now rejected at build time with: `invalid value for build.service_account: provide a user-managed service account`. The default compute SA (`<PROJECT_NUMBER>-compute@developer.gserviceaccount.com`) is user-managed but ships with `roles/editor` (too broad for least-privilege). The correct pattern is to create a dedicated build SA with scoped roles — symmetric to `carddroper-runtime` which we created in ticket 0006 for the Cloud Run runtime.

Create `carddroper-build` and grant it six roles:

```bash
BUILD_SA="carddroper-build@carddroper-staging.iam.gserviceaccount.com"

# Create the SA
gcloud iam service-accounts create carddroper-build \
    --display-name="Carddroper Cloud Build" \
    --project=carddroper-staging

# Grant the roles the build pipeline needs
for ROLE in \
    run.admin \
    iam.serviceAccountUser \
    cloudsql.client \
    secretmanager.secretAccessor \
    logging.logWriter \
    artifactregistry.writer
do
  gcloud projects add-iam-policy-binding carddroper-staging \
    --member="serviceAccount:${BUILD_SA}" \
    --role="roles/${ROLE}" \
    --condition=None
done
```

Role rationale:
- `run.admin` — deploy Cloud Run services.
- `iam.serviceAccountUser` — "act as" `carddroper-runtime` when attaching it to the deployed service.
- `cloudsql.client` — open the Auth Proxy connection for the migration step.
- `secretmanager.secretAccessor` — read `carddroper-migration-database-url` during the migration step.
- `logging.logWriter` — write to Cloud Logging. (The legacy Cloud Build SA had this implicitly; user-managed SAs need it explicitly.)
- `artifactregistry.writer` — push built images. (The legacy SA had this implicitly too.)

Verify:
```bash
gcloud projects get-iam-policy carddroper-staging \
    --flatten="bindings[].members" \
    --filter="bindings.members:serviceAccount:${BUILD_SA}" \
    --format="value(bindings.role)" | sort
# Expected: 6 roles, in alphabetical order.
```

**Gotcha about the IAM browser view:** GCP's IAM page hides Google-managed service agents by default. If you're looking for `carddroper-build` in the browser, use the search box (our custom SA will always appear). The `Include Google-provided role grants` toggle only affects Google-managed agents.

### Phase 3: Create the Cloud Build trigger (user, browser + CLI)

Cloud Build's GitHub integration uses "2nd gen" connections. You create a host connection once (authenticates GCP to GitHub), link specific repos to it, then create triggers that reference the linked repo.

**Step 3a — Create the host connection + link the repo** (browser):

1. GCP Console: **Cloud Build** → **Triggers** → **Manage repositories** (or "Connect Repository").
2. Region: `us-west1`. Generation: **2nd gen**.
3. **Create host connection** → name: `github-xingxc`. Encryption: leave default (Google-managed; no KMS needed for staging).
4. Authenticate to GitHub → install the Google Cloud Build GitHub App on the `xingxc/carddroper` repo.
5. **Link repository** → connection: `github-xingxc` → select `xingxc/carddroper` → leave auto-generated repository resource name (`xingxc-carddroper`).

Verify: `gcloud builds repositories list --connection=github-xingxc --region=us-west1` lists `xingxc-carddroper`.

**Step 3b — Create the trigger** (browser):

1. **Triggers** → **Create Trigger**.
2. Name: `carddroper-staging-main`; Region: `us-west1`.
3. Event: **Push to a branch**.
4. Source: **2nd gen**. Repository: `xingxc-carddroper`. Branch: `^main$`.
5. Configuration: **Cloud Build configuration file (yaml or json)**. Location: Repository, path `cloudbuild.yaml` (default).
6. Service account: **ignore the dropdown** — it defaults to the compute SA and doesn't surface custom SAs. We'll fix it in Step 3c via CLI. Just leave whatever's selected and click Create.

**Step 3c — Point the trigger at the `carddroper-build` SA** (CLI):

The browser dropdown only surfaces a subset of SAs and often reverts to the compute SA on save. Fix via export → sed → import (there is no `gcloud builds triggers update` command; the proper edit flow is export/import, and at time of writing both commands live under `gcloud beta`):

```bash
gcloud beta builds triggers export carddroper-staging-main \
    --region=us-west1 \
    --destination=trigger.yaml

# Replace whatever SA the browser saved with carddroper-build
sed -i '' "s|serviceAccounts/.*$|serviceAccounts/carddroper-build@carddroper-staging.iam.gserviceaccount.com|" trigger.yaml

gcloud beta builds triggers import \
    --region=us-west1 \
    --source=trigger.yaml
```

macOS `sed -i ''` syntax; Linux users drop the `''`.

Verify:

```bash
gcloud builds triggers describe carddroper-staging-main --region=us-west1 --format="value(serviceAccount)"
# Expected: projects/carddroper-staging/serviceAccounts/carddroper-build@carddroper-staging.iam.gserviceaccount.com
```

Cleanup: `rm trigger.yaml` (it's scratch — the authoritative trigger config lives in GCP).

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
