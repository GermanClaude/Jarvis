@echo off
rem JARVIS mit Sprachsteuerung starten ("Hey Jarvis"). Weitere Optionen: --no-window, --no-wake
cd /d "%~dp0\.."
title JARVIS
if not exist .venv\Scripts\activate.bat (
  echo Bitte zuerst windows\setup.bat ausfuehren.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
python -m jarvis voice %*
if errorlevel 1 pause
