"""Unit tests for Settings.validate_cors_origins model validator.

These tests construct Settings() with explicit kwargs so no OS env-var
mocking is needed. The validator runs at construction time (mode="after"),
so a bad config raises pydantic.ValidationError immediately.
"""

import pytest
from pydantic import ValidationError

# Minimal required fields so Settings() can construct without a real .env.
# SENDGRID_SANDBOX=True prevents validate_sendgrid_production from firing
# (tests don't exercise email delivery; sandbox silences that check).
_BASE = {
    "DATABASE_URL": "postgresql+asyncpg://test@localhost/test",
    "JWT_SECRET": "a-secret-for-unit-tests-only-not-prod",
    "SENDGRID_SANDBOX": True,
}


def _make(**overrides):
    """Return a kwargs dict with the required base fields plus any overrides."""
    return {**_BASE, **overrides}


class TestCorsOriginsValidator:
    def test_happy_path_url_in_origins(self):
        """FRONTEND_BASE_URL is present in CORS_ORIGINS list — should construct."""
        s = __import__("app.config", fromlist=["Settings"]).Settings(
            **_make(
                FRONTEND_BASE_URL="http://localhost:3000",
                CORS_ORIGINS="http://localhost:3000",
            )
        )
        assert s.FRONTEND_BASE_URL == "http://localhost:3000"

    def test_happy_path_multiple_origins(self):
        """FRONTEND_BASE_URL is one of several comma-separated origins."""
        s = __import__("app.config", fromlist=["Settings"]).Settings(
            **_make(
                FRONTEND_BASE_URL="https://staging.carddroper.com",
                CORS_ORIGINS="http://localhost:3000,https://staging.carddroper.com",
            )
        )
        assert "https://staging.carddroper.com" in s.cors_origins_list

    def test_failing_path_url_not_in_origins(self):
        """FRONTEND_BASE_URL not in CORS_ORIGINS and no regex — must raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            __import__("app.config", fromlist=["Settings"]).Settings(
                **_make(
                    FRONTEND_BASE_URL="http://x.com",
                    CORS_ORIGINS="http://y.com",
                )
            )

        error_str = str(exc_info.value)
        assert "CORS misconfiguration" in error_str
        assert "FRONTEND_BASE_URL=http://x.com" in error_str
        assert "CORS_ORIGIN_REGEX=(unset)" in error_str
        assert "A browser served from the frontend URL cannot call this API." in error_str

    def test_failing_path_regex_not_set_shows_unset(self):
        """Error message shows '(unset)' when CORS_ORIGIN_REGEX is not configured."""
        with pytest.raises(ValidationError) as exc_info:
            __import__("app.config", fromlist=["Settings"]).Settings(
                **_make(
                    FRONTEND_BASE_URL="http://a.com",
                    CORS_ORIGINS="http://b.com",
                    CORS_ORIGIN_REGEX=None,
                )
            )

        assert "(unset)" in str(exc_info.value)

    def test_happy_path_via_regex(self):
        """FRONTEND_BASE_URL matches CORS_ORIGIN_REGEX — should construct even if not in list."""
        s = __import__("app.config", fromlist=["Settings"]).Settings(
            **_make(
                FRONTEND_BASE_URL="https://staging.carddroper.com",
                CORS_ORIGINS="http://localhost:3000",
                CORS_ORIGIN_REGEX=r"https://.*\.carddroper\.com",
            )
        )
        assert s.CORS_ORIGIN_REGEX == r"https://.*\.carddroper\.com"

    def test_failing_path_regex_set_but_no_match(self):
        """FRONTEND_BASE_URL does not match the regex — must still raise."""
        with pytest.raises(ValidationError) as exc_info:
            __import__("app.config", fromlist=["Settings"]).Settings(
                **_make(
                    FRONTEND_BASE_URL="http://evil.example.com",
                    CORS_ORIGINS="http://localhost:3000",
                    CORS_ORIGIN_REGEX=r"https://.*\.carddroper\.com",
                )
            )

        error_str = str(exc_info.value)
        assert "CORS misconfiguration" in error_str
        assert r"https://.*\.carddroper\.com" in error_str


class TestCookieDomainValidator:
    """Tests for Settings.validate_cookie_domain model validator (0015.6).

    Each test passes CORS_ORIGINS equal to FRONTEND_BASE_URL so the earlier
    CORS validator never trips before we reach the cookie-domain check.
    """

    def test_happy_cookie_domain_none(self):
        """COOKIE_DOMAIN=None (default) — validator is skipped, constructs cleanly."""
        s = __import__("app.config", fromlist=["Settings"]).Settings(
            **_make(
                FRONTEND_BASE_URL="http://localhost:3000",
                CORS_ORIGINS="http://localhost:3000",
                COOKIE_DOMAIN=None,
            )
        )
        assert s.COOKIE_DOMAIN is None

    def test_happy_cookie_domain_exact_match(self):
        """COOKIE_DOMAIN covers FRONTEND_BASE_URL host exactly (with leading dot)."""
        s = __import__("app.config", fromlist=["Settings"]).Settings(
            **_make(
                FRONTEND_BASE_URL="https://staging.carddroper.com",
                CORS_ORIGINS="https://staging.carddroper.com",
                COOKIE_DOMAIN=".staging.carddroper.com",
            )
        )
        assert s.COOKIE_DOMAIN == ".staging.carddroper.com"

    def test_happy_cookie_domain_parent_covers_subdomain(self):
        """COOKIE_DOMAIN is a parent domain covering a deeper-nested FRONTEND_BASE_URL."""
        s = __import__("app.config", fromlist=["Settings"]).Settings(
            **_make(
                FRONTEND_BASE_URL="https://app.carddroper.com",
                CORS_ORIGINS="https://app.carddroper.com",
                COOKIE_DOMAIN=".carddroper.com",
            )
        )
        assert s.COOKIE_DOMAIN == ".carddroper.com"

    def test_happy_cookie_domain_no_leading_dot(self):
        """COOKIE_DOMAIN without leading dot — leading-dot stripping normalises it correctly."""
        s = __import__("app.config", fromlist=["Settings"]).Settings(
            **_make(
                FRONTEND_BASE_URL="https://staging.carddroper.com",
                CORS_ORIGINS="https://staging.carddroper.com",
                COOKIE_DOMAIN="staging.carddroper.com",
            )
        )
        assert s.COOKIE_DOMAIN == "staging.carddroper.com"

    def test_failing_cookie_domain_wrong_domain(self):
        """COOKIE_DOMAIN does not cover FRONTEND_BASE_URL host — must raise."""
        with pytest.raises(ValidationError) as exc_info:
            __import__("app.config", fromlist=["Settings"]).Settings(
                **_make(
                    FRONTEND_BASE_URL="https://staging.carddroper.com",
                    CORS_ORIGINS="https://staging.carddroper.com",
                    COOKIE_DOMAIN=".other.com",
                )
            )

        error_str = str(exc_info.value)
        assert "Cookie-domain misconfiguration" in error_str
        assert "FRONTEND_BASE_URL host=staging.carddroper.com" in error_str
        assert "COOKIE_DOMAIN=.other.com" in error_str
        assert "Browsers will not forward cookies" in error_str

    def test_failing_cookie_domain_localhost_mismatch(self):
        """COOKIE_DOMAIN set but FRONTEND_BASE_URL is localhost — must raise."""
        with pytest.raises(ValidationError) as exc_info:
            __import__("app.config", fromlist=["Settings"]).Settings(
                **_make(
                    FRONTEND_BASE_URL="http://localhost:3000",
                    CORS_ORIGINS="http://localhost:3000",
                    COOKIE_DOMAIN=".staging.carddroper.com",
                )
            )

        error_str = str(exc_info.value)
        assert "Cookie-domain misconfiguration" in error_str
        assert "FRONTEND_BASE_URL host=localhost" in error_str


class TestStripeSecretValidator:
    """Tests for Settings.validate_stripe_secret_key model validator (0021).

    Must pass CORS_ORIGINS=FRONTEND_BASE_URL so the CORS validator doesn't trip first.
    """

    def test_happy_billing_disabled_no_key(self):
        """BILLING_ENABLED=False (default) — validator skipped, constructs cleanly."""
        s = __import__("app.config", fromlist=["Settings"]).Settings(
            **_make(
                FRONTEND_BASE_URL="http://localhost:3000",
                CORS_ORIGINS="http://localhost:3000",
                BILLING_ENABLED=False,
                STRIPE_SECRET_KEY=None,
            )
        )
        assert s.BILLING_ENABLED is False

    def test_happy_billing_enabled_with_key(self):
        """BILLING_ENABLED=True + both keys set — constructs cleanly."""
        s = __import__("app.config", fromlist=["Settings"]).Settings(
            **_make(
                FRONTEND_BASE_URL="http://localhost:3000",
                CORS_ORIGINS="http://localhost:3000",
                BILLING_ENABLED=True,
                STRIPE_SECRET_KEY="sk_test_abc123",
                STRIPE_WEBHOOK_SECRET="whsec_abc123",
            )
        )
        assert s.STRIPE_SECRET_KEY == "sk_test_abc123"

    def test_failing_billing_enabled_no_key(self):
        """BILLING_ENABLED=True but STRIPE_SECRET_KEY missing — must raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            __import__("app.config", fromlist=["Settings"]).Settings(
                **_make(
                    FRONTEND_BASE_URL="http://localhost:3000",
                    CORS_ORIGINS="http://localhost:3000",
                    BILLING_ENABLED=True,
                    STRIPE_SECRET_KEY=None,
                    STRIPE_WEBHOOK_SECRET="whsec_abc123",
                )
            )
        error_str = str(exc_info.value)
        assert "Stripe misconfiguration" in error_str
        assert "STRIPE_SECRET_KEY" in error_str
        assert "BILLING_ENABLED=True" in error_str


