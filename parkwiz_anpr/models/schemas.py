"""
Pydantic models for API request/response validation.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


# ─── Request Models ─────────────────────────────────────────────────────────


class CaptureRequest(BaseModel):
    lane_number: str = Field(
        ...,
        min_length=1,
        max_length=10,
        description="Lane number as configured in PMS (e.g. '01', '28')",
        examples=["01"],
    )
    org_id: str = Field(
        default="PARKWIZ",
        max_length=50,
        description="Organisation ID",
        examples=["PARKWIZ"],
    )


# ─── Response Models ────────────────────────────────────────────────────────


class CaptureResponse(BaseModel):
    success: bool
    plate: Optional[str] = None
    confidence: float = 0.0
    lane_number: str
    camera_ip: Optional[str] = None
    captured_at: str  # ISO-8601 UTC
    processing_ms: int = 0
    request_id: str
    error_code: Optional[str] = None
    raw_ocr: Optional[str] = None
    detection_method: Optional[str] = None
    telemetry: Optional[dict] = None


class HealthResponse(BaseModel):
    status: str
    uptime_seconds: int
    version: str
    db_connected: bool
    cameras_configured: int


class LaneStatEntry(BaseModel):
    lane_number: str
    total_captures: int
    successful: int
    success_rate: float
    avg_processing_ms: Optional[int] = None


class StatsResponse(BaseModel):
    total_captures_today: int
    successful_today: int
    overall_success_rate: float
    avg_processing_ms: Optional[int] = None
    per_lane: list[LaneStatEntry]


class LogEntry(BaseModel):
    log_id: int
    lane_number: str
    org_id: str
    camera_ip: Optional[str] = None
    plate: Optional[str] = None
    raw_ocr: Optional[str] = None
    confidence: Optional[float] = None
    detection_method: Optional[str] = None
    processing_ms: Optional[int] = None
    error_code: Optional[str] = None
    image_path: Optional[str] = None
    captured_at: Optional[str] = None
    request_id: Optional[str] = None
