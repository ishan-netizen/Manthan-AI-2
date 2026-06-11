"""
Meeting Analysis API — FastAPI application factory.
"""
import os
import logging
import logging.config
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.database import ensure_indexes, sessions_collection
from app.routers import analyze, auth, meta
from app.utils.file_handler import cleanup_temp_files
from app.utils.config import get_settings

settings = get_settings()

logging.config.dictConfig(settings.get_log_config())
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""

    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"Debug mode: {settings.DEBUG}")

    api_key = os.getenv("GEMINI_API_KEY")
    if api_key:
        logger.info(f"GEMINI_API_KEY loaded (length: {len(api_key)} chars)")
    else:
        logger.error("GEMINI_API_KEY missing")

    try:
        if not settings.validate_api_keys():
            logger.error("API key validation failed")
            if not settings.DEBUG:
                raise RuntimeError("Invalid API configuration")
        else:
            logger.info("API key validated")

        temp_dir = settings.get_temp_dir()
        logger.info(f"Temp directory: {temp_dir}")

        await ensure_indexes()
        logger.info("Database indexes ready")

        if sessions_collection is not None:
            result = await sessions_collection.delete_many({})
            logger.info(f"Cleared {result.deleted_count} existing sessions — fresh login required")

        import shutil
        disk_usage = shutil.disk_usage(temp_dir)
        available_gb = disk_usage.free / (1024 ** 3)
        logger.info(f"Available disk space: {available_gb:.1f} GB")
        if available_gb < 1.0:
            logger.warning("Low disk space")

        logger.info("API started successfully")

    except Exception as e:
        logger.error(f"Startup failed: {e}")
        if not settings.DEBUG:
            raise

    yield

    logger.info("Shutting down API...")
    try:
        cleanup_temp_files()
        logger.info("Cleanup completed")
    except Exception as e:
        logger.error(f"Cleanup failed: {e}")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
app = FastAPI(
    title=settings.APP_NAME,
    description="AI-powered meeting transcription and analysis service using OpenAI APIs",
    version=settings.APP_VERSION,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    lifespan=lifespan,
    contact={"name": "Meeting Analysis API", "url": "https://github.com/YashPansare31/Manthan-AI"},
    license_info={"name": "MIT", "url": "https://opensource.org/licenses/MIT"},
)

if settings.is_production():
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=3600,
)


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "HTTP Error", "message": exc.detail, "status_code": exc.status_code},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=422,
        content={"error": "Validation Error", "message": "Invalid request data", "details": exc.errors()},
    )


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal Server Error",
            "message": "An unexpected error occurred" if settings.is_production() else str(exc),
        },
    )


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(meta.router, tags=["meta"])
app.include_router(analyze.router, prefix="/api", tags=["analysis"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
