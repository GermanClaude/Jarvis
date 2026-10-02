@echo off
rem Legt eine Verknuepfung im Autostart-Ordner an: JARVIS startet minimiert mit Windows.
set "TARGET=%~dp0jarvis-voice.bat"
set "LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\JARVIS.lnk"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LINK); $s.TargetPath=$env:TARGET; $s.WorkingDirectory=(Split-Path $env:TARGET); $s.WindowStyle=7; $s.Save()"
if errorlevel 1 (echo Verknuepfung konnte nicht angelegt werden. & pause & exit /b 1)
echo JARVIS startet ab jetzt automatisch mit Windows (minimiert).
echo Wieder entfernen: Datei "%LINK%" loeschen.
pause
