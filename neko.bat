@echo off
setlocal

set CMD=%~1

if "%CMD%"=="" goto :usage
if /i "%CMD%"=="build"  goto :build
if /i "%CMD%"=="up"     goto :up
if /i "%CMD%"=="down"   goto :down
if /i "%CMD%"=="restart" goto :restart
if /i "%CMD%"=="logs"   goto :logs
if /i "%CMD%"=="ps"     goto :ps
if /i "%CMD%"=="shell"  goto :shell
goto :usage

:build
echo Building NekoProxy Docker images...
docker compose build
if %errorlevel% neq 0 ( echo Build failed. & exit /b 1 )
echo Done. Run:  neko.bat up
goto :end

:up
echo Starting NekoProxy...
REM Create env files from examples if they don't exist yet
if not exist "docker\controller.env" (
    echo NOTE: docker\controller.env not found - copying from example.
    echo       Edit docker\controller.env to set tokens before going to production.
    copy "docker\controller.env.example" "docker\controller.env" >nul
)
if not exist "docker\agent.env" (
    echo NOTE: docker\agent.env not found - copying from example.
    copy "docker\agent.env.example" "docker\agent.env" >nul
)
docker compose up -d --build
if %errorlevel% neq 0 ( echo Startup failed. & exit /b 1 )
echo.
echo Controller web UI:  http://localhost:8001
echo Check logs for the auto-generated admin API token if you have not set NEKO_API_TOKEN.
echo Run:  neko.bat logs controller
goto :end

:down
echo Stopping NekoProxy...
docker compose down
goto :end

:restart
echo Restarting NekoProxy...
docker compose restart %~2
goto :end

:logs
REM  neko.bat logs              -> all services
REM  neko.bat logs controller   -> controller only
REM  neko.bat logs agent        -> agent only
if "%~2"=="" (
    docker compose logs -f
) else (
    docker compose logs -f %~2
)
goto :end

:ps
docker compose ps
goto :end

:shell
REM  neko.bat shell controller  -> bash in controller container
REM  neko.bat shell agent       -> bash in agent container
if "%~2"=="" ( echo Usage: neko.bat shell ^<controller^|agent^> & exit /b 1 )
docker compose exec %~2 bash
goto :end

:usage
echo.
echo  NekoProxy Docker helper
echo.
echo  Usage:  neko.bat ^<command^> [service]
echo.
echo  Commands:
echo    build              Build (or rebuild) both images
echo    up                 Start all services in the background
echo    down               Stop and remove containers
echo    restart [service]  Restart all services or a specific one
echo    logs    [service]  Tail logs (all services, or controller / agent)
echo    ps                 Show running containers
echo    shell   ^<service^>  Open a bash shell inside a container
echo.
echo  Examples:
echo    neko.bat up
echo    neko.bat logs controller
echo    neko.bat shell agent
echo    neko.bat restart controller
echo.
exit /b 0

:end
endlocal
