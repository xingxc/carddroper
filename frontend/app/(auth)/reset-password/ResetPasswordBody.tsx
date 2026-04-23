"use client";

import { useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useSearchParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { brand } from "@/config/brand";
import { FormField } from "@/components/forms/FormField";
import { FormError } from "@/components/forms/FormError";
import { SubmitButton } from "@/components/forms/SubmitButton";

// ---------------------------------------------------------------------------
// Validation schema
// ---------------------------------------------------------------------------

const schema = z
  .object({
    newPassword: z.string().min(10, "Password must be at least 10 characters."),
    confirmPassword: z.string(),
  })
  .refine((d) => d.newPassword === d.confirmPassword, {
    message: "Passwords do not match.",
    path: ["confirmPassword"],
  });

type FormValues = z.infer<typeof schema>;

// ---------------------------------------------------------------------------
// Validation states
// ---------------------------------------------------------------------------

type ValidationState = "pending" | "valid" | "invalid";

interface ValidateResponse {
  valid: boolean;
  reason?: string;
}

// ---------------------------------------------------------------------------
// Shared UI panels
// ---------------------------------------------------------------------------

function Spinner({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center gap-4 py-8">
      <svg
        className="h-8 w-8 animate-spin text-blue-600"
        viewBox="0 0 24 24"
        fill="none"
        aria-label={label}
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
      <p className="text-sm text-gray-600">{label}</p>
    </div>
  );
}

function InvalidLinkPanel() {
  return (
    <div className="flex flex-col gap-6 text-center">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-gray-900">
          Link invalid or expired
        </h1>
        <p className="mt-3 text-sm text-gray-600">
          This reset link is invalid or expired.
        </p>
      </div>
      <div className="flex flex-col items-center gap-3">
        <Link
          href="/forgot-password"
          className="inline-flex items-center justify-center rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 transition-colors"
        >
          Request a new link
        </Link>
        <Link href="/login" className="text-sm text-gray-600 hover:underline">
          Back to sign in
        </Link>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Password reset form
// ---------------------------------------------------------------------------

function ResetPasswordForm({ token }: { token: string }) {
  const router = useRouter();
  const [formError, setFormError] = useState<string | null>(null);
  const [alreadyUsed, setAlreadyUsed] = useState(false);

  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
  });

  async function onSubmit(values: FormValues) {
    setFormError(null);
    setAlreadyUsed(false);

    try {
      await api.post("/auth/reset-password", {
        token,
        new_password: values.newPassword,
      });
      // Do NOT call markLoggedIn() — backend cleared cookies; user must log in fresh.
      router.push("/login?reset=success");
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.code === "NETWORK_ERROR") {
          setFormError(
            "Could not reach the server. Check your connection and try again."
          );
        } else if (err.status === 422) {
          // Weak password or HIBP breach check — surface on the password field.
          setError("newPassword", { message: err.message });
        } else if (err.status === 401) {
          // Token already used or concurrent reset.
          setAlreadyUsed(true);
        } else {
          setFormError(err.message);
        }
      } else {
        setFormError("An unexpected error occurred. Please try again.");
      }
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="text-center">
        <h1 className="text-2xl font-bold tracking-tight text-gray-900">
          Reset your {brand.name} password
        </h1>
        <p className="mt-2 text-sm text-gray-600">
          Enter a new password for your account.
        </p>
      </div>

      <form
        onSubmit={(e) => void handleSubmit(onSubmit)(e)}
        noValidate
        className="flex flex-col gap-4"
      >
        <FormError message={formError} />

        <FormField
          id="newPassword"
          label="New password"
          type="password"
          autoComplete="new-password"
          {...register("newPassword")}
          error={errors.newPassword?.message}
        />

        <FormField
          id="confirmPassword"
          label="Confirm new password"
          type="password"
          autoComplete="new-password"
          {...register("confirmPassword")}
          error={errors.confirmPassword?.message}
        />

        <SubmitButton
          isPending={isSubmitting}
          label="Reset password"
          pendingLabel="Resetting…"
        />
      </form>

      {alreadyUsed && (
        <p className="text-sm text-red-700 text-center">
          This reset link has already been used.{" "}
          <Link href="/forgot-password" className="text-blue-600 hover:underline">
            Request a new link
          </Link>
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Outer body — handles token validation on mount
// ---------------------------------------------------------------------------

/**
 * Inner client component that reads `?token=` from the URL, validates it once
 * on mount via GET /auth/validate-reset-token, and renders the appropriate state.
 *
 * Must be inside a <Suspense> boundary (see page.tsx) because it calls
 * `useSearchParams()`, which suspends until URL params are available.
 */
export function ResetPasswordBody() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");

  // Short-circuit: no token means invalid — skip the validation request entirely.
  if (!token) {
    return <InvalidLinkPanel />;
  }

  return <ResetPasswordBodyWithToken token={token} />;
}

/**
 * Rendered only when `token` is non-null. Validates the token on mount.
 */
function ResetPasswordBodyWithToken({ token }: { token: string }) {
  const [validationState, setValidationState] =
    useState<ValidationState>("pending");

  const firedRef = useRef(false);

  useEffect(() => {
    // React 19 strict-mode double-mount guard.
    if (firedRef.current) return;
    firedRef.current = true;

    void api
      .get<ValidateResponse>(
        `/auth/validate-reset-token?token=${encodeURIComponent(token)}`
      )
      .then((data) => {
        setValidationState(data.valid ? "valid" : "invalid");
      })
      .catch(() => {
        // Network error or unexpected response — treat as invalid to avoid
        // showing a broken form.
        setValidationState("invalid");
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (validationState === "pending") {
    return <Spinner label="Checking your reset link…" />;
  }

  if (validationState === "invalid") {
    return <InvalidLinkPanel />;
  }

  // validationState === "valid" — token is confirmed good, show the form.
  return <ResetPasswordForm token={token} />;
}
