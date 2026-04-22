import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export function proxy(request: NextRequest): NextResponse {
  const { pathname } = request.nextUrl;
  const hasAccessToken = request.cookies.has("access_token");

  // Guard: any path under /app requires an access_token cookie.
  // Middleware cannot decode the JWT (HttpOnly + no secret on edge), so
  // cookie presence is the gate. GET /auth/me in AuthProvider confirms validity.
  if (pathname.startsWith("/app") && !hasAccessToken) {
    const loginUrl = request.nextUrl.clone();
    loginUrl.pathname = "/login";
    return NextResponse.redirect(loginUrl, { status: 307 });
  }

  // Convenience: authed user visiting /login or /register → send to /app.
  if (
    (pathname === "/login" || pathname === "/register") &&
    hasAccessToken
  ) {
    const appUrl = request.nextUrl.clone();
    appUrl.pathname = "/app";
    return NextResponse.redirect(appUrl, { status: 307 });
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)).*)",
  ],
};
