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
import { BALANCE_QUERY_KEY, type BalanceResponse } from "@/hooks/useBalance";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type TopupStatus =
  | "idle"
  | "fetching_secret"
  | "ready"
  | "submitting"
  | "confirmed_polling"
  | "success"
  | "processing";

export interface TopupFormProps {
  presetAmounts?: number[];
  minAmountMicros?: number;
  maxAmountMicros?: number;
  onSuccess?: () => void;
}

interface TopupResponse {
  client_secret: string;
  amount_micros: number;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function microsToDisplay(micros: number): string {
  return `$${(micros / 1_000_000).toFixed(0)}`;
}

function formatMicrosAmount(micros: number): string {
  return `$${(micros / 1_000_000).toFixed(2)}`;
}

// ---------------------------------------------------------------------------
// InnerCardForm — must be rendered inside <Elements>
// Handles confirmPayment + post-confirm balance polling.
// Calls onDone(matched: boolean) when the polling loop finishes.
// ---------------------------------------------------------------------------

interface InnerCardFormProps {
  amountMicros: number;
  submitting: boolean;
  onSubmitStart: () => void;
  onError: (msg: string) => void;
  onDone: (matched: boolean) => void;
}

function InnerCardForm({
  amountMicros,
  submitting,
  onSubmitStart,
  onError,
  onDone,
}: InnerCardFormProps) {
  const stripe = useStripe();
  const elements = useElements();
  const queryClient = useQueryClient();

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!stripe || !elements) return;

    onSubmitStart();

    const result = await stripe.confirmPayment({
      elements,
      redirect: "if_required",
    });

    if (result.error) {
      const { error } = result;
      if (error.type === "card_error") {
        onError(error.message ?? "Your card was declined. Please try again.");
      } else {
        onError("Something went wrong, please try again.");
      }
      return; // parent resets status to "ready" via onError path
    }

    // confirmPayment succeeded — capture prior balance, then poll.
    const priorBalance =
      queryClient.getQueryData<BalanceResponse>(BALANCE_QUERY_KEY)
        ?.balance_micros ?? 0;

    await queryClient.invalidateQueries({ queryKey: BALANCE_QUERY_KEY });

    const MAX_ITERATIONS = 10;
    let matched = false;

    for (let i = 0; i < MAX_ITERATIONS; i++) {
      await new Promise<void>((r) => setTimeout(r, 1000));
      await queryClient.refetchQueries({ queryKey: BALANCE_QUERY_KEY });
      const newBalance =
        queryClient.getQueryData<BalanceResponse>(BALANCE_QUERY_KEY)
          ?.balance_micros ?? 0;
      if (newBalance - priorBalance >= amountMicros) {
        matched = true;
        break;
      }
    }

    onDone(matched);
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <PaymentElement />
      <button
        type="submit"
        disabled={!stripe || !elements || submitting}
        className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {submitting ? "Processing…" : `Pay ${formatMicrosAmount(amountMicros)}`}
      </button>
    </form>
  );
}

// ---------------------------------------------------------------------------
// TopupForm (outer component — owns all state)
// ---------------------------------------------------------------------------

