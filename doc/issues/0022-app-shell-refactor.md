---
id: 0022
title: app-shell refactor (chassis) — left-rail sidebar + profile popover menu
status: open
priority: medium (pure UI chassis primitive; unblocks 0023's billing link integration; no user-visible functional change beyond chrome reshape)
found_by: 0022/0023 planning session on 2026-04-23 — reshaped the original "add Billing link to AppHeader" plan into a Canva/Stripe-style left-rail app shell. Split from the original combined-scope ticket for reliability reasons: layout primitive must land on a stable substrate before billing features consume it.
---

## Context

The current `(app)/layout.tsx` renders a top-nav `AppHeader` with `[Carddroper]   email  Logout`. This works for a single page (`/app`) but doesn't scale as feature pages accumulate. 0023 will add `/app/billing`; future tickets will add more (`/app/settings`, `/app/history`, `/app/send`, etc.). A top nav gets noisy fast; a left rail scales naturally — icons stack vertically, main content gets horizontal room.

Ticket 0022 reshapes the authed layout once, cleanly, before any feature page consumes it. After 0022:
- `(app)/*` routes render inside a two-column app shell: fixed-width left rail, flex-1 main content.
- The rail hosts a **brand mark** (top) and a **profile avatar** (bottom).
- Clicking the profile avatar opens a popover with the user's email (display), a `Settings` section label (placeholder; children land with their feature tickets — `Billing` in 0023), and the Logout action.
- Main content gets tighter padding so feature surfaces have room.

0023's billing link slots into the profile popover's Settings section via a single-line addition — no layout churn.

**Chassis framing.** This is a chassis-level app-shell primitive. Every future project adopting this chassis inherits the left-rail shape, profile popover pattern, and "projects add their own icons without rewriting the layout" extensibility. No carddroper-specific opinions in the chassis code.

## Design decisions (pre-committed)

Decisions locked in during the 2026-04-23 planning session:

1. **Canva/Stripe left-rail pattern.** Fixed-width sidebar, ~64-72px. Main content is flex-1 with its own padding. Sidebar is `position: fixed` (Stripe convention) so it stays visible during page scroll.

2. **Rail content for 0022:**
   - **Top:** Brand mark — `{brand.name[0]}` letter in a styled square. Links to `/app`. Replaces the text "Carddroper" from the old top nav. Projects substitute their own logo/mark by editing a single component.
   - **Middle:** Empty (flex spacer). Future features add icons here (0023+ tickets).
   - **Bottom:** Profile avatar — `{user.email[0].toUpperCase()}` letter in a styled circle. Click opens the popover. Fallback to `?` if email is somehow missing.

3. **Profile popover contents (flat, no nested submenus):**
   ```
   ┌───────────────────────────┐
   │ user@example.com          │  ← display-only, truncated if long
   │ ─────────────────────── │
   │ Settings                  │  ← section label, non-clickable
   │ ─────────────────────── │
   │ Logout                    │  ← action (calls existing LogoutButton)
   └───────────────────────────┘
   ```
   `Billing` is not added in 0022 — lands in 0023 under the Settings label as a direct nested link. Future settings children (`Account`, `Notifications`, etc.) land with their feature tickets.

4. **Popover interactions (minimum viable accessibility):**
   - Click profile icon → menu opens.
   - Click outside the menu → closes (mousedown listener on document, ref-based detection).
   - Press `Escape` → closes (keydown listener on document).
   - `aria-expanded`, `aria-haspopup="menu"`, `role="menu"` on the panel, `role="menuitem"` on actionable items.
   - Deferred: arrow-key navigation within the menu, focus trap, focus-return-to-trigger on close. Minimum is enough for chassis; full WAI-ARIA Menu pattern is a later hardening.

5. **Hand-rolled markup, no new dependencies.** `lucide-react` is attractive but deferred until we have ≥10 icons to justify the dep. 0022 needs zero SVG icons — brand mark and profile avatar are both letter-in-styled-shape, which is pure Tailwind + `<div>`.

6. **Main-content padding trimmed.** Current `/app/page.tsx` uses `p-8`. New default across `(app)/*` pages: `px-6 py-4` (wrap pages in a container that applies this, or apply at page level). Features take horizontal room back; vertical density stays comfortable.

7. **Preserve 0016.x auth behaviors exactly.** The new layout keeps:
   - `const { isLoading, isAuthenticated } = useAuth();`
   - `useEffect(() => { if (!isLoading && !isAuthenticated) router.replace("/login"); }, [isLoading, isAuthenticated, router]);` (0016.2 redirect)
   - `if (isLoading || !isAuthenticated) return <LoadingScreen />;` (0016.5 pre-decision blur)
   - These wrap the new JSX output (sidebar + main) identically to how they wrapped the old output (header + main).

8. **z-index layering (deliberate, not accidental):**
   - Sidebar: `z-20`
   - Profile popover: `z-30`
   - LoadingScreen: `z-50` (unchanged from 0016.5; covers everything during pre-decision)

9. **Marketing layout (`/`) is not touched.** Top-nav pattern on `/` stays. Marketing and authed app have different shape intentionally — they serve different audiences. This is a consistent chassis pattern (Stripe, Notion, Linear all do this).

10. **No sidebar collapse toggle in 0022.** Rail is already minimal (2 rendered elements: brand + profile, with a spacer). Collapse is meaningful when there are 5+ rail items competing for space. Revisit in a future ticket once the rail has grown.

11. **No mobile responsive work in 0022.** Narrow desktop (~1024px+) is the target. On narrow mobile, the 64px rail still renders but the popover may overflow awkwardly. Mobile hamburger pattern lands in a dedicated ticket when we have real mobile UX requirements.

12. **`AppHeader` function is deleted.** The inline `AppHeader` function in `(app)/layout.tsx` (currently renders the top nav) goes away entirely. Its responsibilities split across `AppSidebar` (brand mark) and `ProfileMenu` (user email + Logout). No references remain to `AppHeader` after the refactor.

13. **`LogoutButton` component is preserved.** It still owns the logout HTTP flow + hard reload (per 0016.7). We render it inside `ProfileMenu` with a className styled for menu-item presentation (matches existing className-prop pattern). No refactor of LogoutButton's internals.

## Out of scope

- `Billing` menu link (lands in 0023).
- Any other profile-menu items (Account, Notifications, etc.) — they arrive with their feature tickets.
- Sidebar collapse toggle.
- Mobile responsive (hamburger, drawer, narrow-screen rework).
- Keyboard arrow-key navigation within the menu.
- Focus trap / focus return on popover close.
- Active-route highlighting on sidebar icons.
- Tooltip-on-hover for sidebar icons.
- Avatar upload / custom avatar image system.
- `lucide-react` adoption.
- Any changes to `(marketing)/layout.tsx` or `(auth)/layout.tsx`.
- Any changes to `/app/page.tsx` content beyond padding adjustment (the "you're logged in as" text stays).
- Any backend changes.
- Any new dependencies.

## Acceptance

### Phase 0 — frontend (frontend-builder)

Read `doc/issues/0022-app-shell-refactor.md` end-to-end first. The Design decisions section is authoritative; follow it verbatim.

**Repository root:** /Users/johnxing/mini/postapp. Work on the current branch (main or dev — match the user's pattern). Do NOT touch `backend/`.

**1. New component — `frontend/components/app-shell/AppSidebar.tsx` (client component):**

- Fixed-position left rail: `fixed inset-y-0 left-0 w-16 z-20 flex flex-col items-center justify-between py-4 bg-gray-50 border-r border-gray-200` (Tailwind; adjust subtly if it reads visually off, but keep the shape).
- Contents top-to-bottom:
  - `<BrandMark />` (inline or a separate sub-component; renders `brand.name[0]` in a styled square, wrapped in a `Link` to `/app`).
  - `<div className="flex-1" />` (spacer — future features will fill this).
  - `<ProfileMenu />`.
- No props. Component renders the same for every authed user.

**2. New component — `frontend/components/app-shell/ProfileMenu.tsx` (client component):**

- Reads `useAuth()` for `user.email`.
- Internal state: `const [open, setOpen] = useState(false);` and `const menuRef = useRef<HTMLDivElement>(null);`
- Trigger: a button rendering the profile avatar (initial-in-circle). Tailwind: `w-8 h-8 rounded-full bg-gray-200 text-gray-700 flex items-center justify-center text-sm font-medium hover:bg-gray-300 focus:outline-none focus:ring-2 focus:ring-blue-500`.
  - aria-expanded={open}, aria-haspopup="menu", aria-label="Account menu".
  - On click: `setOpen((prev) => !prev)`.
- Popover panel (conditionally rendered when `open`):
  - Anchored via `fixed bottom-4 left-20 z-30` (left-20 = 80px = sidebar width + 16px gap). Use `absolute` with offset from the trigger if you prefer; whichever yields a clean visual.
  - Styled as a small card: `w-64 rounded-lg bg-white shadow-lg border border-gray-200 py-2`.
  - Contents in order:
    1. Email row: `<div className="px-4 py-2 text-sm text-gray-900 truncate">{user?.email ?? ""}</div>`.
    2. Horizontal divider: `<hr className="my-1 border-gray-200" />`.
    3. Settings section label: `<div className="px-4 py-1 text-xs font-semibold uppercase text-gray-500">Settings</div>`. No children in 0022; `Billing` lands in 0023 as an indented child link.
    4. Horizontal divider.
    5. LogoutButton wrapped in a menuitem row: re-render the existing `<LogoutButton>` with a `className` that matches the menu aesthetic — `block w-full text-left px-4 py-2 text-sm text-red-600 hover:bg-red-50` (or similar; preserve the existing "red" logout visual hint from the current top-nav).
  - `role="menu"` on the panel.
- Click-outside detection:
  ```tsx
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);
  ```
  Wrap BOTH trigger and panel inside `menuRef` so clicking the trigger doesn't immediately close the menu it just opened.
- Escape key:
  ```tsx
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open]);
  ```

**3. Refactor — `frontend/app/(app)/layout.tsx`:**

- Keep the `"use client"` directive at the top.
- Preserve imports: `useEffect, ReactNode, useRouter, useAuth, LoadingScreen`.
- Remove the inline `AppHeader` function entirely.
- Remove the import of `LogoutButton` (now imported by `ProfileMenu` instead).
- Remove the import of `Link` and `brand` (now used by `AppSidebar`'s `BrandMark`).
- Add import: `import { AppSidebar } from "@/components/app-shell/AppSidebar";`
- `AppLayout` function structure (preserving 0016.2 + 0016.5):
  ```tsx
  export default function AppLayout({ children }: { children: ReactNode }) {
    const router = useRouter();
    const { isLoading, isAuthenticated } = useAuth();

    useEffect(() => {
      if (!isLoading && !isAuthenticated) router.replace("/login");
    }, [isLoading, isAuthenticated, router]);

    if (isLoading || !isAuthenticated) return <LoadingScreen />;

    return (
      <div className="min-h-screen">
        <AppSidebar />
        <main className="ml-16 px-6 py-4">{children}</main>
      </div>
    );
  }
  ```
  `ml-16` compensates for the fixed-width 64px sidebar.

**4. Touch — `frontend/app/(app)/app/page.tsx`:**

- Current padding `p-8` is redundant now that `<main>` wraps with `px-6 py-4`. Remove the `p-8` on the outer div (or change to a much smaller value if visual polish requires it). Otherwise leave the page's "You're logged in as ..." content untouched.

**5. No other files touched.**

- Do NOT touch `(marketing)/layout.tsx`, `(auth)/layout.tsx`, `LogoutButton.tsx`, `lib/api.ts`, `context/auth.tsx`, or any backend file.
- Do NOT add dependencies to `package.json`.
- Do NOT add environment variables.

### Phase 1 — user manual verification

**Base (required before merge):**

1. `docker-compose up -d --build frontend`
2. Navigate to `http://localhost:3000/login`. Sign in with an existing user.
3. Land on `/app`. Verify visually:
   - Left rail, ~64px wide, gray background.
   - Top of rail: brand mark (single letter `C` in a styled square).
   - Bottom of rail: profile avatar (user's email initial in a gray circle).
   - Main content area starts immediately after the rail, no large empty gutter.
4. Click the profile avatar. Popover appears anchored near the trigger.
   - First row: your email.
   - Then divider, then `SETTINGS` label (uppercase small gray).
   - Then divider, then red `Logout`.
5. Press `Escape` → popover closes.
6. Re-open popover (click profile). Click anywhere else on the page → popover closes.
7. Re-open popover. Click `Logout`. Verify it still works: one intentional `POST /auth/logout`, hard reload, lands on `/`. (0016.7 behavior unchanged.)
8. **Auth regressions:** in a separate incognito tab, directly visit `/app`. Verify redirect to `/login` within a beat (0016.2 effect still fires). Verify brief blurry screen rather than empty chrome (0016.5 LoadingScreen still renders).
9. **Marketing not touched:** visit `/` (logged out). Top nav pattern still present, exactly as it was before.

**Automated gates:**

- `npm run lint` clean.
- `npx tsc --noEmit` clean.
- `npm run build` succeeds.

**Smoke scripts:** no new smoke script needed. Existing `smoke_auth.py` still exercises register/login/me/refresh/logout — none of those flows change. Staging deploy can re-run the existing battery.

### Phase 2 — staging

- Merge + push. Cloud Build redeploys.
- Manual: visit `https://staging.carddroper.com/login`, log in, same visual checks as Phase 1 items 3-8.
- Re-run the existing smoke battery (`smoke_healthz`, `smoke_auth`, `smoke_cors`, `smoke_verify_email`). All should pass (no functional auth change).

## Verification

**Automated (frontend-builder Phase 0 report):**

- `npm run lint` + `npx tsc --noEmit` + `npm run build` clean.
- Paste the final `AppLayout` function inline (shows the preserved 0016.2 effect + 0016.5 early-return + new sidebar+main JSX).
- Paste the `AppSidebar` component inline.
- Paste the `ProfileMenu` component inline, including the two `useEffect` blocks (click-outside + Escape).
- Confirm no other files touched.
- Confirm no new dependencies added to `package.json`.

**Functional (user, Phase 1):**

- Visual items 3-6 pass.
- Logout item 7 works per 0016.7.
- Auth redirect item 8 works per 0016.2 + 0016.5.
- Marketing item 9 unchanged.
- Automated gates green.

**Staging (user, Phase 2):**

- Deploy clean.
- Visual spot-check on staging matches local.
- Existing smoke battery green.

## Chassis implications

0022 establishes the app-shell chassis primitive. Any future project adopting this chassis inherits:

- Two-column authed layout (left rail + main content).
- Fixed-position sidebar anchored to the left viewport edge.
- Brand mark at top-left (one line to swap to a project logo when ready).
- Profile popover at bottom-left with email display + Settings section + Logout.
- Click-outside + Escape popover dismissal pattern (reusable for future popovers).
- Clean extension point — future features add icons to the middle of the sidebar without touching layout.tsx or ProfileMenu.

Projects wire their own features by adding icons to `AppSidebar` (middle section) and menu items to `ProfileMenu`'s Settings section. Both are trivial one-line additions in project-layer code.

No `chassis-contract.md` entry — this is a visual/UX pattern, not a startup invariant.

## Report

Frontend-builder (Phase 0):

- Files modified + one-line what-changed each.
- Paste the final `AppLayout`, `AppSidebar`, `ProfileMenu` components inline.
- Confirm 0016.2 redirect useEffect is preserved verbatim.
- Confirm 0016.5 LoadingScreen early-return is preserved verbatim.
- Confirm `LogoutButton` is unchanged (only its render site moved).
- Confirm `(marketing)/layout.tsx` + `(auth)/layout.tsx` + backend are untouched.
- `npm run lint` / `npx tsc --noEmit` / `npm run build` summary lines.
- Any deviation from the brief, with reasoning.

Orchestrator (on close):

- User Phase 1 visual + regression outcomes.
- User Phase 2 staging outcome.

## Resolution

*(filled in by orchestrator after user confirms Phase 1 visual + regression checks + Phase 2 staging pass)*
