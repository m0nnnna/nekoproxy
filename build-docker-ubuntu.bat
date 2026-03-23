@echo off
REM NekoProxy Docker Build — Ubuntu 20.04 (glibc)
REM
REM Builds Linux binaries inside a Docker container (Ubuntu 20.04 base).
REM Use these binaries on Ubuntu / Debian / any glibc-based Linux distro.
REM For Alpine Linux use build-docker-alpine.bat instead.
REM
REM Usage:
REM   build-docker-ubuntu.bat [all|agent|controller]
REM
REM Output:
REM   dist\linux\nekoproxy-agent
REM   dist\linux\nekoproxy-controller

setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "COMPONENT=%~1"
if "%COMPONENT%"=="" set "COMPONENT=all"

set "IMAGE_NAME=nekoproxy-builder"
set "VOLUME_NAME=nekoproxy-build-output"
set "OUTPUT_DIR=%SCRIPT_DIR%dist\linux"

echo ============================================================
echo NekoProxy Docker Build -- Ubuntu 20.04 (glibc)
echo ============================================================
echo Target   : Ubuntu 20.04 LTS (glibc 2.31+)
echo Component: %COMPONENT%
echo Output   : %OUTPUT_DIR%
echo.

REM Validate component arg
if /i "%COMPONENT%"=="all"        goto :valid_component
if /i "%COMPONENT%"=="agent"      goto :valid_component
if /i "%COMPONENT%"=="controller" goto :valid_component
echo Error: Unknown component "%COMPONENT%"
echo Usage: build-docker-ubuntu.bat [all^|agent^|controller]
pause
exit /b 1
:valid_component

REM Check Docker is available
echo Checking Docker...
docker info
if %errorlevel% neq 0 (
    echo.
    echo Error: Docker is not running or not installed.
    echo Start Docker Desktop and try again.
    pause
    exit /b 1
)
echo Docker is running.
echo.

REM Prepare output directory
if not exist "%OUTPUT_DIR%" mkdir "%OUTPUT_DIR%"

REM ── Step 1: Build the Docker builder image ──────────────────────────────────
echo [1/3] Building Docker builder image (%IMAGE_NAME%)...
echo       This will take a few minutes on first run (downloading base image + pip install).
echo.
docker build -t "%IMAGE_NAME%" -f build\Dockerfile.linux .
if %errorlevel% neq 0 (
    echo.
    echo Error: Docker image build failed. See output above.
    pause
    exit /b 1
)

REM ── Step 2: Run PyInstaller inside the container ────────────────────────────
echo.
echo [2/3] Running PyInstaller inside container...
echo.

docker volume rm "%VOLUME_NAME%" >nul 2>&1
docker volume create "%VOLUME_NAME%" >nul

docker run --rm ^
    -v "%VOLUME_NAME%:/output" ^
    "%IMAGE_NAME%" ^
    "%COMPONENT%"

if %errorlevel% neq 0 (
    echo.
    echo Error: Build inside container failed. See output above.
    docker volume rm "%VOLUME_NAME%" >nul 2>&1
    pause
    exit /b 1
)

REM ── Step 3: Copy output from the named volume to local dist\linux ────────────
echo.
echo [3/3] Copying build artifacts to %OUTPUT_DIR%...

for /f "delims=" %%C in ('docker create -v "%VOLUME_NAME%:/output" alpine') do set "TEMP_CONTAINER=%%C"
if "%TEMP_CONTAINER%"=="" (
    echo Error: Could not create helper container to copy artifacts.
    docker volume rm "%VOLUME_NAME%" >nul 2>&1
    pause
    exit /b 1
)

docker cp "%TEMP_CONTAINER%:/output/." "%OUTPUT_DIR%"
docker rm "%TEMP_CONTAINER%" >nul
docker volume rm "%VOLUME_NAME%" >nul 2>&1

echo.
echo ============================================================
echo Build complete!
echo ============================================================
echo Output directory: %OUTPUT_DIR%
dir /b "%OUTPUT_DIR%" 2>nul
echo.
echo Copy binaries to your Linux server, then:
echo   chmod +x nekoproxy-agent nekoproxy-controller
echo   ./install-agent.sh       (agent servers)
echo   ./install-controller.sh  (controller server)
echo.
pause
endlocal
