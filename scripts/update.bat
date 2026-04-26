@echo off
REM Champ Assistant updater. Uses curl + tar (built into Windows 10 1803+).
REM Usage: place this script next to champ-assistant.exe, double-click.
REM Close the app before running — Windows can't overwrite a running EXE.

setlocal

if not exist "champ-assistant.exe" (
    echo [updater] error: run this from the champ-assistant folder.
    echo [updater] champ-assistant.exe was not found in the current directory.
    pause
    exit /b 1
)

echo.
echo [updater] Make sure champ-assistant.exe is CLOSED before continuing.
pause

echo.
echo [updater] Downloading latest release from GitHub...
REM /releases/latest/download/<asset> redirects to the most recent stable release.
curl -L --fail -o update.zip "https://github.com/dedlich/lolchecker/releases/latest/download/champ-assistant-windows.zip"
if errorlevel 1 (
    echo.
    echo [updater] download failed. Check your internet connection or try again.
    pause
    exit /b 1
)

echo.
echo [updater] Extracting over the current installation...
tar -xf update.zip
if errorlevel 1 (
    echo.
    echo [updater] extraction failed. Make sure champ-assistant.exe is fully closed.
    pause
    exit /b 1
)

del update.zip 2>nul

echo.
echo [updater] Done. Start champ-assistant.exe to use the new version.
pause
