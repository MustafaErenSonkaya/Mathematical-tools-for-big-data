@echo off
REM Windows: double-click this file to start everything (app + Prometheus + Grafana).
REM It runs "docker compose up -d --build" from this folder, then opens the dashboard.

cd /d "%~dp0"

docker info >nul 2>&1
if errorlevel 1 (
    echo.
    echo  Docker is not running.
    echo  Open Docker Desktop, wait until it says "Engine running", then run start.bat again.
    echo.
    pause
    exit /b 1
)

echo.
echo  Building and starting... The first time this downloads about 1 GB and takes a few minutes.
echo.
docker compose up -d --build
if errorlevel 1 (
    echo.
    echo  Something went wrong. See the "Troubleshooting" section in README.md.
    echo.
    pause
    exit /b 1
)

echo.
echo  Started! Waiting a few seconds for Grafana, then opening http://localhost:3000
REM Wait ~15 seconds (ping is used as a portable sleep).
ping -n 16 127.0.0.1 >nul
start "" http://localhost:3000
echo.
echo  The charts fill up during the first minute.
echo  To stop everything, double-click stop.bat
echo.
pause
