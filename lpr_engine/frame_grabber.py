import cv2
import time
import threading
import logging
import os
from typing import Dict, Optional
import numpy as np

logger = logging.getLogger(__name__)

# Force TCP globally — eliminates UDP handshake delays
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
# Suppress FFmpeg H264 decoding spam (e.g. error while decoding MB)
os.environ["OPENCV_LOG_LEVEL"] = "ERROR"
os.environ["OPENCV_VIDEOIO_DEBUG"] = "0"
os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"  # AV_LOG_QUIET

class CameraShutter:
    """
    Background Camera Reader (The "Beast" Architecture)
    One thread per camera that always reads and overwrites a single frame buffer.
    When the main thread calls capture(), it instantly snaps the latest fully decoded frame.
    Zero queueing, zero buffering, always perfectly synced to real-time.
    """
    def __init__(self, cam_id: str, rtsp_url: str):
        self.cam_id = cam_id
        self.rtsp_url = rtsp_url
        self.latest_frame = None
        self.frame_time = 0.0
        self.lock = threading.Lock()
        
        logger.info(f"[{self.cam_id}] Connecting strictly to RTSP stream...")
        self.cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
        # KEY: Tell OpenCV to limit its internal buffer to precisely 1 frame
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        
        self.running = True
        self.thread = threading.Thread(
            target=self._keep_fresh, 
            daemon=True,
            name=f"shutter-{self.cam_id}"
        )
        self.thread.start()

    def _keep_fresh(self):
        """Dedicated thread executing a tight read cycle."""
        while self.running:
            if not self.cap.isOpened():
                time.sleep(2)  # Back-off before reconnecting
                logger.info(f"[{self.cam_id}] Reconnecting stream...")
                self.cap.release()
                self.cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
                self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                continue
                
            # Calling read() continuously is sometimes not enough for FFmpeg over TCP.
            # We must aggressively drain the buffer using grab() which is extremely fast.
            # Then we only retrieve() the final frame in the buffer.
            ret = self.cap.grab()
            if ret:
                # Discard buffered frames as fast as possible if they piled up
                # Note: OpenCV's internal queue on TCP can still grow, this drains it.
                while True:
                    has_next = self.cap.grab()
                    if not has_next:
                        break
                        
                ret, frame = self.cap.retrieve()
                if ret:
                    with self.lock:
                        self.latest_frame = frame  # Always overwrite instantly
                        self.frame_time = time.time()
            else:
                logger.warning(f"[{self.cam_id}] Frame read failed. Forcing reconnect.")
                self.cap.release()

    def capture(self) -> tuple[Optional[np.ndarray], Optional[str]]:
        """Call this when AR signal fires — instant, always fresh"""
        with self.lock:
            if self.latest_frame is None:
                return None, "NO_FRAME_YET"
            
            # Ensure the frame isn't terribly frozen from a dead stream
            age = time.time() - self.frame_time
            if age > 10.0:
                return None, "CAMERA_STALE_FRAME"
                
            return self.latest_frame.copy(), None

    def stop(self):
        """Cleanup."""
        self.running = False
        if self.cap:
            self.cap.release()


# ── Backwards-compatible FrameGrabber ────────────────────────────────────────
# Keeps existing pipeline.py working natively.

class CameraManager:
    """Mock CameraManager just to satisfy pipeline.py camera_health()."""
    @classmethod
    def get(cls):
        return cls()
    
    def health(self) -> dict:
        result = {}
        for cam_id, shutter in FrameGrabber._shutters.items():
            age_ms = round((time.time() - shutter.frame_time) * 1000) if shutter.frame_time else -1
            result[cam_id] = {
                "alive": age_ms > 0 and age_ms < 10000,
                "last_frame_age_ms": age_ms,
            }
        return result
        
    def add_camera(self, cam_id: str, url: str, wait_ready: bool = True):
        pass # Ignored, FrameGrabber handles it


class FrameGrabber:
    """
    Manages active CameraShutter instances silently.
    """
    _shutters: Dict[str, CameraShutter] = {}
    _reg_lock = threading.Lock()

    @classmethod
    def _ensure_active(cls, rtsp_url: str) -> CameraShutter:
        cam_id = f"cam_{abs(hash(rtsp_url)) % 100000:05d}"
        if cam_id not in cls._shutters:
            with cls._reg_lock:
                if cam_id not in cls._shutters:
                    cls._shutters[cam_id] = CameraShutter(cam_id, rtsp_url)
                    
                    # Optional: wait up to 10s for the very first frame to arrive on boot
                    deadline = time.time() + 10.0
                    while time.time() < deadline:
                        if cls._shutters[cam_id].latest_frame is not None:
                            break
                        time.sleep(0.05)
                        
        return cls._shutters[cam_id]

    @classmethod
    def get_frames(cls, rtsp_url: str) -> tuple[Optional[list], Optional[str]]:
        shutter = cls._ensure_active(rtsp_url)
        frame, error = shutter.capture()

        if error:
            return None, error

        return [frame], None
