@echo off
REM NekoProxy Build Script for Windows
REM
REM This script builds the controller for Windows.
REM Note: Agent is only supported on Linux (Ubuntu).
REM
REM Usage:
REM   build.bat [controller|all] [--clean]
REM
REM Examples:
REM   build.bat controller   - Build controller for Windows
REM   build.bat --clean      - Clean build artifacts

setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

REM Parse arguments
set "COMPONENT="
set "CLEAN="

:parse_args
if "%~1"=="" goto :after_parse
if /i "%~1"=="controller" (
    set "COMPONENT=controller"
) else if /i "%~1"=="all" (
    set "COMPONENT=all"
) else if /i "%~1"=="agent" (
    echo.
    echo Warning: Agent is only supported on Linux ^(Ubuntu^).
    echo To build the agent, run build.sh on an Ubuntu system.
    echo.
    exit /b 1
) else if /i "%~1"=="--clean" (
    set "CLEAN=1"
) else if /i "%~1"=="-h" (
    goto :show_usage
) else if /i "%~1"=="--help" (
    goto :show_usage
) else (
    echo Error: Unknown argument: %~1
    goto :show_usage
)
shift
goto :parse_args

:after_parse

if not defined COMPONENT if not defined CLEAN goto :show_usage

echo ============================================================
echo NekoProxy Build System - Windows
echo ============================================================
echo Platform: Windows
echo Project: %SCRIPT_DIR%
echo.

REM Clean if requested
if defined CLEAN (
    echo Cleaning build artifacts...
    if exist dist\windows rmdir /s /q dist\windows
    if exist build\controller rmdir /s /q build\controller
    for /d /r %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d"
    echo Clean complete.
    echo.
)

if not defined COMPONENT exit /b 0

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python is not installed or not in PATH.
    exit /b 1
)

REM Install PyInstaller if needed
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    python -m pip install pyinstaller
)

REM Install dependencies
echo Installing dependencies...
python -m pip install -r requirements.txt -q

if "%COMPONENT%"=="controller" goto :build_controller
if "%COMPONENT%"=="all" goto :build_controller

:build_controller
echo.
echo ============================================================
echo Building Controller for Windows...
echo ============================================================

python -m PyInstaller --clean --noconfirm --distpath dist\windows --workpath build\controller build\controller.spec

if exist "dist\windows\nekoproxy-controller.exe" (
    echo.
    echo Controller built successfully!
    for %%A in (dist\windows\nekoproxy-controller.exe) do echo Output: dist\windows\nekoproxy-controller.exe ^(%%~zA bytes^)
) else (
    echo Error: Controller build failed!
    exit /b 1
)

echo.
echo ============================================================
echo Build Complete!
echo ============================================================
echo Output directory: dist\windows\
dir /b dist\windows\ 2>nul
exit /b 0

:show_usage
echo.
echo NekoProxy Build Script for Windows
echo.
echo Usage: build.bat [controller^|all] [--clean]
echo.
echo Components:
echo   controller  Build the controller for Windows
echo   all         Build all Windows-supported components ^(controller only^)
echo.
echo Options:
echo   --clean     Clean build artifacts before building
echo.
echo Examples:
echo   build.bat controller       - Build controller
echo   build.bat --clean all      - Clean and rebuild
echo.
echo Note: Agent is only supported on Linux. Run build.sh on Ubuntu to build the agent.
exit /b 1
