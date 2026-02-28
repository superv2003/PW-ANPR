"""
Admin & monitoring endpoints for PARKWIZ ANPR Service.

Health checks, statistics, log viewing, config reload, and camera testing.
No auth required — these are internal/local-network only.
"""

import asyncio
import logging
import time

from fastapi import APIRouter, Query
from typing import Optional

from ...core.config import settings
from ...core import database
from ...core.lane_config import lane_cache
from ...models.schemas import (
    HealthResponse,
    StatsResponse,
    LaneStatEntry,
    LogEntry,
)
from ... import __version__

logger = logging.getLogger(__name__)

router = APIRouter()

# Set at startup by main.py
_start_time: float = 0.0


def set_start_time(t: float):
    global _start_time
    _start_time = t


# ─── GET /health ────────────────────────────────────────────────────────────


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health check",
)
async def health():
    uptime = int(time.time() - _start_time) if _start_time else 0
    db_ok = await database.check_connection()

    return HealthResponse(
        status="healthy" if db_ok else "degraded",
        uptime_seconds=uptime,
        version=__version__,
        db_connected=db_ok,
        cameras_configured=lane_cache.lane_count,
    )


# ─── GET /stats ─────────────────────────────────────────────────────────────


@router.get(
    "/stats",
    response_model=StatsResponse,
    summary="Today's capture statistics",
)
async def stats():
    rows = await database.fetch_stats_today()

    per_lane = []
    total_captures = 0
    total_successful = 0
    total_processing_ms = 0
    count_with_ms = 0

    for row in rows:
        lane_total = row.get("total_captures", 0)
        lane_success = row.get("successful", 0)
        lane_avg_ms = row.get("avg_processing_ms")
        lane_number = row.get("PMSLaneNumber", "?")

        total_captures += lane_total
        total_successful += lane_success
        if lane_avg_ms is not None:
            total_processing_ms += lane_avg_ms * lane_total
            count_with_ms += lane_total

        rate = (lane_success / lane_total * 100) if lane_total > 0 else 0.0

        per_lane.append(LaneStatEntry(
            lane_number=lane_number,
            total_captures=lane_total,
            successful=lane_success,
            success_rate=round(rate, 1),
            avg_processing_ms=int(lane_avg_ms) if lane_avg_ms else None,
        ))

    overall_rate = (total_successful / total_captures * 100) if total_captures > 0 else 0.0
    overall_avg_ms = int(total_processing_ms / count_with_ms) if count_with_ms > 0 else None

    return StatsResponse(
        total_captures_today=total_captures,
        successful_today=total_successful,
        overall_success_rate=round(overall_rate, 1),
        avg_processing_ms=overall_avg_ms,
        per_lane=per_lane,
    )


# ─── GET /logs ──────────────────────────────────────────────────────────────


@router.get(
    "/logs",
    response_model=list[LogEntry],
    summary="Recent capture log entries",
)
async def logs(
    lane: Optional[str] = Query(None, description="Filter by lane number"),
    plate: Optional[str] = Query(None, description="Search by plate (partial match)"),
    limit: int = Query(50, ge=1, le=500, description="Max rows to return"),
):
    rows = await database.fetch_recent_logs(
        lane_number=lane,
        plate_search=plate,
        limit=limit,
    )

    return [
        LogEntry(
            log_id=r.get("LogID", 0),
            lane_number=r.get("PMSLaneNumber", ""),
            org_id=r.get("ANPROrgID", ""),
            camera_ip=r.get("CameraIP"),
            plate=r.get("PlateDetected"),
            raw_ocr=r.get("RawOCRText"),
            confidence=float(r["Confidence"]) if r.get("Confidence") is not None else None,
            detection_method=r.get("DetectionMethod"),
            processing_ms=r.get("ProcessingMs"),
            error_code=r.get("ErrorCode"),
            image_path=r.get("ImagePath"),
            captured_at=r["CapturedAt"].isoformat() if r.get("CapturedAt") else None,
            request_id=r.get("RequestID"),
        )
        for r in rows
    ]


# ─── POST /admin/reload-config ─────────────────────────────────────────────


@router.post(
    "/admin/reload-config",
    summary="Force-reload lane configuration from database",
)
async def reload_config():
    await lane_cache.reload()
    return {
        "success": True,
        "message": f"Lane config reloaded — {lane_cache.lane_count} lane(s) active",
    }


# ─── GET /admin/test-camera ────────────────────────────────────────────────


@router.get(
    "/admin/test-camera",
    summary="Test RTSP connectivity for a specific lane",
)
async def test_camera(
    lane: str = Query(..., description="Lane number to test"),
    org_id: str = Query("PARKWIZ", description="Organisation ID"),
):
    cfg = lane_cache.get_lane(lane, org_id)
    if cfg is None:
        return {
            "success": False,
            "error": f"Lane {lane} not found for org {org_id}",
        }

    camera_ip = cfg.camera_ip

    # Quick RTSP frame-grab test
    from lpr_engine.frame_grabber import FrameGrabber

    loop = asyncio.get_running_loop()
    rtsp_url = (
        f"rtsp://{settings.camera.rtsp_username}:{settings.camera.rtsp_password}"
        f"@{camera_ip}:{settings.camera.rtsp_port}{settings.camera.rtsp_path}"
    )

    start = time.perf_counter()
    try:
        frames, error = await asyncio.wait_for(
            loop.run_in_executor(None, FrameGrabber.get_frames, rtsp_url),
            timeout=settings.performance.camera_connect_timeout_sec + 1,
        )
    except asyncio.TimeoutError:
        return {
            "success": False,
            "lane": lane,
            "camera_ip": camera_ip,
            "error": "Connection timed out",
            "elapsed_ms": int((time.perf_counter() - start) * 1000),
        }

    elapsed_ms = int((time.perf_counter() - start) * 1000)

    if error:
        return {
            "success": False,
            "lane": lane,
            "camera_ip": camera_ip,
            "error": error,
            "elapsed_ms": elapsed_ms,
        }

    return {
        "success": True,
        "lane": lane,
        "camera_ip": camera_ip,
        "frames_grabbed": len(frames) if frames else 0,
        "frame_shape": list(frames[0].shape) if frames else None,
        "elapsed_ms": elapsed_ms,
    }
