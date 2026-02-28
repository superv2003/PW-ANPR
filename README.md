# PARKWIZ ANPR Service

Production-grade Automatic Number Plate Recognition service for **PARKWIZ Parking Management System**.

Runs on-premise on Windows servers with no GPU and no internet dependency.

---

## Architecture

```
PMS (POST /api/v1/capture) → FastAPI → Lane Config Cache → CV Pipeline → MSSQL Log → Response
```

| Component | Technology |
|-----------|-----------|
| API Server | FastAPI + Uvicorn |
| CV Pipeline | YOLOv8 (ONNX) + PaddleOCR + EasyOCR |
| Database | Microsoft SQL Server (pyodbc) |
| Deployment | NSSM Windows Service |

---

## Quick Start

### 1. Prerequisites

| Requirement | Download |
|-------------|----------|
| Python 3.10+ (64-bit) | [python.org](https://www.python.org/downloads/) |
| ODBC Driver 17 for SQL Server | [Microsoft](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server) |
| NSSM (for Windows Service) | [nssm.cc](https://nssm.cc/download) |

### 2. Virtual Environment

```cmd
cd C:\ParkwizANPR
python -m venv venv
call venv\Scripts\activate.bat
pip install -r requirements.txt
```

### 3. YOLO Model

Place the ONNX model file at `lpr_engine/models/indian_plate_detector.onnx`.  
See [README_CV.md](README_CV.md) for model sourcing instructions.

### 4. Database Setup

Run the SQL script to create the capture log table:

```cmd
sqlcmd -S PW\V2019 -d PARKWIZ -U sa -P pwiz -i scripts\create_capture_log.sql
```

### 5. Configuration

```cmd
copy config.ini.example config.ini
notepad config.ini
```

Edit `config.ini` with your site-specific values:
- **Database**: `server`, `database`, `username`, `password`
- **Camera**: `rtsp_username`, `rtsp_password`
- **Storage**: `image_dir`, `log_dir`
- **API Key** (optional): set `api_key` under `[service]`

### 6. Test Run (Development)

```cmd
call venv\Scripts\activate.bat
python -m parkwiz_anpr.main
```

Verify at:
- Health: http://localhost:8765/api/v1/health
- Dashboard: http://localhost:8765/dashboard
- API Docs: http://localhost:8765/docs

### 7. Install as Windows Service

```cmd
:: Run as Administrator
install_service.bat
```

The service will:
- Start automatically on boot
- Restart on crash (3-second delay)
- Write stdout/stderr to `C:\ParkwizANPR\logs\`

### 8. Firewall

Open port 8765 for LAN access:

```cmd
netsh advfirewall firewall add rule name="ParkwizANPR" dir=in action=allow protocol=tcp localport=8765
```

---

## API Reference

### `POST /api/v1/capture`

**Request:**
```json
{
  "lane_number": "01",
  "org_id": "PARKWIZ"
}
```

**Headers** (if API key is configured):
```
X-API-Key: your-api-key
```

**Success Response (200):**
```json
{
  "success": true,
  "plate": "KA01AB1234",
  "confidence": 0.94,
  "lane_number": "01",
  "camera_ip": "192.168.1.152",
  "captured_at": "2026-02-25T11:56:00.123Z",
  "processing_ms": 1240,
  "request_id": "a1b2c3d4e5f6"
}
```

**No Plate (200):**
```json
{
  "success": false,
  "plate": null,
  "error_code": "NO_PLATE_DETECTED",
  "processing_ms": 890,
  "request_id": "..."
}
```

**Lane Not Found (404):**
```json
{
  "detail": "Lane 99 not configured or disabled"
}
```

### Admin Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/health` | GET | Service health + DB status |
| `/api/v1/stats` | GET | Today's capture statistics |
| `/api/v1/logs?lane=01&limit=50` | GET | Recent capture logs |
| `/api/v1/admin/reload-config` | POST | Force lane config refresh |
| `/api/v1/admin/test-camera?lane=01` | GET | Test RTSP connectivity |

### Dashboard

Open http://localhost:8765/dashboard for a live monitoring dashboard with:
- Stat cards (total, success rate, avg speed, uptime, DB status)
- Per-lane breakdown
- Recent captures table with plate search
- Auto-refresh every 5 seconds

---

## Logs

| File | Level | Location |
|------|-------|----------|
| `anpr_service.log` | INFO+ | `C:\ParkwizANPR\logs\` |
| `anpr_errors.log` | WARNING+ | `C:\ParkwizANPR\logs\` |
| `service_stdout.log` | NSSM stdout | `C:\ParkwizANPR\logs\` |
| `service_stderr.log` | NSSM stderr | `C:\ParkwizANPR\logs\` |

Rotation: 10 MB per file, 10 backups kept.

---

## Updating Lane Config

When cameras are added or changed in `tblLaneANPRConfiguration`:

1. Update the row in MSSQL (via PMS admin)
2. Either:
   - Wait up to 60 seconds (auto-refresh), **or**
   - Call `POST /api/v1/admin/reload-config` for immediate refresh

No service restart required.

---

## Uninstalling

```cmd
:: Run as Administrator
uninstall_service.bat
```

This removes the Windows service but preserves log files and plate images.

---

## Project Structure

```
PW-ANPR/
├── parkwiz_anpr/               ← Backend service (this project)
│   ├── main.py                 ← FastAPI app + lifecycle
│   ├── api/v1/capture.py       ← POST /capture endpoint
│   ├── api/v1/admin.py         ← Admin/monitoring endpoints
│   ├── core/config.py          ← config.ini loader
│   ├── core/database.py        ← MSSQL connection pool
│   ├── core/lane_config.py     ← In-memory lane cache
│   ├── core/image_store.py     ← Plate image storage
│   ├── services/capture_service.py ← Orchestration logic
│   ├── models/schemas.py       ← Pydantic models
│   └── templates/dashboard.html ← Web dashboard
├── lpr_engine/                 ← CV pipeline (separate ownership)
├── config.ini.example          ← Configuration template
├── requirements.txt            ← Python dependencies
├── scripts/create_capture_log.sql ← DB table creation
├── install_service.bat         ← Windows Service installer
├── uninstall_service.bat       ← Windows Service remover
├── test_api.py                 ← Integration tests
└── README.md                   ← This file
```
