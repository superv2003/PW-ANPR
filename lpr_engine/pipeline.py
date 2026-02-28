import time
import asyncio
import logging
import os
import cv2
from concurrent.futures import ThreadPoolExecutor

from .frame_grabber import FrameGrabber
from .preprocessor import Preprocessor
from .detector import PlateDetector
from .ocr_engine import OCRPool, OCRAgent
from .postprocessor import PostProcessor
from .config import DEBUG_SAVE_IMAGES, DEBUG_OUT_DIR

logger = logging.getLogger(__name__)

# Determine worker count based on CPU cores, max out reasonably for the Dell Xeon servers
_MAX_WORKERS = min(10, (os.cpu_count() or 4) // 2)

class LPRPipeline:
    """
    Orchestrates the ANPR execution pipeline.
    Maintains a single robust ThreadPoolExecutor for processing triggers concurrently.
    """
    
    _executor = None
    _initialized = False
    
    @classmethod
    def initialize(cls):
        """Pre-load models and allocate thread pools at startup."""
        if cls._initialized:
            return
            
        logger.info(f"Initializing LPR Pipeline with {_MAX_WORKERS} workers...")
        cls._executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS)
        
        # Pre-load the ONNX YOLO detector into shared memory
        PlateDetector.initialize()
        
        # Pre-allocate OCR worker instances
        OCRPool.initialize(pool_size=_MAX_WORKERS)
        
        if DEBUG_SAVE_IMAGES:
            os.makedirs(DEBUG_OUT_DIR, exist_ok=True)
            
        cls._initialized = True

    @classmethod
    async def process(cls, camera_ip: str, rtsp_user: str = "admin", rtsp_pass: str = "Parkwiz@2022", lane_number: str = "01") -> dict:
        """
        Asynchronous FastAPI-friendly entrypoint to process a single lane trigger.
        Returns a structured dictionary representing the result or the error.
        """
        if not cls._initialized:
            cls.initialize()
            
        start_time = time.perf_counter()
        
        # Build strict RTSP URL per user requirements
        # e.g., rtsp://admin:Parkwiz@2022@192.168.1.152:554/Streaming/Channels/101
        rtsp_url = f"rtsp://{rtsp_user}:{rtsp_pass}@{camera_ip}:554/Streaming/Channels/101"
        
        # Run the CPU-bound blocking pipeline strictly in the defined thread pool
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(cls._executor, cls._run_pipeline_sync, rtsp_url)
        except Exception as e:
            logger.error(f"Lane {lane_number} pipeline fatal error: {e}")
            result = {"plate": None, "error": "PIPELINE_ERROR", "confidence": 0}

        end_time = time.perf_counter()
        processing_ms = int((end_time - start_time) * 1000)
        
        result["processing_ms"] = processing_ms
        return result

    @classmethod
    def _run_pipeline_sync(cls, rtsp_url: str) -> dict:
        """
        Synchronous workflow that runs fully inside a single worker thread.
        Never blocks the async event loop.
        """
        telemetry = {
            "grab_ms": 0,
            "preprocess_ms": 0,
            "detection_ms": 0,
            "ocr_ms": 0
        }
        
        # 1. Grab Frames
        t0 = time.perf_counter()
        frames, error = FrameGrabber.get_frames(rtsp_url)
        t1 = time.perf_counter()
        telemetry["grab_ms"] = int((t1 - t0) * 1000)
        
        if error:
            return {"plate": None, "error": error, "confidence": 0, "telemetry": telemetry}
            
        frame_results = []
        
        if DEBUG_SAVE_IMAGES:
            timestamp = int(time.time() * 1000)
        
        # Process each grabbed frame
        for i, frame in enumerate(frames):
            if DEBUG_SAVE_IMAGES:
                cv2.imwrite(os.path.join(DEBUG_OUT_DIR, f"lane_trigger_{timestamp}_frame_{i}.jpg"), frame)
                
            # 2. Preprocess 
            t2 = time.perf_counter()
            processed = Preprocessor.process(frame)
            t3 = time.perf_counter()
            telemetry["preprocess_ms"] += int((t3 - t2) * 1000)
            
            # 3. Detect Plate Region
            t4 = time.perf_counter()
            rois_info = PlateDetector.detect(frame, processed)
            t5 = time.perf_counter()
            telemetry["detection_ms"] += int((t5 - t4) * 1000)
            
            if not rois_info:
                continue
                
            for roi_idx, (roi, det_conf, det_method) in enumerate(rois_info):
                if DEBUG_SAVE_IMAGES:
                    cv2.imwrite(os.path.join(DEBUG_OUT_DIR, f"lane_trigger_{timestamp}_frame_{i}_roi_{roi_idx}.jpg"), roi)
                    
                # 4. Read OCR
                t6 = time.perf_counter()
                raw_text, ocr_conf, ocr_method = OCRAgent.read_plate(roi)
                t7 = time.perf_counter()
                telemetry["ocr_ms"] += int((t7 - t6) * 1000)
                
                if raw_text:
                    frame_results.append({
                        "raw_text": raw_text,
                        "det_conf": det_conf,
                        "ocr_conf": ocr_conf,
                        "method": f"{det_method}+{ocr_method}"
                    })
                
        if not frame_results:
            return {"plate": None, "error": "NO_PLATE_DETECTED", "confidence": 0, "telemetry": telemetry}

        # 5. Postprocess, validate, and compute final confidence
        best_result = PostProcessor.process_frames(frame_results)
        best_result["telemetry"] = telemetry
        
        return best_result

# Expose a singleton instance alias for clean imports
pipeline = LPRPipeline()
