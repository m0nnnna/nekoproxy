import socket
import sys
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import model_validator
from typing import Optional


def get_hostname() -> str:
    """Get the system hostname."""
    return socket.gethostname()


def _agent_config_dir() -> Path:
    """Directory for agent config file. On Windows (frozen exe), use exe dir; else cwd."""
    if getattr(sys, "frozen", False) and getattr(sys, "executable", None):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


class AgentSettings(BaseSettings):
    # Agent identification
    hostname: str = get_hostname()
    wireguard_ip: Optional[str] = None  # Optional for internal agents (no WireGuard)
    public_ip: Optional[str] = None
    version: str = "2.0.0"

    # Controller connection
    controller_url: str = "http://localhost:8001"

    # Network settings
    listen_ip: str = "0.0.0.0"
    buffer_size: int = 8192
    connection_timeout: int = 10

    # Heartbeat
    heartbeat_interval: int = 30

    # Control API (for receiving push notifications from controller)
    api_port: int = 8002
    # Optional base URL for controller to reach this agent (e.g. http://127.0.0.1:8002 when agent and controller on same machine)
    control_url: Optional[str] = None

    # Stats reporting
    stats_batch_size: int = 100
    stats_report_interval: int = 60

    # Firewall baseline (auto-generated rules for public vs WireGuard)
    # When True: public interface = default-deny, only proxy ports + established allowed
    harden_public_interface: bool = True
    # When True: WireGuard interface = allow (so controller/SSH over WG still work)
    allow_wireguard_freedom: bool = True

    # Safe out-of-the-box: bind proxy to WireGuard only (no proxy listeners on public)
    listen_on_wireguard_only: bool = False

    # Rate limiting: max new connections per client IP per minute; 0 = disabled
    rate_limit_per_minute: int = 60
    # When True, IPs that exceed rate limit are auto-blocked and reported to controller
    rate_limit_auto_block: bool = True

    # Optional: secret for agent registration (must match controller NEKO_AGENT_SECRET)
    agent_secret: Optional[str] = None

    # GeoIP: path to MaxMind GeoLite2-Country.mmdb (optional; enable geo allow/block on controller)
    geolite2_db_path: Optional[str] = None

    # Paranoid preset (DDoS lockdown): one env turns on stricter defaults
    paranoid: bool = False

    # Install directory for push-update (where binary and update script live). Default: Linux /opt/nekoproxy, Windows = exe dir
    install_dir: Optional[str] = None

    @property
    def install_dir_resolved(self) -> Path:
        if self.install_dir:
            return Path(self.install_dir)
        if sys.platform == "win32":
            exe = getattr(sys, "executable", None) or __file__
            return Path(exe).resolve().parent
        return Path("/opt/nekoproxy")

    class Config:
        env_prefix = "NEKO_AGENT_"
        # Load .env from cwd first, then agent.env / .env from exe dir (Windows) so exe-dir overrides
        env_file = [".env", str(_agent_config_dir() / "agent.env"), str(_agent_config_dir() / ".env")]

    @model_validator(mode="after")
    def apply_paranoid_preset(self):
        """When paranoid=True, apply maximum lockdown defaults."""
        if not self.paranoid:
            return self
        self.rate_limit_per_minute = 30
        self.listen_on_wireguard_only = True
        self.rate_limit_auto_block = True
        return self


settings = AgentSettings()
