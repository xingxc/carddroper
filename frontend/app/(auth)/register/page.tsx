"use client";

import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/context/auth";
import { brand } from "@/config/brand";
import { FormField } from "@/components/forms/FormField";
import { FormError } from "@/components/forms/FormError";
import { SubmitButton } from "@/components/forms/SubmitButton";

const schema = z
  .object({
    email: z.string().email("Please enter a valid email address."),
    password: z
      .string()
      .min(10, "Password must be at least 10 characters."),
    confirmPassword: z.string(),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: "Passwords do not match.",
    path: ["confirmPassword"],
  });

type FormValues = z.infer<typeof schema>;

export default function RegisterPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { markLoggedIn } = useAuth();
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
      await api.post("/auth/register", {
        email: values.email,
        password: values.password,
      });

      markLoggedIn();
      await queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
      router.push("/verify-email-sent");
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.code === "NETWORK_ERROR") {
          setFormError(
            "Could not reach the server. Check your connection and try again."
          );
        } else if (err.status === 409) {
          // Email already registered — surface as form-level (anti-enumeration).
          setFormError(err.message);
        } else if (err.status === 422) {
          // Weak password or malformed request.
          setError("password", { message: err.message });
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
          Create your {brand.name} account
        </h1>
        <p className="mt-2 text-sm text-gray-600">
          Already have an account?{" "}
          <Link href="/login" className="text-blue-600 hover:underline">
            Sign in
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
          autoComplete="new-password"
          {...register("password")}
          error={errors.password?.message}
        />

        <FormField
          id="confirmPassword"
          label="Confirm password"
          type="password"
          autoComplete="new-password"
          {...register("confirmPassword")}
          error={errors.confirmPassword?.message}
        />

        <SubmitButton
          isPending={isSubmitting}
          label="Create account"
          pendingLabel="Creating account…"
        />
      </form>
    </div>
  );
}
