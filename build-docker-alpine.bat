@echo off
REM NekoProxy Docker Build — Alpine Linux (musl)
REM
REM Builds Linux binaries inside a Docker container (Alpine 3.19 base / musl libc).
REM Use these binaries on Alpine Linux ONLY.
REM For Ubuntu / Debian / glibc-based distros use build-docker-ubuntu.bat instead.
REM
REM Usage:
REM   build-docker-alpine.bat [all|agent|controller]
REM
REM Output:
REM   dist\linux-alpine\nekoproxy-agent
REM   dist\linux-alpine\nekoproxy-controller

setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "COMPONENT=%~1"
if "%COMPONENT%"=="" set "COMPONENT=all"

set "IMAGE_NAME=nekoproxy-builder-alpine"
set "VOLUME_NAME=nekoproxy-build-output-alpine"
set "OUTPUT_DIR=%SCRIPT_DIR%dist\linux-alpine"

echo ============================================================
echo NekoProxy Docker Build -- Alpine Linux (musl)
echo ============================================================
echo Target   : Alpine 3.19 (musl libc)
echo Component: %COMPONENT%
echo Output   : %OUTPUT_DIR%
echo.

REM Validate component arg
if /i "%COMPONENT%"=="all"        goto :valid_component
if /i "%COMPONENT%"=="agent"      goto :valid_component
if /i "%COMPONENT%"=="controller" goto :valid_component
echo Error: Unknown component "%COMPONENT%"
echo Usage: build-docker-alpine.bat [all^|agent^|controller]
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
docker build -t "%IMAGE_NAME%" -f build\Dockerfile.linux.alpine .
if %errorlevel% neq 0 (
    echo.
    echo Error: Docker image build failed. See output above.
    pause
    exit /b 1
)

REM ── Step 2: Run PyInstaller inside the container ────────────────────────────
echo.
echo [2/3] Running PyInstaller inside container (Alpine)...
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

REM ── Step 3: Copy output from the named volume to local dist\linux-alpine ────
echo.
echo [3/3] Copying build artifacts to %OUTPUT_DIR%...

for /f "delims=" %%C in ('docker create -v "%VOLUME_NAME%:/output" alpine:3.19') do set "TEMP_CONTAINER=%%C"
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
echo Note: Alpine binaries only run on Alpine Linux (musl libc).
echo       For Ubuntu/Debian use build-docker-ubuntu.bat instead.
echo.
pause
endlocal
