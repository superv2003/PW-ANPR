import time
import numpy as np
import cv2
import sys

from lpr_engine.preprocessor import Preprocessor
from lpr_engine.detector import PlateDetector
from lpr_engine.ocr_engine import OCRAgent, OCRPool

def print_result(stage, ms):
    color = "\033[92m" if ms < 500 else "\033[93m" if ms < 1000 else "\033[91m"
    reset = "\033[0m"
    print(f"[{stage.ljust(15)}] {color}{ms:>6.1f} ms{reset}")

def run_benchmark():
    print("Initializing components...")
    
    # 1. Warmup / Setup
    PlateDetector.initialize()
    OCRPool.initialize(pool_size=1)
    
    # Generate a dummy 6MP Image (Prama PT-NC360P1 is ~3072x2048)
    print("Generating dummy 6 Megapixel frame (3072x2048)...")
    dummy_frame = np.ones((2048, 3072, 3), dtype=np.uint8) * 128
    
    # Put a white rectangle to simulate a plate
    cv2.rectangle(dummy_frame, (1000, 1000), (1400, 1100), (255, 255, 255), -1)
    # Add some text directly to the dummy image
    cv2.putText(dummy_frame, "MH12AB1234", (1050, 1060), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)

    print("Running Warmup Pass...")
    proc_warmup = Preprocessor.process(dummy_frame)
    roi_warmup, _, _ = PlateDetector.detect(dummy_frame, proc_warmup)
    if roi_warmup is None:
        roi_warmup = dummy_frame[1000:1100, 1000:1400]
    OCRAgent.read_plate(roi_warmup)

    print("-" * 40)
    print("BENCHMARK TIMINGS (Mocker)")
    print("-" * 40)
    
    total_time = 0

    # 1. Preprocessor
    t0 = time.perf_counter()
    processed = Preprocessor.process(dummy_frame)
    t1 = time.perf_counter()
    ms = (t1 - t0) * 1000
    print_result("Preprocess", ms)
    total_time += ms

    # 2. Detector
    t0 = time.perf_counter()
    roi, det_conf, m = PlateDetector.detect(dummy_frame, processed)
    t1 = time.perf_counter()
    ms = (t1 - t0) * 1000
    print_result("Detect (YOLO/fb)", ms)
    total_time += ms
    
    if roi is None:
        print("Detector failed to find the dummy plate. Fallback didn't trigger correctly or ROI is none.")
        # Fallback to a slice for OCR testing if detector misses the injected rect
        roi = dummy_frame[1000:1100, 1000:1400]

    # 3. OCR Prep & Execution
    t0 = time.perf_counter()
    text, ocr_conf, m = OCRAgent.read_plate(roi)
    t1 = time.perf_counter()
    ms = (t1 - t0) * 1000
    print_result("OCR (Paddle/EZ)", ms)
    total_time += ms
    
    print("-" * 40)
    print(f"Total Pipeline: {total_time:.1f} ms")
    if total_time < 2000.0:
         print("SUCCESS: Total time is under 2.0s target!")
    else:
         print("WARNING: Total time exceeds 2.0s target.")

if __name__ == "__main__":
    run_benchmark()
