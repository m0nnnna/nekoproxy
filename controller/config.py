import sys
from pathlib import Path
from pydantic_settings import BaseSettings
from typing import Optional


def get_base_path() -> Path:
    """Get the base path for resources, handling frozen executables."""
    if getattr(sys, 'frozen', False):
        # Running as compiled executable
        return Path(sys._MEIPASS)
    else:
        # Running as script
        return Path(__file__).parent


class Settings(BaseSettings):
    # Application
    app_name: str = "NekoProxy Controller"
    debug: bool = False

    # Server
    host: str = "0.0.0.0"
    port: int = 8001

    # Database
    database_url: str = "sqlite:///./nekoproxy.db"

    # Agent settings
    heartbeat_interval: int = 30  # seconds
    heartbeat_timeout: int = 90   # seconds before marking unhealthy
    # Optional: require this secret for agent registration (set NEKO_AGENT_SECRET)
    agent_secret: Optional[str] = None
    # Geo filtering (pushed to all agents): off | allowlist | blocklist; countries = comma-separated ISO codes
    geo_mode: str = "off"
    geo_countries: str = ""
    # Idle connection timeout (seconds) for proxy connections; 0 = disabled
    idle_connection_timeout_seconds: int = 0
    # Paranoid preset (DDoS lockdown): when True, force stricter defaults in config sent to agents
    paranoid: bool = False

    # Config versioning
    config_version: int = 1

    # Stats cleanup
    stats_retention_days: int = 30

    # Agent binary uploads (for push-update): directory to store uploaded agent binaries
    uploads_dir: str = "./uploads/agent"

    # Security: admin API token (required for all API requests).
    # Auto-generated on first run and printed to the log if not provided.
    api_token: Optional[str] = None

    # Security: token the controller sends to agents when calling their ControlAPI.
    # Auto-generated on first run. Set NEKO_CONTROLLER_TOKEN to override.
    controller_token: Optional[str] = None

    # TLS: paths to PEM certificate and key for HTTPS.
    # If both are set, uvicorn listens with TLS (recommended for production).
    ssl_certfile: Optional[str] = None
    ssl_keyfile: Optional[str] = None

    # When calling agents' ControlAPI over HTTPS, verify their certificate.
    # Defaults to False because agents use auto-generated self-signed certs.
    # Set to True only when agents use CA-signed certs or a shared CA is configured.
    agent_api_ssl_verify: bool = False

    # Web UI - paths resolved at runtime
    @property
    def templates_dir(self) -> Path:
        return get_base_path() / "controller" / "web" / "templates"

    @property
    def static_dir(self) -> Path:
        return get_base_path() / "controller" / "web" / "static"

    @property
    def uploads_agent_dir(self) -> Path:
        """Directory for uploaded agent binaries (push-update). Resolved at runtime."""
        p = Path(self.uploads_dir)
        if not p.is_absolute():
            # Relative to cwd when running (or to executable dir when frozen)
            p = Path.cwd() / p
        return p

    class Config:
        env_prefix = "NEKO_"
        env_file = ".env"


settings = Settings()
