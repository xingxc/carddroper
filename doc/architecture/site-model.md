# Site Model — auth experience and page structure

**Status:** DRAFT / DISCUSSION. This is not a decision yet; it's the frame for making one. Edit in place as we converge.

## Purpose

One question: **how much of Carddroper works without an account?**

The answer shapes:
- What `/` is (marketing page? editor? card browser?).
- Where the auth wall sits (every page? certain actions? only monetized actions?).
- Whether anonymous users can persist work (drafts, addresses, preferences).
- The shape of the login/register flow (gate vs. capability prompt).
- What 0015 actually builds and in what order.

Revisit when: first gated feature ships, or conversion funnel data argues for a change.

## Reference models

### Canva — login-first

**What it does.** `canva.com` hits a marketing page; to touch the editor you must sign up (email + password, no credit card, instant access). Templates browse requires no login but is cosmetic — the moment you click a template, you're in the auth wall.

**Why.** Every canvas is tied to a user workspace. No anonymous persistence. Freemium business: conversion is *email → free account*, then *free → paid* later. The auth wall is early so they collect the email as early as possible.

**Good for.** Products where (a) every action produces state worth persisting to a specific user, and (b) the business model wants the email address up front (remarketing, free-tier nurturing).

**Friction cost.** First-time visitors who just wanted to *see* hit an account wall and bounce. Canva accepts this because their templates gallery is the marketing page and the editor is the conversion point.

### Paperless Post — open browse, auth at send

**What it does.** Full browse + design + preview without signing in. You can pick a card, customize it, address the recipient list, preview the rendered email — all anonymously. The auth wall hits at **send**, which is also the payment moment (for paid cards) or the account-creation moment (for free cards).

**Why.** The impulsive-gift use case: *"oh shoot, her birthday is today"* → design a card in five minutes → send. Requiring signup first would kill the impulse. By the time the user hits the send button, they've invested 5–15 minutes in a design and are strongly motivated to complete the transaction even with friction.

**Good for.** Products where (a) the "aha" moment is the thing *before* the paid action, and (b) the paid action is a natural conversion point.

