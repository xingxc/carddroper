# Deployment

Google Cloud deployment playbook. This doc is a stub that gets filled in as we stand up staging and prod. The foodapp deployment doc is the reference (`/Users/johnxing/mini/foodapp/docs/operations/deployment.md`) — carddroper's version will be a simplified, carddroper-specific copy.

## Status

- [x] `carddroper-staging` GCP project created
- [x] `carddroper-staging` Cloud SQL instance created
- [x] `carddroper-staging` secrets uploaded
- [ ] `carddroper-staging` Cloud Build trigger wired to `main`
- [ ] `carddroper-staging` deployed (backend + frontend)
- [ ] `carddroper-staging` Stripe webhook endpoint configured
- [ ] `carddroper-prod` GCP project created
- [ ] `carddroper-prod` Cloud SQL instance created
- [ ] `carddroper-prod` secrets uploaded
- [ ] `carddroper-prod` Cloud Build trigger wired to `v*` tags
- [ ] `carddroper-prod` deployed
- [ ] `carddroper-prod` domain mapped (frontend + api)
- [ ] `carddroper-prod` Stripe live webhook endpoint configured

## High-level plan

1. **Create GCP projects** — `carddroper-staging` and `carddroper-prod`. Link billing accounts.
2. **Enable APIs** — `run`, `sqladmin`, `cloudbuild`, `artifactregistry`, `secretmanager`, `compute`.
3. **Create Cloud SQL instance** per project — Postgres 16, 10 GB SSD, zonal. Sizing differs:
   - **staging**: smallest shared-core tier, **PITR off**, daily backup only. Stoppable (`--activation-policy=NEVER` when idle) to cut cost.
   - **prod**: 1 vCPU / 3.75 GB, **PITR on**, 7-day backup retention. Always on.
4. **Create Artifact Registry repo** per project — `carddroper-repo` in `us-west1`.
5. **Upload secrets** — `DATABASE_URL`, `MIGRATION_DATABASE_URL`, `JWT_SECRET`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `SENDGRID_API_KEY`.
6. **Create Cloud Build trigger** — `main` branch push on staging; `v*` tag on prod.
7. **Write `cloudbuild.yaml`** — build → migrate (via Cloud SQL Auth Proxy) → deploy backend → deploy frontend. Deploy flags differ:
   - **staging**: `--min-instances=0` on backend and frontend. Cold starts acceptable.
   - **prod**: `--min-instances=1` on backend (protects Stripe webhook latency budget). `--min-instances=0` on frontend.
8. **Map custom domains (both envs).**
   - In **Cloudflare**, create a `CNAME` record for each hostname below pointing to `ghs.googlehosted.com`. Set proxy status to **DNS-only** (grey cloud) for v1.
     - Staging: `staging.carddroper.com`, `api.staging.carddroper.com`
     - Prod: `carddroper.com`, `api.carddroper.com`
   - In **Cloud Run** for each service, create a domain mapping:
     ```bash
     gcloud run domain-mappings create --service=<svc> --domain=<hostname> --region=us-west1
     gcloud run domain-mappings list --region=us-west1   # check SSL provisioning (~10-30 min)
     ```
   - Deferred: flip Cloudflare to proxy mode later — requires SSL mode "Full (strict)" and potentially a WAF carve-out for Stripe webhooks.
9. **Configure Stripe webhook endpoints** in Stripe dashboard pointing at `https://api.carddroper.com/billing/webhook` (prod) and the staging URL. Record signing secrets into Secret Manager.

Once we've walked through steps 1-9 for staging, we'll copy the relevant commands here. Prod is the same commands against the prod project, with the sizing / flag differences called out in step 3 and step 7.

## Staging cost optimization — stopping the DB when idle

Staging's Cloud SQL is the single biggest line item. When we're not actively testing we can park it:

```bash
# Park (billing drops to storage only, ~$1/mo)
gcloud sql instances patch carddroper-staging-db \
    --project=carddroper-staging \
    --activation-policy=NEVER

# Wake
gcloud sql instances patch carddroper-staging-db \
    --project=carddroper-staging \
    --activation-policy=ALWAYS
```

A Cloud Scheduler job can automate this (stop at 20:00, start at 08:00 weekdays) if we want it hands-off.

## Secrets layout

Per project (`carddroper-staging` and `carddroper-prod` each have their own):

| Secret name | Value | Used by |
|---|---|---|
| `carddroper-database-url` | `postgresql+asyncpg://.../carddroper?host=/cloudsql/...` | Cloud Run backend |
| `carddroper-migration-database-url` | `postgresql+asyncpg://...@127.0.0.1:5432/carddroper` | Cloud Build migration step |
| `carddroper-jwt-secret` | 48-byte random | Cloud Run backend |
| `carddroper-stripe-secret-key` | `sk_test_...` (staging) or `sk_live_...` (prod) | Cloud Run backend |
| `carddroper-stripe-webhook-secret` | `whsec_...` | Cloud Run backend |
| `carddroper-sendgrid-api-key` | `SG....` | Cloud Run backend |

The Compute Engine default service account needs `roles/secretmanager.secretAccessor` on each.

## Why migrate-before-deploy

`cloudbuild.yaml` runs `alembic upgrade head` *before* flipping traffic to the new Cloud Run revision. If the migration fails, the old code keeps serving traffic with the old schema — no downtime, no schema/code drift.

## Rollback

```bash
# List revisions
gcloud run revisions list --service=carddroper-backend --region=us-west1

# Shift 100% of traffic back
gcloud run services update-traffic carddroper-backend \
    --region=us-west1 \
    --to-revisions=carddroper-backend-00012=100
```

For schema-involving rollbacks, we prefer roll-forward (a new migration that reverts the change) over `alembic downgrade` in prod. Downgrade scripts are easy to get wrong on running data.
