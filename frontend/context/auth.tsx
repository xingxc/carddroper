"use client";

import {
  createContext,
  useContext,
  useCallback,
  type ReactNode,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, markLoggedIn, markLoggedOut } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface User {
  id: number;
  email: string;
  full_name: string | null;
  verified_at: string | null;
}

interface AuthState {
  user: User | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  /** true when the user has a non-null verified_at */
  isVerified: boolean;
  /** Set HAS_SESSION_KEY + invalidate ['auth','me'] after login or register */
  markLoggedIn: () => void;
  /** Clear HAS_SESSION_KEY + reset ['auth','me'] after logout or verify-email */
  markLoggedOut: () => void;
}

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const AuthContext = createContext<AuthState | undefined>(undefined);

const AUTH_QUERY_KEY = ["auth", "me"] as const;

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();

  // Single source of auth truth — fetches GET /auth/me and caches for 30 s.
  // retry: false overrides the global retry: 1 so logged-out users don't pay
  // an extra round-trip.
  const { data: user = null, isLoading } = useQuery<User | null>({
    queryKey: AUTH_QUERY_KEY,
    queryFn: () => api.get<User>("/auth/me"),
    retry: false,
    staleTime: 30_000,
  });

  const isAuthenticated = user !== null;
  const isVerified = isAuthenticated && user.verified_at !== null;

  const handleMarkLoggedIn = useCallback(() => {
    markLoggedIn();
    void queryClient.invalidateQueries({ queryKey: AUTH_QUERY_KEY });
  }, [queryClient]);

  const handleMarkLoggedOut = useCallback(() => {
    markLoggedOut();
    queryClient.resetQueries({ queryKey: AUTH_QUERY_KEY });
  }, [queryClient]);

  return (
    <AuthContext.Provider
      value={{
        user,
        isLoading,
        isAuthenticated,
        isVerified,
        markLoggedIn: handleMarkLoggedIn,
        markLoggedOut: handleMarkLoggedOut,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
