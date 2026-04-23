"use client";

import { useAuth } from "@/context/auth";

export default function AppPage() {
  const { user } = useAuth();
  return (
    <div>
      <p className="text-lg">
        You&apos;re logged in as{" "}
        <span className="font-medium">{user?.email}</span>.
      </p>
    </div>
  );
}
