import cv2
import time
import threading
import logging
import os
import urllib.parse
import requests
from requests.auth import HTTPDigestAuth
from typing import Dict, Optional
import numpy as np
from .config import FRAMES_TO_GRAB

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
        
        self.cap = cv2.VideoCapture()
        # Force strict RTSP over TCP via explicit dictionary parameters to bypass OS env issues
        # Enforce 3-second timeout instead of 30-second default
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
        params = [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 3000,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000,
            cv2.CAP_PROP_BUFFERSIZE, 1
        ]
        self.cap.open(self.rtsp_url, cv2.CAP_FFMPEG, params)
        
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
                
                self.cap = cv2.VideoCapture()
                params = [
                    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 3000,
                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000,
                    cv2.CAP_PROP_BUFFERSIZE, 1
                ]
                self.cap.open(self.rtsp_url, cv2.CAP_FFMPEG, params)
                continue
                
            # Calling read() natively pulls from the 1-frame FFmpeg buffer queue.
            # This blocks until a frame arrives, effectively sleeping the thread naturally.
            ret, frame = self.cap.read()
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

class HttpSnapshotGrabber:
    """
    Stateless HTTP Snapshot Grabber (The "Hikvision" Architecture).
    Directly hits the camera's CGI/ISAPI endpoint to pull the absolute latest
    hardware frame instantly on demand.
    """
    def __init__(self, cam_id: str, http_url: str):
        self.cam_id = cam_id
        
        # Parse HTTP URL to extract embedded credentials if they exist
        parsed = urllib.parse.urlparse(http_url)
        self.username = urllib.parse.unquote(parsed.username) if parsed.username else ""
        self.password = urllib.parse.unquote(parsed.password) if parsed.password else ""
        self.ip = parsed.hostname
        self.port = f":{parsed.port}" if parsed.port else ""
        
        # Reconstruct the clean URL without creds for requests
        self.snapshot_url = f"{parsed.scheme}://{self.ip}{self.port}{parsed.path}"
        if parsed.query: self.snapshot_url += f"?{parsed.query}"
        
        logger.info(f"[{self.cam_id}] Initializing HTTP Snapshot Grabber for {self.snapshot_url}...")
        
        self.session = requests.Session()
        # Default to Digest auth (standard for Hikvision ISAPI / Dahua CGI)
        if self.username and self.password:
            self.session.auth = HTTPDigestAuth(self.username, self.password)

    def capture_burst(self, num_frames=3) -> tuple[Optional[list], Optional[str]]:
        """Fetch multiple frames instantly to catch the best focus."""
        frames = []
        
        for _ in range(num_frames):
            try:
                resp = self.session.get(self.snapshot_url, timeout=2.0)
                
                # If Digest Auth failed, automatically try Basic Auth
                if resp.status_code == 401 and isinstance(self.session.auth, HTTPDigestAuth):
                    logger.warning(f"[{self.cam_id}] Digest Auth failed, falling back to Basic Auth.")
                    self.session.auth = (self.username, self.password)
                    resp = self.session.get(self.snapshot_url, timeout=2.0)
                
                if resp.status_code == 200:
                    img_array = np.frombuffer(resp.content, dtype=np.uint8)
                    frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
                    if frame is not None:
                        frames.append(frame)
                else:
                    logger.error(f"[{self.cam_id}] Snapshot failed with status {resp.status_code}")
                    
            except requests.exceptions.Timeout:
                logger.error(f"[{self.cam_id}] Snapshot request TCP timeout.")
            except Exception as e:
                logger.error(f"[{self.cam_id}] Snapshot fetch failed: {e}")
                
            # Allow the camera hardware to generate the next frame
            time.sleep(0.1)

        if not frames:
            return None, "CAMERA_TIMEOUT"
            
        return frames, None


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
        for cam_id, _ in FrameGrabber._http_grabbers.items():
            result[cam_id] = {
                "alive": True,
                "last_frame_age_ms": 0, # HTTP is always perfectly live
            }
        return result
        
    def add_camera(self, cam_id: str, url: str, wait_ready: bool = True):
        pass # Ignored, FrameGrabber handles it


class FrameGrabber:
    """
    Manages active CameraShutter and HttpSnapshotGrabber instances silently.
    Routes the request dynamically based on the URL protocol.
    """
    _shutters: Dict[str, CameraShutter] = {}
    _http_grabbers: Dict[str, HttpSnapshotGrabber] = {}
    _reg_lock = threading.Lock()
    _url_to_id: Dict[str, str] = {} # Fix for pipeline startup pre-connect

    @classmethod
    def _ensure_active(cls, rtsp_url: str) -> CameraShutter:
        cam_id = f"cam_{abs(hash(rtsp_url)) % 100000:05d}"
        if cam_id not in cls._shutters:
            with cls._reg_lock:
                if cam_id not in cls._shutters:
                    cls._shutters[cam_id] = CameraShutter(cam_id, rtsp_url)
                    
                    # Optional: wait up to 10s for the very first frame to arrive on boot
                    # This prevents NO_FRAME_YET on the very first API call when the thread is just starting
                    deadline = time.time() + 10.0
                    while time.time() < deadline:
                        with cls._shutters[cam_id].lock:
                            if cls._shutters[cam_id].latest_frame is not None:
                                break
                        time.sleep(0.02)
                        
        return cls._shutters[cam_id]

    @classmethod
    def _ensure_http_active(cls, http_url: str) -> HttpSnapshotGrabber:
        cam_id = f"cam_{abs(hash(http_url)) % 100000:05d}"
        if cam_id not in cls._http_grabbers:
            with cls._reg_lock:
                if cam_id not in cls._http_grabbers:
                    cls._http_grabbers[cam_id] = HttpSnapshotGrabber(cam_id, http_url)
        return cls._http_grabbers[cam_id]

    @classmethod
    def get_frames(cls, camera_url: str) -> tuple[Optional[list], Optional[str]]:
        if camera_url.startswith("http://") or camera_url.startswith("https://"):
            grabber = cls._ensure_http_active(camera_url)
            # Capture rapid frames as defined in config, pipeline.py will pick the clearest plate
            frames, error = grabber.capture_burst(num_frames=FRAMES_TO_GRAB)
            return frames, error
        else:
            shutter = cls._ensure_active(camera_url)
            frame, error = shutter.capture()
            if error:
                return None, error
            return [frame], None
