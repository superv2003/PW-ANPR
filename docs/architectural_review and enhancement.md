
ANPR Camera Pipeline – Architecture Review & Enhancement Discussion
Context

The current ANPR system integrates a CP Plus VMDS camera using RTSP streaming and performs plate detection + OCR through a Python pipeline.

The key modules involved are:

frame_grabber.py – manages RTSP ingestion and frame freshness

config.py – system configuration and detection parameters

ocr_engine.py – OCR pipeline using PaddleOCR + EasyOCR

YOLO model – plate detection

OCR post-processing – regex validation for Indian number plates

From reviewing the code, the overall architecture is already strong and follows best practices for real-time video ingestion systems.

In particular:

Persistent RTSP streaming avoids connection latency.

Background frame draining prevents decoder buffering.

OCR models are pooled to avoid memory overhead.

Detection + OCR results are validated with Indian plate regex patterns.

However, there are several areas where performance, stability, and accuracy could be improved further.

The following sections describe potential enhancements and open questions.

1. Frame Burst Capture Instead of Single Frame
Current Implementation

The system currently captures one frame per trigger.

FRAMES_TO_GRAB = 1

(defined in config.py) 

config

Problem

Vehicles are moving when the loop sensor triggers. A single frame may contain:

motion blur

partial plate

plate entering frame

Proposed Enhancement

Capture a short burst of frames and choose the sharpest frame before OCR.

Example logic:

AR Trigger
↓
Capture 3 frames
↓
Calculate sharpness (Laplacian variance)
↓
Select best frame
↓
Run detection + OCR

Example algorithm:

def sharpness(img):
    return cv2.Laplacian(img, cv2.CV_64F).var()

best_frame = max(frames, key=sharpness)
Benefits

Improves OCR accuracy

Reduces motion blur failures

Better handling of high-speed vehicles

Question

Should we increase FRAMES_TO_GRAB to 3 and implement a sharpness-based frame selection before running OCR?

2. Region-of-Interest Cropping Before Plate Detection
Current Implementation

YOLO runs on the entire frame.

Relevant configuration:

YOLO_INPUT_SIZE = (416, 416)
MAX_IMAGE_WIDTH = 1280

(defined in config.py) 

config

Problem

In most lane camera installations:

the plate always appears in the lower central region

large parts of the frame contain irrelevant areas

Running detection on the entire frame increases:

inference time

false positives

CPU load

Proposed Enhancement

Crop a fixed ROI before running detection.

Example:

Top area → sky / background
Middle area → vehicle body
Lower middle → plate region

Sample ROI cropping:

h, w = frame.shape[:2]
roi = frame[int(h*0.35):h, int(w*0.15):int(w*0.85)]
Benefits

2–3× faster detection

fewer false positives

lower CPU usage

Question

Since the camera is fixed per lane, should we crop a lower-center ROI before running YOLO?

3. AR Trigger Timing Adjustment
Current Behavior

The pipeline likely captures a frame immediately when AR signal is received.

Problem

Loop sensors typically trigger before the vehicle plate reaches the center of the camera frame.

Capturing immediately may result in:

plate partially visible

vehicle still entering frame

Proposed Enhancement

Introduce a small configurable delay.

Example:

time.sleep(0.12)

Typical timing:

Loop Trigger
↓
120–150 ms
↓
Plate centered in frame
Question

Should we add a configurable AR delay (~120 ms) before capturing frames?

4. CPU Optimization in Frame Grabber Thread

The current frame grabber architecture is excellent.

Key logic:

while True:
    has_next = self.cap.grab()

(from frame_grabber.py) 

frame_grabber

This aggressively drains buffered frames to ensure only the latest frame is retrieved.

Potential Issue

This loop may run at maximum CPU speed.

Proposed Enhancement

Add a micro sleep to reduce CPU load:

time.sleep(0.002)
Question

Should a small sleep interval be added in the grab loop to prevent unnecessary CPU usage?

5. RTSP Stream Health Monitoring

The current code reconnects when:

cap.isOpened() == False

(from frame_grabber.py) 

frame_grabber

Problem

Some cameras may freeze while still reporting the stream as open.

Proposed Enhancement

Reconnect when frame timestamps stop updating.

Example:

if time.time() - self.frame_time > 5:
    reconnect_stream()
Benefits

prevents silent camera freezes

improves long-running stability

Question

Should we trigger reconnection when frame age exceeds a threshold (e.g., 5 seconds)?

6. OCR Preprocessing Enhancement

Current preprocessing includes:

grayscale conversion

Otsu thresholding

(from ocr_engine.py) 

ocr_engine

Possible Enhancement

Add CLAHE contrast enhancement.

Example:

clahe = cv2.createCLAHE(clipLimit=2.0)
enhanced = clahe.apply(gray)
Benefits

Helps in difficult scenarios:

IR glare

reflective plates

night lighting

dirty plates

Question

Should CLAHE enhancement be added before OCR preprocessing?

7. OCR Worker Pool Scaling

The OCR pool currently pre-allocates instances:

OCRPool.initialize(pool_size=4)

(from ocr_engine.py) 

ocr_engine

Possible Improvement

Make pool size dynamic based on CPU cores.

Example:

pool_size = CPU_CORES / 2
Question

Should OCR worker pool size scale dynamically with available CPU cores?

8. Plate Validation Scoring

Currently the system validates plates using regex patterns:

PATTERN_STANDARD
PATTERN_BH_SERIES

(from config.py) 

config

Possible Improvement

Introduce a confidence scoring layer.

Example:

KA03MR8318 → high confidence
KA03M88318 → lower confidence

This can help reject OCR noise.

Question

Should regex validation include a confidence scoring mechanism?

9. Debug Image Storage Control

Currently debug image saving is enabled:

DEBUG_SAVE_IMAGES = True

(from config.py) 

config

Potential Issue

In production environments this may lead to:

excessive disk usage

unnecessary I/O

Question

Should debug image saving automatically disable in production mode?

10. Camera Performance Monitoring

The CameraManager health logic already tracks frame freshness.

(from frame_grabber.py) 

frame_grabber

Enhancement

Add logging for:

frame rate

frame delay

RTSP jitter

This would help diagnose:

network instability

camera lag

packet loss

Question

Should we track camera FPS and frame latency metrics for monitoring?

Summary

The existing pipeline is already architecturally strong and implements many best practices:

persistent RTSP streaming

buffer draining

OCR worker pooling

regex-based validation

The suggested improvements mainly focus on:

Improving OCR accuracy

Reducing compute cost

Increasing system stability

Optimizing real-time performance