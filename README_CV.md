# ParkWiz ANPR Service

A pure-CPU, fast, and multi-threaded Automatic Number Plate Recognition micro-service designed for Indian Plates on Intel Xeon servers.

## Environment Constraints
- Pure CPU Pipeline (No CUDA/GPU fallback used)
- OS: Windows Server 2022 / Windows 10 Pro
- Cameras: Prama 6MP RTSP streams on demand
- Target time limit: < 2000ms end-to-end
- Thread-safe scaling using `ThreadPoolExecutor`

## Installation

We HIGHLY recommend Python 3.10+ targeting 64-bit architecture. 
To ensure fast execution, the libraries used rely on pre-compiled CPU optimization vectors (AVX2/AVX-512).

1. Clone inside your Parking Server directory.
2. Setup virtual environment:
   ```cmd
   python -m venv venv
   call venv\Scripts\activate.bat
   ```
3. Install dependencies from `requirements_cv.txt`:
   ```cmd
   pip install -r requirements_cv.txt
   ```
   **Important**: Make sure `opencv-python-headless` is used instead of standard `opencv-python` to prevent QT/GUI overhead on server setups.

## Sourcing the YOLOv8 Model

You must supply `indian_plate_detector.onnx` placing it inside `lpr_engine/models/`.
To obtain one:
1. Go to Roboflow Universe -> Search "Indian license plate YOLOv8"
2. Download the best weights (`.pt`).
3. Export it sequentially for CPU ONNX (doing this on any machine):
   ```bash
   pip install ultralytics
   yolo export model=best.pt format=onnx opset=12 simplify=True
   ```
4. Rename to `indian_plate_detector.onnx` and place in `models/`.

## Running the Tests

1. Unit tests for Plate Corrections & OCR Voting:
   ```cmd
   python test_plates.py
   ```
2. Speed Timestamps (Mock 6MP load):
   ```cmd
   python benchmark.py
   ```

## Tuning Configurations
Thresholds and constraints can be changed in `lpr_engine/config.py`:
- `RTSP_TIMEOUT_SEC`: Maximum waiting time connecting to PRAMA cameras. Default `3.0`.
- `YOLO_CONF_THRESHOLD`: Rejects trash boxes early. Default `0.5`.
- `PADDLE_CONF_THRESHOLD`: When to fallback to EasyOCR. Default `0.6`.
