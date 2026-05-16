# NekoProxy v4

A distributed TCP/UDP proxy management system with centralized control. Deploy proxy agents across multiple servers and manage them from a single web interface.

## Features

- **Centralized Management** – Control all proxy agents from a single web dashboard  
- **TCP/UDP Proxying** – Forward traffic on any port to backend servers  
- **Forward Proxy Server** – Agents act as HTTP(S) forward proxies; point devices or applications at the agent to route their outbound traffic  
- **DNS Forwarder** – Agents act as DNS resolvers; point devices' DNS at the agent and queries are forwarded to any upstream resolver  
- **Route-via Agent** – Internal/container agents chain their outbound traffic through a full VPS agent (exit from VPS IP, not local IP)  
- **Upstream Proxy** – Route agent backend connections through a SOCKS5/HTTP proxy for VPS IP exit  
- **Firewall Rules** – Block or allow ports on specific interfaces (public/WireGuard)  
- **IP Blocklist** – Block connections from specific IP addresses  
- **Real-time Sync** – Push configuration changes to agents instantly  
- **Health Monitoring** – Track agent status, connections, and resource usage  
- **Alerts** – Get notified of suspicious activity and security events  
- **Auto-TLS** – Controller and agents auto-generate self-signed certificates; agents use Trust On First Use (TOFU) cert pinning with self-healing on cert changes  
- **Push Updates** – Upload a new binary in the web UI and push it to agents over the network; agents replace themselves and restart  
- **Live Traffic View** – Real-time per-channel traffic monitor streamed to the browser via SSE; four separate panels (Incoming, DNS, Forward Outbound, Email) fed live from all agents  

## Architecture

```
┌─────────────────┐         ┌──────────────────────┐
│   Controller    │◄───────►│   VPS Agent          │
│   (Web UI)      │   WG    │   Forward Proxy :8080 │
│   Port 8001     │         │   Exits to internet  │
└────────┬────────┘         └──────────┬───────────┘
         │                             ▲
         │ WireGuard                   │ upstream_proxy
         │                             │
         └─────────────────►┌──────────┴───────────┐
                            │   Internal Agent     │
                            │   Forward Proxy :8080 │
                            │   (routes via VPS)   │
                            └──────────────────────┘
```

- **Controller**: Central management server with web UI  
- **Full agents**: Deployed on VPS/proxy servers with WireGuard; handle reverse proxy services and act as forward proxy exit nodes  
- **Internal agents**: Deployed on local machines/containers without WireGuard; use `control_url` so the controller can reach them; can route outbound traffic through a full agent  
- **Communication**: Agents connect to controller over WireGuard VPN; internal agents use direct URL  

**Standard ports:** Controller listens on **8001**, agent control API on **8002** by default.

## Requirements

