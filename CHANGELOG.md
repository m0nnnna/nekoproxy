# Changelog

All notable changes to NekoProxy are documented here.

## [Unreleased]

### Added

- **Alpine Linux support**
  - `build/Dockerfile.linux.alpine` and `build-docker-alpine.sh` for building musl binaries (Alpine). Ubuntu build remains glibc-only.
  - Linux install/update scripts (`install-controller.sh`, `install-agent.sh`, `update-controller.sh`, `update-agent.sh`) detect systemd vs OpenRC and create the appropriate service (systemd unit or `/etc/init.d/` script) so the same scripts work on Ubuntu/Debian and Alpine.

- **Windows agent and install scripts**
  - Windows agent build in `build.bat` (agent/all targets).
  - `agent/core/firewall_windows.py`: Windows Firewall management (blocklist, port rules, baseline) when agent runs as Administrator.
  - `dist/windows/install-controller.ps1` and `dist/windows/install-agent.ps1`: register the .exe in the same directory as a Windows service (`-StartService`, `-Uninstall`).
  - Agent config loads from `agent.env` or `.env` next to the exe; WireGuard IP is optional for internal agents.

- **Windows: run the controller (and agent) as a boot-start service**
  - `install-controller.ps1` / `install-agent.ps1` now set the service to **Automatic** start (or `-DelayedStart`) and configure **SCM auto-restart on failure** (5s/10s/30s). The controller installer can write `.env` from `-ListenHost`/`-Port`/`-DatabaseUrl`; the agent installer writes `agent.env` from prompts or `-ControllerUrl`/`-Hostname`/`-WireguardIp`. Added `dist/windows/agent.env.example`.
  - The frozen controller now anchors its working directory to the exe folder, so the SQLite DB, TLS cert, `.env` and uploads no longer land in `C:\Windows\System32` when started by the SCM.
  - `_controller_service_run` detects a uvicorn startup failure (e.g. port already in use at boot) and exits non-zero so SCM recovery fires, instead of hanging in "Running".
  - Both services log to `logs\<name>.log` next to the exe (no console under the SCM) and report crashes to the Windows Event Log.
  - Agent retries controller registration with backoff instead of exiting, so a boot-time race (controller/network not ready) no longer stops the service.
  - Fixed the Windows service `_run_callback` being bound as a method (service started but did nothing). `pywin32` is now a real Windows dependency in `requirements.txt`, and `pywintypes`/`pythoncom` are bundled in the specs.

- **Controller sync for internal/same-machine agents**
  - Optional `control_url` on agents (e.g. `http://127.0.0.1:8002`) so the controller can trigger sync and push-update when the agent has no WireGuard IP. DB migration adds `agents.control_url`; agent sends it at registration via `NEKO_AGENT_CONTROL_URL`.

- **Port reachability test**
  - Firewall page: "Test port reachability" (agent + port) to verify from the controller whether a port is reachable or blocked.
  - API: `GET /api/v1/agents/{id}/test-port?port=...` and `POST /firewall/test-port` (HTMX).

- **Live connections view**
  - `/live` page with auto-refresh showing connections in the last 60 seconds.

- **Internal agent toggle**
  - Agents list: "Internal" checkbox to mark an agent as internal (looser port control; dangerous ports allowed on public). DB: `agents.internal`; config sync pushes `internal` to agent.

- **Stats and dashboard**
  - "IPs auto-added to blocklist" stat (blocklist `source` column: `manual` vs `agent_report`).
  - Connection/firewall/email logs show service name and agent hostname.

- **Line endings**
  - `.gitattributes`: `*.sh` and `*.ps1` use `eol=lf` to avoid bad interpreter on Linux/WSL.

### Changed

- **Sync "Apply to agents"**
  - Sync now targets all reachable agents (`get_all()` then filter by `wireguard_ip` or `control_url`), not only healthy ones, so the reported count matches the number of agents with a reachable address (fixes "Synced to 2" when more agents exist).

- **Agent install script**
  - WireGuard IP is optional: leave blank for internal agent. Config only writes `NEKO_AGENT_WIREGUARD_IP` when set.

- **Build**
  - `build/docker-build.sh` message is generic "Linux" (used by both Ubuntu and Alpine Dockerfiles).
  - Alpine Dockerfile adds `bash` so `docker-build.sh` (shebang `#!/bin/bash`) runs.

### Documentation

- **README.md**
  - Standard ports (8001 controller, 8002 agent), Alpine build instructions, Windows install scripts, `NEKO_AGENT_CONTROL_URL`, test port on Firewall page, internal agent and WireGuard-optional behaviour.
