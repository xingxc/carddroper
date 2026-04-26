# GCP deployment tutorial — chassis adopter walkthrough

> **Audience:** someone standing up a new staging (or prod) environment from this chassis on Google Cloud. You finish with a live Cloud Run backend + frontend, custom domains, Stripe webhooks wired, email deliverability validated, and the chassis-discipline IAM cleanup applied.
>
> **As-of:** 2026-04-25. The GCP surface and chassis design will continue to evolve; this tutorial captures the procedure that landed `carddroper-staging` end-to-end. Prefer `doc/operations/deployment.md` (reference) and `doc/operations/chassis-contract.md` (invariants) over this tutorial when they disagree — those are the authoritative source.
>
> **Why a tutorial in addition to the reference docs?** The reference docs answer "what is the rule" and "what is the topology." This tutorial answers "what do I do next, in what order" with copy-paste commands and inline gotchas captured from real rollouts. It pulls together what's spread across `doc/operations/{deployment,environments,development,testing}.md`, `doc/operations/chassis-contract.md`, and tickets 0006, 0007, 0008, 0010, 0021, 0023, 0018.
>
> **Scope:** chassis layer only. Adopter project-specific work (pricing, features, branding) is out of scope — those decisions land in your own tickets after the chassis is deployed.

---

## 0 — Before you start

### Accounts you need

- **GCP** with a billing account in `OPEN` state.
- **Stripe** (test mode for staging; live mode for prod).
- **SendGrid** (one account; staging and prod use distinct API keys).
- **Cloudflare** (or any DNS provider; tutorial uses Cloudflare).
- **GitHub** (or any git provider Cloud Build supports; tutorial uses GitHub).

### Tools installed locally

- `gcloud` CLI (any version from the last 12 months) — `gcloud auth login`, `gcloud auth application-default login`.
- Docker Desktop (for `docker-compose`).
- Python 3.11, Node 20 LTS.
- A `psql` client (optional but useful for DB spot-checks).

### Naming convention used in this tutorial

- `<your-project>` — your GCP project ID (e.g., `myapp-staging`).
- `<your-domain>` — your apex domain (e.g., `myapp.com`).
- Chassis-internal names retain the `carddroper-*` prefix; if you fork and rename, do a single repo-wide search-replace of `carddroper` → `<your-project>` and propagate to GCP resource names accordingly. The chassis tries to keep project-specific naming inside the repo to a minimum.

### Mental model

Three environments per `doc/operations/environments.md`:

```
dev (local)         →    staging (auto on push to main)    →    prod (auto on git tag v*.*.*)
docker-compose         GCP project carddroper-staging          GCP project carddroper-prod
Stripe test            Stripe test                              Stripe live
                       Cloud SQL shared-core, no PITR           Cloud SQL 1 vCPU, PITR on
                       min-instances=0                          min-instances=1 (backend)
```

Per-environment isolation is the chassis discipline. A compromised staging key cannot affect prod; a botched migration in staging cannot corrupt prod data.

---

## 1 — Local-first verification

Before touching GCP, prove the chassis works on your laptop.

```bash
git clone <your-fork-of-the-chassis>
cd <repo>

# Three env files (per development.md §Env-var tiers)
cp .env.example .env                         # root: docker-compose ${VAR} substitutions
cp backend/.env.example backend/.env         # backend container runtime
cp frontend/.env.example frontend/.env.local # frontend (only used outside docker-compose)

# Generate JWT_SECRET (≥32 chars per chassis-contract.md)
echo "JWT_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" >> backend/.env

# (Optional for local) Stripe test keys, SendGrid key — skip for now if you just want healthz green.
docker-compose up --build
```

In another terminal:

```bash
cd backend
.venv/bin/python scripts/smoke_healthz.py    # SMOKE OK: healthz
```

If healthz fails, fix locally before going further. **The single most common cause of cloud-side debugging is "I didn't validate locally first."**

References: `doc/operations/development.md` (full local setup), `doc/operations/testing.md` (smoke pattern).

---

## 2 — GCP project foundation

