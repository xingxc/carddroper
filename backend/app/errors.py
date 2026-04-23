from __future__ import annotations

from typing import Any, Optional

from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )


def not_found(resource: str) -> AppError:
    return AppError(code="NOT_FOUND", message=f"{resource} not found.", status_code=404)


def unauthorized(message: str = "Authentication required.") -> AppError:
    return AppError(code="UNAUTHORIZED", message=message, status_code=401)


def missing_auth(message: str = "Authentication required.") -> AppError:
    return AppError(code="AUTHENTICATION_REQUIRED", message=message, status_code=401)


def invalid_token(message: str = "Invalid or expired token.") -> AppError:
    return AppError(code="INVALID_TOKEN", message=message, status_code=401)


def forbidden(message: str = "You do not have permission to perform this action.") -> AppError:
    return AppError(code="FORBIDDEN", message=message, status_code=403)


def conflict(message: str) -> AppError:
    return AppError(code="CONFLICT", message=message, status_code=409)


def validation_error(message: str, details: Optional[dict[str, Any]] = None) -> AppError:
    return AppError(
        code="VALIDATION_ERROR",
        message=message,
        status_code=422,
        details=details or {},
    )


def too_many_requests(message: str = "Too many attempts. Try again later.") -> AppError:
    return AppError(code="TOO_MANY_REQUESTS", message=message, status_code=429)
