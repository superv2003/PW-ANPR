"""
Plate image storage with date-partitioned directories and retention cleanup.

Images are saved for audit purposes.  A background task cleans up images
older than the configured retention period.
"""

import asyncio
import logging
import os
import shutil
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .config import settings

logger = logging.getLogger(__name__)


class ImageStore:
    """Saves and manages captured plate images."""

    def __init__(self):
        self._base_dir = Path(settings.storage.image_dir)
        self._retention_days = settings.storage.retention_days
        self._cleanup_task: asyncio.Task | None = None

    # ── Public API ──────────────────────────────────────────────────────

    async def save_image(
        self,
        image_bytes: bytes | None,
        org_id: str,
        lane_number: str,
        plate: str | None,
        request_id: str,
    ) -> str | None:
        """
        Save a JPEG image to the date-partitioned directory.
        Returns the relative path for DB logging, or None if no image.
        """
        if not image_bytes:
            return None

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self._save_sync,
            image_bytes,
            org_id,
            lane_number,
            plate,
            request_id,
        )

    def start_cleanup_task(self):
        """Start the background retention cleanup (runs once per hour)."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info(
            f"Image cleanup task started (retention={self._retention_days} days)"
        )

    async def stop(self):
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

    # ── Internals ───────────────────────────────────────────────────────

    def _save_sync(
        self,
        image_bytes: bytes,
        org_id: str,
        lane_number: str,
        plate: str | None,
        request_id: str,
    ) -> str | None:
        try:
            now = datetime.now(timezone.utc)
            date_dir = self._base_dir / org_id / now.strftime("%Y") / now.strftime("%m") / now.strftime("%d")
            date_dir.mkdir(parents=True, exist_ok=True)

            plate_tag = plate if plate else "NOPLATE"
            ts = now.strftime("%Y%m%d_%H%M%S")
            short_id = request_id[:8]
            filename = f"LANE{lane_number}_{plate_tag}_{ts}_{short_id}.jpg"

            filepath = date_dir / filename
            filepath.write_bytes(image_bytes)

            # Return path relative to base_dir for DB storage
            return str(filepath.relative_to(self._base_dir))
        except Exception as e:
            logger.error(f"[req:{request_id}] Failed to save image: {e}")
            return None

    async def _cleanup_loop(self):
        """Delete date-partitioned directories older than retention_days."""
        while True:
            # Run cleanup once per hour
            await asyncio.sleep(3600)
            try:
                await asyncio.get_running_loop().run_in_executor(
                    None, self._run_cleanup
                )
            except Exception as e:
                logger.error(f"Image cleanup error: {e}")

    def _run_cleanup(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        removed = 0

        if not self._base_dir.exists():
            return

        # Walk org_id / YYYY / MM / DD structure
        for org_dir in self._base_dir.iterdir():
            if not org_dir.is_dir():
                continue
            for year_dir in org_dir.iterdir():
                if not year_dir.is_dir():
                    continue
                for month_dir in year_dir.iterdir():
                    if not month_dir.is_dir():
                        continue
                    for day_dir in month_dir.iterdir():
                        if not day_dir.is_dir():
                            continue
                        try:
                            dir_date = datetime(
                                int(year_dir.name),
                                int(month_dir.name),
                                int(day_dir.name),
                                tzinfo=timezone.utc,
                            )
                            if dir_date < cutoff:
                                shutil.rmtree(day_dir)
                                removed += 1
                        except (ValueError, OSError):
                            continue

        if removed:
            logger.info(f"Image cleanup: removed {removed} day directories older than {self._retention_days} days")


# ── Module-level singleton ──────────────────────────────────────────────────
image_store = ImageStore()
