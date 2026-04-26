# Deployment

Google Cloud deployment playbook. This doc is a stub that gets filled in as we stand up staging and prod. The foodapp deployment doc is the reference (`/Users/johnxing/mini/foodapp/docs/operations/deployment.md`) — carddroper's version will be a simplified, carddroper-specific copy.

## Status

- [x] `carddroper-staging` GCP project created
- [x] `carddroper-staging` Cloud SQL instance created
- [x] `carddroper-staging` secrets uploaded
- [x] `carddroper-staging` Cloud Build trigger wired to `main`
- [x] `carddroper-staging` deployed (backend + frontend)
- [x] `carddroper-staging` custom domain mapped (frontend + api)
- [x] `carddroper-staging` Stripe webhook endpoint configured (2026-04-25, `payment_intent.succeeded` only; subscription/invoice events deferred to 0024)
- [x] `carddroper-staging` default compute SA deleted; both Cloud Run services on dedicated runtime SAs (per §Service-account discipline; 2026-04-25 / ticket 0018)
- [ ] `carddroper-prod` GCP project created
- [ ] `carddroper-prod` Cloud SQL instance created
- [ ] `carddroper-prod` secrets uploaded
- [ ] `carddroper-prod` Cloud Build trigger wired to `v*` tags
- [ ] `carddroper-prod` deployed
- [ ] `carddroper-prod` domain mapped (frontend + api)
- [ ] `carddroper-prod` Stripe live webhook endpoint configured
- [ ] `carddroper-prod` default compute SA deleted (apply same sequence as staging — see §Service-account discipline)

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

The dedicated backend runtime SA (`carddroper-runtime@<project>.iam.gserviceaccount.com`) needs `roles/secretmanager.secretAccessor` on each. The default Compute Engine SA is **deleted** per §Service-account discipline below — relying on it would inherit `Editor` and is a chassis-reliability violation.

## Service-account discipline

Every Cloud Run service deploys with an explicit `--service-account` flag in `cloudbuild.yaml`. The default Compute Engine SA (`<PROJECT_NUMBER>-compute@developer.gserviceaccount.com`) is **deleted** per project — relying on it is a chassis-reliability violation. With the default SA absent, any Cloud Run deploy missing `--service-account` fails at deploy time with a clear error rather than silently inheriting the SA's `Editor` role.

### Per-service runtime SAs

| Service | Runtime SA | Project-level roles | Why |
|---|---|---|---|
| `carddroper-backend` | `carddroper-runtime@<project>.iam.gserviceaccount.com` | `roles/secretmanager.secretAccessor`, `roles/cloudsql.client` | Reads secrets at boot; opens Cloud SQL connections via Auth Proxy |
| `carddroper-frontend` | `carddroper-frontend-runtime@<project>.iam.gserviceaccount.com` | none | Stateless Next.js SSR; makes no outbound GCP API calls |

### Default compute SA cleanup (one-time, per project)

Origin: 0018 chassis-hardening audit. Executed for `carddroper-staging` on 2026-04-25; same cleanup pending for `carddroper-prod` when it stands up. Run as the project Owner.

```bash
PROJECT=carddroper-staging  # or carddroper-prod
PROJECT_NUMBER=$(gcloud projects describe $PROJECT --format='value(projectNumber)')
```

1. **Verify dependencies** on the default compute SA. Expected: only the frontend Cloud Run service depends on it (any service deployed without `--service-account`).

   ```bash
   gcloud run services list --project=$PROJECT \
     --format='table(metadata.name,spec.template.spec.serviceAccountName)'
   gcloud functions list --project=$PROJECT 2>/dev/null || echo "no functions"
   gcloud scheduler jobs list --project=$PROJECT 2>/dev/null || echo "no scheduler"
   gcloud run jobs list --project=$PROJECT
   ```

2. **Create dedicated SAs** for any service that lacks one (carddroper-staging needed `carddroper-frontend-runtime`; the backend already had `carddroper-runtime`):

   ```bash
   gcloud iam service-accounts create carddroper-frontend-runtime \
     --display-name="Carddroper frontend runtime" --project=$PROJECT
   ```

3. **Grant Cloud Build `actAs` permission** on each new SA so the deploy step can run as it:

   ```bash
   gcloud iam service-accounts add-iam-policy-binding \
     carddroper-frontend-runtime@${PROJECT}.iam.gserviceaccount.com \
     --member="serviceAccount:carddroper-build@${PROJECT}.iam.gserviceaccount.com" \
     --role="roles/iam.serviceAccountUser" --project=$PROJECT
   ```

4. **Update `cloudbuild.yaml`** deploy steps to pass `--service-account=<sa>@${PROJECT}.iam.gserviceaccount.com`. Push; verify Cloud Build succeeds and `gcloud run services list` shows the new binding.

5. **Delete the default compute SA:**

   ```bash
   gcloud iam service-accounts delete \
     ${PROJECT_NUMBER}-compute@developer.gserviceaccount.com --project=$PROJECT
   ```

6. **Stale-binding cleanup (gotcha).** GCP doesn't auto-remove IAM bindings when an SA is deleted — it marks them `deleted:serviceAccount:...?uid=<uid>` for 30-day `undelete` recovery. The binding is functionally inert but pollutes the IAM policy. Find the `?uid=...` suffix in `get-iam-policy` output and remove explicitly:

   ```bash
   gcloud projects get-iam-policy $PROJECT \
     --flatten="bindings[].members" \
     --format="table(bindings.role,bindings.members)" \
     --filter="bindings.members:${PROJECT_NUMBER}-compute"
   # Copy the ?uid=... suffix from the deleted-member entry.

   gcloud projects remove-iam-policy-binding $PROJECT \
     --member="deleted:serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com?uid=<paste>" \
     --role="roles/editor" \
     --condition=None
   ```

   `--condition=None` skips the interactive condition selector — required because the policy has conditional bindings.

7. **Verify clean.** No bindings reference `${PROJECT_NUMBER}-compute`; project Editor scan returns only human Owners (Owner > Editor, so no project-Editor binding is expected).

   ```bash
   gcloud projects get-iam-policy $PROJECT \
     --flatten="bindings[].members" \
     --format="table(bindings.role,bindings.members)" \
     --filter="bindings.role:roles/editor"
   ```

**Recovery:** within 30 days, `gcloud iam service-accounts undelete <unique_id>` restores the SA. After 30 days, recreation by name requires that the deleted SA's tombstone has cleared.

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
