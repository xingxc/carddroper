---
id: 0008
title: staging custom domains — Cloudflare CNAMEs + Cloud Run domain mappings
status: resolved
priority: high
found_by: orchestrator 2026-04-20
resolved_by: user 2026-04-20
---

## Resolution

Staging custom domains are live with Google-managed SSL:

- Frontend: https://staging.carddroper.com — renders styled `<h1>Carddroper</h1>`, padlock valid.
- Backend: https://api.staging.carddroper.com — `/health` → `{"status":"ok","database":"connected"}`, `/auth/me` → 401.
- Cert issuer: `Google Trust Services` (WR3 intermediate).
- Cloudflare: three DNS records — two CNAMEs (`staging`, `api.staging` → `ghs.googlehosted.com`, DNS-only) + one TXT (`carddroper.com` → `google-site-verification=...`).

### Deviations from the initial draft (now folded into the body)

1. **`gcloud run domain-mappings` is not in the GA track for regional services.** `--region` is rejected with "flag is available in one or more alternate release tracks." Body now uses `gcloud beta run domain-mappings` throughout.

2. **Google requires root-domain ownership verification via Search Console.** First `domain-mappings create` failed until we added a TXT record at the `carddroper.com` apex with the Google-site-verification token and clicked Verify in Search Console. Body now documents this inline in Phase 2.

3. **`describe` subcommand takes `--domain=...`, not a positional arg.** Body still uses `--domain=...` consistently.

4. **Edge cert propagation lags `status: Ready` by ~10-20 minutes.** `describe` reported `Ready: True, CertificateProvisioned: True` at 18:11 PDT, but TLS handshakes from curl/browser failed with "no peer certificate available" + "read 0 bytes" for the next ~17 minutes until the cert fully rolled out to the Google edge serving `142.250.101.121`. No action needed — just wait.

### Frontend bundle check — expected empty

`grep 'api.staging.carddroper.com'` across `_next/static/chunks/*.js` returned no hits. Root cause: `frontend/lib/api.ts` reads `process.env.NEXT_PUBLIC_API_BASE_URL`, but nothing in `frontend/app/**` currently imports `@/lib/api`, so Next.js tree-shakes the module and the env-var string never reaches a compiled chunk. Pipeline is correct end-to-end (Dockerfile ARG → ENV → `next build`); the URL will materialise in the bundle the moment the first component imports from `@/lib/api` (auth forms, Stripe flow, etc.).

### Checkbox added + flipped in `doc/operations/deployment.md`
- `carddroper-staging` custom domain mapped (frontend + api)

Next: Phase 5 of PLAN.md §10 — Stripe layer (create Customer on signup, PAYG Payment Intent, credit ledger, webhook handler). First Stripe ticket to be drafted.

---

## Context

PLAN.md §10.4 (third and final ticket of the staging push). Ticket 0006 created the GCP foundation; ticket 0007 got code running at `*.run.app` URLs. This ticket puts `staging.carddroper.com` (frontend) and `api.staging.carddroper.com` (backend) in front of those services, terminated with Google-managed SSL certs.

Why this now, not later: custom domains are a cloud-specific failure surface we want to shake out *before* we layer Stripe/email/auth on top. Stripe webhooks target a specific hostname; email verification links go in user inboxes and break on a domain swap. Getting the production-shape URLs in place early means every feature we build afterwards is tested against the URL shape users will actually see.

**Frontend → backend wiring:** the frontend's `NEXT_PUBLIC_API_BASE_URL` is baked in at build time. Today `cloudbuild.yaml` captures whatever `gcloud run services describe` returns (always the `*.run.app` URL, even after custom domain mapping). This ticket hardcodes the backend URL in the frontend build arg to `https://api.staging.carddroper.com` so the frontend bundle points at the custom domain, not the run.app URL.

Reference docs:
- `doc/operations/environments.md` §DNS — the 4-row CNAME table + proxy-status guidance.
- `doc/operations/deployment.md` step 8 — domain-mapping command + SSL provisioning note.
- `doc/PLAN.md` §10.4 — why staging domains land in the staging push.

**Execution model:** mixed.
- **Phase 0** is agent-executed (orchestrator dispatches backend-builder to edit `cloudbuild.yaml`).
- **Phases 1-5** are user-executed: Cloudflare DNS, Cloud Run domain mappings, SSL wait, merge-to-main rebuild, verification.

## Pre-requisites

All ticket 0007 deliverables resolved. Confirm:

```bash
gcloud run services list --region=us-west1 --format="value(metadata.name)" | sort
# Expected: carddroper-backend, carddroper-frontend

curl -sSf "$(gcloud run services describe carddroper-backend --region=us-west1 --format='value(status.url)')/health"
# Expected: {"status":"ok","database":"connected"}
```

