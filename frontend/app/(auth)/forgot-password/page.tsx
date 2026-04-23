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

const schema = z.object({
  email: z.string().email("Please enter a valid email address."),
});

type FormValues = z.infer<typeof schema>;

type PageState = "idle" | "submitted";

export default function ForgotPasswordPage() {
  const [pageState, setPageState] = useState<PageState>("idle");
  const [formError, setFormError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
  });

  async function onSubmit(values: FormValues) {
    setFormError(null);

    try {
      await api.post("/auth/forgot-password", { email: values.email });
      // Backend is anti-enumeration: always 200 regardless of whether the email
      // exists. Flip to submitted on success.
      setPageState("submitted");
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.code === "NETWORK_ERROR") {
          setFormError(
            "Could not reach the server. Check your connection and try again."
          );
        } else if (err.status === 429) {
          setFormError("Too many attempts, please try again later.");
        } else {
          // All other 4xx/5xx: mirror backend anti-enumeration — show success
          // panel regardless so we don't reveal whether the email is registered.
          setPageState("submitted");
        }
      } else {
        setFormError("An unexpected error occurred. Please try again.");
      }
    }
  }

  if (pageState === "submitted") {
    return (
      <div className="flex flex-col gap-6 text-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-gray-900">
            Check your inbox
          </h1>
          <p className="mt-3 text-sm text-gray-600">
            If an account exists with that email, we&apos;ve sent a password
            reset link. Check your inbox.
          </p>
        </div>
        <Link
          href="/login"
          className="text-sm text-blue-600 hover:underline"
        >
          &larr; Back to sign in
        </Link>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="text-center">
        <h1 className="text-2xl font-bold tracking-tight text-gray-900">
          Reset your {brand.name} password
        </h1>
        <p className="mt-2 text-sm text-gray-600">
          Enter your email and we&apos;ll send you a reset link.
        </p>
      </div>

      <form
        onSubmit={(e) => void handleSubmit(onSubmit)(e)}
        noValidate
        className="flex flex-col gap-4"
      >
        <FormError message={formError} />

        <FormField
          id="email"
          label="Email"
          type="email"
          autoComplete="email"
          {...register("email")}
          error={errors.email?.message}
        />

        <SubmitButton
          isPending={isSubmitting}
          label="Send reset link"
          pendingLabel="Sending…"
        />
      </form>

      <p className="text-center text-sm text-gray-600">
        <Link href="/login" className="text-blue-600 hover:underline">
          &larr; Back to sign in
        </Link>
      </p>
    </div>
  );
}
