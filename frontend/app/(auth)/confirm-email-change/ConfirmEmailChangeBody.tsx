"use client";

import { useEffect, useRef } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ConfirmResponse {
  message: string;
}

// ---------------------------------------------------------------------------
// Spinner sub-component (mirrors VerifyEmailBody pattern)
// ---------------------------------------------------------------------------

function Spinner() {
  return (
    <div className="flex flex-col items-center gap-4 py-8">
      <svg
        className="h-8 w-8 animate-spin text-blue-600"
        viewBox="0 0 24 24"
        fill="none"
        aria-label="Confirming email change"
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
      <p className="text-sm text-gray-600">Confirming your email change…</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ConfirmEmailChangeBody
// ---------------------------------------------------------------------------

/**
 * Inner client component that reads `?token=` from the URL, fires the
 * confirm-email-change mutation exactly once on mount, and renders the
 * appropriate state panel.
 *
 * Auth not required — this page is reached from an email link. The user may
 * or may not be logged in. If they are, their session self-invalidates on the
 * next request due to the token_version bump from the backend.
 *
 * Must be inside a <Suspense> boundary (see page.tsx) because it calls
 * `useSearchParams()`, which suspends until URL params are available.
 */
export function ConfirmEmailChangeBody() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const token = searchParams.get("token");

  // Guard against React 19 strict-mode double-mount firing the mutation twice.
  const firedRef = useRef(false);

  // Guard against scheduling the redirect timer more than once.
  const redirectScheduledRef = useRef(false);

  const mutation = useMutation<ConfirmResponse, ApiError, string>({
    mutationFn: (t: string) =>
      api.post<ConfirmResponse>("/auth/confirm-email-change", { token: t }),
  });

  useEffect(() => {
    if (!token) return;
    if (firedRef.current) return;
    firedRef.current = true;
    mutation.mutate(token);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Schedule auto-redirect to /login after success (once only).
  useEffect(() => {
    if (!mutation.isSuccess || redirectScheduledRef.current) return;
    redirectScheduledRef.current = true;
    const timer = setTimeout(() => {
      router.push("/login");
    }, 4000);
    return () => clearTimeout(timer);
  }, [mutation.isSuccess, router]);

  // --- Missing token ---
  if (!token) {
    return (
      <div className="flex flex-col gap-6 text-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-gray-900">
            Invalid link
          </h1>
          <p className="mt-3 text-sm text-gray-600">
            This link is invalid. Please request a new email change from your
            account settings.
          </p>
        </div>
        <div className="flex flex-col items-center gap-3">
          <Link
            href="/app/change-email"
            className="inline-flex items-center justify-center rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 transition-colors"
          >
            Request a new email change
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
    return <Spinner />;
  }

  // --- Success ---
  if (mutation.isSuccess) {
    return (
      <div className="flex flex-col gap-6 text-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-gray-900">
            Email updated
          </h1>
          <p className="mt-3 text-sm text-gray-600">
            Your email address has been updated. Please log in with your new
            email address.
          </p>
          <p className="mt-2 text-xs text-gray-400">
            Redirecting to sign in…
          </p>
        </div>
        <Link
          href="/login"
          className="inline-flex items-center justify-center rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 transition-colors"
        >
          Go to sign in
        </Link>
      </div>
    );
  }

  // --- Error ---
  if (mutation.isError) {
    const err = mutation.error;

    let heading = "Something went wrong";
    let body = "Please try again or request a new email change.";

    if (err.status === 400 || err.status === 410) {
      heading = "Link expired or already used";
      body =
        "This link has expired or has already been used. Please request a new email change.";
    } else if (err.status === 409) {
      heading = "Email no longer available";
      body =
        "That email address is no longer available. Please choose a different email and try again.";
    }

    return (
      <div className="flex flex-col gap-6 text-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-gray-900">
            {heading}
          </h1>
          <p className="mt-3 text-sm text-gray-600">{body}</p>
        </div>
        <div className="flex flex-col items-center gap-3">
          <Link
            href="/app/change-email"
            className="inline-flex items-center justify-center rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 transition-colors"
          >
            Request a new email change
          </Link>
          <Link href="/login" className="text-sm text-gray-600 hover:underline">
            Back to sign in
          </Link>
        </div>
      </div>
    );
  }

  // Idle (token present but mutation hasn't fired yet — very brief flash,
  // usually covered by the useEffect firing synchronously in the commit phase).
  return null;
}
