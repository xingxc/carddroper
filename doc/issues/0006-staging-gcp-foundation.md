---
id: 0006
title: staging GCP foundation — project, IAM, Cloud SQL, Artifact Registry, Secret Manager
status: resolved
priority: high
found_by: orchestrator 2026-04-19
resolved_by: user 2026-04-20
---

## Resolution

All 5 phases executed against `carddroper-staging` GCP project. Verification block output confirmed:
- Project `carddroper-staging` active, billing linked.
- 7 required APIs enabled (regex matched 8 due to `iamcredentials.googleapis.com` also containing `iam` — harmless, extra API auto-enabled by GCP).
- Artifact Registry `carddroper-repo` in `us-west1`.
- Cloud SQL `carddroper-staging-db` RUNNABLE (Postgres 16, `db-f1-micro`, `ENTERPRISE` edition).
- Database `carddroper` + users `postgres` + `carddroper` created.
- Service account `carddroper-runtime@carddroper-staging.iam.gserviceaccount.com` with `secretmanager.secretAccessor` + `cloudsql.client` roles.
- Three secrets in Secret Manager: `carddroper-database-url`, `carddroper-migration-database-url`, `carddroper-jwt-secret`.

### Fixes baked into ticket mid-execution
1. **Password policy (Phase 3)** — `openssl rand -base64 24` alone doesn't guarantee Cloud SQL's required non-alphanumeric. Suffix `Aa1!` added to both `ROOT_PW` and `APP_PW` to guarantee all four character classes.
2. **Cloud SQL edition (Phase 3)** — new SQL instances default to `ENTERPRISE_PLUS` which rejects shared-core tiers like `db-f1-micro`. Added `--edition=ENTERPRISE` flag to the create command.

### Checkboxes flipped in `doc/operations/deployment.md`
- `carddroper-staging` GCP project created
- `carddroper-staging` Cloud SQL instance created
- `carddroper-staging` secrets uploaded

Next: ticket 0007 (Cloud Build + first deploy to `*.run.app` URLs).

---

## Context

PLAN.md §10.4 (first ticket of three covering the staging push). This ticket creates the GCP infrastructure that staging will run on, **without deploying any code yet**. Splitting infra-from-deploy means each layer is independently verifiable; if a Cloud SQL connection fails later, we know the SQL instance was good before we added Cloud Build to the picture.

Reference docs:
- `doc/operations/environments.md` — staging sizing, regions, parity rules.
- `doc/operations/deployment.md` — high-level GCP plan + secret naming convention.

**Execution model:** this ticket is **user-executed**, not agent-executed. Agents can't run `gcloud` against your account. The orchestrator will verify each phase from the gcloud listing commands and update `doc/operations/deployment.md` checkboxes as you complete them.

## Pre-requisites

Confirm before starting (one terminal command each):

1. `gcloud version` — gcloud CLI installed (any version from the last 12 months).
2. `gcloud auth list` — your Google account is authenticated. If not: `gcloud auth login`.
3. `gcloud billing accounts list` — at least one billing account in `OPEN` state. Note the `ACCOUNT_ID` (looks like `01ABCD-2EFGHI-3JKLMN`).
4. `gcloud projects list` — confirm `carddroper-staging` does NOT already exist under your account. If it does (left over from prior work), either reuse it or pick a new ID like `carddroper-staging-2`.

Project IDs are globally unique across all of GCP. If `carddroper-staging` is taken by someone else, append a random suffix (e.g., `carddroper-staging-x4j2`) and use that everywhere below.

## Acceptance

Run the commands in order. Substitute `<BILLING_ACCOUNT_ID>` with the value from pre-req 3. Region is `us-west1` per `environments.md`.

### Phase 1: Project + APIs

```bash
# Create the project
gcloud projects create carddroper-staging --name="Carddroper Staging"

# Link billing
gcloud billing projects link carddroper-staging \
    --billing-account=<BILLING_ACCOUNT_ID>

# Set as default for the rest of this session
gcloud config set project carddroper-staging
gcloud config set run/region us-west1
gcloud config set artifacts/location us-west1

# Enable required APIs
gcloud services enable \
    run.googleapis.com \
    sqladmin.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    secretmanager.googleapis.com \
    compute.googleapis.com \
    iam.googleapis.com
```

Verify: `gcloud services list --enabled --filter="name:(run OR sqladmin OR cloudbuild OR artifactregistry OR secretmanager OR compute OR iam)"` — all 7 listed.

### Phase 2: Artifact Registry

```bash
gcloud artifacts repositories create carddroper-repo \
    --repository-format=docker \
    --location=us-west1 \
    --description="Carddroper container images"
```

Verify: `gcloud artifacts repositories list` — shows `carddroper-repo` in `us-west1`.

### Phase 3: Cloud SQL (Postgres 16)

```bash
# Create instance (smallest shared-core, zonal, no PITR — staging defaults per environments.md)
# --edition=ENTERPRISE is required to use shared-core tiers; the default ENTERPRISE_PLUS
# only accepts db-perf-optimized-* tiers (~$200/mo floor).
gcloud sql instances create carddroper-staging-db \
    --database-version=POSTGRES_16 \
    --tier=db-f1-micro \
    --edition=ENTERPRISE \
    --region=us-west1 \
    --availability-type=zonal \
    --storage-size=10 \
    --storage-type=SSD \
    --no-backup

# Set the postgres root password (generate one and save it to your password manager — you'll need it once for app-user creation)
# Suffix "Aa1!" guarantees Cloud SQL's policy (upper + lower + digit + non-alphanumeric) is met.
ROOT_PW="$(openssl rand -base64 24)Aa1!"
echo "Save this root password to your password manager: $ROOT_PW"
gcloud sql users set-password postgres \
    --instance=carddroper-staging-db \
    --password="$ROOT_PW"

# Create the carddroper database
gcloud sql databases create carddroper --instance=carddroper-staging-db

# Create the app user (generate a password — we'll put this in Secret Manager next phase)
APP_PW="$(openssl rand -base64 24)Aa1!"
echo "Save this APP_PW value — you'll paste it into the DATABASE_URL secret in Phase 5: $APP_PW"
gcloud sql users create carddroper \
    --instance=carddroper-staging-db \
    --password="$APP_PW"
```

