"""
Integration tests for PARKWIZ ANPR Service.

All tests mock the CV pipeline and database — no camera or MSSQL required.
Run:  python -m pytest test_api.py -v
"""

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

# Ensure project root is on sys.path so lpr_engine is importable
_PROJECT_ROOT = str(Path(__file__).resolve().parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pytest
from httpx import AsyncClient, ASGITransport

# ── Pre-import lpr_engine class (NOT via lpr_engine.pipeline which is the instance) ──
from lpr_engine.pipeline import LPRPipeline as _LPRPipelineClass

# ── Patch config BEFORE importing the app ────────────────────────────────────

import parkwiz_anpr.core.config as config_module

_test_settings = config_module.Settings()
_test_settings.service.api_key = "test-key-123"
_test_settings.database.server = "localhost"
_test_settings.database.database = "TestDB"
config_module.settings = _test_settings

# ── Import DB and lane cache modules ─────────────────────────────────────────

import parkwiz_anpr.core.database as db_module
import parkwiz_anpr.core.lane_config as lc_module
import parkwiz_anpr.core.image_store as is_module
from parkwiz_anpr.core.lane_config import LaneConfig

# ── Pre-import modules that contain patch targets ────────────────────────────
import parkwiz_anpr.services.capture_service as cs_module

# ── Fake lane configs ────────────────────────────────────────────────────────

_fake_lane = LaneConfig(
    anpr_id=1,
    lane_number="01",
    enabled=True,
    org_id="PARKWIZ",
    lane_id="LANE01",
    public_key="",
    private_key="",
    source="PARKWIZ",
    camera_ip="192.168.1.152",
    camera_ip_backup="",
    active=True,
)

_fake_lane_disabled = LaneConfig(
    anpr_id=2,
    lane_number="02",
    enabled=False,
    org_id="PARKWIZ",
    lane_id="LANE02",
    public_key="",
    private_key="",
    source="PARKWIZ",
    camera_ip="192.168.1.153",
    camera_ip_backup="",
    active=True,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def patch_lifecycle():
    """Patch DB, lane cache, and pipeline so the app starts without real infra."""

    # Database mocks
    with patch.object(db_module, "initialize", new_callable=AsyncMock, return_value=True), \
         patch.object(db_module, "close", new_callable=AsyncMock), \
         patch.object(db_module, "check_connection", new_callable=AsyncMock, return_value=True), \
         patch.object(db_module, "log_capture", new_callable=AsyncMock), \
         patch.object(db_module, "fetch_recent_logs", new_callable=AsyncMock, return_value=[]), \
         patch.object(db_module, "fetch_stats_today", new_callable=AsyncMock, return_value=[]), \
         patch.object(db_module, "fetch_lane_configs", new_callable=AsyncMock, return_value=[]):

        # Lane cache — directly populate internal _cache dict
        lc_module.lane_cache._cache = {
            ("01", "PARKWIZ"): _fake_lane,
            ("02", "PARKWIZ"): _fake_lane_disabled,
        }
        lc_module.lane_cache._loaded = True

        with patch.object(lc_module.lane_cache, "load", new_callable=AsyncMock), \
             patch.object(lc_module.lane_cache, "start_background_refresh", new_callable=AsyncMock), \
             patch.object(lc_module.lane_cache, "stop", new_callable=AsyncMock), \
             patch.object(lc_module.lane_cache, "reload", new_callable=AsyncMock):

            # Pipeline mock — patch on the actual class object
            with patch.object(_LPRPipelineClass, "initialize"), \
                 patch.object(is_module.image_store, "start_cleanup_task"), \
                 patch.object(is_module.image_store, "stop", new_callable=AsyncMock):
                yield


@pytest.fixture
def app_client():
    """Create an async test client."""
    from parkwiz_anpr.main import app
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://testserver")


# ── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_root(app_client):
    async with app_client as client:
        r = await client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert data["service"] == "PARKWIZ ANPR"
    assert "version" in data


@pytest.mark.asyncio
async def test_health(app_client):
    async with app_client as client:
        r = await client.get("/api/v1/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] in ("healthy", "degraded")
    assert "uptime_seconds" in data
    assert "db_connected" in data


@pytest.mark.asyncio
async def test_capture_no_api_key(app_client):
    """Requests without API key should be rejected when key is configured."""
    async with app_client as client:
        r = await client.post(
            "/api/v1/capture",
            json={"lane_number": "01", "org_id": "PARKWIZ"},
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_capture_wrong_api_key(app_client):
    async with app_client as client:
        r = await client.post(
            "/api/v1/capture",
            json={"lane_number": "01", "org_id": "PARKWIZ"},
            headers={"X-API-Key": "wrong-key"},
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_capture_success(app_client):
    """Simulate a successful plate detection."""
    mock_result = {
        "plate": "KA01AB1234",
        "confidence": 0.94,
        "raw_ocr": "KA 01 AB 1234",
        "method": "yolo+paddle",
        "processing_ms": 1200,
    }

    with patch.object(
        _LPRPipelineClass, "process",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        async with app_client as client:
            r = await client.post(
                "/api/v1/capture",
                json={"lane_number": "01", "org_id": "PARKWIZ"},
                headers={"X-API-Key": "test-key-123"},
            )

    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert data["plate"] == "KA01AB1234"
    assert data["confidence"] == 0.94
    assert data["lane_number"] == "01"
    assert "request_id" in data


@pytest.mark.asyncio
async def test_capture_no_plate(app_client):
    """Simulate no plate detected — should still be HTTP 200."""
    mock_result = {
        "plate": None,
        "confidence": 0,
        "error": "NO_PLATE_DETECTED",
        "processing_ms": 800,
    }

    with patch.object(
        _LPRPipelineClass, "process",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        async with app_client as client:
            r = await client.post(
                "/api/v1/capture",
                json={"lane_number": "01", "org_id": "PARKWIZ"},
                headers={"X-API-Key": "test-key-123"},
            )

    assert r.status_code == 200
    data = r.json()
    assert data["success"] is False
    assert data["plate"] is None
    assert data["error_code"] == "NO_PLATE_DETECTED"


@pytest.mark.asyncio
async def test_capture_lane_not_found(app_client):
    """Requesting a non-existent lane → HTTP 404."""
    async with app_client as client:
        r = await client.post(
            "/api/v1/capture",
            json={"lane_number": "99", "org_id": "PARKWIZ"},
            headers={"X-API-Key": "test-key-123"},
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_capture_lane_disabled(app_client):
    """Requesting a disabled lane → HTTP 404."""
    async with app_client as client:
        r = await client.post(
            "/api/v1/capture",
            json={"lane_number": "02", "org_id": "PARKWIZ"},
            headers={"X-API-Key": "test-key-123"},
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_capture_camera_error(app_client):
    """Camera unreachable → HTTP 200 with error_code."""
    mock_result = {
        "plate": None,
        "confidence": 0,
        "error": "CAMERA_UNREACHABLE",
        "processing_ms": 150,
    }

    with patch.object(
        _LPRPipelineClass, "process",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        async with app_client as client:
            r = await client.post(
                "/api/v1/capture",
                json={"lane_number": "01", "org_id": "PARKWIZ"},
                headers={"X-API-Key": "test-key-123"},
            )

    assert r.status_code == 200
    data = r.json()
    assert data["success"] is False
    assert data["error_code"] == "CAMERA_UNREACHABLE"


@pytest.mark.asyncio
async def test_capture_validation_error(app_client):
    """Missing required field → HTTP 422."""
    async with app_client as client:
        r = await client.post(
            "/api/v1/capture",
            json={"org_id": "PARKWIZ"},  # missing lane_number
            headers={"X-API-Key": "test-key-123"},
        )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_stats(app_client):
    async with app_client as client:
        r = await client.get("/api/v1/stats")
    assert r.status_code == 200
    data = r.json()
    assert "total_captures_today" in data
    assert "per_lane" in data


@pytest.mark.asyncio
async def test_logs(app_client):
    async with app_client as client:
        r = await client.get("/api/v1/logs?limit=10")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_reload_config(app_client):
    async with app_client as client:
        r = await client.post("/api/v1/admin/reload-config")
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True


@pytest.mark.asyncio
async def test_dashboard(app_client):
    async with app_client as client:
        r = await client.get("/dashboard")
    assert r.status_code == 200
    assert "PARKWIZ" in r.text