This phase mirrors ticket 0006 verbatim with project-name placeholders. Read 0006 for the inline rationale on each step.

### 2.1 Create the project, link billing, enable APIs

```bash
PROJECT=<your-project>             # e.g., myapp-staging
BILLING=$(gcloud billing accounts list --filter="open=true" --format='value(name)' | head -1)

gcloud projects create $PROJECT --name="$PROJECT"
gcloud billing projects link $PROJECT --billing-account=$BILLING

gcloud config set project $PROJECT
gcloud config set run/region us-west1
gcloud config set artifacts/location us-west1

gcloud services enable \
  run.googleapis.com sqladmin.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com \
  compute.googleapis.com iam.googleapis.com
```

GCP project IDs are globally unique. If your preferred ID is taken, append a suffix (`myapp-staging-x4j2`).

### 2.2 Artifact Registry

```bash
gcloud artifacts repositories create carddroper-repo \
  --repository-format=docker --location=us-west1 \
  --description="Carddroper container images"
```

### 2.3 Cloud SQL (Postgres 16)

**Two gotchas captured by 0006:**

1. New SQL instances default to `ENTERPRISE_PLUS` which rejects shared-core tiers like `db-f1-micro`. Use `--edition=ENTERPRISE`.
2. Cloud SQL's password policy requires four character classes (upper + lower + digit + non-alphanumeric). `openssl rand -base64 24` alone may not satisfy this; suffix `Aa1!` to force compliance.

```bash
gcloud sql instances create carddroper-staging-db \
  --database-version=POSTGRES_16 \
  --tier=db-f1-micro \
  --edition=ENTERPRISE \
  --region=us-west1 \
  --availability-type=zonal \
  --storage-size=10 --storage-type=SSD \
  --no-backup

ROOT_PW="$(openssl rand -base64 24)Aa1!"
echo "Root password (save to password manager): $ROOT_PW"
gcloud sql users set-password postgres --instance=carddroper-staging-db --password="$ROOT_PW"

gcloud sql databases create carddroper --instance=carddroper-staging-db

APP_PW="$(openssl rand -base64 24)Aa1!"
echo "App password (paste into DATABASE_URL secret in 3.1): $APP_PW"
gcloud sql users create carddroper --instance=carddroper-staging-db --password="$APP_PW"
```

For prod, swap sizing: `--tier=db-custom-1-3840`, `--backup-start-time=...`, `--enable-point-in-time-recovery`. See `environments.md §Provisioning asymmetry`.

---

## 3 — Service accounts (chassis discipline)

The chassis requires **per-service runtime SAs with explicit `--service-account` flags** in `cloudbuild.yaml`. This is enforced by deleting the default compute SA in step 7 — any future Cloud Run deploy missing the flag will fail loudly. See `doc/operations/deployment.md §Service-account discipline` for the full discipline rationale.

You'll create three SAs in this phase:

1. **Backend runtime** (`carddroper-runtime`) — Secret Manager Accessor + Cloud SQL Client.
2. **Frontend runtime** (`carddroper-frontend-runtime`) — zero project-level roles.
3. **Cloud Build** (`carddroper-build`) — six scoped roles for the build pipeline.

### 3.1 Backend runtime SA

```bash
gcloud iam service-accounts create carddroper-runtime \
  --display-name="Carddroper Cloud Run runtime"

gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:carddroper-runtime@${PROJECT}.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor" --condition=None

gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:carddroper-runtime@${PROJECT}.iam.gserviceaccount.com" \
  --role="roles/cloudsql.client" --condition=None
```

### 3.2 Frontend runtime SA (zero-roles)

The Next.js frontend makes **no outbound GCP API calls**. It needs no project-level roles. Cloud Run handles its logging/metrics via service-level grants, not the runtime SA.

```bash
gcloud iam service-accounts create carddroper-frontend-runtime \
  --display-name="Carddroper frontend runtime"

# No add-iam-policy-binding calls. Zero roles is intentional.
```

### 3.3 Cloud Build SA (the 0007 gotcha)

