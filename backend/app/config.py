from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Database
    DATABASE_URL: str

    # JWT
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 15
    REFRESH_TOKEN_DAYS: int = 7
    JWT_ISSUER: str = "carddroper"
    JWT_AUDIENCE: str = "carddroper-api"

    # Auth cookies
    COOKIE_SECURE: bool = True
    COOKIE_DOMAIN: Optional[str] = None

    # CORS — CSV string, exposed as list via `cors_origins_list`
    CORS_ORIGINS: str = "http://localhost:3000"
    # Optional regex for multi-subdomain projects. When set, FRONTEND_BASE_URL
    # may match the regex instead of appearing literally in CORS_ORIGINS.
    # NOTE: not wired into CORSMiddleware.allow_origin_regex in this version.
    CORS_ORIGIN_REGEX: Optional[str] = None

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str) -> str:
        return v

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @model_validator(mode="after")
    def validate_cors_origins(self) -> "Settings":
        """Refuse to construct if FRONTEND_BASE_URL is not covered by CORS config.

        A browser served from FRONTEND_BASE_URL must be able to reach this API.
        If the URL is absent from CORS_ORIGINS (and no matching CORS_ORIGIN_REGEX
        is set), every preflight will be rejected and the frontend will be broken.
        Failing loudly at startup is cheaper than a silent runtime CORS failure.
        """
        frontend_url = self.FRONTEND_BASE_URL
        origins_list = self.cors_origins_list
        regex = self.CORS_ORIGIN_REGEX

        in_list = frontend_url in origins_list
        regex_match = bool(regex and re.search(regex, frontend_url))

        if not in_list and not regex_match:
            regex_display = regex if regex else "(unset)"
            raise ValueError(
                f"CORS misconfiguration: FRONTEND_BASE_URL={frontend_url} is not in CORS_ORIGINS={origins_list}\n"
                f"and does not match CORS_ORIGIN_REGEX={regex_display}.\n"
                f"A browser served from the frontend URL cannot call this API.\n"
                f"Set CORS_ORIGINS to include FRONTEND_BASE_URL (CSV) or CORS_ORIGIN_REGEX to match it."
            )
        return self

    @model_validator(mode="after")
    def validate_cookie_domain(self) -> "Settings":
        """Refuse to construct if COOKIE_DOMAIN is set but does not cover FRONTEND_BASE_URL.

        When frontend and backend live on different subdomains, cookies must be
        scoped to a parent domain that both hosts share. If COOKIE_DOMAIN is set
        to a domain that does not cover FRONTEND_BASE_URL, browsers will not
        forward the cookies to the frontend host and the proxy auth-gate will
        always see 'no cookie', making login appear broken.
        """
        cookie_domain = self.COOKIE_DOMAIN
        if not cookie_domain:
            # None or empty string — unset, skip (correct for local dev).
            return self

        host = urlparse(self.FRONTEND_BASE_URL).hostname or ""
        suffix = cookie_domain.lstrip(".")
        valid = (host == suffix) or host.endswith("." + suffix)

        if not valid:
            raise ValueError(
                f"Cookie-domain misconfiguration: FRONTEND_BASE_URL host={host} is not covered by COOKIE_DOMAIN={cookie_domain}.\n"
                f"Browsers will not forward cookies scoped to COOKIE_DOMAIN to the frontend host, so the frontend proxy cannot gate auth routes.\n"
                f'Either leave COOKIE_DOMAIN unset (single-host deployments) or set it to a parent domain of FRONTEND_BASE_URL (e.g. ".example.com" for https://app.example.com).'
            )
        return self

    # Rate limits (slowapi strings, per-IP)
    REGISTER_RATE_LIMIT: str = "3/minute"
    LOGIN_RATE_LIMIT: str = "5/minute"
    REFRESH_RATE_LIMIT: str = "10/minute"
    LOGOUT_RATE_LIMIT: str = "10/minute"
    FORGOT_PASSWORD_RATE_LIMIT: str = "3/hour"
    RESEND_VERIFICATION_RATE_LIMIT: str = "3/hour"
    VERIFY_EMAIL_RATE_LIMIT: str = "10/minute"
    CHANGE_EMAIL_RATE_LIMIT: str = "3/hour"
    CONFIRM_EMAIL_CHANGE_RATE_LIMIT: str = "10/minute"

    # Per-account login lockout (independent of per-IP)
    LOCKOUT_THRESHOLD: int = 10
    LOCKOUT_WINDOW_MINUTES: int = 15
    LOCKOUT_DURATION_MINUTES: int = 15

    # Password policy
    PASSWORD_MIN_LENGTH: int = 10
    HIBP_ENABLED: bool = True

    # Token lifetimes
    PASSWORD_RESET_EXPIRY_MINUTES: int = 15
    EMAIL_VERIFY_EXPIRY_HOURS: int = 24
    EMAIL_CHANGE_EXPIRY_HOURS: int = 1

    # Stripe (optional in Phase 1)
    STRIPE_SECRET_KEY: Optional[str] = None
    STRIPE_WEBHOOK_SECRET: Optional[str] = None

    # SendGrid / Email — leave API key empty to log to stdout in dev.
    SENDGRID_API_KEY: SecretStr = SecretStr("")
    SENDGRID_SANDBOX: bool = False
    SENDGRID_TEMPLATE_VERIFY_EMAIL: str = ""
    SENDGRID_TEMPLATE_RESET_PASSWORD: str = ""
    SENDGRID_TEMPLATE_CHANGE_EMAIL: str = ""
    SENDGRID_TEMPLATE_EMAIL_CHANGED: str = ""
    SENDGRID_TEMPLATE_CREDITS_PURCHASED: str = ""
    FROM_EMAIL: str = "noreply@carddroper.com"
    FROM_NAME: str = "Carddroper"
    FRONTEND_BASE_URL: str = "http://localhost:3000"


settings = Settings()
