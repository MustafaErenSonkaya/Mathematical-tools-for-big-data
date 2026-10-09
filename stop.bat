@echo off
REM Windows: double-click this file to stop everything. Your data is kept.

cd /d "%~dp0"
docker compose down
echo.
echo  Stopped. Run start.bat to start again.
echo.
pause
