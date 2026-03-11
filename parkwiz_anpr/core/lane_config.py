"""
In-memory cache for lane ANPR configuration.

Loads all rows from tblLaneANPRConfiguration at startup and refreshes
in the background every N seconds.  API calls hit this cache (~0.1 ms)
instead of querying MSSQL on every request.
"""

import asyncio
import logging
from dataclasses import dataclass

from . import database
from .config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LaneConfig:
    """Immutable snapshot of one lane's ANPR configuration."""
    anpr_id: int
    lane_number: str
    enabled: bool
    org_id: str
    lane_id: str
    public_key: str
    private_key: str
    source: str
    camera_ip: str          # primary (ANPRAPIURL)
    camera_ip_backup: str   # secondary (ANPRAPIURL2)
    active: bool


class LaneConfigCache:
    """Thread-safe in-memory cache of lane configurations."""

    def __init__(self):
        # Key: (lane_number, org_id)  →  LaneConfig
        self._cache: dict[tuple[str, str], LaneConfig] = {}
        self._lock = asyncio.Lock()
        self._refresh_task: asyncio.Task | None = None
        self._loaded = False

    # ── Public API ──────────────────────────────────────────────────────

    async def load(self):
        """Initial load from database.  Called once during startup."""
        await self._refresh()
        self._loaded = True
        logger.info(
            f"Lane config cache loaded: {len(self._cache)} lane(s) configured"
        )

    async def start_background_refresh(self):
        """Kick off a periodic background refresh task."""
        interval = settings.performance.lane_cache_refresh_sec
        self._refresh_task = asyncio.create_task(self._refresh_loop(interval))
        logger.info(f"Lane config auto-refresh every {interval}s started")

    async def stop(self):
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass

    async def reload(self):
        """Manual cache refresh (admin endpoint)."""
        await self._refresh()
        logger.info(f"Lane config cache reloaded: {len(self._cache)} lane(s)")

    def get_lane(self, lane_number: str, org_id: str) -> LaneConfig | None:
        """Ultra-fast cache lookup — O(1) dict access."""
        return self._cache.get((lane_number, org_id))

    @property
    def lane_count(self) -> int:
        return len(self._cache)

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def all_lanes(self) -> list[LaneConfig]:
        return list(self._cache.values())

    # ── Internals ───────────────────────────────────────────────────────

    async def _refresh(self):
        rows = await database.fetch_lane_configs()
        new_cache: dict[tuple[str, str], LaneConfig] = {}
        for row in rows:
            lane_num = str(row.get("PMSLaneNumber", "")).strip()
            camera_ip = str(row.get("ANPRAPIURL", "")).strip()
            
            # -- Override camera IP for testing --
            if lane_num in settings.camera.ip_overrides:
                override_ip = settings.camera.ip_overrides[lane_num]
                logger.warning(
                    f"[TESTING] Overriding Lane {lane_num} camera IP "
                    f"from {camera_ip} to {override_ip}"
                )
                camera_ip = override_ip

            cfg = LaneConfig(
                anpr_id=row.get("ANPRID", 0),
                lane_number=lane_num,
                enabled=bool(row.get("flgEnableANPR", 0)),
                org_id=str(row.get("ANPROrgID", "")).strip(),
                lane_id=str(row.get("ANPRLaneID", "")).strip(),
                public_key=str(row.get("ANPRPublicKey", "")).strip(),
                private_key=str(row.get("ANPRPrivateKey", "")).strip(),
                source=str(row.get("ANPRSource", "")).strip(),
                camera_ip=camera_ip,
                camera_ip_backup=str(row.get("ANPRAPIURL2", "")).strip(),
                active=(str(row.get("ActiveStatus", "N")).strip().upper() == "Y"),
            )
            key = (cfg.lane_number, cfg.org_id)
            new_cache[key] = cfg

        async with self._lock:
            self._cache = new_cache

    async def _refresh_loop(self, interval: int):
        while True:
            await asyncio.sleep(interval)
            try:
                await self._refresh()
                logger.debug(f"Lane config auto-refreshed ({len(self._cache)} lanes)")
            except Exception as e:
                logger.error(f"Lane config refresh failed: {e}")


# ── Module-level singleton ──────────────────────────────────────────────────
lane_cache = LaneConfigCache()
