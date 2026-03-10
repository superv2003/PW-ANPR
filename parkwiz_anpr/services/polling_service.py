import asyncio
import logging
import uuid
from datetime import datetime, timezone

from parkwiz_anpr.core.config import settings
from parkwiz_anpr.core import database
from parkwiz_anpr.services.capture_service import process_capture

logger = logging.getLogger(__name__)


class DBPollingService:
    """
    Background service that polls tblHDDReadWrite for 'AR' (Arm Request) signals.
    
    This allows testing the ANPR pipeline on a live site without modifying the 
    existing Parking Management System software to make REST API calls. 
    It acts as a 'shadow' trigger.
    """

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._last_processed_time: dict[str, datetime] = {}

    async def start(self):
        """Start the background polling loop if enabled in config."""
        if not settings.polling.enabled:
            logger.info("DB Polling is disabled via config.ini")
            return

        # Parse lanes configured for polling
        lanes = [l.strip() for l in settings.polling.lanes.split(",") if l.strip()]
        if not lanes:
            logger.warning("DB Polling is enabled but no lanes are configured in config.ini -> [polling] lanes")
            return

        interval_ms = settings.polling.interval_ms
        logger.info(f"Starting DB Polling Service for lanes: {lanes} (every {interval_ms}ms)")
        self._task = asyncio.create_task(self._poll_loop(lanes, interval_ms / 1000.0))

    async def stop(self):
        """Stop the background polling loop."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            logger.info("DB Polling Service stopped.")

    async def _poll_loop(self, lanes: list[str], interval_sec: float):
        """Infinite loop checking the database for new triggers."""
        # Optional: fetch initial UpdateDateTime so we don't trigger on old records
        # when the service first starts up.
        await self._prime_last_processed_times(lanes)

        while True:
            try:
                await self._check_lanes(lanes)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in DB polling loop: {e}")
            
            await asyncio.sleep(interval_sec)

    async def _prime_last_processed_times(self, lanes: list[str]):
        """Fetch the current state of the lanes so we safely ignore past AR events."""
        rows = await database.fetch_hdd_read_write(lanes)
        for row in rows:
            lane_number = str(row.get("HDDID", ""))
            update_dt = row.get("UpdateDateTime")
            if lane_number and update_dt:
                self._last_processed_time[lane_number] = update_dt

    async def _check_lanes(self, lanes: list[str]):
        """Query the database and trigger the pipeline for any new AR signals."""
        rows = await database.fetch_hdd_read_write(lanes)
        
        for row in rows:
            lane_number = str(row.get("HDDID", ""))
            sDataRequest = str(row.get("sDataRequest", "")).strip().upper()
            update_dt = row.get("UpdateDateTime")

            if not lane_number or not update_dt:
                continue

            if sDataRequest == "AR":
                last_dt = self._last_processed_time.get(lane_number)
                
                # Check if this is a genuinely new Arm Request
                if last_dt is None or update_dt > last_dt:
                    self._last_processed_time[lane_number] = update_dt
                    
                    req_id = f"poll-{uuid.uuid4().hex[:6]}"
                    logger.info(
                        f"[POLL] Detected 'AR' signal for lane {lane_number}. "
                        f"Triggering shadow capture {req_id}"
                    )
                    
                    # Fire-and-forget the capture process
                    asyncio.create_task(
                        process_capture(
                            lane_number=lane_number, 
                            org_id="PARKWIZ", 
                            request_id=req_id
                        )
                    )

# Singleton
polling_service = DBPollingService()
