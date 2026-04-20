# Environments

Three environments. Two live on GCP. One lives on your Mac.

## Overview

| Env | Where | Database | Stripe keys | SendGrid | Deploy trigger | Domain |
|---|---|---|---|---|---|---|
| **dev** | Local Docker Compose | Postgres 16 in a container | Test mode | Sandbox / skipped | `docker-compose up` | `localhost:3000` + `localhost:8000` |
| **staging** | GCP project `carddroper-staging` | Cloud SQL — smallest shared-core, zonal, no PITR, stoppable | Test mode | Real (low-volume key) | Push to `main` | `staging.carddroper.com` + `api.staging.carddroper.com` |
| **prod** | GCP project `carddroper-prod` | Cloud SQL — 1 vCPU / 3.75 GB, zonal, PITR on, 7-day backup | Live mode | Real (production key) | Push a `v*` git tag | `carddroper.com` + `api.carddroper.com` |

## Why three (and not one, not five)

- **dev** alone would mean cloud-only bugs ship to users. Bad.
- **dev + prod only** (foodapp pattern) catches local bugs but not cloud-specific ones: Unix socket paths, IAM bindings, Secret Manager access, Cloud Build migrations, domain mapping, SSL provisioning. We hit all of these in foodapp. Staging catches them cheaply.
- **dev + staging + prod** is the sweet spot: full cloud testing with zero risk to real users. ~$20/mo extra.
- Per-PR preview envs are overkill at this stage — add later if the team grows.

## Promotion path

```
┌──────────────────┐     push to main     ┌─────────────────────┐     tag v*.*.*     ┌───────────────────┐
│  dev (local)     │ ──────────────────▶  │ carddroper-staging     │ ─────────────────▶ │  carddroper-prod     │
│  docker-compose  │                      │ Cloud Run, Cloud SQL│                    │ Cloud Run, SQL    │
│  Stripe test     │                      │ Stripe test         │                    │ Stripe live       │
└──────────────────┘                      └─────────────────────┘                    └───────────────────┘
      feature/*                                   main                                      v*.*.*
      → PR to main                                → auto staging                            → auto prod
```

**Promoting to staging:**
```bash
git checkout dev
# ... make changes ...
git checkout main
git merge dev
git push origin main
# Cloud Build trigger fires, builds, runs migrations against carddroper-staging Cloud SQL,
# deploys backend + frontend to carddroper-staging Cloud Run.
```

**Promoting to prod:**
```bash
# After QA on staging:
git tag v0.1.0
git push origin v0.1.0
# Cloud Build tag trigger fires, builds with live Stripe publishable key,
# runs migrations against carddroper-prod Cloud SQL, deploys to carddroper-prod Cloud Run.
```

The tag is immutable — re-running the prod pipeline always deploys the same code. Hotfixes get their own tag (`v0.1.1`).

## DNS (Cloudflare)

All four domains are managed at Cloudflare — both the registrar and the authoritative nameservers, not Cloud DNS.

| Hostname | Record | Target | Purpose |
|---|---|---|---|
| `carddroper.com` | CNAME (or ALIAS flattening) | `ghs.googlehosted.com` | prod frontend Cloud Run |
| `api.carddroper.com` | CNAME | `ghs.googlehosted.com` | prod backend Cloud Run |
| `staging.carddroper.com` | CNAME | `ghs.googlehosted.com` | staging frontend Cloud Run |
| `api.staging.carddroper.com` | CNAME | `ghs.googlehosted.com` | staging backend Cloud Run |

**Proxy status: start DNS-only, revisit proxy mode later.**

- **DNS-only ("grey cloud")** — Cloudflare only answers DNS; traffic flows directly to Cloud Run, and Cloud Run's managed SSL cert terminates for users. Simplest, lowest-risk. This is the v1 default.
- **Proxy mode ("orange cloud")** — Cloudflare terminates TLS at its edge and re-encrypts to Cloud Run. Adds CDN, DDoS protection, WAF. Requires Cloudflare SSL mode **Full (strict)** and may need WAF carve-outs for Stripe's webhook POSTs to `api.carddroper.com/billing/webhook`. Flip once the deployment is stable and we have a reason to want edge protections.

**Email DNS** — separate records for SendGrid deliverability (added after we verify the sending domain in SendGrid):
- **SPF** — `TXT @ "v=spf1 include:sendgrid.net -all"`.
- **DKIM** — two `CNAME` records with values SendGrid provides post-verification.
- **DMARC** — `TXT _dmarc "v=DMARC1; p=quarantine; rua=mailto:dmarc@carddroper.com"`. Start at `p=quarantine` (not `p=reject`) until reporting confirms we aren't dropping legitimate mail.