**Google's 2024+ policy:** Cloud Build triggers must use a user-managed SA. The legacy `<NUM>@cloudbuild.gserviceaccount.com` is rejected; the default compute SA carries `Editor` (we delete it in step 7 anyway). Create a dedicated build SA with six scoped roles:

```bash
BUILD_SA="carddroper-build@${PROJECT}.iam.gserviceaccount.com"

gcloud iam service-accounts create carddroper-build \
  --display-name="Carddroper Cloud Build"

for ROLE in run.admin iam.serviceAccountUser cloudsql.client \
            secretmanager.secretAccessor logging.logWriter artifactregistry.writer; do
  gcloud projects add-iam-policy-binding $PROJECT \
    --member="serviceAccount:${BUILD_SA}" \
    --role="roles/${ROLE}" --condition=None
done
```

Role rationale (per ticket 0007 §Phase 1):
- `run.admin` — deploy Cloud Run services
- `iam.serviceAccountUser` — `actAs` runtime SAs at deploy time
- `cloudsql.client` — Auth Proxy connection during the migration step
- `secretmanager.secretAccessor` — read `MIGRATION_DATABASE_URL` secret
- `logging.logWriter` — write to Cloud Logging
- `artifactregistry.writer` — push built images

### 3.4 Grant Cloud Build `actAs` on each runtime SA

Cloud Build needs explicit `iam.serviceAccountUser` permission on each runtime SA it deploys. Without this, deploys fail with `PERMISSION_DENIED: iam.serviceaccounts.actAs`.

```bash
for SA in carddroper-runtime carddroper-frontend-runtime; do
  gcloud iam service-accounts add-iam-policy-binding \
    ${SA}@${PROJECT}.iam.gserviceaccount.com \
    --member="serviceAccount:${BUILD_SA}" \
    --role="roles/iam.serviceAccountUser"
done
```

---

## 4 — Secrets in Secret Manager

The chassis reads secrets via Cloud Run's `--set-secrets` mechanism. All names match `doc/operations/deployment.md §Secrets layout`.

### 4.1 Database URLs

```bash
SQL_CONN=$(gcloud sql instances describe carddroper-staging-db --format="value(connectionName)")
# Looks like: <your-project>:us-west1:carddroper-staging-db

# Runtime DATABASE_URL — uses Cloud SQL Auth Proxy unix socket
printf "postgresql+asyncpg://carddroper:${APP_PW}@/carddroper?host=/cloudsql/${SQL_CONN}" | \
  gcloud secrets create carddroper-database-url --data-file=-

# Migration DATABASE_URL — TCP for the Auth Proxy sidecar in Cloud Build
printf "postgresql+asyncpg://carddroper:${APP_PW}@127.0.0.1:5432/carddroper" | \
  gcloud secrets create carddroper-migration-database-url --data-file=-
```

The `postgresql+asyncpg://` prefix is enforced by the chassis (`validate_database_url` per `chassis-contract.md`). Plain `postgresql://` will refuse to start.

### 4.2 JWT secret

Chassis-contract minimum is 32 characters. Generate 48 bytes for headroom:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))" | tr -d '\n' | \
  gcloud secrets create carddroper-jwt-secret --data-file=-
```

### 4.3 SendGrid API key + 5 templates

When `SENDGRID_SANDBOX=false` and `SENDGRID_API_KEY` is set, the chassis requires all five template IDs to be set (`validate_sendgrid_production` per chassis-contract.md):

```bash
echo -n "SG.your-real-api-key" | gcloud secrets create carddroper-sendgrid-api-key --data-file=-

for TPL in verify-email reset-password change-email email-changed credits-purchased; do
  echo -n "d-replace-with-real-template-id" | \
    gcloud secrets create carddroper-sendgrid-template-${TPL} --data-file=-
