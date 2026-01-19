import socket
from pydantic_settings import BaseSettings
from typing import Optional


def get_hostname() -> str:
    """Get the system hostname."""
    return socket.gethostname()


class AgentSettings(BaseSettings):
    # Agent identification
    hostname: str = get_hostname()
    wireguard_ip: str = "10.0.0.1"  # Must be configured
    public_ip: Optional[str] = None
    version: str = "1.0.0"

    # Controller connection
    controller_url: str = "http://localhost:8001"

    # Network settings
    listen_ip: str = "0.0.0.0"
    buffer_size: int = 8192
    connection_timeout: int = 10

    # Heartbeat
    heartbeat_interval: int = 30

    # Stats reporting
    stats_batch_size: int = 100
    stats_report_interval: int = 60

    class Config:
        env_prefix = "NEKO_AGENT_"
        env_file = ".env"


settings = AgentSettings()
