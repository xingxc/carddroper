from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import delete, text

from app.config import settings
from app.database import AsyncSessionLocal, engine, init_db
from app.errors import AppError, app_error_handler
from app.logging import LoggingMiddleware, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up — testing database connection…")
    await init_db()
    logger.info("Database connection OK")

    from app.models.refresh_token import RefreshToken

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(RefreshToken).where(
                (RefreshToken.expires_at < datetime.now(timezone.utc).replace(tzinfo=None))
                | (RefreshToken.revoked_at.is_not(None))
            )
        )
        await session.commit()
        if result.rowcount:
            logger.info(
                "Cleaned up expired/revoked refresh tokens",
                extra={"count": result.rowcount},
            )

    yield
    logger.info("Shutting down")


app = FastAPI(title="Carddroper API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Requested-With"],
)

app.add_middleware(LoggingMiddleware)

app.add_exception_handler(AppError, app_error_handler)

from app.routes.auth import limiter, router as auth_router  # noqa: E402

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    logger.exception(
        "unhandled_exception",
        extra={
            "event": "unhandled_exception",
            "request_id": request_id,
            "path": request.url.path,
            "method": request.method,
            "exc_type": type(exc).__name__,
            "exc_message": str(exc),
        },
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred.",
                "request_id": request_id,
            }
        },
    )


app.add_exception_handler(Exception, internal_error_handler)


@app.get("/health", tags=["meta"])
async def health_check():
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}


app.include_router(auth_router)
