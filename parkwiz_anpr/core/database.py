"""
MSSQL database layer for PARKWIZ ANPR Service.

Uses pyodbc with a simple connection-pool pattern.  Every public method is
async-safe by running the blocking pyodbc calls inside the default
ThreadPoolExecutor.
"""

import asyncio
import logging
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from queue import Queue, Empty

import pyodbc

from .config import settings

logger = logging.getLogger(__name__)

# ─── Connection Pool ────────────────────────────────────────────────────────


class _ConnectionPool:
    """
    Minimal fixed-size pyodbc connection pool.

    pyodbc connections are NOT thread-safe, so the pool hands out one
    connection per thread and puts it back after use.
    """

    def __init__(self, conn_str: str, size: int = 5):
        self._conn_str = conn_str
        self._size = size
        self._pool: Queue[pyodbc.Connection] = Queue(maxsize=size)
        self._lock = threading.Lock()
        self._created = 0

    # ── lifecycle ──

    def initialize(self) -> bool:
        """Create one test connection at startup. Returns True if DB is reachable."""
        try:
            conn = pyodbc.connect(self._conn_str, timeout=5)
            conn.close()
            logger.info("Database connection verified ✅")
            return True
        except Exception as e:
            logger.critical(f"Database unreachable at startup: {e}")
            return False

    def close_all(self):
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                conn.close()
            except Exception:
                pass
        self._created = 0

    # ── acquire / release ──

    def _make_connection(self) -> pyodbc.Connection:
        return pyodbc.connect(self._conn_str, timeout=5, autocommit=True)

    @contextmanager
    def get_connection(self):
        """Thread-safe context manager that borrows a connection from the pool."""
        conn = None
        try:
            conn = self._pool.get_nowait()
        except Empty:
            with self._lock:
                if self._created < self._size:
                    conn = self._make_connection()
                    self._created += 1
            if conn is None:
                # Pool exhausted — block up to 5 s
                conn = self._pool.get(timeout=5)

        try:
            yield conn
        except pyodbc.Error as e:
            # Connection may be stale — discard and create fresh on next request
            logger.warning(f"DB connection error, discarding: {e}")
            try:
                conn.close()
            except Exception:
                pass
            with self._lock:
                self._created -= 1
            conn = None  # prevent double return
            raise  # Re-raise so caller knows the query failed
        finally:
            if conn is not None:
                try:
                    self._pool.put_nowait(conn)
                except Exception:
                    conn.close()


# ── Module-level pool singleton ─────────────────────────────────────────────

_pool: _ConnectionPool | None = None


def get_pool() -> _ConnectionPool:
    global _pool
    if _pool is None:
        _pool = _ConnectionPool(
            settings.database.connection_string,
            size=settings.database.pool_size,
        )
    return _pool


# ─── Public async helpers ───────────────────────────────────────────────────


async def initialize() -> bool:
    """Call during startup. Returns True if DB is reachable."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_pool().initialize)


async def close():
    """Drain pool on shutdown."""
    if _pool:
        _pool.close_all()


async def check_connection() -> bool:
    """Quick liveness probe for /health."""
    def _probe():
        try:
            with get_pool().get_connection() as conn:
                conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    return await asyncio.get_running_loop().run_in_executor(None, _probe)


async def log_capture(
    lane_number: str,
    org_id: str,
    camera_ip: str | None,
    plate: str | None,
    raw_ocr: str | None,
    confidence: float,
    detection_method: str | None,
    processing_ms: int,
    error_code: str | None,
    image_path: str | None,
    request_id: str,
) -> None:
    """Insert a row into tblANPRCaptureLog.  Fire-and-forget safe."""

    def _insert():
        sql = """
        INSERT INTO tblANPRCaptureLog
            (PMSLaneNumber, ANPROrgID, CameraIP, PlateDetected, RawOCRText,
             Confidence, DetectionMethod, ProcessingMs, ErrorCode, ImagePath,
             CapturedAt, RequestID)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, GETUTCDATE(), ?)
        """
        try:
            with get_pool().get_connection() as conn:
                conn.execute(
                    sql,
                    lane_number,
                    org_id,
                    camera_ip,
                    plate,
                    raw_ocr,
                    confidence,
                    detection_method,
                    processing_ms,
                    error_code,
                    image_path,
                    request_id,
                )
        except Exception as e:
            logger.error(f"[req:{request_id}] Failed to log capture to DB: {e}")

    await asyncio.get_running_loop().run_in_executor(None, _insert)


async def fetch_recent_logs(
    lane_number: str | None = None,
    plate_search: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Query tblANPRCaptureLog for the admin/logs endpoint."""

    def _query():
        clauses = []
        params = []
        if lane_number:
            clauses.append("PMSLaneNumber = ?")
            params.append(lane_number)
        if plate_search:
            clauses.append("PlateDetected LIKE ?")
            params.append(f"%{plate_search}%")

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
        SELECT TOP (?) LogID, PMSLaneNumber, ANPROrgID, CameraIP,
               PlateDetected, RawOCRText, Confidence, DetectionMethod,
               ProcessingMs, ErrorCode, ImagePath, CapturedAt, RequestID
        FROM tblANPRCaptureLog
        {where}
        ORDER BY CapturedAt DESC
        """
        try:
            with get_pool().get_connection() as conn:
                cursor = conn.execute(sql, limit, *params)
                columns = [col[0] for col in cursor.description]
                rows = cursor.fetchall()
                return [dict(zip(columns, row)) for row in rows]
        except Exception as e:
            logger.error(f"Failed to fetch logs: {e}")
            return []

    return await asyncio.get_running_loop().run_in_executor(None, _query)


async def fetch_stats_today() -> dict:
    """Aggregate stats for the /stats endpoint."""

    def _query():
        sql = """
        SELECT
            COUNT(*) AS total_captures,
            SUM(CASE WHEN PlateDetected IS NOT NULL AND ErrorCode IS NULL THEN 1 ELSE 0 END) AS successful,
            AVG(ProcessingMs) AS avg_processing_ms,
            PMSLaneNumber
        FROM tblANPRCaptureLog
        WHERE CAST(CapturedAt AS DATE) = CAST(GETUTCDATE() AS DATE)
        GROUP BY PMSLaneNumber
        """
        try:
            with get_pool().get_connection() as conn:
                cursor = conn.execute(sql)
                columns = [col[0] for col in cursor.description]
                rows = cursor.fetchall()
                return [dict(zip(columns, row)) for row in rows]
        except Exception as e:
            logger.error(f"Failed to fetch stats: {e}")
            return []

    return await asyncio.get_running_loop().run_in_executor(None, _query)


async def fetch_lane_configs() -> list[dict]:
    """Load all rows from tblLaneANPRConfiguration (READ ONLY)."""

    def _query():
        sql = """
        SELECT ANPRID, PMSLaneNumber, flgEnableANPR, ANPROrgID, ANPRLaneID,
               ANPRPublicKey, ANPRPrivateKey, ANPRSource,
               ANPRAPIURL, ANPRAPIURL2, ActiveStatus
        FROM tblLaneANPRConfiguration
        """
        try:
            with get_pool().get_connection() as conn:
                cursor = conn.execute(sql)
                columns = [col[0] for col in cursor.description]
                rows = cursor.fetchall()
                return [dict(zip(columns, row)) for row in rows]
        except Exception as e:
            logger.error(f"Failed to load lane configs: {e}")
            return []

    return await asyncio.get_running_loop().run_in_executor(None, _query)
