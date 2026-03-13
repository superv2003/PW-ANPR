import cv2
import time
import threading
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, Optional
import numpy as np

logger = logging.getLogger(__name__)

# Force TCP globally — eliminates UDP handshake delays
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"


@dataclass
class _CameraState:
    """Internal state per camera. Holds the latest frame and health info."""
    frame: Optional[np.ndarray] = None
    frame_time: float = 0.0
    is_alive: bool = False
    reconnect_count: int = 0
    url: str = ""


class CameraManager:
    """
    Persistent background RTSP reader for all cameras.

    Architecture
    ────────────
    • One daemon thread per camera continuously reads frames and stores the
      latest one in memory.
    • get_frame() returns the stored frame instantly (~1 ms) — no RTSP
      handshake on every API call.
    • Threads auto-reconnect with exponential back-off if a camera drops.
    • add_camera() is safe to call at any time (hot-plug support).

    Typical result
    ──────────────
    Before : grab_ms ≈ 5 700 ms  (open → handshake → keyframe wait → close)
    After  : grab_ms ≈ 1–5 ms   (memcopy of latest frame)
    """

    _instance: Optional["CameraManager"] = None
    _init_lock = threading.Lock()

    # ── singleton ────────────────────────────────────────────────────────────
    @classmethod
    def get(cls) -> "CameraManager":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._states: Dict[str, _CameraState] = {}
        self._locks: Dict[str, threading.Lock] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._manager_lock = threading.Lock()

    # ── public API ───────────────────────────────────────────────────────────

    def add_camera(self, cam_id: str, url: str, wait_ready: bool = True, ready_timeout: float = 15.0):
        """
        Register a camera and start its background reader thread.

        Parameters
        ----------
        cam_id        : Unique identifier, e.g. "cam_26" or lane number string.
        url           : Full RTSP URL (credentials already encoded).
        wait_ready    : Block until the first frame arrives (recommended at startup).
        ready_timeout : Max seconds to wait for the first frame.
        """
        with self._manager_lock:
            if cam_id in self._threads and self._threads[cam_id].is_alive():
                logger.info(f"[{cam_id}] Already running, skipping.")
                return

            state = _CameraState(url=url)
            self._states[cam_id] = state
            self._locks[cam_id] = threading.Lock()

            t = threading.Thread(
                target=self._reader_loop,
                args=(cam_id,),
                daemon=True,
                name=f"rtsp-{cam_id}",
            )
            self._threads[cam_id] = t
            t.start()
            logger.info(f"[{cam_id}] Reader thread started → {url}")

        if wait_ready:
            deadline = time.time() + ready_timeout
            while time.time() < deadline:
                if self._states[cam_id].frame is not None:
                    logger.info(f"[{cam_id}] ✅ First frame received.")
                    return
                time.sleep(0.05)
            logger.warning(f"[{cam_id}] ⚠️  No frame within {ready_timeout}s — camera may be offline.")

    def get_frame(self, cam_id: str) -> tuple[Optional[np.ndarray], Optional[str]]:
        """
        Return the latest frame for a camera.

        Returns
        -------
        (frame_bgr, None)          on success
        (None, "error_code")       on failure
        """
        if cam_id not in self._states:
            return None, "CAMERA_NOT_REGISTERED"

        with self._locks[cam_id]:
            state = self._states[cam_id]
            if state.frame is None:
                return None, "NO_FRAME_YET"

            # Stale frame guard — if last frame is older than 10 s, camera is likely dead
            age_sec = time.time() - state.frame_time
            if age_sec > 10.0:
                return None, "CAMERA_STALE_FRAME"

            return state.frame.copy(), None

    def health(self) -> dict:
        """
        Return a status snapshot for all registered cameras.
        Useful for a /health or /status API endpoint.

        Example output
        --------------
        {
            "cam_26": {"alive": true,  "last_frame_age_ms": 45,  "reconnects": 0},
            "cam_27": {"alive": false, "last_frame_age_ms": 9800, "reconnects": 3}
        }
        """
        result = {}
        for cam_id, state in self._states.items():
            age_ms = round((time.time() - state.frame_time) * 1000) if state.frame_time else -1
            result[cam_id] = {
                "alive": state.is_alive,
                "last_frame_age_ms": age_ms,
                "reconnects": state.reconnect_count,
            }
        return result

    def remove_camera(self, cam_id: str):
        """
        Stop tracking a camera. The daemon thread will exit on its next loop.
        """
        with self._manager_lock:
            if cam_id in self._states:
                self._states[cam_id].url = ""   # signal thread to exit
                del self._states[cam_id]
                del self._locks[cam_id]
                logger.info(f"[{cam_id}] Removed from manager.")

    # ── internal reader loop ─────────────────────────────────────────────────

    def _reader_loop(self, cam_id: str):
        """
        Runs forever in a daemon thread.
        Opens the RTSP stream once, reads frames continuously, reconnects on failure.
        """
        while True:
            # Exit signal: camera was removed
            if cam_id not in self._states or not self._states[cam_id].url:
                logger.info(f"[{cam_id}] Thread exiting (camera removed).")
                return

            state = self._states[cam_id]
            url = state.url

            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # keep only the freshest frame in buffer

            if not cap.isOpened():
                state.is_alive = False
                state.reconnect_count += 1
                wait = min(30, 2 ** min(state.reconnect_count, 5))  # exponential back-off, cap 30 s
                logger.warning(f"[{cam_id}] Cannot open stream. Retry #{state.reconnect_count} in {wait}s")
                cap.release()
                time.sleep(wait)
                continue

            # Stream opened successfully
            state.is_alive = True
            state.reconnect_count = 0
            logger.info(f"[{cam_id}] Stream connected ✅")

            while True:
                ret, frame = cap.read()

                if not ret:
                    state.is_alive = False
                    logger.warning(f"[{cam_id}] Frame read failed — reconnecting…")
                    break  # break inner loop → reconnect

                with self._locks[cam_id]:
                    state.frame = frame
                    state.frame_time = time.time()

            cap.release()


