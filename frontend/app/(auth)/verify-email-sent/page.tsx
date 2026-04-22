"use client";

import { useState } from "react";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/context/auth";

type ResendState = "idle" | "pending" | "success" | "rate_limited";

export default function VerifyEmailSentPage() {
  const { user } = useAuth();
  const [resendState, setResendState] = useState<ResendState>("idle");

  async function handleResend() {
    setResendState("pending");

    try {
      await api.post<{ message: string }>("/auth/resend-verification");
      setResendState("success");
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setResendState("rate_limited");
      } else {
        // For any other error, reset to idle so the user can retry.
        setResendState("idle");
      }
    }
  }

  return (
    <div className="flex flex-col gap-6 text-center">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-gray-900">
          Check your inbox
        </h1>

        {user ? (
          <p className="mt-3 text-sm text-gray-600">
            We sent a verification email to{" "}
            <strong className="font-medium text-gray-800">{user.email}</strong>.
            Click the link to verify your account.
          </p>
        ) : (
          <p className="mt-3 text-sm text-gray-600">
            Check your inbox for a verification link. If you don&apos;t have an
            account yet,{" "}
            <Link href="/register" className="text-blue-600 hover:underline">
              register here
            </Link>
            .
          </p>
        )}
      </div>

      <div className="flex flex-col items-center gap-2">
        {resendState === "idle" && (
          <button
            type="button"
            onClick={() => void handleResend()}
            className="rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 transition-colors"
          >
            Resend email
          </button>
        )}

        {resendState === "pending" && (
          <button
            type="button"
            disabled
            className="rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-400 cursor-not-allowed opacity-60"
          >
            Sending…
          </button>
        )}

        {resendState === "success" && (
          <p className="text-sm text-green-700 font-medium">
            Verification email sent — check your inbox.
          </p>
        )}

        {resendState === "rate_limited" && (
          <p className="text-sm text-amber-700 font-medium">
            Please wait before requesting another email.
          </p>
        )}
      </div>
    </div>
  );
}