done
```

Get the template IDs from SendGrid Dashboard → Email API → Dynamic Templates. Each template has a `d-...` ID.

### 4.4 Stripe secrets (deferred until step 8 — webhook endpoint)

```bash
echo -n "sk_test_..." | gcloud secrets create carddroper-stripe-secret-key --data-file=-
# carddroper-stripe-webhook-secret created in step 8 after the Stripe Dashboard webhook is registered.
```

Verify all secrets are present:

```bash
gcloud secrets list --format="value(name)" | sort
```

---

## 5 — Cloud Build trigger

### 5.1 Connect the GitHub repo (browser, one-time per project)

GCP Console → **Cloud Build** → **Triggers** → **Manage repositories** → **2nd gen** → region `us-west1`:

1. **Create host connection** — name it `github-<your-org>`. Authenticate via the Google Cloud Build GitHub App; install on the chassis repo.
2. **Link repository** — select your repo; accept the auto-generated resource name.

Verify: `gcloud builds repositories list --connection=github-<your-org> --region=us-west1`.

### 5.2 Create the trigger (browser)

**Triggers** → **Create Trigger**:

- Name: `<your-project>-main`
- Region: `us-west1`
- Event: **Push to a branch**
- Source: **2nd gen**, your linked repo, branch `^main$`
- Configuration: **Cloud Build configuration file** at `cloudbuild.yaml`
- **Service account: ignore the dropdown** — it doesn't surface custom SAs and reverts to the compute SA on save. Fix in 5.3.

### 5.3 Repoint the trigger SA via gcloud (the 0007 Phase 2c gotcha)

The browser dropdown drops back to the compute SA. There is no `gcloud builds triggers update` command; the only edit path is export-edit-import:

```bash
gcloud beta builds triggers export <your-project>-main \
  --region=us-west1 --destination=trigger.yaml

# macOS sed; Linux: drop the empty quotes after -i
sed -i '' "s|serviceAccounts/.*$|serviceAccounts/${BUILD_SA}|" trigger.yaml

gcloud beta builds triggers import --region=us-west1 --source=trigger.yaml

rm trigger.yaml

# Verify
gcloud builds triggers describe <your-project>-main --region=us-west1 \
  --format="value(serviceAccount)"
# Expected: projects/<your-project>/serviceAccounts/carddroper-build@<your-project>.iam.gserviceaccount.com
```

### 5.4 Cloud Build substitution variables (browser)

In the trigger config (browser), add a substitution variable for the frontend Stripe key:

- `_STRIPE_PUBLISHABLE_KEY` = `pk_test_...` (from Stripe Dashboard → Developers → API keys)

This variable is referenced in `cloudbuild.yaml` step 5 as `--build-arg NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=$_STRIPE_PUBLISHABLE_KEY`.

---

## 6 — First deploy (`*.run.app`)

`cloudbuild.yaml` is committed in the chassis. It runs:

1. Build backend image
2. Push backend image
3. Run alembic migrations via Cloud SQL Auth Proxy (downloaded at runtime via Python `urllib` since the slim Python image has no `wget`/`curl`)
4. Deploy backend to Cloud Run with secrets + Cloud SQL socket + `--service-account=carddroper-runtime@...`
5. Build frontend image with `NEXT_PUBLIC_*` build args baked in
6. Push frontend image
7. Deploy frontend to Cloud Run with `--service-account=carddroper-frontend-runtime@...`

Trigger it:

```bash
git checkout main
git push origin main
```

Watch:

```bash
gcloud builds list --region=us-west1 --limit=1
```

First build: 8–15 minutes (no layer cache). Subsequent: 3–6 minutes.

When `SUCCESS`:

```bash
BACKEND_URL=$(gcloud run services describe carddroper-backend --region=us-west1 --format="value(status.url)")
FRONTEND_URL=$(gcloud run services describe carddroper-frontend --region=us-west1 --format="value(status.url)")