If `db-f1-micro` is rejected as deprecated, fall back to `db-g1-small` (slightly more expensive but still cheap).

Verify: `gcloud sql instances list` shows `carddroper-staging-db` with status `RUNNABLE`. `gcloud sql databases list --instance=carddroper-staging-db` lists `carddroper`. `gcloud sql users list --instance=carddroper-staging-db` lists both `postgres` and `carddroper`.

### Phase 4: Service account for Cloud Run runtime

```bash
# Create the runtime service account
gcloud iam service-accounts create carddroper-runtime \
    --display-name="Carddroper Cloud Run runtime"

# Grant access to read secrets
gcloud projects add-iam-policy-binding carddroper-staging \
    --member="serviceAccount:carddroper-runtime@carddroper-staging.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"

# Grant access to connect to Cloud SQL
gcloud projects add-iam-policy-binding carddroper-staging \
    --member="serviceAccount:carddroper-runtime@carddroper-staging.iam.gserviceaccount.com" \
    --role="roles/cloudsql.client"
```

Verify: `gcloud iam service-accounts list` shows `carddroper-runtime@carddroper-staging.iam.gserviceaccount.com`.

### Phase 5: Secrets in Secret Manager

Get the Cloud SQL connection name first — you'll need it in the DATABASE_URL:

```bash
SQL_CONN=$(gcloud sql instances describe carddroper-staging-db --format="value(connectionName)")
echo "Connection name: $SQL_CONN"
# Will look like: carddroper-staging:us-west1:carddroper-staging-db
```

Now create the three secrets. The naming matches `doc/operations/deployment.md:71`:

```bash
# 1. App runtime DATABASE_URL — uses Cloud SQL Auth Proxy unix socket
# Substitute $APP_PW with the password from Phase 3 (or paste it directly)
printf "postgresql+asyncpg://carddroper:${APP_PW}@/carddroper?host=/cloudsql/${SQL_CONN}" | \
    gcloud secrets create carddroper-database-url --data-file=-

# 2. Migration DATABASE_URL — uses TCP via the Cloud SQL Proxy sidecar in Cloud Build
printf "postgresql+asyncpg://carddroper:${APP_PW}@127.0.0.1:5432/carddroper" | \
    gcloud secrets create carddroper-migration-database-url --data-file=-

# 3. JWT secret — 48 bytes of randomness, base64
openssl rand -base64 48 | tr -d '\n' | \
    gcloud secrets create carddroper-jwt-secret --data-file=-
```

Verify: `gcloud secrets list` shows three secrets:
- `carddroper-database-url`
- `carddroper-migration-database-url`
- `carddroper-jwt-secret`

Spot-check: `gcloud secrets versions access latest --secret=carddroper-jwt-secret | wc -c` returns ~64 (48 bytes base64-encoded).

## Verification

After all 5 phases, run this single block — every line should succeed:

```bash
gcloud config get-value project                                              # carddroper-staging
gcloud projects describe carddroper-staging --format="value(projectId)"      # carddroper-staging
gcloud services list --enabled --format="value(name)" | grep -E "(run|sqladmin|cloudbuild|artifactregistry|secretmanager|compute|iam)" | wc -l   # 7
gcloud artifacts repositories list --format="value(name)"                    # includes carddroper-repo
gcloud sql instances describe carddroper-staging-db --format="value(state)"  # RUNNABLE
gcloud sql databases list --instance=carddroper-staging-db --format="value(name)"   # includes carddroper
gcloud sql users list --instance=carddroper-staging-db --format="value(name)"       # includes carddroper
gcloud iam service-accounts list --format="value(email)" | grep carddroper-runtime  # match
gcloud secrets list --format="value(name)" | sort                            # 3 secrets, sorted
```

Paste the output of this block into chat when done — orchestrator will verify and flip the deployment.md checkboxes.

## Out of scope

- Cloud Build trigger (ticket 0007).
- `cloudbuild.yaml` (ticket 0007).
- Deploying any code (ticket 0007).
- Custom domain mapping, Cloudflare DNS records (ticket 0008).
- SSL certificates (handled automatically by Cloud Run; ticket 0008 verifies).
- Stripe/SendGrid secrets (later phase tickets).
- prod GCP project (post-staging, after v0.1.0).
- Stopping the SQL instance for cost savings — we're testing right after; park it later if idle.

## Cost note

After this ticket lands, the running cost is roughly:
- Cloud SQL `db-f1-micro` zonal, 10 GB SSD, no backups: ~$9-12/month.
- Artifact Registry: $0 until images are pushed (ticket 0007), then ~$0.50/month for the staging image set.
- Secret Manager: $0 (3 secrets is well under the free tier).

If you pause carddroper for more than a week between sessions, park the SQL instance:
```bash
gcloud sql instances patch carddroper-staging-db --activation-policy=NEVER
```
That drops the SQL bill to storage-only (~$1/month). Wake it with `--activation-policy=ALWAYS` before the next session.

## Report

You report by pasting the verification block output. Orchestrator handles:
- Verifying each line.
- Updating `doc/operations/deployment.md` status checkboxes for the 3 staging items this ticket covers (`carddroper-staging` GCP project created, Cloud SQL instance created, secrets uploaded).
- Adding the Resolution note + flipping ticket status.