Cloudflare access: `carddroper.com` zone must be manageable from your Cloudflare account. Confirm in the Cloudflare dashboard that `carddroper.com` is listed and nameservers match.

## Acceptance

### Phase 0: Update `cloudbuild.yaml` to hardcode the custom backend URL (agent-executed)

Orchestrator dispatches **backend-builder** with a brief to edit `/Users/johnxing/mini/postapp/cloudbuild.yaml`:

1. Delete the "capture backend URL" step (currently step 5 in the file, writes `/workspace/backend_url.txt`).
2. In the frontend build step (currently step 6), change:
   ```
   --build-arg NEXT_PUBLIC_API_BASE_URL=$(cat /workspace/backend_url.txt)
   ```
   to:
   ```
   --build-arg NEXT_PUBLIC_API_BASE_URL=https://api.staging.carddroper.com
   ```
3. Renumber the remaining step comments (old step 6 becomes step 5, etc.).

Keep every other step identical. Do **not** commit this change yet — it goes to `main` in Phase 4, *after* the custom domain is live, so builds between now and then still work against the `*.run.app` URL.

### Phase 1: Create Cloudflare CNAME records (user, browser)

In the Cloudflare dashboard for `carddroper.com`:

1. **DNS** → **Records** → **Add record** for each hostname below:

   | Type | Name | Target | Proxy status | TTL |
   |---|---|---|---|---|
   | CNAME | `staging` | `ghs.googlehosted.com` | **DNS only** (grey cloud) | Auto |
   | CNAME | `api.staging` | `ghs.googlehosted.com` | **DNS only** (grey cloud) | Auto |

2. Save both records.

Verify from your terminal:

```bash
dig +short CNAME staging.carddroper.com       # ghs.googlehosted.com.
dig +short CNAME api.staging.carddroper.com   # ghs.googlehosted.com.
```

DNS propagation is typically <60s with Cloudflare. If `dig` returns empty, wait 30s and retry. If empty after 2 minutes, double-check the records are saved and proxy status is **grey cloud**.

### Phase 2: Create Cloud Run domain mappings (user, CLI)

Regional Cloud Run domain mappings live under `gcloud beta` (the GA `gcloud run domain-mappings` does not accept `--region` yet):

```bash
gcloud beta run domain-mappings create \
    --service=carddroper-frontend \
    --domain=staging.carddroper.com \
    --region=us-west1

gcloud beta run domain-mappings create \
    --service=carddroper-backend \
    --domain=api.staging.carddroper.com \
    --region=us-west1
```

If either command errors with "domain ownership not verified": Google requires you to prove control of the root domain (`carddroper.com`) in Google Search Console before it will map subdomains. Flow:

1. Open https://www.google.com/webmasters/verification/verification?domain=carddroper.com
2. Choose **Domain** (verifies the whole zone at once, so you only do this once for all future subdomains).
3. Google gives you a `TXT` record (`google-site-verification=...`).
4. In Cloudflare → DNS → Records → Add `TXT @ "google-site-verification=..."`.
5. Wait ~30s, click **Verify** in Search Console.
6. Re-run the two `gcloud beta run domain-mappings create` commands.

The Google account that verifies in Search Console must be the same account that `gcloud auth list` shows — otherwise the mapping still reports "not verified."

Verify:

```bash
gcloud beta run domain-mappings list --region=us-west1 \
    --format="table(metadata.name,spec.routeName,status.conditions[0].status)"
# Expected: 2 rows, both mapped, status eventually "True" (may show "False" for a few
# minutes while SSL provisions — see Phase 3).
```

### Phase 3: Wait for Google-managed SSL certificates to provision (user)

Google-managed SSL provisioning typically takes **10-30 minutes** but has occasionally stretched to a few hours on first-time zones. It is fully automatic — nothing to click.

Poll until both domains return HTTP 200 over HTTPS:

```bash
# Watch loop (Ctrl-C to stop)
while true; do
  BACK=$(curl -sS -o /dev/null -w "%{http_code}" https://api.staging.carddroper.com/health || echo ERR)
  FRONT=$(curl -sS -o /dev/null -w "%{http_code}" https://staging.carddroper.com || echo ERR)
  echo "$(date +%H:%M:%S) backend=$BACK frontend=$FRONT"
  [ "$BACK" = "200" ] && [ "$FRONT" = "200" ] && break
  sleep 30
done
```

Early in provisioning you'll see `ERR` (SSL handshake failure) or `526` (Cloudflare can't verify upstream cert). Both mean "wait more." Once both show `200`, SSL is live.

### Phase 4: Commit and merge the frontend API URL change (user, CLI)

