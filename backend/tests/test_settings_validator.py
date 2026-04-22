"""Unit tests for Settings.validate_cors_origins model validator.

These tests construct Settings() with explicit kwargs so no OS env-var
mocking is needed. The validator runs at construction time (mode="after"),
so a bad config raises pydantic.ValidationError immediately.
"""

import pytest
from pydantic import ValidationError

# Minimal required fields so Settings() can construct without a real .env.
_BASE = {
    "DATABASE_URL": "postgresql+asyncpg://test@localhost/test",
    "JWT_SECRET": "a-secret-for-unit-tests-only-not-prod",
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
