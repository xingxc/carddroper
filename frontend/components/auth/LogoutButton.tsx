"use client";

import { useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, markLoggedOut } from "@/lib/api";

export function LogoutButton({ className }: { className?: string }) {
  const queryClient = useQueryClient();

  const handleLogout = useCallback(async () => {
    // 1. Clear the session signal so the 401 interceptor won't attempt refresh
    //    during the logout window (sessionStorage clear only — no query reset).
    markLoggedOut();
    // 2. Cancel in-flight queries to prevent stale 401s.
    await queryClient.cancelQueries();
    // 3. Revoke refresh token server-side (best-effort).
    try {
      await api.post("/auth/logout");
    } catch {
      // Proceed with client-side logout even if the server call fails.
    }
    // 4. Hard reload navigates to "/" and destroys the entire JS context +
    //    React Query cache automatically — no explicit cache reset needed.
    //    This is the intentional chassis pattern: logout is always hard-reload,
    //    never soft-navigation, so partial auth state cannot persist.
    window.location.href = "/";
  }, [queryClient]);

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
