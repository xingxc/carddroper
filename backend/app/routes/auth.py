"""Authentication routes.

Endpoints:
    POST /auth/register
    POST /auth/login
    POST /auth/logout                 (refresh cookie OR body)
    POST /auth/refresh                (refresh cookie OR body)
    GET  /auth/me
    PUT  /auth/password
    POST /auth/forgot-password
    GET  /auth/validate-reset-token
    POST /auth/reset-password
    POST /auth/verify-email
    POST /auth/resend-verification
    POST /auth/change-email
    POST /auth/confirm-email-change
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user, require_not_locked
from app.errors import conflict, too_many_requests, unauthorized, validation_error
from app.logging import get_logger
from app.models.user import User
from app.services.auth_service import (
    create_access_token,
    create_email_change_token,
    create_refresh_token,
    create_reset_token,
    create_verify_token,
    decode_email_change_token,
    decode_reset_token,
    decode_verify_token,
    hash_password,
    revoke_all_user_tokens,
    revoke_refresh_token,
    verify_password,
    verify_refresh_token,
)
from app.services.email_service import EmailTemplate, send_email
from app.services.hibp import validate_password
from app.services.lockout_service import (
    clear_failures_for,
    is_locked_out,
    record_attempt,
    record_attempt_isolated,
)

logger = get_logger(__name__)

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------


def _set_access_cookie(response: JSONResponse, access_token: str) -> None:
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        max_age=settings.JWT_EXPIRATION_MINUTES * 60,
        path="/",
        domain=settings.COOKIE_DOMAIN,
    )


def _set_auth_cookies(response: JSONResponse, access_token: str, refresh_token: str) -> None:
    _set_access_cookie(response, access_token)
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        max_age=settings.REFRESH_TOKEN_DAYS * 86400,
        path="/auth",
        domain=settings.COOKIE_DOMAIN,
    )


def _clear_auth_cookies(response: JSONResponse) -> None:
    response.delete_cookie(
        key="access_token",
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/",
        domain=settings.COOKIE_DOMAIN,
    )
    response.delete_cookie(
        key="refresh_token",
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/auth",
        domain=settings.COOKIE_DOMAIN,
    )


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: Optional[str] = None


class UserResponse(BaseModel):
    id: int
    email: str
    full_name: Optional[str] = None
    verified_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    user: UserResponse


class MessageResponse(BaseModel):
    message: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class VerifyEmailRequest(BaseModel):
    token: str


class ChangeEmailRequest(BaseModel):
    current_password: str
    new_email: EmailStr


class ConfirmEmailChangeRequest(BaseModel):
    token: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        verified_at=user.verified_at,
    )


async def _enforce_password_policy(password: str) -> None:
    ok, err = await validate_password(password)
    if not ok:
        raise validation_error(err or "Invalid password.")


def _refresh_from_request(request: Request, body: Optional[RefreshRequest]) -> Optional[str]:
    if body is not None and body.refresh_token:
        return body.refresh_token
    return request.cookies.get("refresh_token")


# ---------------------------------------------------------------------------
# POST /auth/register
# ---------------------------------------------------------------------------


@router.post("/register", response_model=AuthResponse)
@limiter.limit(settings.REGISTER_RATE_LIMIT)
async def register(
    request: Request,
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    await _enforce_password_policy(body.password)

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        raise conflict("A user with this email already exists.")

    access_tok = create_access_token(user.id, user.token_version)
    raw_refresh, _ = await create_refresh_token(user.id, db)

    verify_token = create_verify_token(user.id, user.token_version)
    try:
        await send_email(
            template=EmailTemplate.VERIFY_EMAIL,
            to=user.email,
            dynamic_template_data={
                "verify_url": f"{settings.FRONTEND_BASE_URL}/verify-email?token={verify_token}",
                "full_name": user.full_name,
            },
        )
    except Exception:
        logger.exception("verification_email_send_failed", extra={"user_id": user.id})
        # do NOT raise — registration succeeded; email is best-effort.

    payload = AuthResponse(
        access_token=access_tok,
        refresh_token=raw_refresh,
        user=_user_response(user),
    )
    response = JSONResponse(content=payload.model_dump(mode="json"))
    _set_auth_cookies(response, access_tok, raw_refresh)
    return response


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------


@router.post("/login", response_model=AuthResponse)
@limiter.limit(settings.LOGIN_RATE_LIMIT)
async def login(
    request: Request,
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    ip = get_remote_address(request)

    if await is_locked_out(body.email, db):
        raise too_many_requests("Too many login attempts. Try again later.")

    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        await record_attempt_isolated(body.email, ip, success=False)
        raise unauthorized("Invalid email or password.")

    await record_attempt(body.email, ip, success=True, db=db)
    await clear_failures_for(body.email, db)

    access_tok = create_access_token(user.id, user.token_version)
    raw_refresh, _ = await create_refresh_token(user.id, db)

    payload = AuthResponse(
        access_token=access_tok,
        refresh_token=raw_refresh,
        user=_user_response(user),
    )
    response = JSONResponse(content=payload.model_dump(mode="json"))
    _set_auth_cookies(response, access_tok, raw_refresh)
    return response


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------


@router.post("/logout", response_model=MessageResponse)
@limiter.limit(settings.LOGOUT_RATE_LIMIT)
async def logout(
    request: Request,
    body: Optional[RefreshRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    raw_refresh = _refresh_from_request(request, body)
    if raw_refresh:
        await revoke_refresh_token(raw_refresh, db)
    response = JSONResponse(content={"message": "Logged out."})
    _clear_auth_cookies(response)
    return response


# ---------------------------------------------------------------------------
# POST /auth/refresh
# ---------------------------------------------------------------------------


@router.post("/refresh", response_model=MessageResponse)
@limiter.limit(settings.REFRESH_RATE_LIMIT)
async def refresh(
    request: Request,
    body: Optional[RefreshRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    raw_refresh = _refresh_from_request(request, body)
    if not raw_refresh:
        raise unauthorized("No refresh token.")

    row = await verify_refresh_token(raw_refresh, db)
    if not row:
        raise unauthorized("Invalid or expired refresh token.")

    result = await db.execute(select(User).where(User.id == row.user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise unauthorized("User not found.")

    access_tok = create_access_token(user.id, user.token_version)
    response = JSONResponse(content={"message": "Token refreshed.", "access_token": access_tok})
    _set_access_cookie(response, access_tok)
    return response


# ---------------------------------------------------------------------------
# GET /auth/me
# ---------------------------------------------------------------------------


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)) -> UserResponse:
    return _user_response(user)


# ---------------------------------------------------------------------------
# PUT /auth/password
# ---------------------------------------------------------------------------


@router.put("/password", response_model=MessageResponse)
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    current_user: User = Depends(require_not_locked),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(body.current_password, current_user.password_hash):
        raise unauthorized("Current password is incorrect.")

    await _enforce_password_policy(body.new_password)

    current_user.password_hash = hash_password(body.new_password)
    current_user.token_version += 1
    await revoke_all_user_tokens(current_user.id, db)

    access_tok = create_access_token(current_user.id, current_user.token_version)
    raw_refresh, _ = await create_refresh_token(current_user.id, db)

    response = JSONResponse(content={"message": "Password changed."})
    _set_auth_cookies(response, access_tok, raw_refresh)
    return response


# ---------------------------------------------------------------------------
# POST /auth/forgot-password
# ---------------------------------------------------------------------------


@router.post("/forgot-password", response_model=MessageResponse)
@limiter.limit(settings.FORGOT_PASSWORD_RATE_LIMIT)
async def forgot_password(
    request: Request,
    body: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if user:
        token = create_reset_token(user.id, user.token_version)
        try:
            await send_email(
                template=EmailTemplate.RESET_PASSWORD,
                to=user.email,
                dynamic_template_data={
                    "reset_url": f"{settings.FRONTEND_BASE_URL}/reset-password?token={token}",
                    "full_name": user.full_name,
                },
            )
        except Exception:
            logger.exception("password_reset_email_send_failed", extra={"user_id": user.id})

    return {"message": "If an account exists with that email, a reset link has been sent."}


# ---------------------------------------------------------------------------
# GET /auth/validate-reset-token
# ---------------------------------------------------------------------------


@router.get("/validate-reset-token")
async def validate_reset_token(token: str, db: AsyncSession = Depends(get_db)):
    payload = decode_reset_token(token)
    if not payload:
        return {"valid": False, "reason": "Token is invalid or expired."}

    result = await db.execute(select(User).where(User.id == int(payload["sub"])))
    user = result.scalar_one_or_none()
    if not user or user.token_version != payload["tv"]:
        return {"valid": False, "reason": "This reset link has already been used."}

    return {"valid": True}


# ---------------------------------------------------------------------------
# POST /auth/reset-password
# ---------------------------------------------------------------------------


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(body: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    payload = decode_reset_token(body.token)
    if not payload:
        raise validation_error("Invalid or expired reset token.")

    result = await db.execute(select(User).where(User.id == int(payload["sub"])))
    user = result.scalar_one_or_none()
    if not user or user.token_version != payload["tv"]:
        raise unauthorized("This reset link has already been used.")

    await _enforce_password_policy(body.new_password)

    user.password_hash = hash_password(body.new_password)
    user.token_version += 1
    await revoke_all_user_tokens(user.id, db)

    return {"message": "Password reset successfully. Please log in."}


# ---------------------------------------------------------------------------
# POST /auth/verify-email
# ---------------------------------------------------------------------------


@router.post("/verify-email", response_model=MessageResponse)
@limiter.limit(settings.VERIFY_EMAIL_RATE_LIMIT)
async def verify_email(
    request: Request,
    body: VerifyEmailRequest,
    db: AsyncSession = Depends(get_db),
):
    payload = decode_verify_token(body.token)
    if not payload:
        raise validation_error("Invalid or expired verification token.")

    result = await db.execute(select(User).where(User.id == int(payload["sub"])))
    user = result.scalar_one_or_none()
    if not user:
        raise unauthorized("Invalid verification token.")

    if user.verified_at is not None:
        return {"message": "Email already verified."}

    user.verified_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return {"message": "Email verified."}


# ---------------------------------------------------------------------------
# POST /auth/resend-verification
# ---------------------------------------------------------------------------


@router.post("/resend-verification", response_model=MessageResponse)
@limiter.limit(settings.RESEND_VERIFICATION_RATE_LIMIT)
async def resend_verification(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    if current_user.verified_at is not None:
        return {"message": "Email already verified."}

    token = create_verify_token(current_user.id, current_user.token_version)
    try:
        await send_email(
            template=EmailTemplate.VERIFY_EMAIL,
            to=current_user.email,
            dynamic_template_data={
                "verify_url": f"{settings.FRONTEND_BASE_URL}/verify-email?token={token}",
                "full_name": current_user.full_name,
            },
        )
    except Exception:
        logger.exception("verification_email_send_failed", extra={"user_id": current_user.id})

    return {"message": "Verification email sent."}


# ---------------------------------------------------------------------------
# POST /auth/change-email
# ---------------------------------------------------------------------------


@router.post("/change-email", response_model=MessageResponse)
@limiter.limit(settings.CHANGE_EMAIL_RATE_LIMIT)
async def change_email(
    request: Request,
    body: ChangeEmailRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(body.current_password, current_user.password_hash):
        raise unauthorized("Current password is incorrect.")

    if body.new_email.lower() == current_user.email.lower():
        raise validation_error("New email is the same as the current email.")

    existing = await db.execute(select(User).where(User.email == body.new_email))
    if existing.scalar_one_or_none() is not None:
        raise conflict("An account with that email already exists.")

    token = create_email_change_token(current_user.id, current_user.token_version, body.new_email)
    try:
        await send_email(
            template=EmailTemplate.CHANGE_EMAIL,
            to=body.new_email,
            dynamic_template_data={
                "change_url": f"{settings.FRONTEND_BASE_URL}/confirm-email-change?token={token}",
                "full_name": current_user.full_name,
                "new_email": body.new_email,
            },
        )
    except Exception:
        logger.exception("email_change_send_failed", extra={"user_id": current_user.id})

    return {"message": "Verification link sent to the new address."}


# ---------------------------------------------------------------------------
# POST /auth/confirm-email-change
# ---------------------------------------------------------------------------


@router.post("/confirm-email-change", response_model=MessageResponse)
@limiter.limit(settings.CONFIRM_EMAIL_CHANGE_RATE_LIMIT)
async def confirm_email_change(
    request: Request,
    body: ConfirmEmailChangeRequest,
    db: AsyncSession = Depends(get_db),
):
    payload = decode_email_change_token(body.token)
    if not payload:
        raise unauthorized("Invalid or expired token.")

    result = await db.execute(select(User).where(User.id == int(payload["sub"])))
    user = result.scalar_one_or_none()
    if not user or user.token_version != payload["tv"]:
        raise unauthorized("This link has already been used.")

    new_email = payload["new_email"]
    old_email = user.email

    # Final uniqueness check before the write.
    conflict_check = await db.execute(
        select(User).where(User.email == new_email, User.id != user.id)
    )
    if conflict_check.scalar_one_or_none() is not None:
        raise conflict("An account with that email already exists.")

    user.email = new_email
    user.token_version += 1
    await revoke_all_user_tokens(user.id, db)

    try:
        await send_email(
            template=EmailTemplate.EMAIL_CHANGED,
            to=old_email,
            dynamic_template_data={
                "old_email": old_email,
                "new_email": new_email,
                "change_date": datetime.now(timezone.utc).isoformat(),
                "support_email": "support@carddroper.com",
            },
        )
    except Exception:
        logger.exception("email_changed_canary_send_failed", extra={"user_id": user.id})

    return {"message": "Email changed. Please log in with your new email."}
