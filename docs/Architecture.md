# PARKWIZ ANPR System Architecture

Welcome to the PARKWIZ ANPR (Automatic Number Plate Recognition) system! This guide is written for new software and computer vision engineers joining the team. It explains the high-level architecture of the system—how it fits together physically, and exactly what happens in the code when a car drives up to the gate.

---

## 1. The Big Picture (Physical Architecture)

Before diving into code, here is how the real-world hardware works together:

1. **The Camera (CP Plus / Hikvision)**
   * Mounted at the boom barrier.
   * Streaming 1920x1080 video at 5 frames per second over the local network via RTSP (Real-Time Streaming Protocol).
2. **The Trigger (Loop Coil / Ticket Button)**
   * A physical sensor or button detects a car is waiting.
3. **The PMS (Parking Management System)**
   * The local controller receives the hardware trigger and fires a REST API `POST` request to our Python ANPR Server.
4. **The ANPR Server (Our Python App)**
   * Written in FastAPI.
   * Instantly grabs the video frame, reads the plate using AI, and replies to the PMS in **~1 second**.
5. **The Database (SQL Server)**
   * Logs every capture event for reporting and dashboarding.

---

## 2. The Core Bottleneck & The "Persistent Manager" Solution

When reading video across a network, connecting to a camera takes a long time. The camera and server have to shake hands (TCP), negotiate stream formats (RTSP), and then the server has to wait for a "Keyframe" (I-Frame) to arrive before it can reconstruct a picture.

**The Old Way (Slow - 6 seconds):**
The server waited for the PMS API trigger, *then* opened the RTSP connection, grabbed the frame, and disconnected. This took ~5700 milliseconds just to get the picture.

**The New Way (Fast - 1 millisecond): ** `lpr_engine/frame_grabber.py`
We now use a **Persistent CameraManager Architecture**. 
* When the FastAPI server starts up, it creates a "Daemon Thread" (a background worker) for every configured camera. 
* This worker opens the RTSP stream *forever* and continuously reads the video in the background.
* It throws away old frames and keeps exactly **one** image in memory (the freshest one).
* When the API trigger arrives, the system doesn't talk to the network at all—it just copies that one image out of RAM in 1 millisecond.

---

## 3. The Execution Flow (Step-by-Step)

Here is the exact journey of a single capture request through the code, from start to finish.

### Step 1: The API Trigger arrives (`parkwiz_anpr/services/capture_service.py`)
The PMS makes an HTTP request to `/api/v1/capture` saying: *"Read the plate on Lane 26."*

1. The service looks up `Lane 26` in the `lane_cache` (which mirrors the SQL Database) to find the Camera IP.
2. It acquires a "Semaphore" lock (preventing the system from crashing if the PMS sends 10 duplicate triggers in one second).
3. It hands the job off to the `LPRPipeline`.

### Step 2: The Pipeline ThreadPool (`lpr_engine/pipeline.py`)
AI processing uses 100% of a CPU core. If we run it on the main web-server thread, the whole API freezes for 1 second.
To prevent this, `pipeline.py` uses a `ThreadPoolExecutor`. It pushes the heavy lifting to a background CPU worker, keeping the web API fast and responsive.

### Step 3: Grab the Frame (`lpr_engine/frame_grabber.py`)
The worker asks the `CameraManager`: *"Give me the latest picture you have in memory for Lane 26."*
(Cost: ~2 milliseconds).

### Step 4: Preprocess the Image (`lpr_engine/preprocessor.py`)
Raw camera images are messy (glare, shadows, weird colors). We prepare the image for the AI:
1. **Grayscale:** Convert to black and white.
2. **CLAHE:** Fix uneven lighting (e.g., bright sunlight on half the plate, deep shadow on the other).
3. **Bilateral Filter:** Smooth out grain/noise without blurring the sharp edges of the letters.
(Cost: ~40 milliseconds).

### Step 5: Detect the Plate Region (`lpr_engine/detector.py`)
We need to find *where* the plate is in the 1920x1080 image.
1. We feed the preprocessed image into an **ONNX YOLOv8** model. (YOLO = You Only Look Once).
2. The AI returns a bounding box around the license plate.
3. If YOLO fails, we use a "Contour Fallback" (classic computer vision finding rectangular shapes).
4. We crop out just that small rectangle.
(Cost: ~125 milliseconds).

### Step 6: Read the Text - OCR (`lpr_engine/ocr_engine.py`)
Now we have a tiny picture of a plate. We need to turn those pixels into text (Optical Character Recognition).
Because loading gigantic AI models is slow, we use an `OCRPool` that keeps 4 instances of **PaddleOCR** permanently loaded in RAM. 

1. We borrow a PaddleOCR instance from the pool.
2. We feed the tiny plate crop into PaddleOCR.
3. Paddle uses a deep learning network to identify the letters.
4. If the confidence is high (>60%), it succeeds!
*(If confidence is low, the code optionally tries thresholded variants or an EasyOCR fallback).*
(Cost: ~900 milliseconds).

### Step 7: Postprocessing & Cleanup (`lpr_engine/postprocessor.py`)
AI makes mistakes. We clean up the raw text:
1. Strip out spaces and weird symbols (e.g., turning `KA-03 MZ 7276` into `KA03MZ7276`).
2. Apply common correction rules (e.g., the AI thought `0` was an `O`, or `8` was a `B`).
3. Run a **Regex** (Regular Expression) to check if the text mathematically matches the Indian traffic standard pattern.

### Step 8: Return the Result and Log
The pipeline returns the cleaned text to the `capture_service`.
1. The service fires off an async task to save the image to disk (`parkwiz_anpr/core/image_store.py`).
2. It completely logs the event to SQL Server (`parkwiz_anpr/core/database.py`).
3. It replies to the PMS with the final JSON payload containing the plate string, confidence, and telemetry millisecond breakdown.

---

## Folder Structure Guide

To help you navigate the codebase:

```text
├── lpr_engine/                 # The core Computer Vision AI
│   ├── models/                 # Neural network weight files (.onnx / paddle models)
│   ├── config.py               # AI thresholds and regex patterns
│   ├── camera_manager.py       # Daemon threads holding persistent RTSP streams
│   ├── detector.py             # YOLO ONNX logic to find the plate bounding box
│   ├── ocr_engine.py           # PaddleOCR logic to read the text
│   ├── preprocessor.py         # OpenCV logic to clean up the image
│   └── pipeline.py             # Orchestrates the detector, OCR, and threads together
│
├── parkwiz_anpr/               # The FastAPI Web Server
│   ├── api/v1/                 # The routing logic (where HTTP POSTs hit)
│   ├── core/                   # Infrastructure (Database, Config, Logging)
│   ├── models/                 # Pydantic schemas enforcing JSON input/output shapes
│   ├── services/               # Business logic connecting the API to the CV engine
│   └── main.py                 # The server entry point (starts everything)
```
