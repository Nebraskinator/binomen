@echo off
REM Double-clickable wrapper for collect_boot_log.ps1.
REM Writes diag\boot-log-report.txt.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0collect_boot_log.ps1"
echo.
pause
