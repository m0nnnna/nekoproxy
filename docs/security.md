# NekoProxy Security Configuration Guide

This guide covers how to configure authentication tokens, TLS encryption, and
deployment best practices for a secure NekoProxy installation.

---

## Token Architecture

NekoProxy uses three token types:

| Token | Direction | Header | Purpose |
|-------|-----------|--------|---------|
| **Admin API token** | Human / tooling → Controller | `X-API-Token` or `Authorization: Bearer <token>` | Admin API access and web UI login |
| **Per-agent token** | Agent → Controller | `X-Agent-Token` | Authenticates each agent's heartbeats, config polls, and stat reports |
| **Controller token** | Controller → Agent | `X-Controller-Token` | Authenticates controller calls to agent ControlAPI (trigger-sync, push updates, deploy email) |

---

## Quick Start (Minimum Secure Setup)

### 1. Generate and note your API token

On first start the controller auto-generates an API token and prints it to the log:

```
WARNING: GENERATED ADMIN API TOKEN (save this):
  <your-token-here>
Set NEKO_API_TOKEN env var to use a fixed token.
```

Save this token.  It is your master credential for the web UI and admin API.

To use a fixed token instead of a generated one, set in your controller `.env`:

```
NEKO_API_TOKEN=your-strong-random-token-here
```

### 2. Set the controller token on agents

After first startup, find the controller token in the database (or set one explicitly):

```
# In controller .env — set a fixed controller token:
NEKO_CONTROLLER_TOKEN=another-strong-random-token

# In every agent .env or agent.env — set the same value:
NEKO_AGENT_CONTROLLER_TOKEN=another-strong-random-token
```

### 3. Agents receive their token automatically

When an agent registers, the controller returns a per-agent token.  The agent saves it
to `agent_token.txt` in its install directory and loads it on restart automatically.

No manual configuration is needed for per-agent tokens.

---

## TLS / HTTPS Configuration

Encrypting the controller↔agent channel protects configuration data, blocklists, and
tokens in transit.  This is especially important when agents connect over raw IP
addresses rather than WireGuard.

### Controller HTTPS

Generate a certificate.  For a server reachable at a raw IP (e.g. `192.168.0.2`),
the certificate **must include the IP as a Subject Alternative Name (SAN)**:

```bash
# Self-signed cert valid for both a domain and a raw IP
openssl req -x509 -newkey rsa:4096 -keyout controller-key.pem -out controller-cert.pem \
  -days 3650 -nodes \
  -subj "/CN=nekoproxy-controller" \
  -addext "subjectAltName=IP:192.168.0.2,DNS:controller.example.com"
```

Then in controller `.env`:

```
NEKO_SSL_CERTFILE=/etc/nekoproxy/controller-cert.pem
NEKO_SSL_KEYFILE=/etc/nekoproxy/controller-key.pem
```

The controller URL shown to agents should use `https://`:

```
NEKO_CONTROLLER_URL=https://192.168.0.2:8001
# (or set via the Settings page in the web UI)
```

### Agent connecting to HTTPS controller

**Option A — Trust system CA (controller uses a CA-signed cert):**
No extra config needed.  `NEKO_AGENT_CONTROLLER_SSL_VERIFY=true` (default).

**Option B — Custom CA certificate (self-signed with your own CA):**
```
NEKO_AGENT_CONTROLLER_SSL_CA_CERT=/etc/nekoproxy/controller-ca.pem
```

**Option C — Disable verification (self-signed, no shared CA — use only on trusted networks):**
```
NEKO_AGENT_CONTROLLER_SSL_VERIFY=false
```

### Agent ControlAPI HTTPS

To encrypt the controller → agent direction, generate a per-agent certificate:

```bash
# Include the agent's WireGuard IP and/or public IP as SANs
openssl req -x509 -newkey rsa:4096 -keyout agent-key.pem -out agent-cert.pem \
  -days 3650 -nodes \
  -subj "/CN=nekoproxy-agent" \
  -addext "subjectAltName=IP:10.0.0.2,IP:203.0.113.10"
```

In agent `.env` / `agent.env`:
```
NEKO_AGENT_CONTROL_SSL_CERTFILE=/etc/nekoproxy/agent-cert.pem
NEKO_AGENT_CONTROL_SSL_KEYFILE=/etc/nekoproxy/agent-key.pem
```

To disable certificate verification on the controller side (when agents use
self-signed certs):
```
# In controller .env:
NEKO_AGENT_API_SSL_VERIFY=false
```

