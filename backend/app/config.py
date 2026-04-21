from __future__ import annotations

from typing import Optional

from pydantic import SecretStr, field_validator
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

    # Frontend URL (verification / reset email links, Stripe return URL)
    FRONTEND_URL: str = "http://localhost:3000"

    # CORS — CSV string, exposed as list via `cors_origins_list`
    CORS_ORIGINS: str = "http://localhost:3000"

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str) -> str:
        return v

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

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
