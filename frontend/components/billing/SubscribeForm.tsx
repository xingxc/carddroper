"use client";

import { useState } from "react";
import {
  Elements,
  PaymentElement,
  useElements,
  useStripe,
} from "@stripe/react-stripe-js";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { getStripe } from "@/lib/stripe";
import {
  SUBSCRIPTION_QUERY_KEY,
  type SubscriptionResponse,
} from "@/hooks/useSubscription";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Tier {
  lookup_key: string;
  tier_name: string;
  price_display: string; // e.g., "$9.99/month" — chassis doesn't compute; project supplies
  description?: string;
}

export interface SubscribeFormProps {
  tiers: Tier[];
  onSuccess?: () => void;
}

type SubscribePhase =
  | "select"
  | "fetching_secret"
  | "card_entry"
  | "submitting"
  | "requires_action"
  | "confirmed_polling"
  | "active"
  | "processing";

interface SetupIntentResponse {
  client_secret: string;
}

interface SubscribeApiResponse {
  subscription_id: string;
  status: string;
  requires_action: boolean;
  client_secret?: string;
}

// ---------------------------------------------------------------------------
// InnerSubscribeForm — must be rendered inside <Elements>
// Handles confirmSetup → subscribe → 3DS (if needed) → polling.
// ---------------------------------------------------------------------------

interface InnerSubscribeFormProps {
  lookupKey: string;
  submitting: boolean;
  onSubmitStart: () => void;
  onError: (msg: string) => void;
  onRequiresAction: (clientSecret: string) => void;
  onConfirmedPolling: () => void;
}

function InnerSubscribeForm({
  lookupKey,
  submitting,
  onSubmitStart,
  onError,
  onRequiresAction,
  onConfirmedPolling,
}: InnerSubscribeFormProps) {
  const stripe = useStripe();
  const elements = useElements();

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!stripe || !elements) return;

    onSubmitStart();

    // Step 1: confirm the SetupIntent to attach the payment method.
    const setupResult = await stripe.confirmSetup({
      elements,
      redirect: "if_required",
    });

    if (setupResult.error) {
      const stripeError = setupResult.error;
      if (stripeError.type === "card_error") {
        onError(stripeError.message ?? "Your card was declined. Please try again.");
      } else {
        onError("Something went wrong, please try again.");
      }
      return;
    }

    // Extract payment_method_id from the confirmed SetupIntent.
    // setupResult.setupIntent.payment_method is string | PaymentMethod | null
    const pm = setupResult.setupIntent?.payment_method;
    let paymentMethodId: string;
    if (typeof pm === "string") {
      paymentMethodId = pm;
    } else if (pm !== null && pm !== undefined && typeof pm === "object") {
      paymentMethodId = (pm as { id: string }).id;
    } else {
      onError("Could not retrieve payment method. Please try again.");
      return;
    }

    // Step 2: POST /billing/subscribe
    try {
      const resp = await api.post<SubscribeApiResponse>("/billing/subscribe", {
        price_lookup_key: lookupKey,
        payment_method_id: paymentMethodId,
      });

      if (resp.requires_action && resp.client_secret) {
        onRequiresAction(resp.client_secret);
      } else {
        onConfirmedPolling();
      }
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          onError("Please log in again.");
        } else if (err.status === 403) {
          onError("Please verify your email first.");
        } else if (err.status === 404) {
          onError("Selected plan is no longer available.");
        } else if (err.status === 409) {
          onError("You already have an active subscription.");
        } else if (err.status === 422) {
          onError(err.message);
        } else if (err.status === 429) {
          onError("Too many requests, please wait a moment.");
        } else {
          onError("Something went wrong, please try again.");
        }
      } else {
        onError("Something went wrong, please try again.");
      }
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <PaymentElement />
      <button
        type="submit"
        disabled={!stripe || !elements || submitting}
        className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {submitting ? "Processing…" : "Subscribe"}
      </button>
    </form>
  );
}

// ---------------------------------------------------------------------------
// SubscribeForm (outer component — owns all state)
// ---------------------------------------------------------------------------

