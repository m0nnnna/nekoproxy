# NekoProxy

A distributed TCP/UDP proxy management system with centralized control. Deploy proxy agents across multiple servers and manage them from a single web interface.

## Features

- **Centralized Management** – Control all proxy agents from a single web dashboard  
- **TCP/UDP Proxying** – Forward traffic on any port to backend servers  
- **Firewall Rules** – Block or allow ports on specific interfaces (public/WireGuard)  
- **IP Blocklist** – Block connections from specific IP addresses  
- **Real-time Sync** – Push configuration changes to agents instantly  
- **Health Monitoring** – Track agent status, connections, and resource usage  
- **Alerts** – Get notified of suspicious activity and security events  

## Architecture

```
┌─────────────────┐         ┌─────────────────┐
│   Controller    │◄───────►│     Agent 1     │
│   (Web UI)      │   WG    │  (Proxy Server) │
│   Port 8001     │         │                 │
└────────┬────────┘         └─────────────────┘
         │
         │ WireGuard        ┌─────────────────┐
         └─────────────────►│     Agent 2     │
                            │  (Proxy Server) │
                            └─────────────────┘
```

- **Controller**: Central management server with web UI  
- **Agents**: Deployed on proxy servers, execute forwarding rules and firewall policies  
- **Communication**: Agents connect to controller over WireGuard VPN  

## Requirements

- Python 3.10+ (for development)  
- Docker (for building Linux binaries)  
- WireGuard (for secure agent-controller communication)  

## Quick Start

### Building

Build Linux binaries using Docker:

```bash
# Build both agent and controller
./build-docker.sh all

# Or build individually
./build-docker.sh agent
./build-docker.sh controller
```

Output binaries will be in `dist/linux/`.

### Installing the Controller

On your management server:

```bash
sudo ./install-controller.sh
```

The installer will prompt for:
- Listen address and port (default: 0.0.0.0:8001)
- Database location

Access the web UI at `http://<controller-ip>:8001`. To use HTTPS (recommended), run the controller behind Nginx on port 443 and point agents at `https://<controller>:443` — see [docs/HTTPS-SETUP.md](docs/HTTPS-SETUP.md).

### Installing an Agent

On each proxy server:

```bash
sudo ./install-agent.sh
```

The installer will prompt for:
- Controller URL (e.g., `http://10.0.0.1:8001`)
- WireGuard IP of this agent
- Hostname for identification

### Updating (Linux)

To upgrade the binary without changing config or data, use the update scripts from `dist/linux/` (after a Docker build):

```bash
# Controller: replace binary then restart (default: use ./nekoproxy-controller in same dir)
sudo ./update-controller.sh
# Or: sudo ./update-controller.sh /path/to/new/nekoproxy-controller

# Agent
sudo ./update-agent.sh
# Or: sudo ./update-agent.sh /path/to/new/nekoproxy-agent
```

On Windows, use `dist\windows\update-controller.ps1 -BinaryPath "C:\path\to\new\nekoproxy-controller.exe"`.

### Windows: run as a service (no user logged in)

The Windows builds can install themselves as a Windows service so they run without a user logged in.

**Controller**

```powershell
# Install the service (run once; run PowerShell as Administrator)
.\nekoproxy-controller.exe install
# Optional: set startup to automatic
.\nekoproxy-controller.exe update --startup=auto

# Start / stop / remove
.\nekoproxy-controller.exe start
.\nekoproxy-controller.exe stop
.\nekoproxy-controller.exe remove
```

**Agent**

```powershell
.\nekoproxy-agent.exe install
.\nekoproxy-agent.exe update --startup=auto
.\nekoproxy-agent.exe start
.\nekoproxy-agent.exe stop
.\nekoproxy-agent.exe remove
```

Install and start must be run as **Administrator**. After `install`, configure the agent/controller (e.g. env or config file in the same directory as the exe) before starting. The update script (`update-controller.ps1`) detects and restarts the service if it is installed. Running the exe with no arguments (e.g. double‑click) starts the app in the foreground instead of as a service.

## Firewall management and auto-blocking

The system acts as a **firewall manager**: agents block aggressively and report blocks to the controller; the controller pushes the blocklist to all agents so every node stays in sync.

### Agent-side (aggressive)

- **Security alerts → auto-block**  
  When the security monitor hits thresholds (e.g. SSH failures, mail auth failures, relay denied, port scan), the agent:
  1. Adds the IP to the local blocklist (proxy + iptables `NEKOPROXY_BLOCKLIST` chain).
  2. Reports the IP to the controller via `POST /api/v1/blocklist/report`.
  3. Sends the alert to the controller as before.