**MX / mail receiving** — TBD. The legal docs reference `privacy@` and `legal@carddroper.com`. Cheapest path: **Cloudflare Email Routing** (free) forwards those addresses to an existing inbox (e.g. your Gmail). Upgrade to Google Workspace ($6/user/mo) or Zoho if we want real mailboxes.

## Secrets strategy

Secrets live in three places:

1. **Local (dev)**: `backend/.env` and `frontend/.env.local` — **never committed**. Developers copy from `.env.example` and fill in their own test-mode Stripe keys.
2. **Staging**: Secret Manager in `carddroper-staging` project. Test-mode Stripe keys, low-volume SendGrid key.
3. **Prod**: Secret Manager in `carddroper-prod` project. Live Stripe keys, production SendGrid key.

No secret is shared across environments. A compromised test key cannot be used to charge real customers. A compromised prod key cannot affect staging.

## What staging is (and isn't) for

**Is for:**
- Verifying migrations run cleanly against a cloud Postgres instance.
- Testing Stripe webhooks end-to-end with real HTTPS and real signature verification.
- Testing SendGrid deliverability.
- Testing custom-domain + SSL flows.
- QA of new flows with realistic data before exposing to real users.

**Is not for:**
- Load testing (we'd use a dedicated load-test env + paid traffic profile for that).
- Long-term data retention — staging DB can be dropped and recreated as needed.

## Provisioning asymmetry — staging lean, prod warm

The two projects are isolation boundaries, not identically-sized twins. Staging should be as cheap as we can make it; prod should be sized for "a real user just hit the Stripe webhook and it must not miss."

**Staging levers (pick cheap):**
- Cloud SQL: smallest shared-core tier, zonal (no HA), 10 GB SSD, **PITR off**, daily backup only.
- Cloud SQL activation policy can be flipped to `NEVER` when not testing — drops the DB bill to just storage (~$1/mo).
- Cloud Run: `--min-instances=0` on both backend and frontend. Cold starts (2-5s) are fine here.
- No reserved IPs, no VPC connector unless needed, no Cloud Armor.

**Prod levers (pick warm where it matters):**
- Cloud SQL: 1 vCPU / 3.75 GB, zonal to start, **PITR on**, 7-day backup retention. Upgrade to regional (HA) the month real customer traffic arrives — that change roughly doubles the DB bill.
- Cloud Run backend: `--min-instances=1`. This is the one non-negotiable. Stripe webhooks have a ~10s timeout; a cold start on top of processing = missed events = ledger drift. $15-20/mo is cheap insurance.
- Cloud Run frontend: `--min-instances=0` is fine (Next.js cold starts <1s, and it's not on the webhook path).

## Cost estimate (monthly)

| Service | dev | staging | prod |
|---|---|---|---|
| Cloud SQL | $0 | ~$9-12 (or ~$1 when stopped) | ~$45-55 |
| Cloud Run backend | $0 | ~$0-2 (scale to zero) | ~$15-20 (min=1) |
| Cloud Run frontend | $0 | ~$0-2 (scale to zero) | ~$2-5 |
| Artifact Registry | $0 | ~$0.50 | ~$0.50 |
| Cloud Build | $0 | $0 (120 min/day free) | $0 |
| Secret Manager | $0 | ~$0 | ~$0-1 |
| Egress | $0 | ~$0 | ~$1-5 |
| **Total (typical)** | **$0** | **~$12-18** | **~$65-85** |
| **Total (staging DB stopped nights/weekends)** | — | **~$5-8** | — |

Incremental cost of the second GCP project vs. "main = prod" alone: **~$10-20/month**, or as low as **~$5** if we aggressively stop staging. Well under the cost of one prod regression that would have been caught in staging.

First 90 days on each project: $300 free credit covers everything.

## Parity rules

- Same Postgres version everywhere (16).
- Same Python version everywhere (3.11).
- Same Node version everywhere (20 LTS).
- Same Docker images, built once per commit, deployed unchanged. No "dev mode" code paths in backend or frontend.
- Environment-specific behavior happens *only* through config (env vars / secrets), never through `if env == 'prod'` branching.

## Common operations

Filled in fully in [operations/deployment.md](deployment.md) as we stand each env up. Short version:

```bash
# Local
docker-compose up                            # start everything
docker-compose down -v                       # stop and wipe DB

# Staging
gcloud config set project carddroper-staging
gcloud run services describe carddroper-backend --region=us-west1

# Prod
gcloud config set project carddroper-prod
gcloud run services describe carddroper-backend --region=us-west1
```
