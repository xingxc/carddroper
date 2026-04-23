"use client";

import {
  createContext,
  useContext,
  useCallback,
  useEffect,
  type ReactNode,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, markLoggedIn, markLoggedOut, getRefreshPromise } from "@/lib/api";

// 80% of TTL — Auth0/Clerk/industry default. 20% buffer absorbs
// network latency + clock drift. See 0016.6 §Design decisions.
const REFRESH_BEFORE_EXPIRY_RATIO = 0.8;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface User {
  id: number;
  email: string;
  full_name: string | null;
  verified_at: string | null;
}

interface MeResponse {
  user: User;
  expires_in: number;
}

interface AuthState {
  user: User | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  /** true when the user has a non-null verified_at */
  isVerified: boolean;
  /** Remaining access-token lifetime in seconds from the last /auth/me or /auth/refresh response */
  expiresIn?: number;
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
  const { data, isLoading } = useQuery<MeResponse | null>({
    queryKey: AUTH_QUERY_KEY,
    queryFn: () => api.get<MeResponse>("/auth/me"),
    retry: false,
    staleTime: 30_000,
  });

  const user = data?.user ?? null;
  const expiresIn = data?.expires_in;

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

  // Proactive token refresh scheduler — fires at 80% of TTL so active users
  // never hit an expired access token. Best-effort: OS-level timer throttling
  // under tab backgrounding may delay or skip the timer. The existing
  // 0016.2/0016.3/0016.4/0016.5 chain handles the fallback for those cases.
  useEffect(() => {
    if (!isAuthenticated || !expiresIn) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const scheduleRefresh = (delayMs: number) => {
      timer = setTimeout(async () => {
        if (cancelled) return;
        const result = await getRefreshPromise(); // shared dedup with 401 interceptor
        if (cancelled) return;
        if (result.ok && result.expiresIn) {
          scheduleRefresh(result.expiresIn * 1000 * REFRESH_BEFORE_EXPIRY_RATIO);
        }
        // On !result.ok: intentionally do not reschedule, do not markLoggedOut.
        // Next user action 401s → existing 0016.3 chain handles cleanup. This
        // avoids a false-logout on transient network errors (attemptRefresh
        // returns ok=false for both auth-fail and network-fail today).
      }, delayMs);
    };

    scheduleRefresh(expiresIn * 1000 * REFRESH_BEFORE_EXPIRY_RATIO);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [isAuthenticated, expiresIn]);

  return (
    <AuthContext.Provider
      value={{
        user,
        isLoading,
        isAuthenticated,
        isVerified,
        expiresIn,
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