curl -sSf "$BACKEND_URL/healthz"      # {"status":"ok",...}
curl -sS -o /dev/null -w "%{http_code}\n" "$BACKEND_URL/auth/me"   # 401
curl -sSf "$FRONTEND_URL" | grep -i "<your-app-name>"
```

If migration step fails: see `doc/operations/deployment.md §Why migrate-before-deploy` and ticket 0007 Phase 0 step 3 for the migration step's cloud-build-vs-shell-vs-runtime variable escaping rules. Most common cause: `$VAR` should have been `$$VAR` in `cloudbuild.yaml` (Cloud Build substitutes single-`$` at render time).

---

## 7 — Custom domains (Cloudflare + Cloud Run)

### 7.1 Cloudflare DNS records

For each hostname in `environments.md §DNS`, add:

| Type | Name | Target | Proxy | TTL |
|---|---|---|---|---|
| CNAME | `staging` | `ghs.googlehosted.com` | **DNS-only (grey)** | Auto |
| CNAME | `api.staging` | `ghs.googlehosted.com` | **DNS-only (grey)** | Auto |

Verify with `dig +short CNAME staging.<your-domain>` — both should return `ghs.googlehosted.com.` within 60s.

**Stay DNS-only for v1.** Proxy mode (orange cloud) requires Cloudflare SSL "Full (strict)" + WAF carve-outs for Stripe webhooks. Defer until you have a reason.

### 7.2 Cloud Run domain mappings (gcloud beta — 0008 gotcha)

`gcloud run domain-mappings` (GA) doesn't accept `--region` for regional services yet. Use `gcloud beta`:

```bash
gcloud beta run domain-mappings create \
  --service=carddroper-frontend --domain=staging.<your-domain> --region=us-west1

gcloud beta run domain-mappings create \
  --service=carddroper-backend --domain=api.staging.<your-domain> --region=us-west1
```

If either errors with **domain ownership not verified**, do the Search Console dance:

1. Open `https://www.google.com/webmasters/verification/verification?domain=<your-domain>`
2. Choose **Domain** (verifies the whole zone — one-time).
3. Add the `TXT @ "google-site-verification=..."` record at Cloudflare.
4. Wait 30s, click **Verify** in Search Console.
5. Re-run the two `gcloud beta run domain-mappings create` commands.

The Google account in Search Console must match `gcloud auth list`.

### 7.3 Wait for SSL (10–30 min, occasionally hours)