class TestStripeWebhookSecretValidator:
    """Tests for Settings.validate_stripe_webhook_secret model validator (0021).

    Must pass CORS_ORIGINS=FRONTEND_BASE_URL so the CORS validator doesn't trip first.
    """

    def test_happy_billing_disabled_no_secret(self):
        """BILLING_ENABLED=False — validator skipped, constructs cleanly."""
        s = __import__("app.config", fromlist=["Settings"]).Settings(
            **_make(
                FRONTEND_BASE_URL="http://localhost:3000",
                CORS_ORIGINS="http://localhost:3000",
                BILLING_ENABLED=False,
                STRIPE_WEBHOOK_SECRET=None,
            )
        )
        assert s.BILLING_ENABLED is False

    def test_happy_billing_enabled_with_secret(self):
        """BILLING_ENABLED=True + both secrets set — constructs cleanly."""
        s = __import__("app.config", fromlist=["Settings"]).Settings(
            **_make(
                FRONTEND_BASE_URL="http://localhost:3000",
                CORS_ORIGINS="http://localhost:3000",
                BILLING_ENABLED=True,
                STRIPE_SECRET_KEY="sk_test_abc123",
                STRIPE_WEBHOOK_SECRET="whsec_abc123",
            )
        )
        assert s.STRIPE_WEBHOOK_SECRET == "whsec_abc123"

    def test_failing_billing_enabled_no_secret(self):
        """BILLING_ENABLED=True but STRIPE_WEBHOOK_SECRET missing — must raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            __import__("app.config", fromlist=["Settings"]).Settings(
                **_make(
                    FRONTEND_BASE_URL="http://localhost:3000",
                    CORS_ORIGINS="http://localhost:3000",
                    BILLING_ENABLED=True,
                    STRIPE_SECRET_KEY="sk_test_abc123",
                    STRIPE_WEBHOOK_SECRET=None,
                )
            )
        error_str = str(exc_info.value)
        assert "Stripe misconfiguration" in error_str
        assert "STRIPE_WEBHOOK_SECRET" in error_str
        assert "BILLING_ENABLED=True" in error_str


class TestJwtSecretValidator:
    """Tests for Settings.validate_jwt_secret (0018 chassis-hardening).

    Each test provides CORS_ORIGINS=FRONTEND_BASE_URL and SENDGRID_SANDBOX=True
    so no other validator fires before JWT_SECRET is checked.
    """

    def test_happy_long_secret(self):
        """JWT_SECRET of 48 chars — constructs cleanly."""
        s = __import__("app.config", fromlist=["Settings"]).Settings(
            **_make(
                FRONTEND_BASE_URL="http://localhost:3000",
                CORS_ORIGINS="http://localhost:3000",
                JWT_SECRET="a" * 48,
            )
        )
        assert len(s.JWT_SECRET) == 48

    def test_happy_exactly_min_length(self):
        """JWT_SECRET of exactly 32 chars — constructs cleanly."""
        s = __import__("app.config", fromlist=["Settings"]).Settings(
            **_make(
                FRONTEND_BASE_URL="http://localhost:3000",
                CORS_ORIGINS="http://localhost:3000",
                JWT_SECRET="x" * 32,
            )
        )
        assert len(s.JWT_SECRET) == 32

    def test_failing_empty_secret(self):
        """JWT_SECRET='' — must raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            __import__("app.config", fromlist=["Settings"]).Settings(
                **_make(
                    FRONTEND_BASE_URL="http://localhost:3000",
                    CORS_ORIGINS="http://localhost:3000",
                    JWT_SECRET="",
                )
            )
        error_str = str(exc_info.value)
        assert "JWT misconfiguration" in error_str
        assert "JWT_SECRET is empty" in error_str

    def test_failing_short_secret(self):
        """JWT_SECRET shorter than 32 chars — must raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            __import__("app.config", fromlist=["Settings"]).Settings(
                **_make(
                    FRONTEND_BASE_URL="http://localhost:3000",
                    CORS_ORIGINS="http://localhost:3000",
                    JWT_SECRET="tooshort",
                )
            )
        error_str = str(exc_info.value)
        assert "JWT misconfiguration" in error_str
        assert "minimum is 32" in error_str


class TestJwtIssuerAudienceValidator:
    """Tests for Settings.validate_jwt_issuer_audience (0018 chassis-hardening)."""

    def test_happy_defaults(self):
        """Default JWT_ISSUER='carddroper' and JWT_AUDIENCE='carddroper-api' — constructs cleanly."""
        s = __import__("app.config", fromlist=["Settings"]).Settings(
            **_make(
                FRONTEND_BASE_URL="http://localhost:3000",
                CORS_ORIGINS="http://localhost:3000",
            )
        )
        assert s.JWT_ISSUER == "carddroper"
        assert s.JWT_AUDIENCE == "carddroper-api"

    def test_failing_empty_issuer(self):
        """JWT_ISSUER='' — must raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            __import__("app.config", fromlist=["Settings"]).Settings(
                **_make(
                    FRONTEND_BASE_URL="http://localhost:3000",
                    CORS_ORIGINS="http://localhost:3000",
                    JWT_ISSUER="",
                )
            )
        error_str = str(exc_info.value)
        assert "JWT misconfiguration" in error_str
        assert "JWT_ISSUER is empty" in error_str

    def test_failing_empty_audience(self):
        """JWT_AUDIENCE='' — must raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            __import__("app.config", fromlist=["Settings"]).Settings(
                **_make(
                    FRONTEND_BASE_URL="http://localhost:3000",
                    CORS_ORIGINS="http://localhost:3000",
                    JWT_AUDIENCE="",
                )
            )
        error_str = str(exc_info.value)
        assert "JWT misconfiguration" in error_str
        assert "JWT_AUDIENCE is empty" in error_str


class TestDatabaseUrlValidator:
    """Tests for Settings.validate_database_url (0018 chassis-hardening)."""

    def test_happy_asyncpg_prefix(self):
        """DATABASE_URL with postgresql+asyncpg:// prefix — constructs cleanly."""
        s = __import__("app.config", fromlist=["Settings"]).Settings(
            **_make(
                FRONTEND_BASE_URL="http://localhost:3000",
                CORS_ORIGINS="http://localhost:3000",
                DATABASE_URL="postgresql+asyncpg://user:pass@localhost/mydb",
            )
        )
        assert s.DATABASE_URL.startswith("postgresql+asyncpg://")

    def test_failing_plain_postgres_url(self):
        """DATABASE_URL with plain postgresql:// — must raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            __import__("app.config", fromlist=["Settings"]).Settings(
                **_make(
                    FRONTEND_BASE_URL="http://localhost:3000",
                    CORS_ORIGINS="http://localhost:3000",
                    DATABASE_URL="postgresql://user:pass@localhost/mydb",
                )
            )
        error_str = str(exc_info.value)
        assert "Database misconfiguration" in error_str
        assert "postgresql+asyncpg://" in error_str

    def test_failing_postgres_shorthand_url(self):
        """DATABASE_URL with postgres:// (Heroku-style shorthand) — must raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            __import__("app.config", fromlist=["Settings"]).Settings(
                **_make(
                    FRONTEND_BASE_URL="http://localhost:3000",
                    CORS_ORIGINS="http://localhost:3000",
                    DATABASE_URL="postgres://user:pass@localhost/mydb",
                )
            )
        error_str = str(exc_info.value)
        assert "Database misconfiguration" in error_str


