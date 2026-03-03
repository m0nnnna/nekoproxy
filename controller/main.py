import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from controller.config import settings
from controller.database.database import engine, Base
import controller.database.models  # noqa: F401 - register all models (including GlobalSettings) with Base
from controller.api.v1 import agents, services, assignments, stats, blocklist, firewall, alerts, email
from controller.web import routes as web_routes
from controller.core.health_monitor import HealthMonitor

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Health monitor instance
health_monitor: HealthMonitor = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    global health_monitor

    # Startup
    logger.info("Starting NekoProxy Controller...")

    # Create database tables
    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized")

    # Start health monitor
    health_monitor = HealthMonitor()
    await health_monitor.start()
    logger.info("Health monitor started")

    yield

    # Shutdown
    logger.info("Shutting down NekoProxy Controller...")
    if health_monitor:
        await health_monitor.stop()


app = FastAPI(
    title=settings.app_name,
    description="Multi-agent proxy service controller",
    version="2.0.0",
    lifespan=lifespan
)

# Mount static files
settings.static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")

# Include API routers
app.include_router(agents.router, prefix="/api/v1/agents", tags=["agents"])
app.include_router(services.router, prefix="/api/v1/services", tags=["services"])
app.include_router(assignments.router, prefix="/api/v1/assignments", tags=["assignments"])
app.include_router(stats.router, prefix="/api/v1/stats", tags=["stats"])
app.include_router(blocklist.router, prefix="/api/v1/blocklist", tags=["blocklist"])
app.include_router(firewall.router, prefix="/api/v1/firewall", tags=["firewall"])
app.include_router(alerts.router, prefix="/api/v1/alerts", tags=["alerts"])
app.include_router(email.router, prefix="/api/v1/email", tags=["email"])

# Include web routes
app.include_router(web_routes.router, tags=["web"])


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": settings.app_name}


def _controller_service_run(stop_event):
    """Run the controller (uvicorn) until stop_event is set. Used by Windows service."""
    import threading
    import uvicorn
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_level="debug" if settings.debug else "info",
    )
    server = uvicorn.Server(config)
    run_thread = threading.Thread(target=server.run, daemon=False)
    run_thread.start()
    stop_event.wait()
    server.should_exit = True
    run_thread.join(timeout=15)


# Windows service class (used when run as service or with install/start/stop/remove/debug)
def _define_controller_service():
    if __name__ != "__main__":
        return None
    import sys
    if sys.platform != "win32":
        return None
    import win32serviceutil
    from shared.win_service import NekoProxyServiceFramework

    class ControllerService(NekoProxyServiceFramework):
        _svc_name_ = "nekoproxy-controller"
        _svc_display_name_ = "NekoProxy Controller"
        _svc_description_ = "NekoProxy central management server (web UI and API)"
        _run_callback = _controller_service_run
    return ControllerService


if __name__ == "__main__":
    import sys
    import uvicorn

    if sys.platform == "win32":
        # SCM runs the exe with arg "service" (argv = [exe_path, "service"]); we host the service
        if len(sys.argv) >= 2 and sys.argv[1].lower() == "service":
            import servicemanager
            ControllerService = _define_controller_service()
            if ControllerService is not None:
                servicemanager.PrepareToHostSingle(ControllerService)
                ControllerService(sys.argv).SvcRun()
                sys.exit(0)
        # User ran exe with install/start/stop/remove/debug
        cmd = sys.argv[1].lower() if len(sys.argv) > 1 else ""
        if cmd in ("install", "update", "start", "stop", "remove", "debug"):
            import win32serviceutil
            ControllerService = _define_controller_service()
            if ControllerService is not None:
                win32serviceutil.HandleCommandLine(ControllerService, argv=sys.argv)
                sys.exit(0)

    # Check if running as frozen executable (PyInstaller)
    is_frozen = getattr(sys, 'frozen', False)

    uvicorn.run(
        app,  # Use app object directly for frozen builds
        host=settings.host,
        port=settings.port,
        reload=False if is_frozen else settings.debug,
        log_level="debug" if settings.debug else "info"
    )