- **HTTP scraper / AI bot detection**  
  For TCP (HTTP) traffic, the agent peeks at the first request. If the `User-Agent` matches known scrapers (e.g. GPTBot, Claude-Web, CCBot), the connection is dropped and the IP is blocked and reported as above.

- **Blocklist in iptables**  
  Blocked IPs are dropped in an iptables chain (`NEKOPROXY_BLOCKLIST`) before port-based rules, so traffic from those IPs is blocked even outside the proxy.

### Controller-side

- **Blocklist is the source of truth**  
  Add/remove IPs in the Blocklist or Firewall UI. Changes are **pushed to all healthy agents** immediately (trigger-sync); no need to click “Apply” for blocklist add/remove.

- **Agent-reported blocks**  
  When an agent reports an IP via `/api/v1/blocklist/report`, the controller adds it to the blocklist and triggers sync to all agents, so every agent gets the new block.

- **Block from alert**  
  From the Alerts page, “Block IP” adds the IP to the blocklist and triggers sync to all agents.

### Flow summary

1. Agent detects abuse (logs or HTTP User-Agent) → blocks IP locally (proxy + iptables) → reports to controller.
2. Controller adds IP to blocklist → triggers sync on all agents.
3. All agents receive updated config (including blocklist) and apply it (proxy blocklist + firewall blocklist chain).

### Public vs WireGuard baseline (lock down public, keep WG free)

Agents can apply **auto-generated baseline rules** so the **public interface** is default-deny (only proxy ports and established traffic allowed) while the **WireGuard interface** stays permissive. That way:

- **Public**: SSH (22) and other ports not in your proxy services are dropped at the firewall, so you get fewer SSH attempt alerts and less exposure. Only the ports you actually proxy (from controller-assigned services) are allowed for new connections.
- **WireGuard**: Controller sync, SSH over WG, and other management traffic keep working.

Enabled by default. Set in agent env:

- `NEKO_AGENT_HARDEN_PUBLIC_INTERFACE=true` (default) – public interface = default-deny, only proxy ports + ESTABLISHED,RELATED.
- `NEKO_AGENT_ALLOW_WIREGUARD_FREEDOM=true` (default) – WireGuard interface = allow.

Public interface is chosen as the default-route interface, or the first physical interface that is **not** WireGuard (so VPN-as-gateway setups still get the right “public” interface).

**Safe-out-of-the-box extras:** Dangerous ports (SSH, RDP, DBs) are never allowed on public even if added as a service. Rate limiting (default 60/min per IP) with optional auto-block. Option to bind proxy to WireGuard only (`NEKO_AGENT_LISTEN_ON_WIREGUARD_ONLY=true`). Optional agent registration secret (`NEKO_AGENT_SECRET` on controller, `NEKO_AGENT_AGENT_SECRET` on agents).

## Configuration

### Controller

Configuration via environment variables or `/etc/nekoproxy/controller.env`:

| Variable | Default | Description |
|--------|---------|-------------|
| `NEKO_HOST` | `0.0.0.0` | Listen address |
| `NEKO_PORT` | `8001` | Listen port |
| `NEKO_DATABASE_URL` | `sqlite:///./nekoproxy.db` | Database connection |
| `NEKO_DEBUG` | `false` | Enable debug mode |
| `NEKO_GEO_MODE` | `off` | Geo filtering: `off`, `allowlist`, or `blocklist` (pushed to agents) |
| `NEKO_GEO_COUNTRIES` | — | Comma-separated ISO 3166-1 alpha-2 codes (e.g. `US,CA,GB` or `CN,RU,KP`) |
| `NEKO_IDLE_CONNECTION_TIMEOUT_SECONDS` | `0` | Idle connection timeout for proxies (0 = disabled) |
| `NEKO_PARANOID` | `false` | DDoS lockdown: force 120s idle timeout, default geo blocklist (CN,RU,KP,IR) if geo off |

### Agent

Configuration via environment variables or `/etc/nekoproxy/agent.env`:

