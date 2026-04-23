"use client";

import { useState, useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/context/auth";
import { brand } from "@/config/brand";
import { FormField } from "@/components/forms/FormField";
import { FormError } from "@/components/forms/FormError";
import { SubmitButton } from "@/components/forms/SubmitButton";

const schema = z.object({
  email: z.string().email("Please enter a valid email address."),
  // Don't enforce length on login — let the backend decide.
  password: z.string().min(1, "Password is required."),
});

type FormValues = z.infer<typeof schema>;

/**
 * Inner client component that reads `?reset=` from the URL.
 * Must be inside a <Suspense> boundary (see page.tsx) because it calls
 * `useSearchParams()`, which suspends until URL params are available.
 */
export function LoginBody() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { markLoggedIn, isLoading, isAuthenticated } = useAuth();

  useEffect(() => {
    if (!isLoading && isAuthenticated) {
      router.replace("/app");
    }
  }, [isLoading, isAuthenticated, router]);

  const [formError, setFormError] = useState<string | null>(null);

  const searchParams = useSearchParams();
  const showResetBanner = searchParams.get("reset") === "success";
  const [bannerDismissed, setBannerDismissed] = useState(false);

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
      await api.post("/auth/login", {
        email: values.email,
        password: values.password,
      });

      markLoggedIn();
      await queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
      router.push("/app");
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.code === "NETWORK_ERROR") {
          setFormError(
            "Could not reach the server. Check your connection and try again."
          );
        } else if (err.status === 401) {
          // Don't distinguish email-exists vs wrong-password (anti-enumeration).
          setFormError("Invalid email or password.");
        } else if (err.status === 429) {
          setFormError("Too many attempts, please try again later.");
        } else if (err.status === 422) {
          // Backend may provide field context, but most likely a form-level issue.
          setError("email", { message: err.message });
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
      {showResetBanner && !bannerDismissed && (
        <div
          role="status"
          className="flex items-start justify-between gap-3 rounded-md border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800"
        >
          <span>
            Password reset successful. Please sign in with your new password.
          </span>
          <button
            type="button"
            onClick={() => setBannerDismissed(true)}
            aria-label="Dismiss"
            className="shrink-0 text-green-600 hover:text-green-800 focus:outline-none"
          >
            &times;
          </button>
        </div>
      )}

      <div className="text-center">
        <h1 className="text-2xl font-bold tracking-tight text-gray-900">
          Sign in to {brand.name}
        </h1>
        <p className="mt-2 text-sm text-gray-600">
          Don&apos;t have an account?{" "}
          <Link href="/register" className="text-blue-600 hover:underline">
            Register
          </Link>
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

        <FormField
          id="password"
          label="Password"
          type="password"
          autoComplete="current-password"
          {...register("password")}
          error={errors.password?.message}
        />

        <Link
          href="/forgot-password"
          className="text-sm text-blue-600 hover:underline self-end"
        >
          Forgot password?
        </Link>

        <SubmitButton
          isPending={isSubmitting}
          label="Sign in"
          pendingLabel="Signing in…"
        />
      </form>
    </div>
  );
}
