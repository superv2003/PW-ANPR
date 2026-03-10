"""
Configuration loader for PARKWIZ ANPR Service.

Reads config.ini from the project root and exposes a typed Settings object.
Every tunable parameter lives here — nothing is hardcoded in application code.
"""

import os
import configparser
from dataclasses import dataclass, field
from pathlib import Path

# Resolve config.ini path relative to THIS file → parkwiz_anpr/core/config.py
# Project root is two levels up: PW-ANPR/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_PATH = _PROJECT_ROOT / "config.ini"


@dataclass
class ServiceSettings:
    host: str = "0.0.0.0"
    port: int = 8765
    log_level: str = "INFO"
    workers: int = 1
    api_key: str = ""  # empty = no auth enforced


@dataclass
class DatabaseSettings:
    server: str = "localhost"
    database: str = "PARKWIZ"
    trusted_connection: bool = False
    username: str = "sa"
    password: str = ""
    driver: str = "ODBC Driver 17 for SQL Server"
    pool_size: int = 5

    @property
    def connection_string(self) -> str:
        base = f"DRIVER={{{self.driver}}};SERVER={self.server};DATABASE={self.database};"
        if self.trusted_connection:
            base += "Trusted_Connection=yes;"
        else:
            base += f"UID={self.username};PWD={self.password};"
        return base


@dataclass
class CameraSettings:
    rtsp_username: str = "admin"
    rtsp_password: str = "intozi@123"
    rtsp_port: int = 554
    rtsp_path: str = "/Streaming/Channels/101"


@dataclass
class StorageSettings:
    image_dir: str = str(_PROJECT_ROOT / "plate_images")
    log_dir: str = str(_PROJECT_ROOT / "logs")
    retention_days: int = 30


@dataclass
class PerformanceSettings:
    max_workers: int = 0  # 0 = auto-detect (cpu_count // 2)
    request_timeout_sec: float = 5.0
    camera_connect_timeout_sec: float = 3.0
    lane_cache_refresh_sec: int = 60
    per_lane_concurrency: int = 2  # max simultaneous requests per lane


@dataclass
class PollingSettings:
    enabled: bool = False
    lanes: str = ""  # Comma-separated list of lanes to poll (e.g., "26,27")
    interval_ms: int = 500


@dataclass
class Settings:
    service: ServiceSettings = field(default_factory=ServiceSettings)
    database: DatabaseSettings = field(default_factory=DatabaseSettings)
    camera: CameraSettings = field(default_factory=CameraSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    performance: PerformanceSettings = field(default_factory=PerformanceSettings)
    polling: PollingSettings = field(default_factory=PollingSettings)

    def __repr__(self) -> str:
        """Masks sensitive fields in logs."""
        return (
            f"Settings(service={self.service}, "
            f"database=<server={self.database.server}, db={self.database.database}>, "
            f"camera=<user={self.camera.rtsp_username}>, "
            f"storage=<image_dir={self.storage.image_dir}>, "
            f"performance={self.performance})"
        )


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in ("yes", "true", "1", "on")


def load_settings(config_path: str | Path | None = None) -> Settings:
    """Load settings from config.ini. Missing keys fall back to defaults."""
    path = Path(config_path) if config_path else _CONFIG_PATH
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))

    settings = Settings()

    if not path.exists():
        import logging
        logging.getLogger(__name__).warning(
            f"Config file not found at {path}, using defaults."
        )
        return settings

    cfg.read(str(path), encoding="utf-8")

    # --- [service] ---
    if cfg.has_section("service"):
        s = cfg["service"]
        settings.service.host = s.get("host", settings.service.host)
        settings.service.port = int(s.get("port", str(settings.service.port)))
        settings.service.log_level = s.get("log_level", settings.service.log_level).upper()
        settings.service.workers = int(s.get("workers", str(settings.service.workers)))
        settings.service.api_key = s.get("api_key", settings.service.api_key)

    # --- [database] ---
    if cfg.has_section("database"):
        d = cfg["database"]
        settings.database.server = d.get("server", settings.database.server)
        settings.database.database = d.get("database", settings.database.database)
        settings.database.trusted_connection = _parse_bool(
            d.get("trusted_connection", "no")
        )
        settings.database.username = d.get("username", settings.database.username)
        settings.database.password = d.get("password", settings.database.password)
        settings.database.driver = d.get("driver", settings.database.driver)
        settings.database.pool_size = int(d.get("pool_size", str(settings.database.pool_size)))

    # --- [camera] ---
    if cfg.has_section("camera"):
        c = cfg["camera"]
        settings.camera.rtsp_username = c.get("rtsp_username", settings.camera.rtsp_username)
        settings.camera.rtsp_password = c.get("rtsp_password", settings.camera.rtsp_password)
        settings.camera.rtsp_port = int(c.get("rtsp_port", str(settings.camera.rtsp_port)))
        settings.camera.rtsp_path = c.get("rtsp_path", settings.camera.rtsp_path)

    # --- [storage] ---
    if cfg.has_section("storage"):
        st = cfg["storage"]
        settings.storage.image_dir = st.get("image_dir", settings.storage.image_dir)
        settings.storage.log_dir = st.get("log_dir", settings.storage.log_dir)
        settings.storage.retention_days = int(
            st.get("retention_days", str(settings.storage.retention_days))
        )

    # --- [performance] ---
    if cfg.has_section("performance"):
        p = cfg["performance"]
        settings.performance.max_workers = int(
            p.get("max_workers", str(settings.performance.max_workers))
        )
        settings.performance.request_timeout_sec = float(
            p.get("request_timeout_sec", str(settings.performance.request_timeout_sec))
        )
        settings.performance.camera_connect_timeout_sec = float(
            p.get("camera_connect_timeout_sec", str(settings.performance.camera_connect_timeout_sec))
        )
        settings.performance.lane_cache_refresh_sec = int(
            p.get("lane_cache_refresh_sec", str(settings.performance.lane_cache_refresh_sec))
        )
        settings.performance.per_lane_concurrency = int(
            p.get("per_lane_concurrency", str(settings.performance.per_lane_concurrency))
        )

    # --- [polling] ---
    if cfg.has_section("polling"):
        pl = cfg["polling"]
        settings.polling.enabled = _parse_bool(pl.get("enabled", "no"))
        settings.polling.lanes = pl.get("lanes", settings.polling.lanes)
        settings.polling.interval_ms = int(pl.get("interval_ms", str(settings.polling.interval_ms)))

    # Auto-detect workers if set to 0
    if settings.performance.max_workers <= 0:
        settings.performance.max_workers = max(2, (os.cpu_count() or 4) // 2)

    return settings


# ── Module-level singleton ──────────────────────────────────────────────────
settings = load_settings()
