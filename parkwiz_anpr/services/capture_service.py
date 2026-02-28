"""
Capture service — orchestrates the full ANPR capture workflow.

1. Lane config lookup (from cache)
2. CV pipeline invocation (with timeout)
3. Image saving
4. DB logging
5. Structured response assembly

Includes per-lane semaphore to prevent duplicate-trigger storms.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

from lpr_engine.pipeline import LPRPipeline

from ..core.config import settings
from ..core.database import log_capture
from ..core.lane_config import lane_cache
from ..core.image_store import image_store

logger = logging.getLogger(__name__)

# ── Per-lane semaphores ─────────────────────────────────────────────────────
# Prevents the same lane from having more than N simultaneous requests in
# flight.  Protects against PMS sending duplicate triggers on rapid
# back-to-back vehicles on the same lane.

_lane_locks: dict[str, asyncio.Semaphore] = {}


def _get_lane_semaphore(lane_number: str) -> asyncio.Semaphore:
    if lane_number not in _lane_locks:
        _lane_locks[lane_number] = asyncio.Semaphore(
            settings.performance.per_lane_concurrency
        )
    return _lane_locks[lane_number]


# ─── Main capture workflow ──────────────────────────────────────────────────


async def process_capture(
    lane_number: str,
    org_id: str,
    request_id: str,
) -> dict:
    """
    Full orchestration for one ANPR capture request.

    Returns a dict ready to be serialised as CaptureResponse.
    The calling route decides the HTTP status code.
    """
    start = time.perf_counter()
    now_utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds") + "Z"

    # ── 1. Lane config lookup ───────────────────────────────────────────
    lane_cfg = lane_cache.get_lane(lane_number, org_id)

    if lane_cfg is None:
        return _error_response(
            lane_number=lane_number,
            camera_ip=None,
            error_code="LANE_NOT_FOUND",
            detail=f"Lane {lane_number} not configured or disabled for org {org_id}",
            captured_at=now_utc,
            processing_ms=_elapsed_ms(start),
            request_id=request_id,
        )

    if not lane_cfg.enabled or not lane_cfg.active:
        return _error_response(
            lane_number=lane_number,
            camera_ip=lane_cfg.camera_ip,
            error_code="LANE_NOT_FOUND",
            detail=f"Lane {lane_number} is disabled",
            captured_at=now_utc,
            processing_ms=_elapsed_ms(start),
            request_id=request_id,
        )

    camera_ip = lane_cfg.camera_ip
    rtsp_user = settings.camera.rtsp_username
    rtsp_pass = settings.camera.rtsp_password

    # ── 2. Acquire per-lane semaphore ───────────────────────────────────
    sem = _get_lane_semaphore(lane_number)
    try:
        acquired = sem._value  # peek at available slots (for logging)
    except Exception:
        acquired = "?"

    logger.info(
        f"[LANE-{lane_number}] [req:{request_id}] "
        f"Capture started (camera={camera_ip}, sem_avail={acquired})"
    )

    async with sem:
        # ── 3. Call CV pipeline ─────────────────────────────────────────
        try:
            result = await asyncio.wait_for(
                LPRPipeline.process(
                    camera_ip=camera_ip,
                    rtsp_user=rtsp_user,
                    rtsp_pass=rtsp_pass,
                    lane_number=lane_number,
                ),
                timeout=settings.performance.request_timeout_sec,
            )
        except asyncio.TimeoutError:
            processing_ms = _elapsed_ms(start)
            logger.error(
                f"[LANE-{lane_number}] [req:{request_id}] "
                f"Pipeline timeout after {processing_ms}ms"
            )

            # Log to DB even on timeout
            asyncio.create_task(log_capture(
                lane_number=lane_number,
                org_id=org_id,
                camera_ip=camera_ip,
                plate=None,
                raw_ocr=None,
                confidence=0,
                detection_method=None,
                processing_ms=processing_ms,
                error_code="TIMEOUT",
                image_path=None,
                request_id=request_id,
            ))

            return _error_response(
                lane_number=lane_number,
                camera_ip=camera_ip,
                error_code="TIMEOUT",
                detail=f"Pipeline timed out after {settings.performance.request_timeout_sec}s",
                captured_at=now_utc,
                processing_ms=processing_ms,
                request_id=request_id,
            )
        except Exception as e:
            processing_ms = _elapsed_ms(start)
            logger.error(
                f"[LANE-{lane_number}] [req:{request_id}] "
                f"Pipeline error: {e}"
            )

            asyncio.create_task(log_capture(
                lane_number=lane_number,
                org_id=org_id,
                camera_ip=camera_ip,
                plate=None,
                raw_ocr=None,
                confidence=0,
                detection_method=None,
                processing_ms=processing_ms,
                error_code="PIPELINE_ERROR",
                image_path=None,
                request_id=request_id,
            ))

            return _error_response(
                lane_number=lane_number,
                camera_ip=camera_ip,
                error_code="PIPELINE_ERROR",
                detail=str(e),
                captured_at=now_utc,
                processing_ms=processing_ms,
                request_id=request_id,
            )

    # ── 4. Parse pipeline result ────────────────────────────────────────
    processing_ms = result.get("processing_ms", _elapsed_ms(start))
    plate = result.get("plate")
    confidence = round(result.get("confidence", 0), 4)
    raw_ocr = result.get("raw_ocr")
    method = result.get("method")
    error = result.get("error")
    telemetry = result.get("telemetry", {})

    # ── 5. Save image (if pipeline returned one) ────────────────────────
    image_bytes = result.get("image_bytes")  # CV pipeline may not return this yet
    image_path = await image_store.save_image(
        image_bytes=image_bytes,
        org_id=org_id,
        lane_number=lane_number,
        plate=plate,
        request_id=request_id,
    )

    # ── 6. Log to database (fire-and-forget) ────────────────────────────
    error_code = error if error else None
    asyncio.create_task(log_capture(
        lane_number=lane_number,
        org_id=org_id,
        camera_ip=camera_ip,
        plate=plate,
        raw_ocr=raw_ocr,
        confidence=confidence,
        detection_method=method,
        processing_ms=processing_ms,
        error_code=error_code,
        image_path=image_path,
        request_id=request_id,
    ))

    # ── 7. Build response ───────────────────────────────────────────────
    if plate:
        logger.info(
            f"[LANE-{lane_number}] [req:{request_id}] "
            f"Plate detected: {plate} (conf={confidence}, {processing_ms}ms)"
        )
        return {
            "success": True,
            "plate": plate,
            "confidence": confidence,
            "lane_number": lane_number,
            "camera_ip": camera_ip,
            "captured_at": now_utc,
            "processing_ms": processing_ms,
            "request_id": request_id,
            "raw_ocr": raw_ocr,
            "detection_method": method,
            "telemetry": telemetry,
        }
    else:
        logger.info(
            f"[LANE-{lane_number}] [req:{request_id}] "
            f"No plate detected ({error_code or 'NO_PLATE_DETECTED'}, {processing_ms}ms)"
        )
        return {
            "success": False,
            "plate": None,
            "confidence": 0,
            "lane_number": lane_number,
            "camera_ip": camera_ip,
            "captured_at": now_utc,
            "processing_ms": processing_ms,
            "request_id": request_id,
            "error_code": error_code or "NO_PLATE_DETECTED",
            "telemetry": telemetry,
        }


# ─── Helpers ────────────────────────────────────────────────────────────────


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _error_response(
    lane_number: str,
    camera_ip: str | None,
    error_code: str,
    detail: str,
    captured_at: str,
    processing_ms: int,
    request_id: str,
) -> dict:
    return {
        "success": False,
        "plate": None,
        "confidence": 0,
        "lane_number": lane_number,
        "camera_ip": camera_ip,
        "captured_at": captured_at,
        "processing_ms": processing_ms,
        "request_id": request_id,
        "error_code": error_code,
        "detail": detail,
    }
