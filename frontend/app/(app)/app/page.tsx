"use client";

import { useAuth } from "@/context/auth";
import { LogoutButton } from "@/components/auth/LogoutButton";

export default function AppPage() {
  const { user } = useAuth();

  return (
    <div className="p-8">
      <p className="text-lg">
        You&apos;re logged in as{" "}
        <span className="font-medium">{user?.email}</span>.
      </p>
      <LogoutButton className="mt-4 text-sm text-red-600 hover:text-red-800 underline-offset-2 hover:underline" />
    </div>
  );
}
