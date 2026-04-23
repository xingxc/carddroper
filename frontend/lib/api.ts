const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

/** Shape of every error body the backend returns. */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
  };
}

/** Thrown by `apiFetch` when the server responds with a non-OK status. */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

// ---------------------------------------------------------------------------
// Session signal — kept in sessionStorage so truly anonymous visits (no prior
// login in this tab) never trigger a refresh round-trip.
// ---------------------------------------------------------------------------
export const HAS_SESSION_KEY = "has_session";

function hasSession(): boolean {
  try {
    return sessionStorage.getItem(HAS_SESSION_KEY) === "1";
  } catch {
    return false;
  }
}

export function markLoggedIn(): void {
  try {
    sessionStorage.setItem(HAS_SESSION_KEY, "1");
  } catch {
    // sessionStorage unavailable (SSR guard, private-browsing restrictions)
  }
}

export function markLoggedOut(): void {
  try {
    sessionStorage.removeItem(HAS_SESSION_KEY);
  } catch {
    // ignore
  }
}

// ---------------------------------------------------------------------------
// Refresh deduplication — a single Promise<{ok,expiresIn?}> shared across all
// concurrent 401 callers and the proactive scheduler so we fire
// POST /auth/refresh exactly once per in-flight cycle.
// ---------------------------------------------------------------------------
let refreshPromise: Promise<{ ok: boolean; expiresIn?: number }> | null = null;

async function attemptRefresh(): Promise<{ ok: boolean; expiresIn?: number }> {
  try {
    const res = await fetch(`${API_BASE_URL}/auth/refresh`, {
      method: "POST",
      credentials: "include",
    });
    if (!res.ok) return { ok: false };
    const body = (await res.json()) as { expires_in?: number };
    return { ok: true, expiresIn: body.expires_in };
  } catch {
    return { ok: false };
  }
}

export function getRefreshPromise(): Promise<{
  ok: boolean;
  expiresIn?: number;
}> {
  if (!refreshPromise) {
    refreshPromise = attemptRefresh().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

// ---------------------------------------------------------------------------
// Logout cookie cleanup deduplication — a single Promise<void> shared across
// all concurrent callers so we fire POST /auth/logout exactly once per ghost
// session cycle. Direct fetch() (not apiFetch) to bypass the interceptor.
// ---------------------------------------------------------------------------
let logoutCleanupPromise: Promise<void> | null = null;

async function attemptLogoutCleanup(): Promise<void> {
  if (!logoutCleanupPromise) {
    logoutCleanupPromise = (async () => {
      try {
        await fetch(`${API_BASE_URL}/auth/logout`, {
          method: "POST",
          credentials: "include",
        });
      } catch {
        // Best-effort — if logout fails, fall through to the 401 throw.
      }
    })().finally(() => {
      logoutCleanupPromise = null;
    });
  }
  return logoutCleanupPromise;
}

// ---------------------------------------------------------------------------
// Paths that must never trigger a silent-refresh attempt on 401.
// /auth/resend-verification is intentionally NOT on this list — it requires
// an auth cookie, so a 401 there should trigger refresh.
// ---------------------------------------------------------------------------
const REFRESH_EXEMPT_PREFIXES = [
  "/auth/refresh",
  "/auth/login",
  "/auth/register",
  "/auth/forgot-password",
  "/auth/reset-password",
  "/auth/verify-email",
] as const;

function isRefreshExempt(path: string): boolean {
  return REFRESH_EXEMPT_PREFIXES.some((prefix) => path.startsWith(prefix));
}

// ---------------------------------------------------------------------------
// Core fetch helper
// ---------------------------------------------------------------------------

/**
 * Core fetch helper.
 *
 * - Always sends `credentials: "include"` (cookie-based sessions for web).
 * - Automatically sets `Content-Type: application/json` when a body is present.
 * - On 401 for non-exempt paths (and only when HAS_SESSION_KEY is set),
 *   attempts a silent token refresh exactly once via POST /auth/refresh.
 *   Concurrent 401s share one refresh Promise to avoid duplicate calls.
 *   On refresh failure, clears HAS_SESSION_KEY and re-throws the original 401.
 * - On non-OK responses, parses `{ error: { code, message } }` and throws
 *   an `ApiError`. Falls back to status text when the body cannot be parsed.
 * - Returns `T` on success, or `undefined` cast to `T` on 204 No Content.
 */
export async function apiFetch<T>(
  path: string,
  init?: RequestInit
): Promise<T> {
  const url = `${API_BASE_URL}${path}`;

  const headers = new Headers(init?.headers);
  if (init?.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers,
      credentials: "include",
    });
  } catch (err) {
    if (err instanceof TypeError) {
      throw new ApiError(
        0,
        "NETWORK_ERROR",
        "Network error — check your connection."
      );
    }
    throw err;
  }

  // Silent-refresh interceptor -----------------------------------------------
  if (response.status === 401 && !isRefreshExempt(path)) {
    if (hasSession()) {
      const result = await getRefreshPromise();

      if (result.ok) {
        // Retry original request with the freshly-minted access_token cookie.
        let retryResponse: Response;
        try {
          retryResponse = await fetch(url, {
            ...init,
            headers,
            credentials: "include",
          });
        } catch (err) {
          if (err instanceof TypeError) {
            throw new ApiError(
              0,
              "NETWORK_ERROR",
              "Network error — check your connection."
            );
          }
          throw err;
        }

        if (!retryResponse.ok) {
          let code = "UNKNOWN";
          let message = retryResponse.statusText;
          try {
            const body = (await retryResponse.json()) as ApiErrorBody;
            code = body.error.code;
            message = body.error.message;
          } catch {
            // non-JSON body — keep defaults
          }
          throw new ApiError(retryResponse.status, code, message);
        }

        if (retryResponse.status === 204) return undefined as T;
        return retryResponse.json() as Promise<T>;
      }

      // Refresh failed — clear the session signal.
      markLoggedOut();
    }
    // Refresh failed OR we had no session signal to refresh against. In
    // both cases server has already rejected these cookies; ask it to
    // clear them client-side so the proxy stops bouncing /login → /app
    // on ghost state. Awaited so cookies are gone before the throw below,
    // letting (app)/layout's auto-redirect land on /login cleanly.
    await attemptLogoutCleanup();
    // Fall through to throw the original 401 below.
  }

  if (!response.ok) {
    let code = "UNKNOWN";
    let message = response.statusText;

    try {
      const body = (await response.json()) as ApiErrorBody;
      code = body.error.code;
      message = body.error.message;
    } catch {
      // body was not valid JSON — keep the defaults above
    }

    throw new ApiError(response.status, code, message);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Typed convenience wrappers
// ---------------------------------------------------------------------------

export const api = {
  get<T>(path: string): Promise<T> {
    return apiFetch<T>(path);
  },

  post<T>(path: string, body?: unknown): Promise<T> {
    return apiFetch<T>(path, {
      method: "POST",
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  },

  patch<T>(path: string, body?: unknown): Promise<T> {
    return apiFetch<T>(path, {
      method: "PATCH",
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  },

  put(path: string, body?: unknown): Promise<void> {
    return apiFetch<void>(path, {
      method: "PUT",
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  },

  delete(path: string): Promise<void> {
    return apiFetch<void>(path, { method: "DELETE" });
  },
};
