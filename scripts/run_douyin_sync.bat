@echo off
chcp 65001 >nul
setlocal

:: Get script directory and project root
set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%.."
cd /d "%PROJECT_ROOT%"

:: Ensure output directory exists
if not exist "output" mkdir "output"

echo [%DATE% %TIME%] Starting Douyin Podcast Sync... >> "output\douyin_sync_cron.log"
python run_douyin_sync.py >> "output\douyin_sync_cron.log" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"
echo [%DATE% %TIME%] Sync finished with exit code %EXIT_CODE% >> "output\douyin_sync_cron.log"

exit /b %EXIT_CODE%
