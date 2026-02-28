@echo off
:: ============================================================================
:: PARKWIZ ANPR Service — Windows Service Uninstaller
:: ============================================================================

set SERVICE_NAME=ParkwizANPR
set NSSM=C:\ParkwizANPR\nssm.exe

echo.
echo ============================================
echo   PARKWIZ ANPR Service Uninstaller
echo ============================================
echo.

if not exist "%NSSM%" (
    echo ERROR: NSSM not found at %NSSM%
    pause
    exit /b 1
)

echo Stopping %SERVICE_NAME%...
%NSSM% stop %SERVICE_NAME% 2>nul

echo Removing %SERVICE_NAME%...
%NSSM% remove %SERVICE_NAME% confirm

echo.
echo Service removed successfully.
echo Note: Log files and plate images were NOT deleted.
echo.
pause
