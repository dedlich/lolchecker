@echo off
REM Champ Assistant updater — downloads latest GitHub Release, extracts in place.
REM Usage: place this script next to champ-assistant.exe, double-click. Close the
REM app first if it's running (Windows can't overwrite a running EXE).

setlocal enabledelayedexpansion

if not exist "champ-assistant.exe" (
    echo [updater] error: run this from the champ-assistant folder ^(no champ-assistant.exe found here^).
    pause
    exit /b 1
)

echo [updater] checking GitHub for the latest release...
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "(Invoke-RestMethod -Uri 'https://api.github.com/repos/dedlich/lolchecker/releases/latest' -Headers @{'Accept'='application/vnd.github+json'}).tag_name"`) do set TAG=%%i

if "!TAG!"=="" (
    echo [updater] error: could not fetch the latest release tag. Check your internet.
    pause
    exit /b 1
)

echo [updater] latest version: !TAG!

set ZIP_NAME=champ-assistant-windows.zip
set DOWNLOAD_URL=https://github.com/dedlich/lolchecker/releases/download/!TAG!/!ZIP_NAME!

echo [updater] please close champ-assistant.exe before continuing.
pause

echo [updater] downloading %DOWNLOAD_URL% ...
powershell -NoProfile -Command "Invoke-WebRequest -Uri '%DOWNLOAD_URL%' -OutFile 'update.zip' -UseBasicParsing"
if errorlevel 1 (
    echo [updater] error: download failed.
    pause
    exit /b 1
)

echo [updater] extracting...
powershell -NoProfile -Command "Expand-Archive -Path 'update.zip' -DestinationPath . -Force"
if errorlevel 1 (
    echo [updater] error: extraction failed. Make sure the EXE isn't running.
    pause
    exit /b 1
)

del /q update.zip 2>nul

echo.
echo [updater] done. Restart champ-assistant.exe to pick up !TAG!.
pause
