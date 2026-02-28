import cv2
import time
import threading
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from .config import RTSP_TIMEOUT_SEC, FRAMES_TO_GRAB

logger = logging.getLogger(__name__)

import os

class FrameGrabber:
    """
    Connects to RTSP stream on-demand, captures a fixed number of frames,
    and immediately disconnects to save resources. Enforces strict timeouts.
    """
    
    # Shared executor so we don't block waiting for threads to die on timeout
    _executor = ThreadPoolExecutor(max_workers=10)
    
    # Set FFmpeg flags globally for the process to drastically reduce RTSP connection time.
    # tcp bypasses UDP fallback delays. analyzeduration/probesize reduces pre-buffer scanning.
    os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|analyzeduration;500000|probesize;500000|fflags;nobuffer|flags;low_delay"
    
    @staticmethod
    def _connect_and_grab(rtsp_url):
        """Internal synchronous grabber function to be run in a thread."""
        cap = None
        try:
            # Use CAP_PROP_BUFFERSIZE=1 to only keep the most recent frame
            cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            if not cap.isOpened():
                return None, "CAMERA_UNREACHABLE"

            frames = []
            
            # Read and discard a few initial frames if needed, or just grab consecutive.
            # Grabbing the latest frame.
            for _ in range(FRAMES_TO_GRAB):
                ret, frame = cap.read()
                if not ret:
                    break
                frames.append(frame)

            if len(frames) == 0:
                return None, "NO_FRAME_GRABBED"

            return frames, None
        except Exception as e:
            logger.error(f"Error grabbing frame: {e}")
            return None, "CAMERA_GRAB_ERROR"
        finally:
            if cap is not None:
                cap.release()

    @classmethod
    def get_frames(cls, rtsp_url):
        """
        Public method to grab frames with a strict timeout.
        Returns: (frames_list, error_string)
        """
        future = cls._executor.submit(cls._connect_and_grab, rtsp_url)
        try:
            # Wait for the result with a strict timeout
            result, error = future.result(timeout=RTSP_TIMEOUT_SEC)
            return result, error
        except TimeoutError:
            logger.error(f"RTSP connection timed out for {rtsp_url} after {RTSP_TIMEOUT_SEC}s")
            # Note: The thread will continue to run in the background until OpenCV finishes or times out internally.
            # In Python, we can't easily kill threads, but returning fast to the user is the priority.
            return None, "CAMERA_TIMEOUT"
        except Exception as e:
            logger.error(f"Unexpected error in thread pool execution: {e}")
            return None, str(e)