```bash
cd /Users/johnxing/mini/postapp
git checkout dev
git status   # should show cloudbuild.yaml modified from Phase 0
git add cloudbuild.yaml
git commit -m "cloudbuild: point frontend at api.staging.carddroper.com"
git push origin dev

git checkout main
git merge --ff-only dev
git push origin main
```

Cloud Build trigger fires. Expected duration: 3-6 minutes (layer cache is warm from ticket 0007's build).

Watch:

```bash
gcloud builds list --region=us-west1 --limit=1
```

### Phase 5: End-to-end verification (user)

```bash
# 1. Backend on custom domain
curl -sSf https://api.staging.carddroper.com/health
# Expected: {"status":"ok","database":"connected"}

curl -sS -o /dev/null -w "%{http_code}\n" https://api.staging.carddroper.com/auth/me
# Expected: 401

# 2. Frontend on custom domain
curl -sSf https://staging.carddroper.com | grep -o 'Carddroper</h1>'
# Expected: Carddroper</h1>

# 3. Frontend bundle references the new API URL (not *.run.app)
curl -sS https://staging.carddroper.com | grep -o 'api.staging.carddroper.com'
# Expected: at least one hit (from the baked-in NEXT_PUBLIC_API_BASE_URL)
# Expected NOT to match: *.run.app hostnames in the HTML/bundle.

# 4. SSL cert is Google-managed (optional sanity check)
echo | openssl s_client -connect api.staging.carddroper.com:443 -servername api.staging.carddroper.com 2>/dev/null | openssl x509 -noout -issuer
# Expected: issuer=... Google Trust Services (or similar GTS entry)
```

**Browser smoke (user):** open `https://staging.carddroper.com` in a browser. Confirm:
- Page renders the styled `<h1>Carddroper</h1>`.
- No mixed-content or cert warnings in the address bar.
- DevTools → Network: any XHR/fetch calls target `api.staging.carddroper.com`, not a `*.run.app` URL.

## Verification

**Automated checks:**

```bash
# Both CNAME records resolve to Google's hosting target
dig +short CNAME staging.carddroper.com      # ghs.googlehosted.com.
dig +short CNAME api.staging.carddroper.com  # ghs.googlehosted.com.

# Both domain mappings exist in Cloud Run
gcloud beta run domain-mappings list --region=us-west1 --format="value(metadata.name)" | sort
# Expected: api.staging.carddroper.com, staging.carddroper.com

# Last build succeeded
gcloud builds list --region=us-west1 --limit=1 --format="value(status)"   # SUCCESS
```

**Functional smoke:**

- `curl https://api.staging.carddroper.com/health` returns `{"status":"ok","database":"connected"}` with HTTP 200.
- `curl https://api.staging.carddroper.com/auth/me` returns HTTP 401.
- `curl https://staging.carddroper.com` returns HTML containing `<h1 class="text-4xl font-bold text-blue-600">Carddroper</h1>`.
- HTML/bundle references `api.staging.carddroper.com` and contains no `*.run.app` hostnames.
- Browser opens `https://staging.carddroper.com` with a valid HTTPS padlock (no cert warnings), page renders, DevTools shows XHR targeting `api.staging.carddroper.com`.

## Out of scope

- Prod domains (`carddroper.com`, `api.carddroper.com`) — those land after the prod project stands up (future ticket).
- Cloudflare proxy mode (orange cloud) — staging stays DNS-only per `environments.md`. Proxy mode is a future decision once we decide we want WAF/CDN and have a Stripe webhook carve-out strategy.
- SendGrid email DNS (SPF/DKIM/DMARC) — `environments.md` §70-74 tracks this; separate ticket once email verification lands.
- MX / inbound mail for `privacy@`, `legal@`, `support@` — Cloudflare Email Routing setup is a pre-launch ticket (PLAN.md §11).
- CORS configuration on backend — not needed for same-parent-domain requests (browser treats `staging.carddroper.com` → `api.staging.carddroper.com` as cross-origin only on subdomain; depending on browser policy, we may need a later ticket if fetches fail).
- Removing the `*.run.app` URLs from Cloud Run — they remain reachable by default; custom domain is additive. We can restrict via IAM later if we want to force the custom domain.

## Report

User pastes:
1. Output of the Phase 5 verification block.
2. A brief "browser opens `https://staging.carddroper.com`, styled heading renders, Network tab shows `api.staging.carddroper.com`" confirmation.

Orchestrator handles:
- Verifying each line.
- Flipping `doc/operations/deployment.md` checkbox for the staging `Stripe webhook endpoint` row is **not** flipped by this ticket (that lands with the Stripe work). The relevant status items covered here: none directly (staging deployed is already ticked); but the prod template now has the domain pattern validated.
- Appending Resolution note + flipping ticket status.
