/**
 * Chassis loading screen — full-viewport translucent blur.
 *
 * Deliberately empty: no spinner, no text, no brand mark. The blurred
 * backdrop itself is the "we're loading" signal; anything inside would
 * either (a) overlap with the inline operation spinners that sit just
 * underneath (VerifyEmailBody pending, ResetPasswordBody validating,
 * SubmitButton pending), or (b) add visual noise to a state that's
 * typically <500ms. Pattern follows Canva / Notion / Linear for warm
 * transitions; slow-network affordances (top progress bar, pulsing
 * corner dot, content-shaped skeletons) are deferred until a real need
 * surfaces.
 *
 * Use for "pre-decision" loading (auth state resolution, Suspense
 * fallbacks, any state where we can't yet decide what to render). For
 * in-operation feedback (form submission, individual mutation), prefer
 * inline spinners — those live inside the relevant component.
 *
 * Server-component by default (no hooks, no browser APIs). Can be
 * imported from client layouts without a "use client" bridge.
 */
export function LoadingScreen() {
  return (
    <div
      className="fixed inset-0 z-50 bg-white/70 backdrop-blur-md"
      role="status"
      aria-label="Loading"
    />
  );
}