export function SubscribeForm({ tiers, onSuccess }: SubscribeFormProps) {
  const [phase, setPhase] = useState<SubscribePhase>("select");
  const [selectedTier, setSelectedTier] = useState<Tier | null>(null);
  const [clientSecret, setClientSecret] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  // ------------------------------------------------------------------
  // Phase A → Phase B: fetch setup-intent client_secret for selected tier
  // ------------------------------------------------------------------
  async function handleSelectTier(tier: Tier) {
    setSelectedTier(tier);
    setError(null);
    setPhase("fetching_secret");

    try {
      const resp = await api.post<SetupIntentResponse>("/billing/setup-intent");
      setClientSecret(resp.client_secret);
      setPhase("card_entry");
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          setError("Please log in again.");
        } else if (err.status === 403) {
          setError("Please verify your email first.");
        } else if (err.status === 429) {
          setError("Too many requests, please wait a moment.");
        } else {
          setError("Something went wrong, please try again.");
        }
      } else {
        setError("Something went wrong, please try again.");
      }
      setPhase("select");
    }
  }

  // ------------------------------------------------------------------
  // 3DS challenge handling
  // ------------------------------------------------------------------
  async function handle3DSChallenge(actionClientSecret: string) {
    setPhase("requires_action");
    setError(null);

    const stripe = await getStripe();
    if (!stripe) {
      setError("Stripe is not available. Please refresh and try again.");
      setPhase("card_entry");
      return;
    }

    const result = await stripe.confirmPayment({
      clientSecret: actionClientSecret,
      redirect: "if_required",
    });

    if (result.error) {
      setError(result.error.message ?? "Authentication failed. Please try again.");
      setPhase("card_entry");
      return;
    }

    startPolling();
  }

  // ------------------------------------------------------------------
  // Polling loop: refetch GET /billing/subscription every 1s up to 10s
  // ------------------------------------------------------------------
  async function startPolling() {
    setPhase("confirmed_polling");

    const MAX_ITERATIONS = 10;
    let matched = false;

    for (let i = 0; i < MAX_ITERATIONS; i++) {
      await new Promise<void>((r) => setTimeout(r, 1000));
      await queryClient.refetchQueries({ queryKey: SUBSCRIPTION_QUERY_KEY });
      const data =
        queryClient.getQueryData<SubscriptionResponse>(SUBSCRIPTION_QUERY_KEY);
      if (data?.status === "active") {
        matched = true;
        break;
      }
    }

    if (matched) {
      setPhase("active");
      onSuccess?.();
    } else {
      setPhase("processing");
    }
  }

  // ------------------------------------------------------------------
  // Handlers passed into InnerSubscribeForm
  // ------------------------------------------------------------------
  function handleSubmitStart() {
    setPhase("submitting");
    setError(null);
  }

  function handleError(msg: string) {
    setError(msg);
    setPhase("card_entry");
  }

  function handleRequiresAction(actionClientSecret: string) {
    void handle3DSChallenge(actionClientSecret);
  }

  function handleConfirmedPolling() {
    void startPolling();
  }

  // ------------------------------------------------------------------
  // Terminal states
  // ------------------------------------------------------------------

  if (phase === "active") {
    return (
      <div className="rounded-md bg-green-50 border border-green-200 p-4 text-sm text-green-800">
        Subscription activated. Welcome aboard!
      </div>
    );
  }

  if (phase === "processing") {
    return (
      <div className="rounded-md bg-blue-50 border border-blue-200 p-4 text-sm text-blue-800">
        Subscription pending — your account will update shortly.
      </div>
    );
  }

  if (phase === "confirmed_polling" || phase === "requires_action") {
    return (
      <div className="flex items-center gap-2 p-4 text-sm text-gray-600">
        <svg
          className="animate-spin h-4 w-4 text-blue-600 flex-shrink-0"
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
          aria-hidden="true"
        >
          <circle
            className="opacity-25"
            cx="12"
            cy="12"
            r="10"
            stroke="currentColor"
            strokeWidth="4"
          />
          <path
            className="opacity-75"
            fill="currentColor"
            d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
          />
        </svg>
        {phase === "requires_action"
          ? "Completing authentication…"
          : "Activating subscription…"}
      </div>
    );
  }

  // ------------------------------------------------------------------
  // Phase B — card entry (clientSecret is set, phase is card_entry or submitting)
  // ------------------------------------------------------------------

  if (clientSecret && selectedTier && (phase === "card_entry" || phase === "submitting")) {
    return (
      <div className="space-y-4">
        {/* Selected tier summary */}
        <div className="rounded-md bg-gray-50 border border-gray-200 p-3">
          <p className="text-sm font-medium text-gray-900">
            {selectedTier.tier_name}
          </p>
          <p className="text-sm text-gray-600">{selectedTier.price_display}</p>
        </div>

        {error && (
          <div className="rounded-md bg-red-50 border border-red-200 p-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <Elements
          stripe={getStripe()}
          options={{ clientSecret, appearance: { theme: "stripe" } }}
        >
          <InnerSubscribeForm
            lookupKey={selectedTier.lookup_key}
            submitting={phase === "submitting"}
            onSubmitStart={handleSubmitStart}
            onError={handleError}
            onRequiresAction={handleRequiresAction}
            onConfirmedPolling={handleConfirmedPolling}
          />
        </Elements>

        <button
          type="button"
          onClick={() => {
            setClientSecret(null);
            setSelectedTier(null);
            setPhase("select");
            setError(null);
          }}
          className="text-xs text-gray-500 hover:underline"
        >
          &larr; Choose a different plan
        </button>
      </div>
    );
  }

  // ------------------------------------------------------------------
  // Phase A — tier selection (phase is 'select' or 'fetching_secret')
  // ------------------------------------------------------------------

  if (tiers.length === 0) {
    return (
      <div className="rounded-md bg-gray-50 border border-gray-200 p-4 text-sm text-gray-600">
        No plans available — contact support.
      </div>
    );
  }

  const isFetching = phase === "fetching_secret";

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded-md bg-red-50 border border-red-200 p-3 text-sm text-red-700">
          {error}
        </div>
      )}
      <div className="grid gap-3">
        {tiers.map((tier) => (
          <button
            key={tier.lookup_key}
            type="button"
            onClick={() => void handleSelectTier(tier)}
            disabled={isFetching}
            className="text-left w-full rounded-md border border-gray-300 bg-white px-4 py-3 hover:border-blue-500 hover:bg-blue-50 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-gray-900">
                {tier.tier_name}
              </span>
              <span className="text-sm font-semibold text-blue-600">
                {tier.price_display}
              </span>
            </div>
            {tier.description && (
              <p className="mt-1 text-xs text-gray-500">{tier.description}</p>
            )}
          </button>
        ))}
      </div>
      {isFetching && (
        <p className="text-sm text-gray-500">Loading payment form…</p>
      )}
    </div>
  );
}
