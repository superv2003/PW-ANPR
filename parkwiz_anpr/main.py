"""
PARKWIZ ANPR Service — FastAPI Application Entrypoint.

Starts the ANPR service with:
 • Rotating file logging (info + errors)
 • Database pool initialisation
 • Lane config cache loading + background refresh
 • LPR pipeline warmup (eliminates cold-start on first real request)
 • Image retention cleanup task
 • API routes (v1 capture + admin) and web dashboard
"""

import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# ── Ensure the project root is on sys.path so `lpr_engine` is importable ──
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from parkwiz_anpr import __version__
from parkwiz_anpr.core.config import settings
from parkwiz_anpr.core import database
from parkwiz_anpr.core.lane_config import lane_cache
from parkwiz_anpr.core.image_store import image_store
from parkwiz_anpr.api.v1.capture import router as capture_router
from parkwiz_anpr.api.v1.admin import router as admin_router, set_start_time
from parkwiz_anpr.services.polling_service import polling_service


# ─── Logging Setup ──────────────────────────────────────────────────────────


def _setup_logging():
    log_dir = Path(settings.storage.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    log_format = logging.Formatter(
        "%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # INFO+ → anpr_service.log
    info_handler = RotatingFileHandler(
        log_dir / "anpr_service.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=10,
        encoding="utf-8",
    )
    info_handler.setLevel(logging.INFO)
    info_handler.setFormatter(log_format)

    # WARNING+ → anpr_errors.log
    error_handler = RotatingFileHandler(
        log_dir / "anpr_errors.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(log_format)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(log_format)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.service.log_level, logging.INFO))
    root_logger.addHandler(info_handler)
    root_logger.addHandler(error_handler)
    root_logger.addHandler(console_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("multipart").setLevel(logging.WARNING)


_setup_logging()
logger = logging.getLogger("parkwiz_anpr")


# ─── Lifespan ───────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    start = time.time()
    set_start_time(start)

    logger.info("=" * 60)
    logger.info(f"  PARKWIZ ANPR Service v{__version__} starting")
    logger.info(f"  Host: {settings.service.host}:{settings.service.port}")
    logger.info(f"  Workers: {settings.performance.max_workers} threads")
    logger.info(f"  API Key Auth: {'ENABLED' if settings.service.api_key else 'DISABLED'}")
    logger.info("=" * 60)

    # 1. Database
    db_ok = await database.initialize()
    if db_ok:
        logger.info("Database connected ✅")
    else:
        logger.warning("Database unavailable — service starting in degraded mode ⚠️")

    # 2. Lane config cache
    try:
        await lane_cache.load()
        logger.info(f"Lane config cache loaded: {lane_cache.lane_count} lane(s)")
    except Exception as e:
        logger.error(f"Failed to load lane config: {e}")

    await lane_cache.start_background_refresh()

    # 3. LPR Pipeline warmup — eliminate first-request cold-start
    try:
        from lpr_engine.pipeline import LPRPipeline
        import urllib.parse
        
        # Build camera_map from enabled lanes to pre-start persistent RTSP streams
        camera_map = {}
        enc_u = urllib.parse.quote_plus(settings.camera.rtsp_username)
        enc_p = urllib.parse.quote_plus(settings.camera.rtsp_password)
        r_port = settings.camera.rtsp_port
        r_path = settings.camera.rtsp_path
        
        for lane in lane_cache.all_lanes():
            if lane.enabled and lane.active and lane.camera_ip:
                # [HARDCODED FOR TESTING] Override Lane 28 IP
                ip_to_use = "192.168.1.63" if lane.lane_number == "28" else lane.camera_ip
                url = f"rtsp://{enc_u}:{enc_p}@{ip_to_use}:{r_port}{r_path}"
                camera_map[lane.lane_number] = url

        LPRPipeline.initialize(camera_map=camera_map)
        logger.info("LPR Pipeline models and persistent streams pre-loaded ✅")

        # Warmup pass (if available)
        if hasattr(LPRPipeline, "warmup"):
            await LPRPipeline.warmup()
            logger.info("LPR Pipeline warmup complete ✅")
    except Exception as e:
        logger.error(f"LPR pipeline init/warmup failed: {e}")

    # 4. Image cleanup task
    image_store.start_cleanup_task()

    # 5. Background DB Polling test service (if enabled)
    await polling_service.start()

    elapsed = int((time.time() - start) * 1000)
    logger.info(f"PW-ANPR Service ready ✅ (startup took {elapsed}ms)")

    yield  # ── app is running ──

    # Shutdown
    logger.info("PARKWIZ ANPR Service shutting down...")
    await polling_service.stop()
    await lane_cache.stop()
    await image_store.stop()
    await database.close()
    logger.info("Shutdown complete.")


# ─── FastAPI App ────────────────────────────────────────────────────────────


app = FastAPI(
    title="PARKWIZ ANPR Service",
    description="Automatic Number Plate Recognition service for PARKWIZ PMS",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
)

# API routes
app.include_router(capture_router, prefix="/api/v1", tags=["Capture"])
app.include_router(admin_router, prefix="/api/v1", tags=["Admin"])


# ─── Dashboard Route ───────────────────────────────────────────────────────


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    template_path = Path(__file__).parent / "templates" / "dashboard.html"
    if template_path.exists():
        return HTMLResponse(content=template_path.read_text(encoding="utf-8"))
    return HTMLResponse(
        content="<h1>Dashboard template not found</h1>",
        status_code=500,
    )


# ─── Root Redirect ─────────────────────────────────────────────────────────


@app.get("/", include_in_schema=False)
async def root():
    return {
        "service": "PARKWIZ ANPR",
        "version": __version__,
        "docs": "/docs",
        "dashboard": "/dashboard",
        "health": "/api/v1/health",
    }


# ─── Global Exception Handler ──────────────────────────────────────────────


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception on {request.method} {request.url}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error_code": "INTERNAL_ERROR",
            "detail": "An unexpected error occurred. Check server logs.",
        },
    )


# ─── Uvicorn Entrypoint ────────────────────────────────────────────────────


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "parkwiz_anpr.main:app",
        host=settings.service.host,
        port=settings.service.port,
        workers=settings.service.workers,
        log_level=settings.service.log_level.lower(),
        access_log=False,
    )
