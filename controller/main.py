import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
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


class _SuppressWin10054(logging.Filter):
    """Suppress the spurious WinError 10054 noise from asyncio on Windows.

    Browsers closing keep-alive connections trigger ConnectionResetError in
    the ProactorEventLoop. This is harmless but pollutes the log.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "WinError 10054" not in msg and "connection_lost" not in msg.lower()


logging.getLogger("asyncio").addFilter(_SuppressWin10054())

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
    # Add blocklist.source column if missing (for "IPs auto-added" stat)
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE blocklist ADD COLUMN source VARCHAR(20) DEFAULT 'manual'"))
            conn.commit()
        logger.info("Blocklist table: added source column")
    except Exception as e:
        if "duplicate column name" not in str(e).lower():
            logger.warning("Blocklist migration (source column): %s", e)
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE agents ADD COLUMN internal BOOLEAN DEFAULT 0"))
            conn.commit()
        logger.info("Agents table: added internal column")
    except Exception as e:
        if "duplicate column name" not in str(e).lower():
            logger.warning("Agents migration (internal column): %s", e)
    # Make wireguard_ip nullable (for internal agents): SQLite requires table recreate
    try:
        with engine.connect() as conn:
            r = conn.execute(text("PRAGMA table_info(agents)"))
            rows = r.fetchall()
            # Find wireguard_ip column: (cid, name, type, notnull, dflt_value, pk)
            wg_notnull = None
            for row in rows:
                if row[1] == "wireguard_ip":
                    wg_notnull = row[3]  # 1 = NOT NULL, 0 = nullable
                    break
            if wg_notnull == 1:
                conn.execute(text("PRAGMA foreign_keys=OFF"))
                conn.execute(text(
                    "CREATE TABLE agents_new (id INTEGER NOT NULL PRIMARY KEY, hostname VARCHAR(255) NOT NULL, "
                    "wireguard_ip VARCHAR(45), public_ip VARCHAR(45), status VARCHAR(20), last_heartbeat DATETIME, "
                    "active_connections INTEGER, cpu_percent FLOAT, memory_percent FLOAT, version VARCHAR(20), "
                    "internal BOOLEAN, created_at DATETIME, updated_at DATETIME)"
                ))
                conn.execute(text("CREATE UNIQUE INDEX ix_agents_wireguard_ip ON agents_new (wireguard_ip)"))
                conn.execute(text("INSERT INTO agents_new SELECT id, hostname, wireguard_ip, public_ip, status, last_heartbeat, active_connections, cpu_percent, memory_percent, version, COALESCE(internal, 0), created_at, updated_at FROM agents"))
                conn.execute(text("DROP TABLE agents"))
                conn.execute(text("ALTER TABLE agents_new RENAME TO agents"))
                conn.commit()
                logger.info("Agents table: wireguard_ip now nullable")
    except Exception as e:
        if "no such table: agents" in str(e).lower():
            pass  # Fresh DB
        else:
            logger.warning("Agents migration (wireguard_ip nullable): %s", e)
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE agents ADD COLUMN control_url VARCHAR(255)"))
            conn.commit()
        logger.info("Agents table: added control_url column")
    except Exception as e:
        if "duplicate column name" not in str(e).lower():
            logger.warning("Agents migration (control_url): %s", e)
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE agents ADD COLUMN agent_token VARCHAR(64)"))
            conn.commit()
        logger.info("Agents table: added agent_token column")
    except Exception as e:
        if "duplicate column name" not in str(e).lower():
            logger.warning("Agents migration (agent_token): %s", e)
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE global_settings ADD COLUMN api_token VARCHAR(64)"))
            conn.commit()
        logger.info("Global settings: added api_token column")
    except Exception as e:
        if "duplicate column name" not in str(e).lower():
            logger.warning("Global settings migration (api_token): %s", e)
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE global_settings ADD COLUMN controller_token VARCHAR(64)"))
            conn.commit()
        logger.info("Global settings: added controller_token column")
    except Exception as e:
        if "duplicate column name" not in str(e).lower():
            logger.warning("Global settings migration (controller_token): %s", e)
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE agents ADD COLUMN route_via_agent_id INTEGER"))
            conn.commit()
        logger.info("Agents table: added route_via_agent_id column")
    except Exception as e:
        if "duplicate column name" not in str(e).lower():
            logger.warning("Agents migration (route_via_agent_id): %s", e)
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE global_settings ADD COLUMN forward_proxy_port INTEGER DEFAULT 0"))
            conn.commit()
        logger.info("Global settings: added forward_proxy_port column")
    except Exception as e:
        if "duplicate column name" not in str(e).lower():
            logger.warning("Global settings migration (forward_proxy_port): %s", e)
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE global_settings ADD COLUMN forward_proxy_auth VARCHAR(255)"))
            conn.commit()
        logger.info("Global settings: added forward_proxy_auth column")
    except Exception as e:
        if "duplicate column name" not in str(e).lower():
            logger.warning("Global settings migration (forward_proxy_auth): %s", e)
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE global_settings ADD COLUMN dns_port INTEGER DEFAULT 0"))
            conn.commit()
        logger.info("Global settings: added dns_port column")
    except Exception as e:
        if "duplicate column name" not in str(e).lower():
            logger.warning("Global settings migration (dns_port): %s", e)
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE global_settings ADD COLUMN dns_upstream VARCHAR(255)"))
            conn.commit()
        logger.info("Global settings: added dns_upstream column")
    except Exception as e:
        if "duplicate column name" not in str(e).lower():
            logger.warning("Global settings migration (dns_upstream): %s", e)
    logger.info("Database initialized")

    # Initialize security tokens (generate if not already configured)
    from controller.database.database import SessionLocal
    from controller.database.repositories import GlobalSettingsRepository
    from controller.core.auth import generate_token
    _db = SessionLocal()
    try:
        gs_repo = GlobalSettingsRepository(_db)

        # Admin API token
        if not settings.api_token:
            gs = gs_repo.get_or_create()
            if gs.api_token:
                settings.api_token = gs.api_token
            else:
                token = generate_token()
                gs_repo.update(api_token=token)
                settings.api_token = token
                logger.warning("=" * 70)
                logger.warning("GENERATED ADMIN API TOKEN (save this):")
                logger.warning(f"  {token}")
                logger.warning("Set NEKO_API_TOKEN env var to use a fixed token.")
                logger.warning("=" * 70)

        # Controller token (used when calling agents' ControlAPI)
        if not settings.controller_token:
            gs = gs_repo.get()
            if gs and gs.controller_token:
                settings.controller_token = gs.controller_token
            else:
                token = generate_token()
                gs_repo.update(controller_token=token)
                settings.controller_token = token
                logger.info("Generated controller token for agent ControlAPI authentication")
    finally:
        _db.close()

    # Register event loop in live event bus so sync endpoints can push events safely
    from controller.core.live_events import live_events as _live_events
    _live_events.set_loop(asyncio.get_running_loop())

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
    version="4.0.0",
    lifespan=lifespan
)

# Middleware: redirect unauthenticated browser requests (web UI routes) to /login
@app.middleware("http")
async def session_redirect_middleware(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    # Only redirect HTML-accepting requests to non-API, non-static, non-login paths
    if (
        response.status_code == 401
        and not path.startswith("/api/")
        and not path.startswith("/static/")
        and path not in ("/login", "/logout", "/health")
        and "text/html" in request.headers.get("accept", "")
    ):
        return RedirectResponse(url="/login", status_code=302)
    return response


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


def _ensure_tls():
    """Auto-generate a self-signed TLS certificate if none is configured.

    Generates a cert covering all local IPs so the controller works over raw
    IP addresses without manual openssl commands. The cert is written next to
    the database file and reused on subsequent starts.
    """
    if settings.ssl_certfile and settings.ssl_keyfile:
        return  # Manually configured — nothing to do

    from pathlib import Path
    from shared.tls import ensure_cert, cert_fingerprint

    # Store cert alongside the database (cwd when running as a service)
    data_dir = Path.cwd()
    cert_path = data_dir / "nekoproxy-controller-cert.pem"
    key_path  = data_dir / "nekoproxy-controller-key.pem"

    generated, fp = ensure_cert(cert_path, key_path)

    settings.ssl_certfile = str(cert_path)
    settings.ssl_keyfile  = str(key_path)

    if generated:
        logger.warning("=" * 70)
        logger.warning("AUTO-GENERATED TLS CERTIFICATE")
        logger.warning("  Controller will start with HTTPS using a self-signed cert.")
        logger.warning("  Agents will download and cache this cert on first registration")
        logger.warning("  (Trust On First Use — same model as SSH).")
        logger.warning("  Certificate SHA-256 fingerprint:")
        logger.warning("  %s", fp)
        logger.warning("  Cert: %s", cert_path)
        logger.warning("=" * 70)
    else:
        logger.info("Using existing TLS certificate: %s (SHA-256: %s)", cert_path, fp)


def _controller_service_run(stop_event):
    """Run the controller (uvicorn) until stop_event is set. Used by Windows service."""
    import threading
    import uvicorn
    from shared.win_service import setup_service_logging
    setup_service_logging("nekoproxy-controller")
    _ensure_tls()
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_level="debug" if settings.debug else "info",
        ssl_certfile=settings.ssl_certfile or None,
        ssl_keyfile=settings.ssl_keyfile or None,
        # No console under the SCM: don't let uvicorn run its own dictConfig
        # (its colour formatter calls sys.stdout.isatty()). Our root file handler
        # from setup_service_logging catches uvicorn's propagated log records.
        log_config=None,
        use_colors=False,
    )
    server = uvicorn.Server(config)

    result = {}
    def _serve():
        try:
            server.run()
        except BaseException as e:  # port already in use at boot, bad cert, etc.
            result["error"] = e

    run_thread = threading.Thread(target=_serve, daemon=False)
    run_thread.start()

    # Wake on a stop request OR the server thread dying on its own - otherwise a
    # startup failure (e.g. port 8001 busy) would leave the service stuck
    # "Running" and the SCM recovery actions would never fire.
    while not stop_event.wait(timeout=1):
        if not run_thread.is_alive():
            break
    server.should_exit = True
    run_thread.join(timeout=15)

    if "error" in result:
        raise result["error"]
    if not stop_event.is_set():
        raise RuntimeError("uvicorn exited unexpectedly")


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
        _run_callback = staticmethod(_controller_service_run)
    return ControllerService


if __name__ == "__main__":
    import sys
    import uvicorn

    if sys.platform == "win32":
        cmd = sys.argv[1].lower() if len(sys.argv) > 1 else ""
        # The SCM launches the exe as "<exe> service" (see win_service._exe_args_).
        # Hosting a frozen service means: Initialize -> PrepareToHostSingle ->
        # StartServiceCtrlDispatcher. StartServiceCtrlDispatcher is what actually
        # connects this process to the SCM; without it the service class's
        # RegisterServiceCtrlHandler in __init__ fails and the process dies before
        # anything runs (Start-Service -> error 1053).
        if cmd == "service":
            import servicemanager
            ControllerService = _define_controller_service()
            if ControllerService is not None:
                try:
                    servicemanager.Initialize()
                    servicemanager.PrepareToHostSingle(ControllerService)
                    servicemanager.StartServiceCtrlDispatcher()
                except SystemExit:
                    raise
                except BaseException as e:
                    # 1063 = not started by the SCM (someone ran "<exe> service" by hand)
                    if getattr(e, "winerror", None) == 1063:
                        print("This command is for the Service Control Manager. "
                              "Use install-controller.ps1 (or '<exe> install' then 'start').")
                        sys.exit(1)
                    raise
                sys.exit(0)
        # User ran exe with install/start/stop/remove/debug
        if cmd in ("install", "update", "start", "stop", "remove", "debug"):
            import win32serviceutil
            ControllerService = _define_controller_service()
            if ControllerService is not None:
                win32serviceutil.HandleCommandLine(ControllerService, argv=sys.argv)
                sys.exit(0)

    # Auto-generate TLS cert if not manually configured
    _ensure_tls()

    # Check if running as frozen executable (PyInstaller)
    is_frozen = getattr(sys, 'frozen', False)

    uvicorn.run(
        app,  # Use app object directly for frozen builds
        host=settings.host,
        port=settings.port,
        reload=False if is_frozen else settings.debug,
        log_level="debug" if settings.debug else "info",
        ssl_certfile=settings.ssl_certfile or None,
        ssl_keyfile=settings.ssl_keyfile or None,
    )
