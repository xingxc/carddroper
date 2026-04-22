# Site Model — auth experience and page structure

**Status:** DECIDED (2026-04-22). Revisit if conversion data contradicts the bet, or when extracting the chassis into a reusable starter.

## Decision

**Canva model — marketing-then-auth-wall, free email signup, application locked by authentication.**

- Marketing surface (`/`, `/pricing`, `/features`, `/about`, `/legal/*`) is public.
- Application surface (everything of substance — editor, card browser, send, account) requires an authenticated session.
- Signup is free: email + password, no credit card. Verification email sent, 24h window, soft lock at 7 days.
- Auth wall is a single crisp boundary between two route groups, not a scattered set of per-route gates.

Primary optimization: **the chassis should lift cleanly into the next project.** Carddroper is the first build on top of this chassis; future products reuse the same marketing-then-auth-wall structure with a different body.

## Why this model

1. **It's the dominant SaaS pattern.** Linear, Notion, Stripe Dashboard, Vercel, PlanetScale, Supabase — every major B2B/prosumer SaaS uses marketing-then-auth-wall. Ecosystem templates, auth libraries, tutorials, and docs all assume this shape. Copy-pasteability is highest for the most common pattern.
2. **One persistence path.** All user state is user-scoped. No anonymous drafts, no claim-on-signup, no tokenized anonymous sessions. Smaller attack surface, fewer edge cases, less code.
3. **The marketing/app split IS the copy-paste seam.** `app/(marketing)/` stays across projects; `app/(app)/` is the product-specific body. The route-group boundary is the chassis/body boundary.
4. **Email collected early.** The free signup is the conversion moment — users who register but never pay are still addressable via remarketing and nurture sequences.
5. **Compatible with future Option A retrofit.** If conversion data later argues for an open-browse / anonymous-draft surface, it can be layered on top of the authenticated chassis without tearing the chassis out. The chassis doesn't preclude future flexibility.

**What Carddroper specifically gives up:** the Paperless Post impulsive-send flow (anonymous browse → design → auth wall at send). Real cost, accepted.

## Chassis vs. body

The project is structured as a reusable **chassis** that hosts a product-specific **body**. Copy-paste to a new project means taking the chassis and replacing the body.

| Layer | Reusable? | What it contains |
|---|---|---|
| Auth frontend | ✅ chassis | `(auth)/` route group: login, register, verify-email-sent, verify-email, forgot-password, reset-password, change-email. `useAuth` hook, `lib/api.ts` wrappers, 401 silent refresh, `proxy.ts` cookie gate (Next.js 16 rename of `middleware.ts`). |
| Marketing frontend | ✅ chassis (structure) / 🎨 body (content) | `(marketing)/`: landing, features, pricing, about, contact, legal. Structure is reusable; text, imagery, and brand are product-specific. |
| App frontend | ❌ body | `(app)/`: everything product-specific. Carddroper's card browser, customizer, send flow. New project swaps this entirely. |
| Auth backend | ✅ chassis | `routes/auth.py`, `services/email_service.py`, `models/user.py`, `models/refresh_token.py`, `models/login_attempt.py`, their Alembic migrations. |
| App backend | ❌ body | Product-specific. Carddroper's card models, Stripe ledger, send endpoints. |
| Infrastructure | ✅ chassis | `cloudbuild.yaml`, `Dockerfile`s, `docker-compose.yml`, `scripts/smoke_chassis.sh` (local chassis smoke), `backend/scripts/smoke_*.py` (staging smokes), `doc/operations/*`. |
| Legal | 🎨 body | `doc/legal/` drafts; every project re-drafts under its own attorney review. |

## Reusability constraints

Seven design rules that keep the chassis portable. Breaking any of them adds retrofit cost later.

1. **No product-specific logic in the chassis.** The auth service doesn't know cards exist. `User` has `{ email, password_hash, verified_at, token_version, stripe_customer_id, created_at, updated_at }` — nothing more. Product models reference `User` but `User` never references product models. No FKs from auth tables into app tables.

2. **Branding in one file.** `frontend/config/brand.ts` (name, domain, from-email, support-email) and CSS variables in `app/globals.css` (color tokens, font tokens). New project edits these two files; the rest of the chassis reads from them.

3. **Marketing copy is content-driven where cheap.** Hero copy, feature blurbs, pricing tiers in `frontend/config/content/` (typed TS objects or MDX). Button labels and error messages stay inline — over-interpolating hurts readability more than it helps reuse.

4. **Environment toggles for optional chassis features.** Stripe customer creation at register is behind `FEATURE_STRIPE=true` (default true for Carddroper). Same pattern for any future chassis feature that not every project needs.