**Technical cost.** Anonymous draft persistence. Usually done with either localStorage (fragile — clears on browser wipe, doesn't cross devices) or server-side tokenized sessions (a server-generated cookie binds drafts to an anonymous ID). On signup, the anonymous ID's drafts get claimed by the new user ID.

**Friction cost.** Near-zero to the user. Real cost is to engineering: two persistence paths (anonymous + authenticated) and a claim-on-signup step.

### Figma — login-everywhere

**What it does.** Completely auth-walled. Even the editor landing page redirects to login. View-only shared files generally require the viewer to have an account (configurable, but default-locked).

**Why.** Collaboration is the product. Anonymous users break multiplayer semantics (who made this cursor move? who can I @-mention?). Also: Figma is a paid-first business — the free tier is deep, but everyone is identified.

**Good for.** Products where the core value is identity-dependent collaboration or where anonymous sessions create data-model complexity with no offsetting business value.

**Friction cost.** High for first impressions. Figma absorbs it because they're a tool of record — people come in with intent, not curiosity.

## Decision axes

Each axis is a knob we can set independently (within limits):

1. **Anonymous editor access.** Can an unauthenticated user design a card end-to-end (minus send)?
2. **Auth-wall location.** First page / open editor / add recipient / send / payment.
3. **Anonymous persistence.** None (session-only) / localStorage / server-tokenized.
4. **Cross-device anonymous.** Does an anonymous draft follow the user to their phone? Yes requires server-side tokens in a URL.
5. **Anonymous share URLs.** Can an anonymous user send a preview link to a friend? (separate from send-to-recipient.)
6. **Account creation moment.** Just-in-time at send / explicit upgrade CTA / forced at editor entry.
7. **Default `/` for first visit.** Marketing page / editor / card browse / search.

Pick points on each axis and you get a concrete site model.

## Carddroper context

**Product:** curated card templates → customize → send to recipient(s) via email.

**Monetization (from PLAN.md):** credits or subscription to cover send volume; premium/designer templates later.

**The conversion moment.** When the user clicks **send**. That's when money changes hands (credits debit) AND when the email requirement is enforced (verified email). Two gates collapse into one natural checkpoint.

**Closest competitor.** Paperless Post by a wide margin. Canva is a broader creative tool; Figma is a collaboration tool; Paperless Post does exactly the thing Carddroper does.

**Bet about user behavior.** The user flow we care about is *"I remembered someone's birthday / want to send a thank-you / holiday greeting"* — impulsive, time-boxed, emotionally motivated. Any friction before the user has emotionally invested in a specific card kills the session.

## Proposed models for Carddroper

Three concrete options. Pick one; the others are here for contrast.

### Option A — Paperless Post clone ("open-until-send")

Anonymous users can:
- Visit `/` and see the browse grid immediately.
- Pick a template, enter the customizer, edit text/images/colors.
- Preview the final rendered card.
- Enter recipient emails.

Auth wall at the **send** button. Clicking "Send" opens a combined flow: create account (email + password) → verify email → enter payment → send, OR sign in to existing account → verify if needed → pay → send.

Anonymous drafts persist via **server-side tokenized sessions** (HttpOnly cookie with a random ID binding to a `draft` row keyed on that ID). On signup, all drafts under the anon ID get re-keyed to the new user ID.

**Pros:**
- Highest conversion for the impulsive use case.
- Matches the reference competitor exactly.
- Auth wall aligns with payment — natural moment, no artificial friction.

**Cons:**
- Most engineering complexity. Two persistence paths and a claim step. Have to handle: drafts expiring, collision on signup (user already has drafts), abandoned anon drafts (garbage collection).
- Anonymous abuse surface is larger (bot-created drafts, template scraping).

### Option B — Canva-lite ("login-first-editor")

Marketing pages (`/`, `/templates`, `/pricing`) work without login. The editor requires login. Clicking any template or "start designing" from the landing page triggers the login/register flow.

Anonymous users see a templates grid that serves as both marketing surface and template gallery, but can't open the customizer.

**Pros:**
- Simple. One persistence model (authenticated only). No anonymous tokenization, no claim-on-signup.
- Email address collected early → remarketing and nurture flows available from day one.
- Smaller security surface (all state is user-scoped).

**Cons:**
- Loses the impulsive use case. User has to commit to "this feels like a real account" before seeing the product.
- Worse fit for our closest competitor. If someone comes from "I saw a Paperless Post card and want to try Carddroper," the friction differential is felt.

### Option C — hybrid ("browse-open, design-gated")

Marketing + template gallery + template detail pages work without login. Opening the customizer requires login.

Between A and B: the gallery is a rich experience (search, filters, categories, previews), but editing is gated.

**Pros:**
- Preserves SEO and template-browsability without the anonymous-draft complexity.
- Still has a moment of friction, but after the user has demonstrated interest ("I clicked three templates").
- Simpler than A.

**Cons:**
- Still kills the impulse — user has invested *some* attention but hasn't started designing, so the auth wall is still "cost without reward yet."
- Doesn't differentiate from generic SaaS templates-gated patterns.

## Open questions (need answers before picking)

1. **Impulse vs. identity.** Is the "I remembered their birthday, need to send in 5 min" flow critical, or is Carddroper positioned more as a tool users return to?
2. **Cross-device anonymous.** If we go with Option A, do drafts need to sync across devices? Major implementation difference (URL-based tokens vs. cookie-only).
3. **Anonymous share.** Can anon users share a preview URL to collaborate on a card with a friend before sending? (A 4-person birthday card from the whole office.)
4. **Premium templates visibility.** Can anonymous users see premium templates (and hit a paywall at editor open), or are premium gated even from browse?
5. **Abandoned-draft policy.** If we persist anon drafts, how long do they live? Paperless Post uses ~30 days. We'd want to match or undercut to reduce bloat.
6. **Landing page for first visit.** Regardless of model: what does a never-before-user see at `/`? Hero + template grid? Search bar? Category tiles?
7. **Mobile.** Is mobile-web our primary or a secondary surface? Anonymous sessions on mobile are harder (users lose localStorage more often; browser sessions churn).
8. **Signup friction reduction.** Do we want magic-link login (email-only, no password) for the Option A conversion moment? Reduces the send-flow friction further. Separate ticket if yes.

## Leaning

Default recommendation absent further data: **Option A (Paperless Post clone)**, for three reasons:

- **Competitor alignment.** The person who would consider Carddroper has likely used Paperless Post. Matching the expected model lowers the "wait, how does this work" tax.
- **Conversion-at-payment.** Collapsing the account / verify / pay moment into one flow aligns with the user's strongest motivation — they've already designed the card.
- **Long-term stickiness.** Anonymous drafts create a free-tier-lite experience. A user who's made three drafts has more reason to return than one who bounced at a signup wall on visit one.

Cost acknowledged: Option A is the most engineering. If we pick it, 0015 stays focused on **authenticated** flows only (register, login, verify, the auth foundation) because the anonymous-draft work is its own future ticket. The auth flow exists BEFORE the anonymous flow; it just isn't the *only* flow.

**Conservative alternative:** Option C (hybrid). If Option A feels like too much ambition for MVP, Option C preserves the browse surface (which is also the SEO surface) and defers the anonymous-editor complexity. Worse fit for competitor matching; much cheaper to build.

**Not recommended:** Option B. Loses too much of the product's natural positioning.

## Implications for 0015

Regardless of which model we pick, **0015 builds the same auth primitives** — register, login, verify, the cookie/refresh stack, useAuth hook. What changes is the *pages* and the *middleware*:

- **Option A**: `/` is the template grid (stub for now), no redirects. Auth pages live at `/login` / `/register` / `/verify-email-sent` / `/verify-email`. Middleware is empty (no gated pages yet). Users land authed at `/` (same page as before, just with an auth-aware header).
- **Option B**: `/` is the marketing landing, unauthed. Editor routes (none yet) would be protected by middleware. Middleware redirects authed users at `/login` or `/register` → `/`.
- **Option C**: Same as A for 0015 (no editor yet), but middleware gets updated when the editor ticket lands.

So in practice the 0015 work is nearly identical across A/B/C. **Picking A vs. C vs. B doesn't block 0015 — it just affects whether the future editor/send tickets need anonymous-draft work.**

This means we can ship 0015 and defer the hard A-vs-C decision to the first editor ticket, as long as we keep 0015's landing page neutral (doesn't commit to one model).

## Decision

**Pending.** Fill in when we converge.
