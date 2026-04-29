import { api } from "@/lib/api";

/**
 * Create a Stripe Customer Portal session and return the one-shot URL.
 *
 * @param returnUrl - Where Stripe should redirect the user after they leave
 *   the Portal. Must start with the app's origin. Defaults to
 *   `{FRONTEND_BASE_URL}/app/subscribe` on the backend when omitted.
 */
export async function createPortalSession(
  returnUrl?: string
): Promise<{ url: string }> {
  const body = returnUrl ? { return_url: returnUrl } : {};
  return api.post<{ url: string }>("/billing/portal-session", body);
}