Edge cert propagation lags `Ready: True` by ~10–20 minutes (0008 deviation #4 captured this — `Ready: True` reported but TLS still failed for 17 minutes).

```bash
while true; do
  BACK=$(curl -sS -o /dev/null -w "%{http_code}" https://api.staging.<your-domain>/healthz || echo ERR)
  FRONT=$(curl -sS -o /dev/null -w "%{http_code}" https://staging.<your-domain> || echo ERR)
  echo "$(date +%H:%M:%S) backend=$BACK frontend=$FRONT"
  [ "$BACK" = "200" ] && [ "$FRONT" = "200" ] && break
  sleep 30
done
```

### 7.4 Bake the custom backend URL into the frontend bundle

The frontend's `NEXT_PUBLIC_API_BASE_URL` is set at build time via `cloudbuild.yaml` step 5's `--build-arg`. Update that arg from `https://<service>.<hash>.run.app` to your custom domain. Then push to trigger a rebuild that bakes the new URL into the JS bundle.

`grep` the served HTML to confirm:

```bash
curl -sS https://staging.<your-domain> | grep -o 'api.staging.<your-domain>'
# At least one hit (after the auth/billing flows wire up that import)
```

Reference: `doc/operations/development.md §NEXT_PUBLIC_* runtime debugging` for why `printenv` won't show this in the running container — `NEXT_PUBLIC_*` is inlined at build time.

---

## 8 — Stripe webhook + secrets

### 8.1 Register the webhook in Stripe Dashboard

Stripe Dashboard → **Developers** → **Webhooks** → **Add endpoint**:

- **Endpoint URL:** `https://api.staging.<your-domain>/billing/webhook`
- **Listen to:** _Events on your account_
- **Events:** start with the curated list the chassis handles. As of 2026-04-25 that's **only** `payment_intent.succeeded`. Adding events that have no handler causes `stripe_events` rows to accumulate without action — harmless but noisy. Add events as new chassis tickets land handlers (e.g., subscription events in 0024).

After clicking **Add endpoint**, copy the **signing secret** (starts with `whsec_...`).

### 8.2 Store the signing secret + STRIPE_SECRET_KEY in Secret Manager

```bash
echo -n "whsec_..." | gcloud secrets create carddroper-stripe-webhook-secret --data-file=-
# (carddroper-stripe-secret-key already created in step 4.4)
```

### 8.3 Wire the secrets into Cloud Run + flip BILLING_ENABLED

`cloudbuild.yaml` step 4 already references both Stripe secrets in `--set-secrets` and sets `BILLING_ENABLED=true` in `--set-env-vars`. Push to redeploy with the new wiring.

Verify on staging: register a fresh user, log in, navigate to `/app/billing`, complete a `$5` topup with test card `4242 4242 4242 4242`. Confirm:

1. Customer appears in Stripe Dashboard (test mode).
2. `balance_ledger` has a `topup` row tied to the `stripe_event_id`.

Reference: `doc/issues/0021-stripe-foundation.md` (chassis primitives), `doc/issues/0023-payg-topup.md` (PAYG topup flow), `doc/systems/payments.md` (full payment design).

---

## 9 — Email deliverability (SendGrid + Cloudflare)

The chassis defaults to SendGrid Dynamic Templates. For deliverability:

1. SendGrid Dashboard → **Settings** → **Sender Authentication** → **Authenticate Your Domain**.
2. Choose your DNS host (Cloudflare). SendGrid generates DNS records (typically 1 SPF/CNAME + 2 DKIM CNAMEs).
3. Add those records at Cloudflare with **proxy status: DNS-only** (grey).
4. Click **Verify** in SendGrid. Should pass within minutes.
5. Add a DMARC record at Cloudflare:
   - `TXT _dmarc "v=DMARC1; p=quarantine; rua=mailto:dmarc@<your-domain>"`
   - Start at `p=quarantine`, not `p=reject`, until DMARC reports confirm legitimate mail isn't being dropped. Promote to `p=reject` after a few weeks of clean reports.
6. Verify deliverability with a real signup. Check the inbox + spam folder.

Reference: `doc/operations/environments.md §Email DNS`, ticket 0010 (initial SendGrid setup), and (when written) ticket 0019 (full SPF/DKIM/DMARC + Sender Authentication ticket).

---

## 10 — Default compute SA cleanup (chassis discipline)

This is the 0018 IAM hardening. Done after both Cloud Run services are on dedicated SAs (steps 3 + 6).

Reference: `doc/operations/deployment.md §Default compute SA cleanup` for the exact 7-step playbook. Summary:

```bash
PROJECT_NUMBER=$(gcloud projects describe $PROJECT --format='value(projectNumber)')

# 1. Verify dependencies — only services without --service-account would show the default SA
gcloud run services list --project=$PROJECT \
  --format='table(metadata.name,spec.template.spec.serviceAccountName)'
# Expected: backend → carddroper-runtime, frontend → carddroper-frontend-runtime
# If anything shows ${PROJECT_NUMBER}-compute@..., do NOT proceed; add an explicit SA first.

# 2. Delete the default compute SA
gcloud iam service-accounts delete \
  ${PROJECT_NUMBER}-compute@developer.gserviceaccount.com --project=$PROJECT

# 3. Stale-binding cleanup — find the ?uid=... suffix in get-iam-policy output, then:
gcloud projects remove-iam-policy-binding $PROJECT \
  --member="deleted:serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com?uid=<paste>" \
  --role="roles/editor" --condition=None
```

After this, any future Cloud Run deploy missing `--service-account` fails at deploy time with a clear error rather than silently inheriting `Editor`. That's the chassis-reliability principle.

---

## 11 — End-to-end verification

Run the smoke battery against the live custom domains:

```bash
cd backend
.venv/bin/python scripts/smoke_healthz.py
.venv/bin/python scripts/smoke_auth.py --expected-cookie-domain .staging.<your-domain>
.venv/bin/python scripts/smoke_cors.py
.venv/bin/python scripts/smoke_verify_email.py
.venv/bin/python scripts/smoke_billing.py
```

All five should return `SMOKE OK`. Any failure indicates a chassis-level mismatch — typically a missing secret, env-var mismatch (now caught at boot by `extra="forbid"`), or DNS/SSL not yet propagated.

Manual end-to-end (browser):

1. Visit `https://staging.<your-domain>`.
2. Register with a real email; confirm verification email lands.
3. Verify, log in.
4. Visit `/app/billing`, do a $5 topup with `4242 4242 4242 4242`.
5. Confirm Stripe Dashboard shows the customer + the PaymentIntent succeeded; DB `balance_ledger` has the row.

If all green: staging is live and the chassis is honoring all its invariants. Move on to your project-specific work.

---

## 12 — Prod standup (when ready)

When you're ready to stand up `<your-project>-prod`:

1. Repeat steps 2–10 against `<your-project>-prod` instead of `<your-project>-staging`.
2. **Sizing differences** (per `environments.md §Provisioning asymmetry`):
   - Cloud SQL: `db-custom-1-3840`, **PITR on**, 7-day backup retention.
   - Cloud Run backend: `--min-instances=1` (Stripe webhook ~10s timeout — cold start risks missing events).
   - Cloud Run frontend: `--min-instances=0` is fine.
3. **Trigger differences:**
   - Branch trigger fires on `^v[0-9]+\.[0-9]+\.[0-9]+$` tag (immutable release), not on `main` push.
   - Variable: `_STRIPE_PUBLISHABLE_KEY=pk_live_...` (live mode).
4. **Stripe:** real webhook endpoint at `https://api.<your-domain>/billing/webhook`, live signing secret.
5. **SendGrid:** separate API key for prod (per `environments.md §Secrets strategy` — no sharing).

Promotion command:

```bash
git tag v0.1.0
git push origin v0.1.0
```

Cloud Build's tag trigger fires; deploys to prod.

---

## 13 — Common failure modes (gotchas captured from real rollouts)

Quick-reference for the top traps. Each links to the authoritative doc for context.

| Symptom | Root cause | Fix / Reference |
|---|---|---|
| `NEXT_PUBLIC_*` empty in browser bundle | Missing one of the four required wirings | `development.md §NEXT_PUBLIC_* four-file checklist` |
| `printenv` shows env empty but app works | `NEXT_PUBLIC_*` is build-time-baked; runtime env is irrelevant | `development.md §NEXT_PUBLIC_* runtime debugging` |
| `/app/billing` returns 404 in browser | Page placed at `(app)/billing/page.tsx`; route group `(app)` doesn't appear in URL | `site-model.md §Route-group URL convention` — must be `(app)/app/billing/page.tsx` |
| `extra="forbid"` crashes startup with `Extra inputs are not permitted` | Env var in `cloudbuild.yaml` not declared on `Settings` | Pre-flip env-var-surface check; align `cloudbuild.yaml` with `Settings` fields |
| Cloud Build "invalid value for build.service_account" | Trying to use Google-managed cloudbuild SA | Create user-managed `carddroper-build` SA — step 3.3 |
| Cloud Build deploy step `PERMISSION_DENIED: iam.serviceaccounts.actAs` | Missing `iam.serviceAccountUser` on runtime SA | Step 3.4 |
| Trigger reverts to compute SA after browser save | Browser dropdown limitation | `gcloud beta builds triggers export | sed | import` — step 5.3 |
| Cloud SQL create rejects `db-f1-micro` | Default `ENTERPRISE_PLUS` edition rejects shared-core | `--edition=ENTERPRISE` — step 2.3 |
| Cloud SQL password rejected | Policy requires upper + lower + digit + non-alphanumeric | Suffix `Aa1!` — step 2.3 |
| `Could not load backend` on first DB hit | `DATABASE_URL` missing `+asyncpg` driver prefix | `chassis-contract.md` `DATABASE_URL` invariant |
| All authenticated requests return 401 | `JWT_ISSUER` or `JWT_AUDIENCE` empty | `chassis-contract.md` JWT invariants |
| JWT trivially forgeable | `JWT_SECRET` < 32 chars | `chassis-contract.md` JWT_SECRET invariant — generate with `secrets.token_urlsafe(48)` |
| `cookie not set` in browser despite 200 from `/auth/login` | `COOKIE_DOMAIN` doesn't cover `FRONTEND_BASE_URL` host | `chassis-contract.md` cookie-domain invariant |
| Domain mapping "ownership not verified" | Apex domain not verified in Search Console | TXT record + verify — step 7.2 |
| TLS handshake fails despite `Ready: True` | Edge cert propagation lag (~10–20 min, occasionally hours) | Wait — step 7.3 |
| Migration step fails with shell-var resolution | `$VAR` consumed by Cloud Build at render | Escape as `$$VAR` in `cloudbuild.yaml` |
| IAM policy still references deleted SA | GCP marks bindings `deleted:serviceAccount:...?uid=...` for 30-day undelete | Explicit `remove-iam-policy-binding ... --condition=None` — step 10 / `deployment.md §Default compute SA cleanup` |
| Webhook returns 500 after CLI replay | Race in old SELECT-then-INSERT dedup | Already fixed in chassis (0023.2 atomic INSERT…ON CONFLICT) — confirm you're on current main |
| Smoke battery passes locally but staging healthz is 404 | Smoke uses `/healthz`; you may have curl'd `/health` | Use the smoke script as ground truth |

---

## 14 — Reference index

**Reference docs (read these for depth):**

- `doc/operations/deployment.md` — GCP deployment playbook + secrets layout + service-account discipline + cost optimization + rollback.
- `doc/operations/environments.md` — three-env topology, promotion path, DNS records, secrets strategy, parity rules, cost estimates.
- `doc/operations/development.md` — local setup, env-var tiers, NEXT_PUBLIC four-file checklist, runtime debugging.
- `doc/operations/chassis-contract.md` — every chassis invariant 1:1 with its enforcement; the contract adopters must honor.
- `doc/operations/testing.md` — three-tier testing policy + per-ticket coverage checklist + smoke-script pattern.
- `doc/architecture/site-model.md` — Canva-model auth wall + route-group URL convention.
- `doc/architecture/overview.md` — system diagram, request flow.
- `doc/architecture/tech-stack.md` — stack rationale.
- `doc/systems/auth.md` — JWT + refresh tokens, email verification, password reset.
- `doc/systems/payments.md` — Stripe customer lifecycle, PAYG via PaymentIntents, optional subscriptions, balance ledger, webhooks.
- `doc/PLAN.md` — the chassis decision log.

**Tickets (the historical "why"):**

- `0006-staging-gcp-foundation.md` — initial GCP project + Cloud SQL + Secret Manager + backend runtime SA setup. Source of truth for steps 2.1–4.2 + 3.1.
- `0007-staging-first-deploy.md` — `cloudbuild.yaml` + Cloud Build trigger + first deploy verification. Source of truth for steps 3.3, 5, 6.
- `0008-staging-custom-domains.md` — Cloudflare CNAMEs + Cloud Run domain mappings + SSL provisioning. Source of truth for step 7.
- `0010-sendgrid-infrastructure.md` — SendGrid hardening + send_email helper + staging secret wiring.
- `0021-stripe-foundation.md` — Stripe customer lifecycle + balance ledger + webhook skeleton.
- `0023-payg-topup.md` — PAYG topup chassis: `/billing/topup` + `/billing/balance` + `payment_intent.succeeded` handler + Stripe Elements TopupForm. Sources the `NEXT_PUBLIC` four-file gotcha and the route-group URL gotcha.
- `0023.2-webhook-dedup-concurrency.md` — webhook dedup race fix; atomic `INSERT … ON CONFLICT`.
- `0018-chassis-hardening-audit.md` — chassis validators + `extra="forbid"` + IAM SA discipline. Sources steps 3, 4, and 10.

**External:**

- [GCP Cloud Run docs](https://cloud.google.com/run/docs)
- [Cloud Build configuration reference](https://cloud.google.com/build/docs/build-config-file-schema)
- [Stripe Webhooks](https://stripe.com/docs/webhooks)
- [SendGrid Sender Authentication](https://docs.sendgrid.com/ui/account-and-settings/how-to-set-up-domain-authentication)
- [Cloudflare DNS Records](https://developers.cloudflare.com/dns/manage-dns-records/)