- Python 3.10+ (for development)  
- Docker (for building Linux binaries)  
- WireGuard (for full agent communication; internal agents don't need it)  

## Quick Start

### Building

Build Linux binaries using Docker. The default build uses **Ubuntu 20.04** and produces **glibc** binaries that run on Ubuntu, Debian, and other glibc-based distros.

```bash
# Build both agent and controller (Ubuntu/glibc)
./build-docker.sh all

# Or build individually
./build-docker.sh agent
./build-docker.sh controller
```

Output binaries will be in `dist/linux/`.

**Alpine Linux:** The Ubuntu-built binaries are **not** compatible with Alpine (Alpine uses **musl** libc). To run on Alpine, either:
- **Build for Alpine:** use the Alpine Docker build so the binaries are linked against musl:
  ```bash
  ./build-docker-alpine.sh all   # or agent | controller
  ```
  Output will be in `dist/linux-alpine/`. Use these binaries on Alpine only.
- **Or run from source on Alpine:** install Python 3 and run with `pip install -r requirements.txt` then `python -m agent.main` / `python -m uvicorn controller.main:app --port 8001`.

To build the **Windows agent** (and/or controller) on a Windows machine, use PyInstaller: `pyinstaller build/agent.spec` and/or `pyinstaller build/controller.spec`. The agent executable is written to `dist/nekoproxy-agent.exe`. Copy it to `dist/windows/` if you keep Windows builds there. The Windows agent proxies traffic and reports to the controller. **Firewall:** on Windows the agent can manage **Windows Firewall** (blocklist by IP, port allow/block rules, and baseline: only proxy ports on Public profile). Run the agent as **Administrator** (or install as a service, which runs elevated) for firewall rules to be applied; otherwise blocklist and rate limiting still apply in the proxy layer only. Log-based security monitoring and iptables counter stats are Linux-only and are skipped on Windows.

### Installing the Controller

On your management server:

```bash
sudo ./install-controller.sh
```

The installer will prompt for:
- Listen address and port (default: 0.0.0.0:8001)
- Database location

Access the web UI at `http://<controller-ip>:8001`. The controller auto-generates a self-signed TLS certificate on first run; agents use TOFU (Trust On First Use) to pin it automatically. To use a CA-signed cert, set `NEKO_SSL_CERTFILE` and `NEKO_SSL_KEYFILE` — see [docs/HTTPS-SETUP.md](docs/HTTPS-SETUP.md).

### Installing an Agent

On each proxy server:

```bash
sudo ./install-agent.sh
```

The installer will prompt for:
- Controller URL (e.g., `https://10.0.0.1:8001`)
- WireGuard IP of this agent (leave blank for internal agents)
- Hostname for identification

The same install and update scripts work on **Alpine Linux** (OpenRC): they detect systemd vs OpenRC and create the appropriate service. Use the binaries from `dist/linux-alpine/` when building for Alpine.

### Updating (Linux)

```bash
# Controller
sudo ./update-controller.sh
# Or: sudo ./update-controller.sh /path/to/new/nekoproxy-controller

# Agent
sudo ./update-agent.sh
# Or: sudo ./update-agent.sh /path/to/new/nekoproxy-agent
```

On Windows, use `dist\windows\update-controller.ps1` and `dist\windows\update-agent.ps1`.

### Push Update (from Web UI)

Upload a new agent binary on the **Agents** page and click **Update** next to any agent. The controller streams the binary to the agent's ControlAPI; the agent replaces its own binary and restarts. The agent's token, config, and data files are preserved across updates.

### Windows: run as a service

```powershell
# Controller
.\install-controller.ps1
.\install-controller.ps1 -StartService

# Agent (place agent.env in same folder as exe)
.\install-agent.ps1
.\install-agent.ps1 -StartService
.\install-agent.ps1 -Uninstall
```

Or call the exe directly:

```powershell
.\nekoproxy-controller.exe install
.\nekoproxy-controller.exe start
.\nekoproxy-controller.exe stop
.\nekoproxy-controller.exe remove
```

## Forward Proxy & Traffic Routing

### Forward Proxy Server

Agents can act as HTTP(S) forward proxies. Any device or application pointing its proxy settings at the agent will have its traffic forwarded — optionally exiting from a VPS IP.

Enable in **Settings → Forward proxy port** (e.g. `8080`). All agents will sync and start a forward proxy on that port.

Optional Basic auth: set **Forward proxy auth** to `user:password` in Settings.

Point an application at the agent:
```
HTTP proxy: agent-ip:8080
HTTPS proxy: agent-ip:8080
```

Or for CLI tools:
```bash
export http_proxy=http://agent-ip:8080
export https_proxy=http://agent-ip:8080
```

### Route-via Agent (exit via VPS)

For internal agents (local machines, containers) that should appear to come from a VPS IP:

1. On the **Agents** page, set the **Route via** dropdown on the internal agent to the VPS agent.
2. The controller automatically computes the upstream proxy URL and pushes it to the internal agent.
3. The internal agent's forward proxy chains through the VPS agent — traffic exits from the VPS IP.

**What gets routed via VPS:**
- ✅ Traffic from applications configured to use the forward proxy (via `http_proxy` env or app proxy settings)
- ✅ Reverse proxy backend connections (services the agent proxies to backends)
- ❌ Direct TCP from the machine (raw sockets without proxy awareness) — use a VPN for that

**Example — Misskey/fediverse:** Add to `.config/default.yml`:
```yaml
proxy: http://localhost:8080
```
Restart Misskey. All federation and outbound requests will exit via the VPS.

**Verify it's working:**
```bash
# On the internal machine — should return VPS IP
curl -x http://localhost:8080 https://ifconfig.me

# Watch live on the VPS
ss -tnp | grep 8080
```

Or check **OPNsense → Firewall → Log Files → Live View**, filter by the internal machine's source IP — you should only see connections to the VPS IP on port 8080, not direct external connections.

### Forward proxy & DNS binding security

For agents with a WireGuard IP configured, the forward proxy and DNS forwarder bind exclusively to the **WireGuard interface IP** — not `0.0.0.0`. This means they are unreachable from the public internet at the socket level, independent of any firewall rules. Internal agents (no WireGuard) bind to `listen_ip` as usual, protected by their local network topology.

The iptables / Windows Firewall baseline provides a second layer: the forward proxy and DNS ports are not in the list of ports ACCEPTed on the public interface, so they are dropped at the network level even on older setups.

### Upstream Proxy (manual, per-agent env)

To route an agent's backend connections through a SOCKS5/HTTP proxy without using the route-via UI:

```bash
# In agent.env
NEKO_AGENT_UPSTREAM_PROXY=socks5://1.2.3.4:1080
# or
NEKO_AGENT_UPSTREAM_PROXY=http://1.2.3.4:8080
```

Requires `python-socks[asyncio]` to be installed (included in the bundled binary).

## DNS Forwarder

Agents can act as DNS resolvers for devices on the network. DNS queries arrive on the configured port (UDP and TCP) and are forwarded byte-for-byte to an upstream resolver. No DNS parsing or caching — pure relay.

### Enabling

In **Settings → DNS forwarder**:

- **DNS listen port** – port agents listen on for DNS queries (0 = disabled). Common choices:
  - `53` – standard DNS port; requires root/admin on Linux
  - `5353` – unprivileged alternative (avoids needing root)
  - `5300` – another common unprivileged choice
- **Upstream resolver** – where queries are forwarded. Format: `host` or `host:port` (default port: 53).

Examples:
```
1.1.1.1          → Cloudflare, port 53
1.1.1.1:53       → same, explicit port
8.8.8.8:53       → Google
9.9.9.9          → Quad9
```

Click **Save & Apply** to push to all agents.

### Pointing devices at the agent

```
DNS server: <agent-wireguard-ip>:<dns-port>
```

For example with `dns_port = 5353`:
```bash
# Test resolution
dig @10.0.0.2 -p 5353 example.com

# Linux systemd-resolved override
echo "DNS=10.0.0.2:5353" >> /etc/systemd/resolved.conf
systemctl restart systemd-resolved

# Or set per-interface via nmcli
nmcli con mod eth0 ipv4.dns "10.0.0.2"
```

### Binding and security

On full agents (WireGuard IP set), the DNS forwarder binds to the **WireGuard IP only**. It is not reachable from the public internet regardless of firewall state. On internal agents (no WireGuard) it binds to `listen_ip`.

### Verifying

```bash
# Check the forwarder is listening (replace port as configured)
ss -ulnp | grep 5353   # UDP
ss -tlnp | grep 5353   # TCP

# Test a query
dig @<agent-wireguard-ip> -p 5353 example.com

# Agent logs
journalctl -u nekoproxy-agent | grep "DNS forwarder"
```

## Live Traffic View

The **Live** page shows real-time connection events streamed from all agents to the browser via Server-Sent Events (SSE). Traffic is split into four independent panels:

| Panel | Source |
|---|---|
| **Incoming** | TCP/UDP reverse-proxy service connections |
| **DNS** | DNS forwarder queries (per UDP/TCP lookup) |
| **Forward Outbound** | HTTP CONNECT tunnels and plain HTTP proxy requests |
| **Email** | Postfix mail log events (delivered, blocked, deferred, bounced) |

Each panel holds the last 100 events, newest on top, and updates automatically without any page refresh. A green dot in the top-right corner indicates the SSE stream is connected; it turns red and reconnects automatically if the connection drops.

### How it works

- Agents tag every connection event with a `proxy_type` field (`incoming`, `dns`, `forward`) and batch-report them to the controller as usual.
- The controller routes each event into an in-memory ring buffer (100 entries per channel) and pushes it to all connected browser SSE subscribers.
- DNS and forward proxy events are **live-only** — they are not stored in the database, keeping storage clean.
- Incoming (reverse-proxy) connections are stored in the database as before, and also pushed to the live view.
- Email events come from the existing Postfix log collector (`/var/log/mail.log`) and are stored in the database and pushed live.

### Latency

Events appear within one `stats_report_interval` (configurable, default ~10 s). For near-real-time monitoring set `NEKO_AGENT_STATS_REPORT_INTERVAL=2` in `agent.env` on the agents you want to watch closely.

### Email panel (Postfix)

The email panel requires Postfix to be deployed on the agent (via the Email setup flow). The agent tails `/var/log/mail.log`, parses queue events (queued, sent, deferred, bounced, rejected), and ships them to the controller. No additional configuration is needed once Postfix is deployed.

## TLS / Certificate Management

### Auto-generated certificates (default)

The controller auto-generates a self-signed TLS certificate on first run. The cert includes all detected local IPs — including WireGuard interface IPs — as Subject Alternative Names (SANs), so agents can verify it when connecting via any of the controller's IPs.

On each startup the controller checks whether the existing cert's SANs cover all current interface IPs. If new IPs have been added (e.g. a WireGuard interface brought up after first install), the cert is automatically regenerated. Agents will detect the change on their next heartbeat/sync, clear their cached cert, and re-TOFU the new one automatically.

Agents use **TOFU (Trust On First Use)** — they download and cache the controller cert on first registration, then verify it on all future connections.

### Cert regeneration

If the controller's cert changes (e.g. after reinstall), push a cert refresh from the controller:

**Settings → Push cert refresh to all agents** — agents clear their cached cert and re-TOFU the new one automatically, no rebuild needed.

### Custom certificates

Set in the controller's env:
```
NEKO_SSL_CERTFILE=/path/to/cert.pem
NEKO_SSL_KEYFILE=/path/to/key.pem
```

See [docs/HTTPS-SETUP.md](docs/HTTPS-SETUP.md) for Nginx reverse proxy setup.

## Firewall Management and Auto-blocking

The system acts as a **firewall manager**: agents block aggressively and report blocks to the controller; the controller pushes the blocklist to all agents so every node stays in sync.

### Agent-side (aggressive)

- **Security alerts → auto-block:** When the security monitor hits thresholds (SSH failures, mail auth failures, port scan), the agent blocks the IP locally (proxy + iptables) and reports it to the controller.
- **HTTP scraper / AI bot detection:** TCP (HTTP) traffic with known scraper User-Agents (GPTBot, Claude-Web, CCBot, etc.) is dropped and the IP is blocked and reported.
- **Blocklist in iptables:** Blocked IPs are dropped in a `NEKOPROXY_BLOCKLIST` chain before port-based rules.

### Controller-side

- **Blocklist is source of truth:** Add/remove IPs in the Blocklist or Firewall UI. Changes push to all healthy agents immediately.
- **Agent-reported blocks:** When an agent reports an IP, the controller adds it and syncs all agents.
- **Block from alert:** From the Alerts page, "Block IP" adds the IP and triggers sync.

### Public vs WireGuard baseline

Agents apply **auto-generated baseline rules** so the **public interface** is default-deny (only proxy ports and established traffic) while the **WireGuard interface** stays permissive.

- `NEKO_AGENT_HARDEN_PUBLIC_INTERFACE=true` (default) – public interface = default-deny
- `NEKO_AGENT_ALLOW_WIREGUARD_FREEDOM=true` (default) – WireGuard interface = allow
- `NEKO_AGENT_LISTEN_ON_WIREGUARD_ONLY=true` – bind proxy listeners to WireGuard IP only

## Configuration

### Controller

Configuration via environment variables or `.env` in the working directory:

| Variable | Default | Description |
|----------|---------|-------------|
| `NEKO_HOST` | `0.0.0.0` | Listen address |
| `NEKO_PORT` | `8001` | Listen port |
| `NEKO_DATABASE_URL` | `sqlite:///./nekoproxy.db` | Database connection |
| `NEKO_DEBUG` | `false` | Enable debug mode |
| `NEKO_SSL_CERTFILE` | — | TLS certificate (auto-generated if not set) |
| `NEKO_SSL_KEYFILE` | — | TLS private key (auto-generated if not set) |
| `NEKO_API_TOKEN` | — | Admin API token (auto-generated on first run) |
| `NEKO_CONTROLLER_TOKEN` | — | Token sent to agents' ControlAPI (auto-generated) |
| `NEKO_AGENT_SECRET` | — | Optional registration secret agents must send |
| `NEKO_GEO_MODE` | `off` | Geo filtering: `off`, `allowlist`, or `blocklist` |
| `NEKO_GEO_COUNTRIES` | — | Comma-separated ISO codes (e.g. `US,CA,GB`) |
| `NEKO_IDLE_CONNECTION_TIMEOUT_SECONDS` | `0` | Idle connection timeout (0 = disabled) |
| `NEKO_PARANOID` | `false` | DDoS lockdown: 120s idle timeout, default geo blocklist |

Most settings are also configurable from **Settings** in the web UI without restarting.

### Agent

Configuration via environment variables or `agent.env` in the same directory as the binary:

| Variable | Default | Description |
|----------|---------|-------------|
| `NEKO_AGENT_CONTROLLER_URL` | `http://localhost:8001` | Controller URL |
| `NEKO_AGENT_WIREGUARD_IP` | — | Agent WireGuard IP (omit for internal agents) |
| `NEKO_AGENT_HOSTNAME` | System hostname | Display name |
| `NEKO_AGENT_API_PORT` | `8002` | Control API port |
| `NEKO_AGENT_CONTROL_URL` | — | URL for controller to reach this agent (required for internal agents, e.g. `https://127.0.0.1:8002`) |
| `NEKO_AGENT_CONTROL_BIND_IP` | — | Bind ControlAPI to this IP (default: WireGuard IP or `127.0.0.1`) |
| `NEKO_AGENT_FORWARD_PROXY_PORT` | `0` | Forward proxy listen port (0 = disabled; override for env-only config, normally set via controller Settings) |
| `NEKO_AGENT_FORWARD_PROXY_AUTH` | — | Forward proxy Basic auth `user:password` |
| `NEKO_AGENT_UPSTREAM_PROXY` | — | Route backend connections through this proxy (socks5://, socks4://, http://) |
| `NEKO_AGENT_HARDEN_PUBLIC_INTERFACE` | `true` | Default-deny on public interface |
| `NEKO_AGENT_ALLOW_WIREGUARD_FREEDOM` | `true` | Allow all on WireGuard interface |
| `NEKO_AGENT_LISTEN_ON_WIREGUARD_ONLY` | `false` | Bind proxy to WireGuard IP only |
| `NEKO_AGENT_RATE_LIMIT_PER_MINUTE` | `60` | Max new connections per IP per minute; 0 = disabled |
| `NEKO_AGENT_RATE_LIMIT_AUTO_BLOCK` | `true` | Auto-block IPs that exceed rate limit |
| `NEKO_AGENT_AGENT_SECRET` | — | Must match `NEKO_AGENT_SECRET` on controller if set |
| `NEKO_AGENT_GEOLITE2_DB_PATH` | — | Path to MaxMind GeoLite2-Country.mmdb |
| `NEKO_AGENT_PARANOID` | `false` | DDoS lockdown preset |

**Internal agents** (no WireGuard): omit `NEKO_AGENT_WIREGUARD_IP` and set `NEKO_AGENT_CONTROL_URL` so the controller can reach the agent for sync and push-update. Mark the agent as **Internal** in the web UI to allow all proxy ports on the public interface.

**Route-via:** Set in the **Agents** page web UI — no env var needed. The controller computes and pushes the `upstream_proxy` URL automatically.

## Web UI Pages

### Dashboard
Overview of system status, agent health, and recent connections.

### Agents
View and manage connected agents. Per-agent controls:
- **Internal** toggle – allows all proxy ports on the public interface (for local/container agents)
- **Route via** dropdown – select which full agent this agent's forward proxy chains through (for VPS exit routing)
- **Update** button – push a new binary to this agent (upload binary first at the top of the page)
- **Remove** – deregister the agent

### Settings
Global configuration pushed to all agents:
- Geo filtering mode and country list
- Idle connection timeout
- Paranoid preset
- **Forward proxy port** – enables the HTTP(S) forward proxy on all agents
- **Forward proxy auth** – optional Basic auth for the forward proxy (`user:password`)
- **DNS listen port** – enables the DNS forwarder on all agents
- **Upstream resolver** – DNS upstream (e.g. `1.1.1.1:53`)
- Agent registration secret
- Push cert refresh to all agents

### Rules (Proxy Rules)
- Listen port, backend target, protocol (TCP/UDP)
- Deploy to specific agent or all agents

### Firewall
- Port-based allow/deny rules per interface
- **Test port reachability** – verify from controller that a port on an agent is blocked or open

### Blocklist
Block connections from specific IP addresses before proxying.

### Alerts
Security alerts for suspicious activity, with one-click block.

### Live
Real-time traffic monitor with four panels streamed via SSE:
- **Incoming** – reverse-proxy connections (agent, service, client IP, status, duration, bytes)
- **DNS** – DNS resolver queries (agent, client IP, status, round-trip time, bytes)
- **Forward Outbound** – HTTP/HTTPS proxy tunnels (agent, destination host:port, client IP, status, duration, bytes)
- **Email** – Postfix mail events (agent, from, to, status, message size)

A green dot indicates the live stream is connected. Each panel is collapsible and holds the last 100 events.

### Stats
Connection statistics and traffic metrics.

## API

Base path: `/api/v1/`

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/agents/register` | POST | agent_secret (optional) | Register or re-register agent |
| `/agents/{id}/heartbeat` | POST | agent token | Agent heartbeat |
| `/agents/{id}/config` | GET | agent token | Fetch agent configuration |
| `/agents/{id}/push-update` | POST | admin | Push binary update to agent |
| `/agents/push-cert-refresh` | POST | admin | Push cert re-TOFU to all agents |
| `/agents/{id}/push-cert-refresh` | POST | admin | Push cert re-TOFU to one agent |
| `/agents/{id}/route-via` | POST | admin | Set route-via agent |
| `/agents/{id}/internal` | POST | admin | Toggle internal flag |

## Security

- Controller and agents communicate over WireGuard; ControlAPI bound to WireGuard IP by default  
- **Forward proxy and DNS forwarder bind to the WireGuard IP** on full agents — not `0.0.0.0`; unreachable from the public internet at the socket level  
- iptables / Windows Firewall baseline: public interface is default-deny; only configured service ports are accepted; forward proxy and DNS ports are never opened on the public interface  
- Per-agent tokens issued on every registration; agents auto-recover on 401 by re-registering  
- Known agents (with valid existing token) can re-register even if the controller's `agent_secret` changed — prevents push-update failures after security config changes  
- Controller token (`NEKO_CONTROLLER_TOKEN`) authenticates controller→agent ControlAPI calls  
- Dedicated iptables chain (`NEKOPROXY`) for blocklist and rules  
- Rate limiting with optional auto-block  
- Controller should not be directly public-facing  

## Troubleshooting

### Agent not connecting
```bash
wg show
cat /etc/nekoproxy/agent.env   # check NEKO_AGENT_CONTROLLER_URL
journalctl -u nekoproxy-agent -f
```

### Push-update fails / 401 after update
The agent re-registers on startup and gets a fresh token automatically. If the controller requires an `agent_secret`, existing agents (with a valid token) bypass the secret check on re-registration — no action needed.

### Forward proxy not routing via VPS
```bash
# Check forward proxy is listening
ss -tnlp | grep 8080

# Test the chain directly
curl -x http://localhost:8080 https://ifconfig.me
# Should return VPS IP, not local IP

# Check upstream proxy was synced
journalctl -u nekoproxy-agent | grep upstream
```

### Cert errors / SSL verification failures
If agents can't connect to the controller due to cert errors:
1. The controller's cert may not include the WireGuard IP in its SANs (e.g. WireGuard was set up after first install). Restart the controller — it will detect the missing IP and auto-regenerate the cert.
2. Then go to **Settings → Push cert refresh to all agents** so agents re-TOFU the new cert.

### Cert errors after controller reinstall
From the controller web UI: **Settings → Push cert refresh to all agents**. Agents will automatically re-TOFU the new certificate.

### Rules not applying
1. Check agent is healthy (green) on the Agents page
2. Check agent sync logs: `journalctl -u nekoproxy-agent | grep -i sync`
3. Use the "Sync" button on the Agents page to force a push

### DNS forwarder not responding
```bash
# Check it's listening (replace 5353 with your configured port)
ss -ulnp | grep 5353
ss -tlnp | grep 5353

# Test a query directly
dig @<agent-wireguard-ip> -p 5353 example.com

# Check agent logs
journalctl -u nekoproxy-agent | grep -i dns

# Port 53 requires root — if using 53 and it won't bind, check the agent is running as root
journalctl -u nekoproxy-agent | grep "Permission denied"
```

### Firewall issues (Linux)
```bash
iptables -L NEKOPROXY -n
iptables -L NEKOPROXY_BLOCKLIST -n
# Verify interface names match what the agent detected
journalctl -u nekoproxy-agent | grep interface
```

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

# Agent (internal — no WireGuard)
NEKO_AGENT_CONTROLLER_URL=http://localhost:8001 python -m agent.main
```

### Project Structure

```
nekoproxy/
├── agent/
│   ├── core/
│   │   ├── tcp_proxy.py          # TCP reverse proxy (upstream proxy support)
│   │   ├── udp_proxy.py          # UDP reverse proxy
│   │   ├── forward_proxy.py      # HTTP(S) forward proxy server
│   │   ├── dns_forwarder.py      # DNS forwarder (UDP+TCP relay to upstream)
│   │   ├── heartbeat.py          # Controller heartbeat
│   │   ├── config_sync.py        # Config sync from controller
│   │   ├── control_api.py        # ControlAPI (receives push commands)
│   │   ├── cert_utils.py         # TLS cert helpers (TOFU, re-TOFU)
│   │   ├── firewall.py           # iptables management (Linux)
│   │   └── firewall_windows.py   # Windows Firewall management
│   ├── config.py
│   └── main.py
├── controller/
│   ├── api/v1/                   # REST API
│   ├── core/
│   │   ├── agent_manager.py      # Agent config + route-via computation
│   │   ├── agent_sync.py         # Push sync/cert refresh to agents
│   │   └── live_events.py        # In-memory SSE event bus (live traffic view)
│   ├── database/
│   └── web/                      # Jinja2 + HTMX web UI
├── shared/
│   ├── models/                   # Pydantic models (AgentConfig, etc.)
│   └── tls.py                    # TLS cert generation
├── build/
├── build-docker.sh
├── build-docker-alpine.sh
├── install-agent.sh
├── install-controller.sh
└── requirements.txt
```

## Systemd Services

```bash
sudo systemctl status nekoproxy-controller
sudo systemctl restart nekoproxy-controller

sudo systemctl status nekoproxy-agent
sudo systemctl restart nekoproxy-agent
```