| Variable | Default | Description |
|--------|---------|-------------|
| `NEKO_AGENT_CONTROLLER_URL` | `http://localhost:8001` | Controller URL |
| `NEKO_AGENT_WIREGUARD_IP` | — | Agent WireGuard IP (required) |
| `NEKO_AGENT_HOSTNAME` | System hostname | Display name |
| `NEKO_AGENT_API_PORT` | `8002` | Control API port |
| `NEKO_AGENT_HARDEN_PUBLIC_INTERFACE` | `true` | Default-deny on public interface, allow only proxy ports + established |
| `NEKO_AGENT_ALLOW_WIREGUARD_FREEDOM` | `true` | Allow WireGuard interface (controller, SSH over WG) |
| `NEKO_AGENT_LISTEN_ON_WIREGUARD_ONLY` | `false` | Bind proxy to WireGuard IP only (no proxy on public) |
| `NEKO_AGENT_RATE_LIMIT_PER_MINUTE` | `60` | Max new connections per client IP per minute; 0 = disabled |
| `NEKO_AGENT_RATE_LIMIT_AUTO_BLOCK` | `true` | Auto-block and report IPs that exceed rate limit |
| `NEKO_AGENT_AGENT_SECRET` | — | Optional; must match controller `NEKO_AGENT_SECRET` to register |
| `NEKO_AGENT_GEOLITE2_DB_PATH` | — | Path to MaxMind GeoLite2-Country.mmdb for geo allow/block (optional) |
| `NEKO_AGENT_PARANOID` | `false` | One env for DDoS lockdown: 30/min rate limit, WG-only listen, auto-block on rate limit |

**Controller URL with HTTPS:** Use `https://your-controller:443` (or `https://...` with no port) when the controller is behind Nginx; see [docs/HTTPS-SETUP.md](docs/HTTPS-SETUP.md).

**Geo filtering:** Set `NEKO_GEO_MODE` and `NEKO_GEO_COUNTRIES` on the controller; install `geoip2` and set `NEKO_AGENT_GEOLITE2_DB_PATH` on agents (download GeoLite2-Country.mmdb from MaxMind).

**Idle connection timeout:** Set `NEKO_IDLE_CONNECTION_TIMEOUT_SECONDS` on the controller (e.g. 300); agents close TCP connections and UDP sessions after that many seconds with no data.

**Paranoid preset:** Set `NEKO_AGENT_PARANOID=true` on agents and/or `NEKO_PARANOID=true` on the controller for maximum lockdown (stricter rate limit, WG-only listen, idle timeout, default geo blocklist).

## Web UI Pages

### Dashboard
Overview of system status, agent health, and recent connections.

### Agents
View and manage connected proxy agents, including health status and resource usage. You can **upload an agent binary** (Linux or Windows) and use **Update** per agent to push it over the WireGuard link; the agent saves the binary, runs its update script (or Windows service stop/copy/start), and restarts with the new version. No need to copy the binary to each proxy by hand.

### Rules (Proxy Rules)
- Listen Port  
- Backend target  
- Protocol (TCP/UDP)  
- Deployment target (specific or all agents)  

### Firewall
- Port-based allow/deny rules  
- Interface types: `public`, `wireguard`, or specific interfaces  

### Blocklist
Block connections from specific IP addresses before proxying.

### Alerts
Security alerts for suspicious activity.

### Stats
Connection statistics and traffic metrics.

## API

Base path: `/api/v1/`

| Endpoint | Method | Description |
|--------|--------|-------------|
| `/agents/register` | POST | Register new agent |
| `/agents/{id}/heartbeat` | POST | Agent heartbeat |
| `/agents/{id}/config` | GET | Fetch agent configuration |
| `/agents/{id}/stats` | POST | Report stats |

## Development

### Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Running Locally

```bash
# Controller
python -m uvicorn controller.main:app --reload --port 8001

# Agent
NEKO_AGENT_CONTROLLER_URL=http://localhost:8001 NEKO_AGENT_WIREGUARD_IP=10.0.0.2 python -m agent.main
```

### Project Structure

```
nekoproxy/
├── agent/
├── controller/
├── shared/
├── build/
├── build-docker.sh
├── install-agent.sh
├── install-controller.sh
├── update-agent.sh
├── update-controller.sh
└── requirements.txt
```

## Systemd Services

```bash
sudo systemctl status nekoproxy-controller
sudo systemctl restart nekoproxy-controller

sudo systemctl status nekoproxy-agent
sudo systemctl restart nekoproxy-agent
```

## Security Considerations

- All communication over WireGuard
- Agent API bound only to WireGuard interface
- Dedicated iptables chain (`NEKOPROXY`)
- Controller should not be public-facing

## Troubleshooting

### Agent not connecting
1. `wg show`
2. Check `/etc/nekoproxy/agent.env`
3. `journalctl -u nekoproxy-agent -f`

### Rules not applying
1. Apply changes in UI
2. Check agent sync logs
3. Verify agent health

### Firewall issues
1. `iptables -L NEKOPROXY -n`
2. Verify interface names
3. Ensure agent runs as root
