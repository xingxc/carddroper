import { Suspense } from "react";
import { ResetPasswordBody } from "./ResetPasswordBody";

/**
 * Shell page — wraps the client component that reads `useSearchParams()` in a
 * Suspense boundary, which Next.js requires for all `useSearchParams` callers.
 */
export default function ResetPasswordPage() {
  return (
    <Suspense
      fallback={
        <div className="flex flex-col items-center gap-4 py-8">
          <svg
            className="h-8 w-8 animate-spin text-blue-600"
            viewBox="0 0 24 24"
            fill="none"
            aria-label="Loading"
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
          <p className="text-sm text-gray-600">Loading…</p>
        </div>
      }
    >
      <ResetPasswordBody />
    </Suspense>
  );
}