class TestSendgridProductionValidator:
    """Tests for Settings.validate_sendgrid_production (0018 chassis-hardening)."""

    def test_happy_sandbox_true_no_key(self):
        """SENDGRID_SANDBOX=True + empty key — sandbox skips the check, constructs cleanly."""
        s = __import__("app.config", fromlist=["Settings"]).Settings(
            **_make(
                FRONTEND_BASE_URL="http://localhost:3000",
                CORS_ORIGINS="http://localhost:3000",
                SENDGRID_SANDBOX=True,
                SENDGRID_API_KEY="",
            )
        )
        assert s.SENDGRID_SANDBOX is True

    def test_happy_sandbox_false_with_key_and_templates(self):
        """SENDGRID_SANDBOX=False + valid key + all template IDs — constructs cleanly."""
        s = __import__("app.config", fromlist=["Settings"]).Settings(
            **_make(
                FRONTEND_BASE_URL="http://localhost:3000",
                CORS_ORIGINS="http://localhost:3000",
                SENDGRID_SANDBOX=False,
                SENDGRID_API_KEY="SG.fake-key",
                SENDGRID_TEMPLATE_VERIFY_EMAIL="d-aaa",
                SENDGRID_TEMPLATE_RESET_PASSWORD="d-bbb",
                SENDGRID_TEMPLATE_CHANGE_EMAIL="d-ccc",
                SENDGRID_TEMPLATE_EMAIL_CHANGED="d-ddd",
                SENDGRID_TEMPLATE_CREDITS_PURCHASED="d-eee",
            )
        )
        assert s.SENDGRID_SANDBOX is False

    def test_happy_sandbox_false_empty_key_is_dev_preview_mode(self):
        """SENDGRID_SANDBOX=False + empty API key — dev-preview mode, constructs cleanly.

        Empty key with sandbox=false is the documented local dev default: send_email()
        falls through to a stdout log. No validator error — this is intentional, not
        a misconfiguration. Templates may also be empty in this state.
        """
        s = __import__("app.config", fromlist=["Settings"]).Settings(
            **_make(
                FRONTEND_BASE_URL="http://localhost:3000",
                CORS_ORIGINS="http://localhost:3000",
                SENDGRID_SANDBOX=False,
                SENDGRID_API_KEY="",
            )
        )
        assert s.SENDGRID_SANDBOX is False
        assert s.SENDGRID_API_KEY.get_secret_value() == ""

    def test_failing_sandbox_false_key_set_missing_templates(self):
        """SENDGRID_SANDBOX=False + key set but templates missing — must raise ValidationError.

        This is the prod misconfiguration: real key is present so real delivery would
        be attempted, but missing templates cause send_email() to crash at first call.
        """
        with pytest.raises(ValidationError) as exc_info:
            __import__("app.config", fromlist=["Settings"]).Settings(
                **_make(
                    FRONTEND_BASE_URL="http://localhost:3000",
                    CORS_ORIGINS="http://localhost:3000",
                    SENDGRID_SANDBOX=False,
                    SENDGRID_API_KEY="SG.fake-key",
                    SENDGRID_TEMPLATE_VERIFY_EMAIL="",  # missing
                )
            )
        error_str = str(exc_info.value)
        assert "SendGrid misconfiguration" in error_str
        assert "SENDGRID_TEMPLATE_VERIFY_EMAIL" in error_str

    def test_failing_sandbox_false_partial_templates(self):
        """SENDGRID_SANDBOX=False + key set + some templates missing — lists all missing."""
        with pytest.raises(ValidationError) as exc_info:
            __import__("app.config", fromlist=["Settings"]).Settings(
                **_make(
                    FRONTEND_BASE_URL="http://localhost:3000",
                    CORS_ORIGINS="http://localhost:3000",
                    SENDGRID_SANDBOX=False,
                    SENDGRID_API_KEY="SG.fake-key",
                    SENDGRID_TEMPLATE_VERIFY_EMAIL="d-aaa",
                    SENDGRID_TEMPLATE_RESET_PASSWORD="",  # missing
                    SENDGRID_TEMPLATE_CHANGE_EMAIL="d-ccc",
                    SENDGRID_TEMPLATE_EMAIL_CHANGED="",  # missing
                    SENDGRID_TEMPLATE_CREDITS_PURCHASED="d-eee",
                )
            )
        error_str = str(exc_info.value)
        assert "SENDGRID_TEMPLATE_RESET_PASSWORD" in error_str
        assert "SENDGRID_TEMPLATE_EMAIL_CHANGED" in error_str


class TestExtraForbid:
    """Tests for extra='forbid' — unknown env vars raise at construction time (0018)."""

    def test_failing_unknown_field_raises(self):
        """An unknown field (e.g. FRONTEND_URL instead of FRONTEND_BASE_URL) — must raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            __import__("app.config", fromlist=["Settings"]).Settings(
                **_make(
                    FRONTEND_BASE_URL="http://localhost:3000",
                    CORS_ORIGINS="http://localhost:3000",
                    FRONTEND_URL="http://localhost:3000",  # typo — unknown field
                )
            )
        error_str = str(exc_info.value)
        assert (
            "FRONTEND_URL" in error_str
            or "extra" in error_str.lower()
            or "Extra inputs" in error_str
        )
