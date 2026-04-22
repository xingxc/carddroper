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
// Refresh deduplication — a single Promise<boolean> shared across all
// concurrent 401 callers so we fire POST /auth/refresh exactly once.
// ---------------------------------------------------------------------------
let refreshPromise: Promise<boolean> | null = null;

async function attemptRefresh(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE_URL}/auth/refresh`, {
      method: "POST",
      credentials: "include",
    });
    return res.ok;
  } catch {
    return false;
  }
}

function getRefreshPromise(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = attemptRefresh().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
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
  if (
    response.status === 401 &&
    !isRefreshExempt(path) &&
    hasSession()
  ) {
    const refreshed = await getRefreshPromise();

    if (refreshed) {
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

    // Refresh failed — clear the session signal so future requests don't retry.
    markLoggedOut();
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
