@echo off
rem 环境无关启动脚本：使用脚本自身目录（%~dp0），不写死本机路径
cd /d "%~dp0"
start "" /MIN pwsh.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0start-detached.ps1"
