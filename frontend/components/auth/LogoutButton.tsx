"use client";

import { useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, markLoggedOut } from "@/lib/api";
import { useAuth } from "@/context/auth";

export function LogoutButton({ className }: { className?: string }) {
  const queryClient = useQueryClient();
  const { markLoggedOut: markAuthLoggedOut } = useAuth();

  const handleLogout = useCallback(async () => {
    // 1. Clear the session signal so the 401 interceptor won't attempt refresh.
    markLoggedOut();
    // 2. Cancel in-flight queries to prevent stale 401s.
    await queryClient.cancelQueries();
    // 3. Revoke refresh token server-side (best-effort).
    try {
      await api.post("/auth/logout");
    } catch {
      // Proceed with client-side logout even if the server call fails.
    }
    // 4. Clear auth state.
    markAuthLoggedOut();
    // 5. Full reload to clear any in-memory state and navigate to home.
    window.location.href = "/";
  }, [queryClient, markAuthLoggedOut]);

  return (
    <button
      type="button"
      onClick={() => void handleLogout()}
      className={className}
    >
      Logout
    </button>
  );
}
