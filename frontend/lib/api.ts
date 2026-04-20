const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

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

/**
 * Core fetch helper.
 *
 * - Always sends `credentials: "include"` (cookie-based sessions for web).
 * - Automatically sets `Content-Type: application/json` when a body is present.
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

  const response = await fetch(url, {
    ...init,
    headers,
    credentials: "include",
  });

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
