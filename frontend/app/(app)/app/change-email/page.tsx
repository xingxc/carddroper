"use client";

import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { brand } from "@/config/brand";
import { FormField } from "@/components/forms/FormField";
import { FormError } from "@/components/forms/FormError";
import { SubmitButton } from "@/components/forms/SubmitButton";

// ---------------------------------------------------------------------------
// Validation schema
// ---------------------------------------------------------------------------

const schema = z.object({
  current_password: z.string().min(1, "Current password is required."),
  new_email: z
    .string()
    .email("Please enter a valid email address.")
    .min(1, "New email is required."),
});

type FormValues = z.infer<typeof schema>;

// ---------------------------------------------------------------------------
// Page states
// ---------------------------------------------------------------------------

type PageState = "idle" | "sent";

// ---------------------------------------------------------------------------
// Page component
// ---------------------------------------------------------------------------

export default function ChangeEmailPage() {
  const [pageState, setPageState] = useState<PageState>("idle");
  const [sentEmail, setSentEmail] = useState<string>("");
  const [formError, setFormError] = useState<string | null>(null);

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

    try {
      await api.post("/auth/change-email", {
        current_password: values.current_password,
        new_email: values.new_email,
      });
      setSentEmail(values.new_email);
      setPageState("sent");
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.code === "NETWORK_ERROR") {
          setFormError(
            "Could not reach the server. Check your connection and try again."
          );
        } else if (
          err.code === "INVALID_CREDENTIALS" ||
          err.status === 401
        ) {
          setError("current_password", {
            message: "Current password is incorrect.",
          });
        } else if (err.code === "EMAIL_TAKEN" || err.status === 409) {
          setError("new_email", {
            message: "That email is already registered.",
          });
        } else if (err.status === 422) {
          // Pydantic validation error — surface on new_email field.
          setError("new_email", { message: err.message });
        } else if (err.status === 429) {
          setFormError("Too many requests, please wait a moment.");
        } else {
          setFormError("Something went wrong, please try again.");
        }
      } else {
        setFormError("An unexpected error occurred. Please try again.");
      }
    }
  }

  // --- Success state ---
  if (pageState === "sent") {
    return (
      <div className="max-w-md">
        <div className="flex flex-col gap-6">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-gray-900">
              Check your inbox
            </h1>
            <p className="mt-3 text-sm text-gray-600">
              We sent a verification link to{" "}
              <span className="font-medium text-gray-900">{sentEmail}</span>.
              Click the link within 1 hour to confirm the change.
            </p>
            <p className="mt-2 text-sm text-gray-500">
              Your current email address remains active until you verify the
              new one.
            </p>
          </div>
          <Link
            href="/app"
            className="text-sm text-blue-600 hover:underline"
          >
            &larr; Back to dashboard
          </Link>
        </div>
      </div>
    );
  }

  // --- Idle state: show form ---
  return (
    <div className="max-w-md">
      <div className="flex flex-col gap-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-gray-900">
            Change your {brand.name} email
          </h1>
          <p className="mt-2 text-sm text-gray-600">
            Enter your current password and the new email address you want to
            use. We&apos;ll send a verification link to the new address.
          </p>
        </div>

        <form
          onSubmit={(e) => void handleSubmit(onSubmit)(e)}
          noValidate
          className="flex flex-col gap-4"
        >
          <FormError message={formError} />

          <FormField
            id="current_password"
            label="Current password"
            type="password"
            autoComplete="current-password"
            {...register("current_password")}
            error={errors.current_password?.message}
          />

          <FormField
            id="new_email"
            label="New email address"
            type="email"
            autoComplete="email"
            {...register("new_email")}
            error={errors.new_email?.message}
          />

          <div className="flex flex-col gap-2 pt-1">
            <SubmitButton
              isPending={isSubmitting}
              label="Send verification email"
              pendingLabel="Sending…"
            />

            <Link
              href="/app"
              className="flex w-full items-center justify-center rounded-md border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 transition-colors"
            >
              Cancel
            </Link>
          </div>
        </form>
      </div>
    </div>
  );
}
