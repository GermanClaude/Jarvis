@echo off
rem JARVIS - Einrichtung unter Windows (Doppelklick genuegt)
setlocal
cd /d "%~dp0\.."
echo === JARVIS Einrichtung ===
echo.

set "PY="
py -3.12 --version >nul 2>nul && set "PY=py -3.12"
if not defined PY (py -3 --version >nul 2>nul && set "PY=py -3")
if not defined PY (python --version >nul 2>nul && set "PY=python")
if not defined PY (
  echo Python fehlt. Installiere Python 3.12 von https://www.python.org/downloads/
  echo und hake beim Installieren "Add python.exe to PATH" an. Danach diese Datei erneut starten.
  pause
  exit /b 1
)
echo Verwende: %PY%
%PY% --version

if not exist .venv (
  %PY% -m venv .venv || (echo Konnte .venv nicht anlegen. & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements-voice.txt -r requirements-pc.txt || (echo Installation fehlgeschlagen. & pause & exit /b 1)

echo.
set /p CLAUDE="Claude (Premium, kostenpflichtig) mit installieren? [j/N] "
if /i "%CLAUDE%"=="j" pip install -r requirements-claude.txt

if not exist "%USERPROFILE%\jarvis-data" mkdir "%USERPROFILE%\jarvis-data"
if not exist "%USERPROFILE%\jarvis-data\.env" (
  copy .env.example "%USERPROFILE%\jarvis-data\.env" >nul
  echo.
  echo Gleich oeffnet sich die Einstellungsdatei im Editor:
  echo   - API-Schluessel eintragen ^(z. B. GEMINI_API_KEY=...^)
  echo   - JARVIS_PC_CONTROL=1 setzen, damit Jarvis Maus und Tastatur steuern darf
  echo Danach speichern und den Editor schliessen.
  pause
  notepad "%USERPROFILE%\jarvis-data\.env"
)

echo.
echo Fertig! Starten mit: windows\jarvis-voice.bat
pause
