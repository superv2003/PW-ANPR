import time
import asyncio
import urllib.parse
import logging
import os
import cv2
import requests
from requests.auth import HTTPDigestAuth
from concurrent.futures import ThreadPoolExecutor

from .frame_grabber import FrameGrabber, CameraManager
from .preprocessor import Preprocessor
from .detector import PlateDetector
from .ocr_engine import OCRPool, OCRAgent
from .postprocessor import PostProcessor
from .config import DEBUG_SAVE_IMAGES, DEBUG_OUT_DIR

logger = logging.getLogger(__name__)

_MAX_WORKERS = min(10, (os.cpu_count() or 4) // 2)


class LPRPipeline:
    """
    Orchestrates the ANPR execution pipeline.

    Key change from v1
    ──────────────────
    FrameGrabber no longer opens a new RTSP connection on every request.
    CameraManager keeps one persistent TCP connection per camera alive in a
    background thread.  get_frames() now returns the cached latest frame in
    ~1 ms instead of ~5 700 ms.

    Startup flow
    ────────────
    1. Call LPRPipeline.initialize(camera_map) at application startup.
       camera_map = {"lane_id": "rtsp://...", ...}
    2. CameraManager connects to every camera once and keeps the streams open.
    3. Every /capture call hits _run_pipeline_sync which reads from memory.
    """

    _executor = None
    _initialized = False

    @classmethod
    def initialize(cls, camera_map: dict = None):
        """
        Pre-load models, allocate thread pools, and start all camera streams.

        Parameters
        ----------
        camera_map : dict mapping lane_id (str) → rtsp_url (str)
                     e.g. {"26": "rtsp://admin:intozi%40123@192.168.1.63:554/..."}
                     If None, cameras are lazily registered on first request.
        """
        if cls._initialized:
            return

        logger.info(f"Initializing LPR Pipeline with {_MAX_WORKERS} workers…")
        cls._executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS)

        # Pre-load ONNX YOLO detector
        PlateDetector.initialize()

        # Pre-allocate OCR worker pool
        OCRPool.initialize(pool_size=_MAX_WORKERS)

        if DEBUG_SAVE_IMAGES:
            os.makedirs(DEBUG_OUT_DIR, exist_ok=True)

        # ── Pre-connect all known cameras ──────────────────────────────────
        # This moves the ~5 s RTSP handshake to startup time, not request time.
        if camera_map:
            mgr = CameraManager.get()
            for lane_id, rtsp_url in camera_map.items():
                cam_id = f"cam_{lane_id}"
                logger.info(f"Pre-connecting camera lane={lane_id} → {rtsp_url}")
                mgr.add_camera(cam_id, rtsp_url, wait_ready=True)
                # Also register in FrameGrabber's url→id map so get_frames() works
                FrameGrabber._url_to_id[rtsp_url] = cam_id

        cls._initialized = True
        logger.info("LPR Pipeline ready ✅")

    @classmethod
    async def process(
        cls,
        camera_ip: str,
        rtsp_user: str = "admin",
        rtsp_pass: str = "Parkwiz@2022",
        rtsp_port: int = 554,
        rtsp_path: str = "/Streaming/Channels/101",
        lane_number: str = "01",
    ) -> dict:
        """
        Async FastAPI-friendly entrypoint.  Runs the CPU-bound pipeline in the
        thread pool so the event loop is never blocked.
        """
        if not cls._initialized:
            cls.initialize()

        start_time = time.perf_counter()

        # Dynamic Protocol Detection
        # If the user supplied an HTTP URL in the DB or config override, bypass RTSP
        if camera_ip.startswith("http://") or camera_ip.startswith("https://"):
            camera_url = camera_ip
        else:
            enc_user = urllib.parse.quote_plus(rtsp_user)
            enc_pass = urllib.parse.quote_plus(rtsp_pass)
            camera_url = f"rtsp://{enc_user}:{enc_pass}@{camera_ip}:{rtsp_port}{rtsp_path}"

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                cls._executor, cls._run_pipeline_sync, camera_url
            )
        except Exception as e:
            logger.error(f"Lane {lane_number} pipeline fatal error: {e}")
            result = {"plate": None, "error": "PIPELINE_ERROR", "confidence": 0}

        end_time = time.perf_counter()
        result["processing_ms"] = int((end_time - start_time) * 1000)
        return result

    @classmethod
    def _run_pipeline_sync(cls, camera_url: str) -> dict:
        """
        Synchronous pipeline — runs inside a worker thread.

        Step 1 — grab_ms   : ~1 ms  (memory copy from CameraManager or HTTP fetch)
        Step 2 — preprocess : ~18 ms
        Step 3 — detect     : ~23 ms
        Step 4 — ocr        : ~250 ms
        ─────────────────────────────
        Total               : ~300 ms  (was ~6 000 ms)
        """
        telemetry = {"grab_ms": 0, "preprocess_ms": 0, "detection_ms": 0, "ocr_ms": 0}

        # 1. Grab frame from in-memory cache (CameraManager background thread)
        t0 = time.perf_counter()
        frames, error = FrameGrabber.get_frames(camera_url)
        telemetry["grab_ms"] = int((time.perf_counter() - t0) * 1000)

        if error:
            return {"plate": None, "error": error, "confidence": 0, "telemetry": telemetry}

        frame_results = []
        timestamp = int(time.time() * 1000) if DEBUG_SAVE_IMAGES else None

        for i, frame in enumerate(frames):
            if DEBUG_SAVE_IMAGES:
                cv2.imwrite(
                    os.path.join(DEBUG_OUT_DIR, f"lane_trigger_{timestamp}_frame_{i}.jpg"),
                    frame,
                )

            # 1.5 Dynamic ROI Cropping
            # Slice away the top 35% (sky/wall/top timestamps)
            # Slice away the bottom 15% (Camera 01 watermark)
            # Slice away the outer 15% edges (sidewalls)
            h, w = frame.shape[:2]
            crop_y1 = int(h * 0.35)
            crop_y2 = int(h * 0.85) # Cut off bottom 15% to eliminate 'Camera 01'
            crop_x1 = int(w * 0.15)
            crop_x2 = int(w * 0.85)
            
            cropped_frame = frame[crop_y1:crop_y2, crop_x1:crop_x2]

            # 2. Preprocess
            t2 = time.perf_counter()
            processed = Preprocessor.process(cropped_frame)
            telemetry["preprocess_ms"] += int((time.perf_counter() - t2) * 1000)

            # 3. Detect plate region (Runs on the deeply cropped ROI for massive speedup)
            t4 = time.perf_counter()
            rois_info = PlateDetector.detect(cropped_frame, processed)
            telemetry["detection_ms"] += int((time.perf_counter() - t4) * 1000)

            if not rois_info:
                continue

            for roi_idx, (roi, det_conf, det_method) in enumerate(rois_info):
                if DEBUG_SAVE_IMAGES:
                    cv2.imwrite(
                        os.path.join(
                            DEBUG_OUT_DIR,
                            f"lane_trigger_{timestamp}_frame_{i}_roi_{roi_idx}.jpg",
                        ),
                        roi,
                    )

                # 4. OCR
                t6 = time.perf_counter()
                raw_text, ocr_conf, ocr_method = OCRAgent.read_plate(roi)
                telemetry["ocr_ms"] += int((time.perf_counter() - t6) * 1000)

                if raw_text:
                    frame_results.append(
                        {
                            "raw_text": raw_text,
                            "det_conf": det_conf,
                            "ocr_conf": ocr_conf,
                            "method": f"{det_method}+{ocr_method}",
                        }
                    )

        if not frame_results:
            return {
                "plate": None,
                "error": "NO_PLATE_DETECTED",
                "confidence": 0,
                "telemetry": telemetry,
            }

        # 5. Postprocess
        best_result = PostProcessor.process_frames(frame_results)
        best_result["telemetry"] = telemetry
        return best_result


# ── Health helper (add to your /health route) ────────────────────────────────
def camera_health() -> dict:
    """Call this from your FastAPI /health endpoint."""
    return CameraManager.get().health()


# Singleton alias for clean imports
pipeline = LPRPipeline()
