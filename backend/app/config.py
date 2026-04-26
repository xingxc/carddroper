from __future__ import annotations

import re
from typing import Literal, Optional
from urllib.parse import urlparse

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Minimum JWT_SECRET length (chars). 32 bytes of entropy from secrets.token_urlsafe(48) gives
# 64 chars; 32 chars is the absolute floor to prevent trivially-brute-forceable secrets.
_JWT_SECRET_MIN_LEN = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        # extra="forbid" — every env var the chassis reads must be a declared
        # field on Settings. Unknown variables raise at startup rather than
        # being silently dropped. Prevents typo-silent-fallback bugs (e.g.
        # FRONTEND_URL= being ignored because the real field is FRONTEND_BASE_URL).
        extra="forbid",
    )

    # Database
    DATABASE_URL: str

    # JWT
    JWT_SECRET: str
    JWT_ALGORITHM: str = (
        "HS256"  # deliberately un-validated: safe default; alg is library-constrained
    )
    JWT_EXPIRATION_MINUTES: int = 15  # deliberately un-validated: purely product-tunable
    REFRESH_TOKEN_DAYS: int = 7  # deliberately un-validated: purely product-tunable
    JWT_ISSUER: str = "carddroper"
    JWT_AUDIENCE: str = "carddroper-api"

    # Auth cookies
    COOKIE_SECURE: bool = (
        True  # deliberately un-validated: True is the safe default; False is valid for local dev
    )
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
    def validate_jwt_secret(self) -> "Settings":
        """Refuse to construct when JWT_SECRET is empty or shorter than _JWT_SECRET_MIN_LEN.

        A missing or trivially-short secret makes every JWT produced by this service
        trivially forgeable. Failing loudly at startup surfaces the misconfiguration
        before any user touches auth. Generate with:
            python -c "import secrets; print(secrets.token_urlsafe(48))"
        """
        secret = self.JWT_SECRET
        if not secret:
            raise ValueError(
                "JWT misconfiguration: JWT_SECRET is empty.\n"
                "Set JWT_SECRET to a random string of at least 32 characters.\n"
                'Generate with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        if len(secret) < _JWT_SECRET_MIN_LEN:
            raise ValueError(
                f"JWT misconfiguration: JWT_SECRET is {len(secret)} characters; minimum is {_JWT_SECRET_MIN_LEN}.\n"
                "A short secret makes JWTs trivially forgeable.\n"
                'Generate with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return self

    @model_validator(mode="after")
    def validate_jwt_issuer_audience(self) -> "Settings":
        """Refuse to construct when JWT_ISSUER or JWT_AUDIENCE are empty strings.

        Tokens minted without iss/aud are rejected by the decoder, but the error
        only surfaces at the first authenticated request. Failing at startup is
        cheaper and more informative.
        """
        if not self.JWT_ISSUER:
            raise ValueError(
                "JWT misconfiguration: JWT_ISSUER is empty.\n"
                "Set JWT_ISSUER to a non-empty string (e.g. 'carddroper').\n"
                "Tokens minted with an empty issuer will be rejected by the decoder."
            )
        if not self.JWT_AUDIENCE:
            raise ValueError(
                "JWT misconfiguration: JWT_AUDIENCE is empty.\n"
                "Set JWT_AUDIENCE to a non-empty string (e.g. 'carddroper-api').\n"
                "Tokens minted with an empty audience will be rejected by the decoder."
            )
        return self

    @model_validator(mode="after")
    def validate_database_url(self) -> "Settings":
        """Refuse to construct when DATABASE_URL does not use the asyncpg driver prefix.

        SQLAlchemy's async engine requires 'postgresql+asyncpg://'. A plain
        'postgresql://' or 'postgres://' URL causes a 'Could not load backend'
        error at first DB operation rather than at startup. Failing loudly at
        startup is cheaper and provides a clearer error message.
        """
        url = self.DATABASE_URL
        if not url.startswith("postgresql+asyncpg://"):
            raise ValueError(
                f"Database misconfiguration: DATABASE_URL must begin with 'postgresql+asyncpg://'.\n"
                f"Got: {url[:40]}{'...' if len(url) > 40 else ''}\n"
                "The async SQLAlchemy engine requires the asyncpg driver prefix.\n"
                "Example: DATABASE_URL=postgresql+asyncpg://user:pass@host/dbname"
            )
        return self

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
    # Deliberately un-validated: these are purely product-tunable; safe defaults exist;
    # no startup-time check can determine what "non-zero" means for slowapi strings.
    REGISTER_RATE_LIMIT: str = "3/minute"
    LOGIN_RATE_LIMIT: str = "5/minute"
    REFRESH_RATE_LIMIT: str = "10/minute"
    LOGOUT_RATE_LIMIT: str = "10/minute"
    FORGOT_PASSWORD_RATE_LIMIT: str = "3/hour"
    RESEND_VERIFICATION_RATE_LIMIT: str = "3/hour"
    VERIFY_EMAIL_RATE_LIMIT: str = "10/minute"
    CHANGE_EMAIL_RATE_LIMIT: str = "3/hour"
    CONFIRM_EMAIL_CHANGE_RATE_LIMIT: str = "10/minute"
    TOPUP_RATE_LIMIT: str = "10/minute"

    # Per-account login lockout (independent of per-IP)
    # Deliberately un-validated: purely product-tunable; all have safe non-zero defaults.
    LOCKOUT_THRESHOLD: int = 10
    LOCKOUT_WINDOW_MINUTES: int = 15
    LOCKOUT_DURATION_MINUTES: int = 15

    # Password policy
    # Deliberately un-validated: purely product-tunable; safe defaults exist.
    PASSWORD_MIN_LENGTH: int = 10
    HIBP_ENABLED: bool = True

    # Token lifetimes
    # Deliberately un-validated: purely product-tunable; safe defaults exist.
    PASSWORD_RESET_EXPIRY_MINUTES: int = 15
    EMAIL_VERIFY_EXPIRY_HOURS: int = 24
    EMAIL_CHANGE_EXPIRY_HOURS: int = 1

    # Stripe (optional in Phase 1)
    STRIPE_SECRET_KEY: Optional[str] = None
    STRIPE_WEBHOOK_SECRET: Optional[str] = None

    # Billing chassis
    BILLING_ENABLED: bool = False
    BILLING_CURRENCY: Literal["usd"] = "usd"  # deliberately un-validated: only usd supported
    BILLING_TOPUP_MIN_MICROS: int = 500_000  # deliberately un-validated: purely product-tunable
    BILLING_TOPUP_MAX_MICROS: int = 500_000_000  # deliberately un-validated: purely product-tunable
    STRIPE_TAX_ENABLED: bool = False  # deliberately un-validated: purely product-tunable
    BILLING_SIGNUP_BONUS_MICROS: int = 0  # deliberately un-validated: purely product-tunable
    BILLING_VERIFY_BONUS_MICROS: int = 0  # deliberately un-validated: purely product-tunable

    @model_validator(mode="after")
    def validate_stripe_secret_key(self) -> "Settings":
        """Refuse to construct when BILLING_ENABLED=True but STRIPE_SECRET_KEY is unset.

        Stripe API calls at signup and in webhook handlers will fail immediately
        with an AuthenticationError if the key is missing. Failing loudly at
        startup is cheaper than a silent 401 from Stripe mid-request.
        """
        if self.BILLING_ENABLED and not self.STRIPE_SECRET_KEY:
            raise ValueError(
                "Stripe misconfiguration: BILLING_ENABLED=True but STRIPE_SECRET_KEY is not set.\n"
                "Set STRIPE_SECRET_KEY to a valid Stripe secret key (sk_live_... or sk_test_...).\n"
                "Required because BILLING_ENABLED=True enables Stripe Customer creation at registration."
            )
        return self

    @model_validator(mode="after")
    def validate_stripe_webhook_secret(self) -> "Settings":
        """Refuse to construct when BILLING_ENABLED=True but STRIPE_WEBHOOK_SECRET is unset.

        The webhook endpoint verifies every inbound event with this secret.
        Without it, stripe.Webhook.construct_event raises immediately on every
        POST /billing/webhook, making the endpoint non-functional.
        """
        if self.BILLING_ENABLED and not self.STRIPE_WEBHOOK_SECRET:
            raise ValueError(
                "Stripe misconfiguration: BILLING_ENABLED=True but STRIPE_WEBHOOK_SECRET is not set.\n"
                "Set STRIPE_WEBHOOK_SECRET to the webhook signing secret (whsec_...).\n"
                "Required because BILLING_ENABLED=True mounts POST /billing/webhook."
            )
        return self

    # SendGrid / Email — leave API key empty to log to stdout in dev.
    SENDGRID_API_KEY: SecretStr = SecretStr("")
    SENDGRID_SANDBOX: bool = False
    SENDGRID_TEMPLATE_VERIFY_EMAIL: str = ""
    SENDGRID_TEMPLATE_RESET_PASSWORD: str = ""
    SENDGRID_TEMPLATE_CHANGE_EMAIL: str = ""
    SENDGRID_TEMPLATE_EMAIL_CHANGED: str = ""
    SENDGRID_TEMPLATE_CREDITS_PURCHASED: str = ""
    FROM_EMAIL: str = "noreply@carddroper.com"  # deliberately un-validated: has-a-safe-default; prod misconfiguration shows as SendGrid bounce, not a startup crash
    FROM_NAME: str = "Carddroper"  # deliberately un-validated: has-a-safe-default
    FRONTEND_BASE_URL: str = "http://localhost:3000"

    @model_validator(mode="after")
    def validate_sendgrid_production(self) -> "Settings":
        """Refuse to construct when SENDGRID_SANDBOX=False, an API key is set, but
        any required template ID is missing.

        Scope of this validator (three modes):

        - SENDGRID_SANDBOX=True: skip entirely — sandbox mode, no real delivery.
        - SENDGRID_SANDBOX=False, SENDGRID_API_KEY empty: dev-preview mode (intentional
          local default). send_email() falls through to a stdout log instead of
          sending. No validator error — empty key is the documented local dev setup.
        - SENDGRID_SANDBOX=False, SENDGRID_API_KEY set, any template ID empty: ERROR.
          The service would attempt real delivery and crash at the first send attempt
          with ValueError("SENDGRID_TEMPLATE_X is not configured"). Failing at startup
          is cheaper and clearer than a mid-request crash.
        """
        if self.SENDGRID_SANDBOX:
            return self

        api_key = self.SENDGRID_API_KEY.get_secret_value()
        if not api_key:
            # Dev-preview mode: no key → no real sends → no template check needed.
            return self

        missing_templates = []
        template_fields = {
            "SENDGRID_TEMPLATE_VERIFY_EMAIL": self.SENDGRID_TEMPLATE_VERIFY_EMAIL,
            "SENDGRID_TEMPLATE_RESET_PASSWORD": self.SENDGRID_TEMPLATE_RESET_PASSWORD,
            "SENDGRID_TEMPLATE_CHANGE_EMAIL": self.SENDGRID_TEMPLATE_CHANGE_EMAIL,
            "SENDGRID_TEMPLATE_EMAIL_CHANGED": self.SENDGRID_TEMPLATE_EMAIL_CHANGED,
            "SENDGRID_TEMPLATE_CREDITS_PURCHASED": self.SENDGRID_TEMPLATE_CREDITS_PURCHASED,
        }
        for field_name, value in template_fields.items():
            if not value:
                missing_templates.append(field_name)

        if missing_templates:
            raise ValueError(
                "SendGrid misconfiguration: SENDGRID_SANDBOX=False and SENDGRID_API_KEY is "
                f"set, but the following template IDs are not set: {', '.join(missing_templates)}.\n"
                "Missing template IDs cause send_email() to raise ValueError at the first "
                "email send attempt rather than at startup.\n"
                "Set each template ID, clear SENDGRID_API_KEY for dev-preview mode, or set "
                "SENDGRID_SANDBOX=True."
            )

        return self


settings = Settings()
