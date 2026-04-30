"use client";

import { useEffect, useState } from "react";
import { useSubscription } from "@/hooks/useSubscription";
import { createPortalSession } from "@/lib/api/billing";
import { ApiError } from "@/lib/api";

export interface ManageSubscriptionButtonProps {
  className?: string;
}

/**
 * Renders a "Manage subscription" button when the user has an active (or
 * otherwise manageable) subscription. Hidden entirely when:
 *   - No subscription exists (`has_subscription: false`)
 *   - The subscription is `incomplete` (recovery in progress via Portal would
 *     not be useful; the Subscribe form handles this case instead)
 *
 * On click: POSTs to /billing/portal-session with the current page URL as the
 * return_url, then redirects to the Stripe-hosted Customer Portal.
 */
export function ManageSubscriptionButton({
  className,
}: ManageSubscriptionButtonProps) {
  const { data } = useSubscription();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset loading state when the page is restored from browser bfcache
  // (e.g., user clicked "Manage subscription" → went to Stripe Portal → hit Back).
  // Without this, React state preserved by bfcache leaves the button stuck on
  // "Loading…" because the redirect-via-window.location.href never let the
  // component reach setLoading(false).
  useEffect(() => {
    const handlePageShow = (event: PageTransitionEvent) => {
      if (event.persisted) {
        setLoading(false);
        setError(null);
      }
    };
    window.addEventListener("pageshow", handlePageShow);
    return () => window.removeEventListener("pageshow", handlePageShow);
  }, []);

  // Render nothing when there is no subscription or it is in an incomplete
  // state (the subscribe form owns recovery for that case).
  if (!data?.has_subscription || data.status === "incomplete") {
    return null;
  }

  async function handleClick() {
    setLoading(true);
    setError(null);

    try {
      const response = await createPortalSession(window.location.href);
      window.location.href = response.url;
      // Navigation is in progress — leave loading=true so the button stays
      // disabled until the browser navigates away.
    } catch (err) {
      setLoading(false);
      if (err instanceof ApiError) {
        if (err.status === 401) {
          setError("Please log in again.");
        } else if (err.status === 503) {
          setError(
            "Billing portal is not configured. Please contact support."
          );
        } else {
          setError("Something went wrong. Please try again.");
        }
      } else {
        setError("Something went wrong. Please try again.");
      }
    }
  }

  return (
    <div className={className}>
      <button
        type="button"
        onClick={() => void handleClick()}
        disabled={loading}
        className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {loading ? "Loading…" : "Manage subscription"}
      </button>
      {error && (
        <p className="mt-2 text-sm text-red-600">{error}</p>
      )}
    </div>
  );
}
