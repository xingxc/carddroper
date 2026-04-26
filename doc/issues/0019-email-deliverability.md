---
id: 0019
title: email deliverability — SendGrid Sender Authentication + SPF / DKIM / DMARC
status: open
priority: high (blocks public launch; hurts signup conversion today)
found_by: 0015 Phase 2 manual browser walkthrough step 1.3 (2026-04-22) — SendGrid-delivered verification email landed in the recipient's spam folder despite successful delivery. Gmail's "Show original" would show whether SPF / DKIM / DMARC are passing, failing, or missing.
---

## Context

Carddroper sends transactional email (verify-email, reset-password, change-email, email-changed, credits-purchased) via SendGrid using `FROM_EMAIL=noreply@carddroper.com` and `FROM_NAME=Carddroper` (set in `cloudbuild.yaml` staging deploy). Deliverability is entirely a DNS / SendGrid-console concern — no code change involved.

The immediate symptom was the verification email landing in spam on a Gmail inbox during the 0015 walkthrough. That's almost always one of:

- Domain has no SPF record that authorizes SendGrid.
- Domain has no DKIM records (SendGrid signs mail with keys published at `sX._domainkey.carddroper.com`); without those in DNS, Gmail sees an unsigned message.
- Domain has no DMARC record and mail carries no alignment → Gmail treats the sender as low-reputation.
- SendGrid's Sender Authentication (formerly "Domain Authentication") was never completed, so SendGrid sends from a generic envelope that Gmail penalizes.

The fix is several small DNS records on Cloudflare plus a click-through in the SendGrid console. All user-owned — AI agents don't touch DNS or third-party consoles.

### Chassis-reliability stake (updated 2026-04-25)

This ticket is no longer just deliverability hygiene. Ticket 0017 (resolved 2026-04-25) landed the **change-email security canary**: a notification email to the OLD address ("your email was changed") that fires when an email change is confirmed. PLAN.md §6 #8 designates this as the silent-account-takeover detection mechanism — if an attacker briefly compromises an account and changes the email, the original owner's only signal is this notification.

Without SPF/DKIM/DMARC, **the canary itself is spoofable**: an attacker could send a fake "your email was changed" notification (forged FROM `noreply@carddroper.com`) to confuse the original owner into thinking the change was legitimate. DMARC at `p=quarantine` or stronger is what hardens the canary against this spoofing class. 0019 is therefore a load-bearing dependency of 0017's security model, not just a "spam-folder" concern.

## Scope

**In scope — set up full transactional email authentication on `carddroper.com`:**

1. **SendGrid Sender Authentication (domain auth).** In the SendGrid console:
   - Settings → Sender Authentication → "Authenticate Your Domain."
   - Choose DNS host: Cloudflare.
   - Enter `carddroper.com` as the from-domain. Leave "Use automated security" ON (SendGrid handles key rotation). Leave "Use a custom return path" at default.
   - SendGrid will generate three CNAME records (typical shape: `em1234.carddroper.com → sendgrid.net`, `s1._domainkey.carddroper.com → s1.domainkey.uXXXXX.wl.sendgrid.net`, `s2._domainkey.carddroper.com → s2.domainkey.uXXXXX.wl.sendgrid.net` — the exact subdomains vary per account).
   - Add each as a CNAME record in Cloudflare DNS with proxy status **DNS only** (grey cloud — CNAMEs to external hosts must not be Cloudflare-proxied for email to work).
   - Return to SendGrid console, click "Verify." All three records must show green. Retry after 5-10 minutes if DNS hasn't propagated.

