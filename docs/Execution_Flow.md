# PARKWIZ ANPR - Execution Flow Architecture

This document tracks the entire lifecycle of an ANPR capture request from trigger to database execution, detailing exactly which files execute and what data formats are handed between them.

---

## 🚀 1. Trigger Origination
A signal fires, demanding a capture. This comes from one of two sources:

### A. HTTP API Trigger (`parkwiz_anpr/api/v1/capture.py`)
- The PMS sends a POST request.
- **Input:** `{"lane_number": "26", "org_id": "PARKWIZ"}`
- `uuid.uuid4()` generates a unique 12-character `request_id` (e.g., `166fd74eb9ba`).

### B. Hardware Polling Trigger (`parkwiz_anpr/services/polling_service.py`)
- Python thread queries SQL `tblHDDReadWrite` (`valAR = 1`) at 1Hz.
- Automatically creates a `request_id` like `poll-5af32d` and bypasses the webserver.

*Both inputs converge exactly here:* `await process_capture(lane=26, org="PARKWIZ")`

---

## 🚦 2. Central Orchestrator (`parkwiz_anpr/services/capture_service.py`)
This is the master transaction coordinator.

1. **Config Cache:** Calls `lane_cache.get_lane(26)`. This fetches `192.168.1.64` from RAM without hitting the DB.
2. **Concurrency Lock:** Calls `_get_lane_semaphore("26")`. If the sensor glitches and fires 5 times in 1 second, the semaphore forces them to wait synchronously, preventing CPU/Camera denial of service.
3. **Pipeline Request:** Calls `LPRPipeline.process()`. Uses `asyncio.wait_for` to strictly enforce a timeout (e.g. 20s). If the CV engine hangs, the capture service kills it and logs a `TIMEOUT` error to the DB.

---

## 🧠 3. Computer Vision Engine (`lpr_engine/pipeline.py`)
The pipeline accepts the request and pushes it into a `ThreadPoolExecutor` so the FastAPI async loop remains unblocked. It executes 6 strict stages:

### Stage 3.1: Frame Grabbing (`lpr_engine/frame_grabber.py`)
- **Action:** Reads the URL string dynamically.
- **If `rtsp://`**: Requests a frame from `CameraShutter`. This is a background daemon pulling raw `H.264` via TCP. It simply returns `self.latest_frame`. Speed: **~1ms**.
- **If `http://`**: Creates `HttpSnapshotGrabber`. It sends `requests.get()` using HTTPDigestAuth exactly `FRAMES_TO_GRAB` times to the camera's `/ISAPI` endpoint. Speed: **~300-800ms**.
- **Hand-off Data:** A native `cv2` (numpy array) frame representing 1920x1080 BGR pixels.

### Stage 3.2: ROI Cropping (`lpr_engine/pipeline.py`)
- **Action:** Math array slicing.
- **Logic:** `frame[y1:y2, x1:x2]`. 
- **Coordinates:** Slices off the top 35%, bottom 15%, and outer 15% edges.
- **Why:** Physically excises dates like `01-01-1970` and watermarks like `Camera 01` before the AI sees them. Shrinks the memory footprint by 50%.
- **Hand-off Data:** A greatly reduced `cv2` array focused solely on the center lane bounds.

### Stage 3.3: Preprocessing (`lpr_engine/preprocessor.py`)
- **Action:** Color space manipulation.
- Converts to Grayscale (`cv2.cvtColor`).
- Applies Contrast Limited Adaptive Histogram Equalization (`cv2.createCLAHE()`) to violently enhance pixel contrast on white reflection plates under harsh headlight glare.
- **Hand-off Data:** An enhanced grayscale numpy array. Speed: **~10ms**.

### Stage 3.4: Bounding Box Detection (`lpr_engine/detector.py`)
- **Action:** Neural Network Inference.
- Prepares an ONNX blob of the ROI and feeds it to `indian_plate_detector.onnx` (`cv2.dnn`).
- **Logic:** Performs Non-Maximum Suppression (NMS) to delete overlapping plate boxes.
- **Hand-off Data:** The exact absolute `(x, y, w, h)` coordinate rectangles where it strongly believes a metallic plate exists. Speed: **~80ms**.

### Stage 3.5: Character Parsing (`lpr_engine/ocr_engine.py`)
- **Action:** Machine Learning Text Extraction.
- The rectangular plate is physically sliced out of the frame and handed to the `OCRPool` (PaddleOCR).
- **Hand-off Data:** Raw string and float: `("K A 0 3 N T 4 0 0 5", 0.948)`. Speed: **~1500-2500ms**.

### Stage 3.6: String Validation (`lpr_engine/postprocessor.py`)
- **Action:** Text cleaning and regex qualification.
- Evaluates `"K A 0 3 N T 4 0 0 5"`. Strips whitespace -> `"KA03NT4005"`.
- Runs common `OCR_CORRECTIONS` (e.g. changes `O` to `0` if in a number locus).
- Tests against `re.compile(r'^[A-Z]{2}[0-9]{2}[A-Z]{1,2}[0-9]{4}$')`.
- **Hand-off Data:** A JSON Dict: `{"plate": "KA03NT4005", "confidence": 0.94, "telemetry": {...}}`.

---

## 💾 4. Persistence & Response Integration
`capture_service.py` receives the final validated JSON from the pipeline and executes two final tasks:

1. **Local Storage (`core/image_store.py`):**
   - Asynchronously takes the raw `cv2` byte array and writes a JPEG to `C:\ParkwizANPR\plate_images\2026\03\13\PARKWIZ_L26_KA03NT4005_166fd7.jpg`.

2. **Database Logging (`core/database.py`):**
   - Strips HTTP credentials from the camera string using `urllib.parse`.
   - Fires a pyodbc `INSERT INTO tblANPRCaptureLog`.

3. **API Response:**
   - The FastAPI `capture()` route packages the dictionary and ships a `200 OK` JSON back to the PMS containing the plate string, confidence score, and granular ms-level telemetry. Execution finished.
