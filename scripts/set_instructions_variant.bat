@echo off
REM Switch the installed extension's instructions variant.
REM
REM   set_instructions_variant.bat unconditional
REM   set_instructions_variant.bat conditional
REM   set_instructions_variant.bat off
REM   set_instructions_variant.bat            (shows the current setting)
REM
REM Restart Claude Desktop afterwards.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0set_instructions_variant.ps1" %1
echo.
pause
