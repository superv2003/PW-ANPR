"""
POST /api/v1/capture — the primary ANPR capture endpoint.

Called by PARKWIZ PMS when a vehicle triggers a lane sensor.
Always returns HTTP 200 for business-level outcomes (plate found,
no plate, camera down).  Only 4xx/5xx for infrastructure failures.
"""

import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from typing import Optional

from ...models.schemas import CaptureRequest, CaptureResponse
from ...services.capture_service import process_capture
from ...core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


# ── API Key dependency ──────────────────────────────────────────────────────


async def verify_api_key(x_api_key: Optional[str] = Header(None)):
    """
    If an API key is configured in config.ini, every request must include
    it in the X-API-Key header.  If no key is configured, auth is skipped.
    """
    configured_key = settings.service.api_key
    if not configured_key:
        return  # no auth configured — open access (local network)
    if x_api_key != configured_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ── Capture route ───────────────────────────────────────────────────────────


@router.post(
    "/capture",
    response_model=CaptureResponse,
    summary="Capture license plate for a lane",
    description="Triggers ANPR capture for the specified lane. Returns plate info or error.",
)
async def capture(
    body: CaptureRequest,
    _auth: None = Depends(verify_api_key),
):
    request_id = uuid.uuid4().hex[:12]

    logger.info(
        f"[LANE-{body.lane_number}] [req:{request_id}] "
        f"Capture request received (org={body.org_id})"
    )

    result = await process_capture(
        lane_number=body.lane_number,
        org_id=body.org_id,
        request_id=request_id,
    )

    # LANE_NOT_FOUND → HTTP 404 (infrastructure error, not business)
    if result.get("error_code") == "LANE_NOT_FOUND":
        raise HTTPException(
            status_code=404,
            detail=result.get("detail", f"Lane {body.lane_number} not configured or disabled"),
        )

    # Everything else → HTTP 200 (PMS expects 200 for all normal scenarios)
    return CaptureResponse(**{
        k: v for k, v in result.items() if k != "detail"
    })
