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

    # Config versioning
    config_version: int = 1

    # Stats cleanup
    stats_retention_days: int = 30

    # Web UI - paths resolved at runtime
    @property
    def templates_dir(self) -> Path:
        return get_base_path() / "controller" / "web" / "templates"

    @property
    def static_dir(self) -> Path:
        return get_base_path() / "controller" / "web" / "static"

    class Config:
        env_prefix = "NEKO_"
        env_file = ".env"


settings = Settings()
