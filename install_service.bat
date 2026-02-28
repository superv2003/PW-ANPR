@echo off
:: ============================================================================
:: PARKWIZ ANPR Service — Windows Service Installer
:: ============================================================================
:: Prerequisites:
::   1. Python 3.10+ installed and venv created at C:\ParkwizANPR\venv\
::   2. NSSM (nssm.exe) present at C:\ParkwizANPR\nssm.exe
::   3. config.ini configured at C:\ParkwizANPR\config.ini
::   4. tblANPRCaptureLog table created in MSSQL
:: ============================================================================

set SERVICE_NAME=ParkwizANPR
set NSSM=C:\ParkwizANPR\nssm.exe
set PYTHON=C:\ParkwizANPR\venv\Scripts\python.exe
set APP_DIR=C:\ParkwizANPR
set LOG_DIR=C:\ParkwizANPR\logs

echo.
echo ============================================
echo   PARKWIZ ANPR Service Installer
echo ============================================
echo.

:: Check NSSM exists
if not exist "%NSSM%" (
    echo ERROR: NSSM not found at %NSSM%
    echo Download NSSM from https://nssm.cc/download and place nssm.exe in %APP_DIR%
    pause
    exit /b 1
)

:: Check Python exists
if not exist "%PYTHON%" (
    echo ERROR: Python venv not found at %PYTHON%
    echo Create it first: python -m venv C:\ParkwizANPR\venv
    pause
    exit /b 1
)

:: Create log directory
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:: Install the service
echo Installing %SERVICE_NAME% service...
%NSSM% install %SERVICE_NAME% %PYTHON% "-m uvicorn parkwiz_anpr.main:app --host 0.0.0.0 --port 8765 --workers 1"
%NSSM% set %SERVICE_NAME% AppDirectory %APP_DIR%
%NSSM% set %SERVICE_NAME% DisplayName "Parkwiz ANPR Service"
%NSSM% set %SERVICE_NAME% Description "Automatic Number Plate Recognition for Parkwiz PMS — v1.0"
%NSSM% set %SERVICE_NAME% Start SERVICE_AUTO_START
%NSSM% set %SERVICE_NAME% AppRestartDelay 3000
%NSSM% set %SERVICE_NAME% AppStdout %LOG_DIR%\service_stdout.log
%NSSM% set %SERVICE_NAME% AppStderr %LOG_DIR%\service_stderr.log
%NSSM% set %SERVICE_NAME% AppStdoutCreationDisposition 4
%NSSM% set %SERVICE_NAME% AppStderrCreationDisposition 4
%NSSM% set %SERVICE_NAME% AppRotateFiles 1
%NSSM% set %SERVICE_NAME% AppRotateBytes 10485760

:: Start the service
echo Starting %SERVICE_NAME%...
%NSSM% start %SERVICE_NAME%

echo.
echo ============================================
echo   Service installed and started!
echo   Dashboard: http://localhost:8765/dashboard
echo   Health:    http://localhost:8765/api/v1/health
echo   Logs:      %LOG_DIR%
echo ============================================
echo.
pause
