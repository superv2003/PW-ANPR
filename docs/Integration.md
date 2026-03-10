# PARKWIZ ANPR Integration & Deployment Guide

This document is written for Backend and PMS Engineers to understand exactly how the PARKWIZ ANPR Service integrates with the existing PARKWIZ SQL Server database and the main Parking Management System software.

There are **two phases** of integration detailed below:
1. **Phase 1: Shadow Testing (Current)** — Zero-touch database polling.
2. **Phase 2: Production (Future)** — Direct REST API integration.

---

## Phase 1: Shadow Testing (The "DB Polling" Method)

To test the accuracy of the ANPR system in the real world without modifying a single line of the existing PARKWIZ PMS code, we have built a **DB Polling Service**.

### How it Works
When a vehicle arrives at the boom barrier, the hardware controller (e.g., T440) writes an "Arm Request" to the database. The ANPR Python server silently watches this table and triggers the camera when it sees that request.

**The Workflow Sequence:**
1. **Hardware:** Vehicle hits the loop coil.
2. **PMS System:** Inserts/Updates a row in `tblHDDReadWrite`:
   * `HDDID`: "26" (The lane number)
   * `sDataRequest`: "AR" (Arm Request)
   * `UpdateDateTime`: Current Timestamp
3. **ANPR Server (`parkwiz_anpr/services/polling_service.py`):**
   * A background thread queries `tblHDDReadWrite` exactly once per second.
   * It sees the new `AR` request for Lane 26.
   * It instantly triggers the internal Computer Vision pipeline.
4. **ANPR Server (`parkwiz_anpr/core/database.py`):**
   * The Computer Vision pipeline finishes reading the plate in <1 second.
   * It **DOES NOT** write to `sDataResponse` in `tblHDDReadWrite` (so it doesn't interfere with your live PMS).
   * It silently logs the result to `tblANPRCaptureLog` for you to review later.

### How to Configure Shadow Testing
On the live Windows Server, open the ANPR `config.ini` file:

```ini
[polling]
enabled = yes
# Type the exact Lane Numbers you want to shadow test here (comma separated)
lanes = 26,27 
interval_ms = 1000
```
Restart the Python service. It is now completely automated.

---

## Phase 2: Production (The Direct REST API Method)

Once you are satisfied with the AI accuracy from the Shadow Testing phase you will turn off DB Polling (`enabled = no` in `config.ini`).

You will then update the PARKWIZ PMS C#/Node/Backend code to directly call the ANPR Server when the boom barrier needs to open.

### The Workflow Sequence

1. **Hardware:** Vehicle hits the loop coil.
2. **PMS System:** Your backend code executes an HTTP POST to the local Python Server.
3. **ANPR Server:** Instantly processes the frame and replies with JSON.
4. **PMS System:** Your backend code receives the JSON, validates the plate against your subscriber database, and commands the boom barrier to open.

### The API Contract

**Request from PMS:**
`POST http://localhost:8765/api/v1/capture`

```json
{
  "lane_number": "26",
  "org_id": "PARKWIZ"
}
```

**Response from ANPR Server (Success):**
```json
{
  "success": true,
  "plate": "KA03MZ7276",
  "confidence": 0.82,
  "lane_number": "26",
  "camera_ip": "192.168.1.63",
  "captured_at": "2026-03-09T13:34:23.987Z",
  "processing_ms": 422,
  "request_id": "4795993d02f8",
  "error_code": null,
  "raw_ocr": "KA03MZ 7276",
  "detection_method": "yolo+paddle_gray",
  "telemetry": {
    "grab_ms": 2,
    "preprocess_ms": 21,
    "detection_ms": 30,
    "ocr_ms": 319
  }
}
```

**Response from ANPR Server (Failure / No Plate):**
```json
{
  "success": false,
  "plate": null,
  "confidence": 0,
  "lane_number": "26",
  "camera_ip": "192.168.1.63",
  "captured_at": "2026-03-09T13:35:10.111Z",
  "processing_ms": 158,
  "request_id": "8a0394e2928b",
  "error_code": "NO_PLATE_DETECTED",
  "raw_ocr": null,
  "detection_method": "yolo",
  "telemetry": { ... }
}
```

### Critical Files & Where to Debug

If a backend engineer needs to trace data flowing through the ANPR system, these are the only 3 files they need to care about:

1. **`parkwiz_anpr/main.py`**
   * The entry point. Handles the startup of the web server and the background camera threads.
2. **`parkwiz_anpr/services/polling_service.py`**
   * The Phase 1 code. Runs an infinite loop checking `tblHDDReadWrite` and triggering the pipeline.
3. **`parkwiz_anpr/core/database.py`**
   * Contains absolutely every SQL query the Python server runs. If you need to change table names or add columns (e.g., to `tblANPRCaptureLog`), edit the SQL strings in this file.