5. **Placeholder strings read as placeholders.** `{brand.name}` in auth-page copy, `{brand.domain}` in email templates. New project edits `brand.ts` once.

6. **Chassis tests don't reference body concepts.** `backend/tests/test_auth_flow.py` tests only auth behavior, not card logic. Body tests live in separate files.

7. **`STARTER.md` at repo root** (future, post-0015). Twenty-line checklist: fork → replace `Carddroper` in N files → edit `brand.ts` → swap color tokens → delete `(app)/*` + body backend models → run. The checklist is written empirically after we've actually done one lift, not prospectively.

## Reference models considered

Kept brief for future readers. Full analysis was in the discussion version of this doc (see git history if needed).

- **Canva — login-first.** Chosen. Marketing page, free signup, editor behind the wall. Dominant SaaS pattern.
- **Paperless Post — open-until-send.** Rejected for v1. Best for impulsive-send UX but costs a second persistence path (anonymous drafts + claim-on-signup). Deferred; can be layered on top of the authenticated chassis later if conversion data demands it.
- **Figma — login-everywhere.** Not considered seriously — public marketing surface is table stakes for a product that isn't purely a collaboration tool.

## Implications for 0015

0015 builds the chassis auth flow exactly as spec'd in `doc/systems/auth.md` and ships the skeletal marketing + app surfaces that demonstrate the boundary works.

**Pages shipped in 0015:**

- **Marketing group `(marketing)/`**:
  - `/` — landing. Auth-aware header (Sign in / Register buttons for anon; email + Logout for authed). Minimal body for 0015 (hero + "coming soon"); real marketing content is a separate design ticket.
- **Auth group `(auth)/`**:
  - `/register` — email + password. No ToS checkbox for 0015 (deferred to a legal-acceptance ticket before first Stripe charge).
  - `/login` — email + password. No "forgot password" link (deferred to 0016).
  - `/verify-email-sent` — post-register landing; explains the email, offers resend.
  - `/verify-email?token=` — client calls `POST /auth/verify-email`, shows success or error, redirects to `/login` on success (verification increments `token_version` → user must re-authenticate).
- **App group `(app)/`**:
  - `/app` — stub page for 0015. "You're logged in as {email}." + Logout button. Exists solely to prove the boundary works and gives the middleware an authed landing target. Real app UI replaces this in later tickets.

**Middleware:**

- Any path under `(app)/` requires `access_token` cookie; redirect to `/login` if missing.
- If authed and on `/login` or `/register` → redirect to `/app`.
- Everything under `(marketing)/` and `(auth)/` is public (auth pages handle their own authed-redirect).

**Verification UX:**

- Unverified users CAN log in and reach `/app` (per auth.md §Soft cap: days 0–6 login works, paid actions 403).
- No persistent banner. Verification is a capability gate, not a nag.
- When the first gated action (send) ships in a later ticket, it shows a contextual modal: *"Please verify your email to send. [Resend verification]"*. For 0015, no gated actions exist, so no modal yet.

**Deferred from 0015 (tracked):**

- Legal acceptance checkbox + `/legal/terms` and `/legal/privacy` static pages — its own ticket, scheduled before first Stripe charge.
- Forgot-password / reset-password — ticket 0016.
- Change-email — ticket 0017.
- Real marketing content — design ticket, post-MVP.
- Real `(app)/` body — Carddroper card-browse + customizer tickets.

## Implications for future tickets

- **Editor / customizer ticket.** Lives entirely under `(app)/`. Middleware already enforces auth; no changes to the boundary.
- **Send ticket.** The `POST /cards/:id/send` endpoint applies `require_verified` dependency server-side; frontend catches the 403 and shows the verification modal. Still no banner.
- **Pricing page.** Public, lives under `(marketing)/`. Click-through to signup.
- **Chassis extraction (future).** Once the chassis stabilizes post-0016/0017, extract to a template repo or starter. Criteria: three viable candidates for reuse, or one concrete "I'm starting a new project" moment.

## Open questions (non-blocking for 0015)

1. **Magic-link auth as a future chassis feature?** Email-only login, no password. Reduces friction for return users. Separate ticket; not in 0015.
2. **OAuth providers (Google, Apple) as future chassis features?** Same — belongs behind a feature flag, later ticket.
3. **Marketing page content/design.** `/`, `/features`, `/pricing` need real copy and design. Tracked separately from 0015.
4. **Public template preview pages?** When Carddroper's card-browse lands, do preview URLs work anonymously (SEO + share) or require login? Punt to the card-browse ticket.