2. **SPF** (if not auto-created by SendGrid's Sender Authentication; most setups bundle it):
   - TXT record at `carddroper.com`: `v=spf1 include:sendgrid.net ~all`.
   - `~all` is softfail — safer starting posture than `-all` while we verify everything works. Tighten to `-all` after a week of clean delivery if desired.
   - If you already have an SPF record (e.g. from Google Workspace), merge — do not add a second TXT at the apex. A domain can only have one SPF record. Merge shape: `v=spf1 include:_spf.google.com include:sendgrid.net ~all`.

3. **DMARC:**
   - TXT record at `_dmarc.carddroper.com`: `v=DMARC1; p=none; rua=mailto:dmarc@carddroper.com; fo=1`.
   - `p=none` = monitor-only, don't reject/quarantine — correct starting posture. Lets us observe auth pass/fail via aggregate reports without breaking legitimate mail.
   - `rua` optional but recommended; if you don't want reports, omit. You'll need to receive mail at `dmarc@carddroper.com` for the reports to be useful — set up a forwarder in Cloudflare Email Routing if desired.
   - After a week of clean monitoring, optionally move to `p=quarantine` then `p=reject`. Not required for this ticket.

4. **SendGrid Link Branding** (optional but improves reputation):
   - SendGrid console → Settings → Sender Authentication → "Link Branding."
   - Generates two more CNAMEs (e.g. `url1234.carddroper.com`, `em_CNAME_verification`). Add to Cloudflare DNS. Verify.
   - After this, SendGrid rewrites click-tracking URLs to use the custom domain instead of `sendgrid.net`, which Gmail rewards with better inbox placement.

5. **Verification test:**

   **Option A — register flow** (single template):

   - After all records are green in SendGrid console, trigger a fresh verify email against staging:
     - Register a new `smoke+deliverability-<date>@<your-personal-domain>` account via the `/register` page on `https://staging.carddroper.com` (or via `curl` against `https://api.staging.carddroper.com/auth/register`).
     - Receive the verify-email delivery in your inbox.
     - **Do NOT click the link** (saves us from burning the verify token during diagnostic).
     - Open the email → "Show original" (Gmail) or "View headers" (Outlook / Mail.app).
     - Confirm the headers carry: `SPF: pass (google.com: domain of ...)`; `DKIM: pass (Authentication-Results: ...)`; `DMARC: pass`.
     - Confirm the email lands in the **Primary inbox**, not Spam or Promotions.

   **Option B — change-email flow** (recommended; exercises 2 templates + the security canary in one action; available since 0017.1 landed 2026-04-25):

   - Log in to staging as an existing user.
   - Profile menu → Change email → submit current password + a new email at a personal domain.
   - **Inspect the verification email at the new address** (template `SENDGRID_TEMPLATE_CHANGE_EMAIL`) — DKIM/SPF/DMARC headers should all pass; should land in Primary inbox. Click the verification link.
   - **Inspect the canary email at the OLD address** (template `SENDGRID_TEMPLATE_EMAIL_CHANGED`) — DKIM/SPF/DMARC headers should all pass; should land in Primary inbox. **This is the most important inspection** — the canary is the security mechanism this ticket protects.
   - Both emails passing all three checks confirms that:
     1. The chassis sends from the authenticated domain (SPF authorization works).
     2. SendGrid signs both templates with valid DKIM keys (DKIM passes).
     3. DMARC alignment between the From header and the SPF/DKIM domains succeeds (DMARC passes).
     4. The security canary surface is no longer spoofable.

**Out of scope:**

- Any code change. Backend `send_email` helper, frontend templates, and SendGrid Dynamic Template IDs are unchanged.
- Anything beyond staging. Prod domain auth is a separate future task (will copy this playbook when prod env is stood up per PLAN.md §10 item 7).
- Transactional-email testing infrastructure (e.g. Mailosaur). If we want automated inbox-level regression testing later, that's its own ticket.
- Customer-facing marketing email. Entirely separate from transactional — Sender Authentication for transactional covers both if FROM_EMAIL stays under the authenticated domain, but marketing content warrants its own review.

## Verification

**During setup (user):**
- SendGrid Sender Authentication status: all CNAME rows green.
- Cloudflare DNS: all SPF / DKIM / DMARC records present; `dig TXT carddroper.com +short` and `dig TXT _dmarc.carddroper.com +short` from any machine should return the expected values.
- Gmail "Show original" on a freshly-received verify email shows SPF pass, DKIM pass, DMARC pass.
- Email lands in Primary inbox, not Spam / Promotions.

**Post-fix (optional but recommended):**
- Run https://www.mail-tester.com — send a test verify-email to the address it generates, check the score (aim for 9+/10 on first try after full setup).
- Monitor DMARC aggregate reports (if `rua` configured) for one week to confirm no legitimate mail is failing.

## Out of scope — tracked deferrals

- **Tightening SPF `~all` → `-all`.** Once a week of clean reports confirms no third-party sender is slipping through. Own task, no ticket needed — trigger: "a week of DMARC reports show no legitimate-but-failing senders."
- **Tightening DMARC `p=none` → `p=quarantine` → `p=reject`.** Same trigger pattern. Don't skip the ramp; quarantine and reject at too-new a setup will burn legitimate deliveries.
- **BIMI (brand indicator).** Nice-to-have logo-in-inbox feature. Requires DMARC `p=quarantine` or stronger + a VMC (costly). Not for v0.1.0.

## Ownership

**User-owned. No agent dispatch needed.** AI agents don't access DNS, SendGrid console, or any third-party authentication surface — this ticket is a runbook for the user to execute. The orchestrator's role:

- Interpret "Show original" output if headers are confusing.
- Suggest record values if something looks off.
- Capture the resolution details (DNS records added, SendGrid green-check timestamp, header snippets) once the user confirms green.
- Update `doc/operations/deployment.md` and `doc/operations/gcp-deployment-tutorial.md` with any new gotchas surfaced during execution.

### `dmarc@<domain>` mailbox setup (recommended)

The DMARC `rua=` address must receive mail or reports go to a black hole. Quickest path with Cloudflare:

1. Cloudflare Dashboard → Email → **Email Routing** → Get Started.
2. Verify a destination email address (your personal Gmail).
3. Cloudflare auto-creates the required MX records.
4. Create a route: `dmarc@carddroper.com` → forward to your verified destination.
5. DMARC aggregate reports (XML) now land in your inbox. (For human-readable analysis, optionally upload to dmarcian, easyDMARC, or similar — free tiers exist. This step is deferred until the volume is worth automating.)

Without this, `rua=mailto:dmarc@carddroper.com` is a black hole and you won't see whether legitimate mail is failing DMARC during the `p=none` ramp-up week.

## Report

On close, append:
- Cloudflare DNS record names + values added.
- SendGrid Sender Authentication green-check date.
- One confirmed "Show original" header snippet showing SPF/DKIM/DMARC all pass.
- Mail-tester score (if run).

## Resolution

*(filled in by orchestrator when user confirms inbox placement + auth-pass headers)*
