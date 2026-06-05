"""
Complete production FastAPI main application (with Render environment debug).
"""

import os
import logging
import logging.config
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi import APIRouter

from app.database import ensure_indexes
from app.routers import analyze, auth
from app.utils.file_handler import cleanup_temp_files
from app.utils.config import get_settings

# DEBUG: Print environment check for Render startup logs
print("DEBUG -> OPENAI_API_KEY (first 8 chars):",
      os.getenv("OPENAI_API_KEY")[:8] + "..." if os.getenv("OPENAI_API_KEY") else "NOT FOUND")

# Get settings
settings = get_settings()

# Configure logging
logging.config.dictConfig(settings.get_log_config())
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup and shutdown."""
    
    # Startup
    logger.info("🚀 Starting Meeting Analysis API...")
    logger.info(f"📊 Version: {settings.APP_VERSION}")
    logger.info(f"🔧 Debug mode: {settings.DEBUG}")
    
    # ✅ Extra: Log API key existence at startup
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        logger.info(f"✅ OPENAI_API_KEY loaded (length: {len(api_key)} chars)")
    else:
        logger.error("❌ OPENAI_API_KEY missing in environment!")

    # Validate configuration
    try:
        if not settings.validate_api_keys():
            logger.error("❌ OpenAI API key validation failed!")
            if not settings.DEBUG:
                raise RuntimeError("Invalid API configuration")
        else:
            logger.info("✅ OpenAI API key validated successfully")

        # Temp directory setup
        temp_dir = settings.get_temp_dir()
        logger.info(f"📁 Temp directory: {temp_dir}")

        await ensure_indexes()
        logger.info("Auth database indexes are ready")

        import shutil
        disk_usage = shutil.disk_usage(temp_dir)
        available_gb = disk_usage.free / (1024**3)
        logger.info(f"💾 Available disk space: {available_gb:.1f} GB")

        if available_gb < 1.0:
            logger.warning("⚠️ Low disk space available!")
        
        logger.info("✅ API started successfully")
        
    except Exception as e:
        logger.error(f"❌ Startup failed: {e}")
        if not settings.DEBUG:
            raise
    
    yield
    
    # Shutdown
    logger.info("🛑 Shutting down Meeting Analysis API...")
    try:
        cleanup_temp_files()
        logger.info("✅ Cleanup completed")
    except Exception as e:
        logger.error(f"❌ Cleanup failed: {e}")

# Create FastAPI app
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

# Security middleware
if settings.is_production():
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE",'OPTIONS'],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=3600
)

# Custom exception handlers
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "HTTP Error", "message": exc.detail, "status_code": exc.status_code}
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=422,
        content={"error": "Validation Error", "message": "Invalid request data", "details": exc.errors()}
    )

@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal Server Error", "message": "An unexpected error occurred" if settings.is_production() else str(exc)}
    )

# Routers
app.include_router(analyze.router, prefix="/api", tags=["analysis"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])

# Root endpoints
@app.get("/")
async def root():
    return {
        "message": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "operational",
        "docs": "/docs" if settings.DEBUG else "disabled",
        "endpoints": {"analyze": "/api/analyze", "auth": "/api/auth", "health": "/health", "status": "/api/status"}
    }

@app.get("/health")
async def health_check():
    import time
    try:
        health_status = {
            "status": "healthy",
            "timestamp": int(time.time()),
            "version": settings.APP_VERSION,
            "services": {
                "openai_api": settings.validate_api_keys(),
                "temp_directory": os.path.exists(settings.get_temp_dir()),
                "disk_space_available": True
            },
            "configuration": {
                "debug_mode": settings.DEBUG,
                "max_file_size_mb": settings.MAX_FILE_SIZE / 1024 / 1024,
                "max_audio_duration": settings.MAX_AUDIO_DURATION,
                "supported_formats": len(settings.supported_formats_list)
            }
        }
        if not all(health_status["services"][s] for s in ["openai_api", "temp_directory"]):
            health_status["status"] = "degraded"
        return health_status
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {"status": "unhealthy", "error": str(e)}

@app.get("/info")
async def api_info():
    return {
        "api": {
            "name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "description": "AI-powered meeting transcription and analysis"
        },
        "capabilities": {
            "transcription": "OpenAI Whisper API",
            "analysis": {
                "summarization": True,
                "action_items": True,
                "sentiment": True,
                "topics": True,
                "decisions": True
            }
        }
    }

# ✅ Added safe environment debug endpoint
router = APIRouter()

@router.get("/debug/env")
async def check_env():
    api_key = os.getenv("OPENAI_API_KEY")
    return {
        "api_key_exists": bool(api_key),
        "api_key_length": len(api_key) if api_key else 0,
        "api_key_preview": api_key[:8] + "..." if api_key else "NONE"
    }

app.include_router(router)

# Startup log
@app.on_event("startup")
async def startup_event():
    logger.info("🎯 Meeting Analysis API is ready to process files!")
    if settings.DEBUG:
        logger.info(f"📚 API docs at /docs")
        logger.info(f"🔍 Debug endpoints available at /debug/*")