# ── Backwards-compatible FrameGrabber ────────────────────────────────────────
# Keeps existing pipeline.py / any other caller working with zero changes.
# Internally delegates to CameraManager instead of opening a new connection.

class FrameGrabber:
    """
    Drop-in replacement for the original on-demand FrameGrabber.

    The first call for a given URL automatically registers the camera with
    CameraManager and waits for the first frame.  Subsequent calls return
    the cached frame in ~1 ms.
    """

    # Map rtsp_url → cam_id so we can look up frames
    _url_to_id: Dict[str, str] = {}
    _reg_lock = threading.Lock()

    @classmethod
    def _ensure_registered(cls, rtsp_url: str) -> str:
        """Auto-register a camera the first time we see its URL."""
        if rtsp_url not in cls._url_to_id:
            with cls._reg_lock:
                if rtsp_url not in cls._url_to_id:
                    # Use a short hash of the URL as the cam_id
                    cam_id = f"cam_{abs(hash(rtsp_url)) % 100000:05d}"
                    cls._url_to_id[rtsp_url] = cam_id
                    CameraManager.get().add_camera(cam_id, rtsp_url, wait_ready=True)
        return cls._url_to_id[rtsp_url]

    @classmethod
    def get_frames(cls, rtsp_url: str) -> tuple[Optional[list], Optional[str]]:
        """
        Public interface — identical signature to the original FrameGrabber.
        Returns: (list_of_frames, error_string_or_None)
        """
        cam_id = cls._ensure_registered(rtsp_url)
        frame, error = CameraManager.get().get_frame(cam_id)

        if error:
            return None, error

        # Wrap in a list to match the original interface (FRAMES_TO_GRAB frames)
        return [frame], None
