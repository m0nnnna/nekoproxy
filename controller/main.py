import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from controller.config import settings
from controller.database.database import engine, Base
from controller.api.v1 import agents, services, assignments, stats, blocklist, firewall, alerts
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
    version="1.0.0",
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

# Include web routes
app.include_router(web_routes.router, tags=["web"])


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": settings.app_name}


if __name__ == "__main__":
    import sys
    import uvicorn

    # Check if running as frozen executable (PyInstaller)
    is_frozen = getattr(sys, 'frozen', False)

    uvicorn.run(
        app,  # Use app object directly for frozen builds
        host=settings.host,
        port=settings.port,
        reload=False if is_frozen else settings.debug,
        log_level="debug" if settings.debug else "info"
    )
