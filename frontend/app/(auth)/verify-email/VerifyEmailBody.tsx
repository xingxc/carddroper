"use client";

import { useEffect, useRef } from "react";
import { useSearchParams } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";

interface VerifyResponse {
  message: string;
}

/**
 * Inner client component that reads `?token=` from the URL, fires the
 * verify-email mutation exactly once on mount, and renders the appropriate
 * state panel.
 *
 * Must be inside a <Suspense> boundary (see page.tsx) because it calls
 * `useSearchParams()`, which suspends until the URL params are available.
 */
export function VerifyEmailBody() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");

  const queryClient = useQueryClient();

  const firedRef = useRef(false);

  const mutation = useMutation<VerifyResponse, ApiError, string>({
    mutationFn: (t: string) =>
      api.post<VerifyResponse>("/auth/verify-email", { token: t }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
    },
  });

  // Fire the mutation exactly once on mount.
  // Guard with a ref so React 19 strict-mode double-mount doesn't double-fire.
  useEffect(() => {
    if (!token) return; // missing token — don't fire
    if (firedRef.current) return;
    firedRef.current = true;
    mutation.mutate(token);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- Missing token ---
  if (!token) {
    return (
      <div className="flex flex-col gap-6 text-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-gray-900">
            Invalid verification link
          </h1>
          <p className="mt-3 text-sm text-gray-600">
            This link is missing the verification token. Please use the full
            link from your email.
          </p>
        </div>
        <div className="flex flex-col items-center gap-3">
          <Link
            href="/verify-email-sent"
            className="inline-flex items-center justify-center rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 transition-colors"
          >
            Request a new email
          </Link>
          <Link href="/login" className="text-sm text-gray-600 hover:underline">
            Back to sign in
          </Link>
        </div>
      </div>
    );
  }

  // --- Pending ---
  if (mutation.isPending) {
    return (
      <div className="flex flex-col items-center gap-4 py-8">
        <svg
          className="h-8 w-8 animate-spin text-blue-600"
          viewBox="0 0 24 24"
          fill="none"
          aria-label="Verifying"
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
            d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
          />
        </svg>
        <p className="text-sm text-gray-600">Verifying your email…</p>
      </div>
    );
  }

  // --- Success (200 — either "Email verified." or "Email already verified.") ---
  if (mutation.isSuccess) {
    return (
      <div className="flex flex-col gap-6 text-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-gray-900">
            Email verified
          </h1>
          <p className="mt-3 text-sm text-gray-600">
            Your email is verified. You&apos;re all set.
          </p>
        </div>
        <Link
          href="/app"
          className="inline-flex items-center justify-center rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 transition-colors"
        >
          Continue
        </Link>
      </div>
    );
  }

  // --- Error ---
  if (mutation.isError) {
    const err = mutation.error;

    // Network error — offer a retry.
    if (err.code === "NETWORK_ERROR") {
      return (
        <div className="flex flex-col gap-6 text-center">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-gray-900">
              Connection error
            </h1>
            <p className="mt-3 text-sm text-gray-600">
              Could not reach the server. Check your connection and try again.
            </p>
          </div>
          <button
            type="button"
            onClick={() => {
              firedRef.current = false;
              mutation.reset();
              // Re-fire after reset.
              firedRef.current = true;
              mutation.mutate(token);
            }}
            className="inline-flex items-center justify-center rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 transition-colors"
          >
            Retry
          </button>
        </div>
      );
    }

    // 401 or 422 — invalid / expired token.
    return (
      <div className="flex flex-col gap-6 text-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-gray-900">
            Link invalid or expired
          </h1>
          <p className="mt-3 text-sm text-gray-600">
            This verification link is invalid or expired.
          </p>
        </div>
        <div className="flex flex-col items-center gap-3">
          <Link
            href="/verify-email-sent"
            className="inline-flex items-center justify-center rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 transition-colors"
          >
            Request a new email
          </Link>
          <Link href="/login" className="text-sm text-gray-600 hover:underline">
            Back to sign in
          </Link>
        </div>
      </div>
    );
  }

  // Idle state (token present but mutation hasn't fired yet — very brief flash,
  // usually covered by the useEffect firing synchronously in the commit phase).
  return null;
}
