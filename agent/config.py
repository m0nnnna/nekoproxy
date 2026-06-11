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


def _agent_env_files() -> list:
    """Candidate env files, lowest precedence first (later entries win).

    The Linux installer (install-agent.sh) writes config to /etc/nekoproxy/agent.env
    and points systemd's EnvironmentFile at it — but the loader historically only
    looked in cwd and the exe dir (/opt/nekoproxy). The two disagreed, so running
    the binary outside systemd (or any time EnvironmentFile wasn't applied) silently
    fell back to the localhost:8001 default instead of reusing the real controller
    URL. Include the canonical /etc path so there's one consistent source of truth.
    """
    files = [".env", str(_agent_config_dir() / "agent.env"), str(_agent_config_dir() / ".env")]
    if sys.platform != "win32":
        files.append("/etc/nekoproxy/agent.env")
    return files


class AgentSettings(BaseSettings):
    # Agent identification
    hostname: str = get_hostname()
    wireguard_ip: Optional[str] = None  # Optional for internal agents (no WireGuard)
    public_ip: Optional[str] = None
    version: str = "3.0.0"

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
    # Explicit bind IP for the ControlAPI server.
    # Defaults to wireguard_ip when set; otherwise 127.0.0.1 (loopback-only, not reachable from public internet).
    # Set to 0.0.0.0 only when the controller must reach this agent without WireGuard AND port 8002
    # is protected at the network level (firewall/VPC/security group).
    control_bind_ip: Optional[str] = None

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

    # Forward proxy server: accept HTTP/HTTPS proxy connections from devices on the network.
    # Set a port to enable. Devices point their proxy settings at agent-ip:port.
    # Traffic exits via upstream_proxy if set (routes out from VPS IP), else direct.
    # Format: NEKO_AGENT_FORWARD_PROXY_PORT=8080
    forward_proxy_port: int = 0  # 0 = disabled
    # Optional Basic auth for the forward proxy: "username:password"
    # NEKO_AGENT_FORWARD_PROXY_AUTH=myuser:mypassword
    forward_proxy_auth: Optional[str] = None
    # Max concurrent forward-proxy connections (hard ceiling on open sockets/FDs).
    # Federating servers (Misskey/Matrix) burst hundreds of outbound connections;
    # without a cap the agent exhausts file descriptors and crashes. Over-capacity
    # connections wait briefly then get a fast 503 instead of dragging the process
    # down. Default 500 is sized for a 1 vCPU / 1 GB VPS (~125 MB worst case);
    # raise it on bigger hosts (≈1000 per 2 GB, ≈3000 per 4 GB+).
    forward_proxy_max_connections: int = 500
    # Idle timeout (seconds) for an established tunnel/relay. A connection with no
    # data in either direction for this long is reaped. Replaces the old absolute
    # lifetime cap that killed active long media transfers and Matrix keepalives.
    forward_proxy_idle_timeout: int = 180
    # How long (seconds) a new connection waits for a free slot when the proxy is
    # at capacity before being shed with 503. A short queue absorbs bursts so most
    # connections are still served; past it, shedding protects a small VPS.
    forward_proxy_overflow_wait: int = 5
    # Max concurrent connections to any single destination host (0 = unlimited).
    # Stops one slow/dead federated server from holding a large share of the pool
    # (idle tunnels linger up to forward_proxy_idle_timeout), starving healthy
    # traffic. ~6% of the global cap is a sane share.
    forward_proxy_max_per_host: int = 32

    # Upstream proxy: route all backend connections through this proxy (exit from VPS, not local IP).
    # Format: socks5://host:port, socks4://host:port, or http://host:port
    # Example: NEKO_AGENT_UPSTREAM_PROXY=socks5://1.2.3.4:1080
    upstream_proxy: Optional[str] = None

    # Rate limiting: max new connections per client IP per minute; 0 = disabled
    rate_limit_per_minute: int = 60
    # When True, IPs that exceed rate limit are auto-blocked and reported to controller
    rate_limit_auto_block: bool = True

    # Optional: secret for agent registration (must match controller NEKO_AGENT_SECRET)
    agent_secret: Optional[str] = None

    # Security: per-agent token (returned by controller on registration).
    # Saved automatically to agent_token.txt in install_dir; loaded from there on restart.
    # Override with NEKO_AGENT_AGENT_TOKEN env var.
    agent_token: Optional[str] = None

    # Security: token the controller sends when calling this agent's ControlAPI.
    # If set, all incoming requests to the ControlAPI must carry X-Controller-Token matching this value.
    # Set to the NEKO_CONTROLLER_TOKEN value from your controller's .env / logs.
    controller_token: Optional[str] = None

    # TLS: SSL verification for HTTPS connections to the controller.
    # Defaults to False so the agent can connect to a controller using an
    # auto-generated self-signed cert without manual CA configuration.
    # After first registration the controller cert is downloaded and cached
    # (TOFU), and NEKO_AGENT_CONTROLLER_SSL_CA_CERT is set automatically.
    # Set to True only when using a CA-signed cert or after TOFU is complete.
    controller_ssl_verify: bool = False
    # Path to a custom CA certificate bundle for verifying the controller's TLS certificate.
    # Used when the controller uses a self-signed cert signed by your own CA.
    controller_ssl_ca_cert: Optional[str] = None

    # TLS: certificate and key for the agent's own ControlAPI (aiohttp server on api_port).
    # When set, the ControlAPI accepts HTTPS connections instead of HTTP.
    control_ssl_certfile: Optional[str] = None
    control_ssl_keyfile: Optional[str] = None

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
        # cwd .env first, then exe-dir agent.env/.env, then the canonical
        # /etc/nekoproxy/agent.env on Linux (later entries win). systemd-injected
        # NEKO_AGENT_* env vars still override all of these.
        env_file = _agent_env_files()

    @model_validator(mode="after")
    def _load_token_and_paranoid(self):
        """Load agent token from file if not set via env; then apply paranoid preset."""
        # Load saved agent token from disk (written after first successful registration)
        if not self.agent_token:
            try:
                token_file = self.install_dir_resolved / "agent_token.txt"
                if token_file.is_file():
                    self.agent_token = token_file.read_text(encoding="utf-8").strip() or None
            except Exception:
                pass

        # Paranoid preset
        if self.paranoid:
            self.rate_limit_per_minute = 30
            self.listen_on_wireguard_only = True
            self.rate_limit_auto_block = True

        return self


settings = AgentSettings()
