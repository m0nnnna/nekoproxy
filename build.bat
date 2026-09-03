@echo off
REM NekoProxy Build Script for Windows
REM
REM Builds the controller and/or agent for Windows.
REM
REM Usage:
REM   build.bat [controller|agent|all] [--clean]
REM
REM Examples:
REM   build.bat controller   - Build controller for Windows
REM   build.bat agent        - Build agent for Windows
REM   build.bat all          - Build controller and agent
REM   build.bat --clean       - Clean build artifacts

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
    set "COMPONENT=agent"
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
    if exist build\agent rmdir /s /q build\agent
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
if "%COMPONENT%"=="agent" goto :build_agent
if "%COMPONENT%"=="all" goto :build_controller
goto :show_usage

:build_controller
echo.
echo ============================================================
echo Building Controller for Windows...
echo ============================================================

python -m PyInstaller --clean --noconfirm --distpath dist\windows --workpath build\controller build\controller.spec

if exist "dist\windows\nekoproxy-controller\nekoproxy-controller.exe" (
    echo.
    echo Controller built successfully ^(onedir^): dist\windows\nekoproxy-controller\
) else (
    echo Error: Controller build failed!
    exit /b 1
)
if "%COMPONENT%"=="all" goto :build_agent
goto :build_done

:build_agent
echo.
echo ============================================================
echo Building Agent for Windows...
echo ============================================================

python -m PyInstaller --clean --noconfirm --distpath dist\windows --workpath build\agent build\agent.spec

if exist "dist\windows\nekoproxy-agent\nekoproxy-agent.exe" (
    echo.
    echo Agent built successfully ^(onedir^): dist\windows\nekoproxy-agent\
) else (
    echo Error: Agent build failed!
    exit /b 1
)
goto :build_done

:build_done
echo.
echo Copying Windows install/update scripts to dist\windows\ ...
if not exist dist\windows mkdir dist\windows
copy /y install-controller.ps1 dist\windows\ >nul 2>&1
copy /y install-agent.ps1      dist\windows\ >nul 2>&1
copy /y update-controller.ps1  dist\windows\ >nul 2>&1
copy /y update-agent.ps1       dist\windows\ >nul 2>&1
copy /y agent.env.example      dist\windows\ >nul 2>&1

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
echo Usage: build.bat [controller^|agent^|all] [--clean]
echo.
echo Components:
echo   controller  Build the controller for Windows
echo   agent       Build the agent for Windows
echo   all         Build controller and agent
echo.
echo Options:
echo   --clean     Clean build artifacts before building
echo.
echo Examples:
echo   build.bat controller       - Build controller
echo   build.bat agent            - Build agent
echo   build.bat all              - Build both
echo   build.bat --clean all      - Clean and rebuild both
exit /b 1
