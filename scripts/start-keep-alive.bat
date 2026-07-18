@echo off
REM Detached keep-alive for novel-mind FE+BE (escapes agent Job Object when launched via WMI/start).
cd /d "%~dp0"
start "" /MIN pwsh.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0keep-alive.ps1"