---

## Environment Variable Reference

### Controller

| Variable | Default | Description |
|----------|---------|-------------|
| `NEKO_API_TOKEN` | *(auto-generated)* | Admin API token.  Required for all API requests and web UI login. |
| `NEKO_CONTROLLER_TOKEN` | *(auto-generated)* | Token sent to agents when controller calls their ControlAPI. |
| `NEKO_SSL_CERTFILE` | *(none)* | Path to TLS certificate for HTTPS. |
| `NEKO_SSL_KEYFILE` | *(none)* | Path to TLS private key for HTTPS. |
| `NEKO_AGENT_API_SSL_VERIFY` | `true` | Verify SSL certificates when calling agents' ControlAPI. |
| `NEKO_AGENT_SECRET` | *(none)* | Shared secret required for agent registration. |

### Agent

| Variable | Default | Description |
|----------|---------|-------------|
| `NEKO_AGENT_AGENT_TOKEN` | *(loaded from agent_token.txt)* | Per-agent token.  Usually auto-saved; set manually if needed. |
| `NEKO_AGENT_CONTROLLER_TOKEN` | *(none)* | Token that the controller must send to this agent.  Set to match `NEKO_CONTROLLER_TOKEN`. |
| `NEKO_AGENT_CONTROLLER_SSL_VERIFY` | `true` | Verify controller TLS certificate. |
| `NEKO_AGENT_CONTROLLER_SSL_CA_CERT` | *(none)* | Path to CA cert for verifying controller's self-signed cert. |
| `NEKO_AGENT_CONTROL_SSL_CERTFILE` | *(none)* | TLS certificate for agent's ControlAPI server. |
| `NEKO_AGENT_CONTROL_SSL_KEYFILE` | *(none)* | TLS private key for agent's ControlAPI server. |
| `NEKO_AGENT_CONTROLLER_URL` | `http://localhost:8001` | Controller base URL (use `https://` when TLS is enabled). |
| `NEKO_AGENT_SECRET` | *(none)* | Shared secret for registration (must match controller `NEKO_AGENT_SECRET`). |

---

## API Usage

### Admin API (curl examples)

```bash
TOKEN="your-api-token"
BASE="https://controller.example.com:8001"

# List agents
curl -H "X-API-Token: $TOKEN" "$BASE/api/v1/agents"

# Or using Authorization: Bearer
curl -H "Authorization: Bearer $TOKEN" "$BASE/api/v1/agents"

# Add to blocklist
curl -X POST -H "X-API-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"ip":"1.2.3.4","reason":"manual block"}' "$BASE/api/v1/blocklist"
```

### Web UI

Navigate to `https://controller.example.com:8001/` in a browser.  You will be
redirected to `/login`.  Enter the API token as the password.  Sessions last 8 hours.

---

## Deployment Checklist

- [ ] Set `NEKO_API_TOKEN` to a strong random secret (or note the auto-generated one)
- [ ] Set `NEKO_CONTROLLER_TOKEN` and matching `NEKO_AGENT_CONTROLLER_TOKEN` on all agents
- [ ] Enable TLS on the controller (`NEKO_SSL_CERTFILE` / `NEKO_SSL_KEYFILE`)
- [ ] Set `NEKO_AGENT_CONTROLLER_URL=https://...` on all agents
- [ ] Optionally enable TLS on agent ControlAPI (`NEKO_AGENT_CONTROL_SSL_CERTFILE` / `NEKO_AGENT_CONTROL_SSL_KEYFILE`)
- [ ] Set `NEKO_AGENT_SECRET` to require a shared secret at registration
- [ ] Restrict access to the database file (`chmod 600 nekoproxy.db`)
- [ ] Place the controller behind a reverse proxy (nginx/Caddy) for additional rate limiting and certificate management
- [ ] Rotate tokens periodically or after any suspected compromise

---

## Token Rotation

1. Generate a new token: `python -c "import secrets; print(secrets.token_urlsafe(32))"`
2. Update `NEKO_API_TOKEN` in the controller environment and restart
3. Agents will continue to use their per-agent tokens (unaffected by admin token rotation)
4. To rotate the controller token: update `NEKO_CONTROLLER_TOKEN` on the controller **and**
   `NEKO_AGENT_CONTROLLER_TOKEN` on all agents, then restart both
5. Per-agent tokens are rotated by deleting the agent from the controller (which removes its
   DB record) and re-registering — the new registration will issue a new token
