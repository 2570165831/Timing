@echo off
cd /d "%~dp0"
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
if errorlevel 1 pause