export function TopupForm({
  presetAmounts = [500_000, 2_000_000, 5_000_000],
  minAmountMicros = 500_000,
  maxAmountMicros = 500_000_000,
  onSuccess,
}: TopupFormProps) {
  const [selectedAmount, setSelectedAmount] = useState<number | null>(null);
  const [customAmount, setCustomAmount] = useState<string>("");
  const [customError, setCustomError] = useState<string | null>(null);
  const [clientSecret, setClientSecret] = useState<string | null>(null);
  const [confirmedAmountMicros, setConfirmedAmountMicros] = useState<number>(0);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<TopupStatus>("idle");

  // Derive the final amount in micros from the current selection state.
  function getFinalAmount(): number | null {
    if (selectedAmount !== null) return selectedAmount;
    const trimmed = customAmount.trim();
    if (trimmed === "") return null;
    const dollars = parseFloat(trimmed);
    if (isNaN(dollars) || dollars < 0) return null;
    return Math.round(dollars * 1_000_000);
  }

  function validateAmount(micros: number): string | null {
    if (micros < minAmountMicros)
      return `Below minimum ${formatMicrosAmount(minAmountMicros)}.`;
    if (micros > maxAmountMicros)
      return `Above maximum ${formatMicrosAmount(maxAmountMicros)}.`;
    return null;
  }

  // Phase A: user clicks "Continue" to POST /billing/topup.
  async function handleContinue() {
    const finalAmount = getFinalAmount();
    if (finalAmount === null) {
      setError("Please select or enter an amount.");
      return;
    }

    const validationMsg = validateAmount(finalAmount);
    if (validationMsg) {
      setCustomError(validationMsg);
      setError(validationMsg);
      return;
    }

    setError(null);
    setCustomError(null);
    setStatus("fetching_secret");

    try {
      const resp = await api.post<TopupResponse>("/billing/topup", {
        amount_micros: finalAmount,
      });
      setClientSecret(resp.client_secret);
      setConfirmedAmountMicros(resp.amount_micros);
      setStatus("ready");
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 403) {
          setError("Please verify your email before adding funds.");
        } else if (err.status === 422) {
          setError(err.message);
        } else if (err.status === 429) {
          setError("Too many requests, please wait a moment.");
        } else {
          setError("Something went wrong, please try again.");
        }
      } else {
        setError("Connection error, please check your network and try again.");
      }
      setStatus("idle");
    }
  }

  // Called when InnerCardForm finishes the polling loop.
  function handlePaymentDone(matched: boolean) {
    if (matched) {
      setStatus("success");
      onSuccess?.();
    } else {
      setStatus("processing");
    }
  }

  // Called when InnerCardForm encounters a card / Stripe error.
  function handleCardError(msg: string) {
    setError(msg);
    setStatus("ready");
  }

  function handleCustomAmountBlur() {
    if (customAmount.trim() === "") {
      setCustomError(null);
      return;
    }
    const micros = getFinalAmount();
    if (micros === null) {
      setCustomError("Please enter a valid amount.");
      return;
    }
    const msg = validateAmount(micros);
    setCustomError(msg);
  }

  function handleCustomAmountChange(value: string) {
    // Allow only numeric input with optional single decimal point + up to 2 decimal places.
    if (value !== "" && !/^\d*\.?\d{0,2}$/.test(value)) return;
    setCustomAmount(value);
    // Typing a custom amount deselects any preset.
    if (value !== "") setSelectedAmount(null);
    setCustomError(null);
  }

  function handlePresetSelect(amount: number) {
    setSelectedAmount(amount);
    setCustomAmount("");
    setCustomError(null);
    setError(null);
  }

  // ---------------------------------------------------------------------------
  // Render: terminal states
  // ---------------------------------------------------------------------------

  if (status === "success") {
    return (
      <div className="rounded-md bg-green-50 border border-green-200 p-4 text-sm text-green-800">
        Balance updated ✓
      </div>
    );
  }

  if (status === "processing") {
    return (
      <div className="rounded-md bg-blue-50 border border-blue-200 p-4 text-sm text-blue-800">
        Payment received. Your balance will update shortly.
      </div>
    );
  }

  if (status === "confirmed_polling") {
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
        Processing your payment…
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Render: Phase B — card entry
  // ---------------------------------------------------------------------------

  if (clientSecret && (status === "ready" || status === "submitting")) {
    return (
      <div className="space-y-4">
        {error && (
          <div className="rounded-md bg-red-50 border border-red-200 p-3 text-sm text-red-700">
            {error}
          </div>
        )}
        <Elements
          stripe={getStripe()}
          options={{ clientSecret, appearance: { theme: "stripe" } }}
        >
          <InnerCardForm
            amountMicros={confirmedAmountMicros}
            submitting={status === "submitting"}
            onSubmitStart={() => setStatus("submitting")}
            onError={handleCardError}
            onDone={handlePaymentDone}
          />
        </Elements>
        <button
          type="button"
          onClick={() => {
            setClientSecret(null);
            setStatus("idle");
            setError(null);
          }}
          className="text-xs text-gray-500 hover:underline"
        >
          ← Change amount
        </button>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Render: Phase A — amount selection
  // ---------------------------------------------------------------------------

  const isFetching = status === "fetching_secret";
  const nothingSelected = selectedAmount === null && customAmount.trim() === "";

  return (
    <div className="space-y-4">
      {/* Preset buttons */}
      <div className="flex flex-wrap gap-2">
        {presetAmounts.map((amount) => (
          <button
            key={amount}
            type="button"
            onClick={() => handlePresetSelect(amount)}
            disabled={isFetching}
            className={`px-4 py-2 rounded-md text-sm font-medium border transition-colors disabled:opacity-50 ${
              selectedAmount === amount
                ? "bg-blue-600 text-white border-blue-600"
                : "bg-white text-gray-700 border-gray-300 hover:bg-gray-50"
            }`}
          >
            {microsToDisplay(amount)}
          </button>
        ))}
      </div>

      {/* Custom amount input */}
      <div>
        <label
          htmlFor="topup-custom-amount"
          className="block text-sm text-gray-700 mb-1"
        >
          Or enter a custom amount
        </label>
        <div className="relative">
          <span className="absolute inset-y-0 left-3 flex items-center text-gray-500 text-sm select-none">
            $
          </span>
          <input
            id="topup-custom-amount"
            type="text"
            inputMode="decimal"
            value={customAmount}
            onChange={(e) => handleCustomAmountChange(e.target.value)}
            onBlur={handleCustomAmountBlur}
            disabled={isFetching}
            placeholder="0.00"
            className="w-full rounded-md border border-gray-300 pl-7 pr-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
          />
        </div>
        {customError && (
          <p className="mt-1 text-xs text-red-600">{customError}</p>
        )}
      </div>

      {/* Global error banner (non-validation errors) */}
      {error && !customError && (
        <div className="rounded-md bg-red-50 border border-red-200 p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Continue button */}
      <button
        type="button"
        onClick={handleContinue}
        disabled={isFetching || nothingSelected}
        className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {isFetching ? "Loading…" : "Continue"}
      </button>
    </div>
  );
}
