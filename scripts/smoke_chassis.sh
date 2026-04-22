#!/bin/bash
# smoke_chassis.sh — verify the auth chassis end-to-end against local docker-compose.
#
# Tests the Canva-model chassis boundary defined in doc/architecture/site-model.md:
#   - (marketing) / (auth) / (app) route groups
#   - proxy.ts cookie-presence gate in both directions
#   - /auth/register, /auth/me, /auth/logout round-trip
#   - Static-asset bypass (matcher)
#
# Run when:
#   - Any chassis code touched: proxy.ts, lib/api.ts, context/auth.tsx,
#     middleware/proxy matcher, or auth backend routes.
#   - Before dispatching a frontend-builder agent that builds on the chassis.
#
# Requires:
#   - docker-compose up (all three services: db, backend, frontend).
#   - Backend on $BACKEND_URL (default http://localhost:8000).
#   - Frontend on $FRONTEND_URL (default http://localhost:3000).
#
# Portable to future projects: override the URLs via env vars.
# Last verified green: 2026-04-22 (Phase 0 of ticket 0015).

set -u
BACKEND_URL="${BACKEND_URL:-http://localhost:8000}"
FRONTEND_URL="${FRONTEND_URL:-http://localhost:3000}"
COOKIE=/tmp/smoke_chassis_cj
EMAIL="smoke+$(date +%s)@example.com"
PW="PasswordTest1234"
FAIL=0

expect() {
  local label="$1" want="$2" got="$3"
  if [[ "$got" == *"$want"* ]]; then
    echo "  PASS  $label"
  else
    echo "  FAIL  $label"
    echo "        wanted substring: $want"
    echo "        first 300 chars : ${got:0:300}"
    FAIL=1
  fi
}

echo "Targeting backend=$BACKEND_URL frontend=$FRONTEND_URL"
echo

echo "== 0. Preflight: services reachable"
R=$(curl -s -o /dev/null -w '%{http_code}' "$BACKEND_URL/health" || echo "000")
expect "backend /health"                   "200"           "$R"
R=$(curl -s -o /dev/null -w '%{http_code}' "$FRONTEND_URL/" || echo "000")
expect "frontend / reachable"              "200"           "$R"

echo
echo "== 1. Register a fresh user (expect 200 + auth cookies)"
R=$(curl -s -i -c "$COOKIE" -X POST "$BACKEND_URL/auth/register" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PW\"}")
expect "register returns 200"              "HTTP/1.1 200"  "$R"
expect "sets access_token cookie"          "access_token=" "$R"
expect "sets refresh_token cookie"         "refresh_token=" "$R"

echo
echo "== 2. GET /auth/me with cookie (expect 200 + our email)"
R=$(curl -s -i -b "$COOKIE" "$BACKEND_URL/auth/me")
expect "me returns 200"                    "HTTP/1.1 200"  "$R"
expect "me returns our email"              "$EMAIL"        "$R"

echo
echo "== 3. Proxy: authed hitting /login redirects to /app"
R=$(curl -s -i -b "$COOKIE" "$FRONTEND_URL/login")
expect "login 307s"                        "307"           "$R"
expect "redirect target is /app"           "location: /app" "$R"

echo
echo "== 4. Proxy: authed hitting /register redirects to /app"
R=$(curl -s -i -b "$COOKIE" "$FRONTEND_URL/register")
expect "register 307s"                     "307"           "$R"
expect "redirect target is /app"           "location: /app" "$R"

echo
echo "== 5. Proxy: authed hitting /app returns 200"
R=$(curl -s -i -b "$COOKIE" "$FRONTEND_URL/app")
expect "authed /app returns 200"           "HTTP/1.1 200"  "$R"

echo
echo "== 6. Proxy: anonymous hitting /app redirects to /login"
R=$(curl -s -i "$FRONTEND_URL/app")
expect "anon /app 307s"                    "307"           "$R"
expect "redirect target is /login"         "location: /login" "$R"

echo
echo "== 7. Proxy: static assets bypass the matcher (no redirect)"
R=$(curl -s -I "$FRONTEND_URL/favicon.ico")
if [[ "$R" == *"307"* ]]; then
  echo "  FAIL  favicon.ico got redirected — matcher is too broad"
  FAIL=1
else
  echo "  PASS  favicon.ico did not 307"
fi

echo
echo "== 8. Logout clears cookies"
R=$(curl -s -i -b "$COOKIE" -c "$COOKIE" -X POST "$BACKEND_URL/auth/logout")
expect "logout 2xx"                        "HTTP/1.1 2"    "$R"

echo
echo "== 9. After logout, proxy gates /app again"
R=$(curl -s -i -b "$COOKIE" "$FRONTEND_URL/app")
expect "post-logout /app 307s"             "307"           "$R"
expect "redirect target is /login"         "location: /login" "$R"

echo
if [[ "$FAIL" == "0" ]]; then
  echo "SMOKE OK  chassis foundations green"
else
  echo "SMOKE FAILED  see FAIL lines above"
  exit 1
fi
