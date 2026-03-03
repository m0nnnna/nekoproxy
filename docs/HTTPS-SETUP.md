# Controller over HTTPS

Run the controller behind Nginx (or another reverse proxy) so agents and the web UI use HTTPS. Agents then point at **port 443** and Nginx forwards to the controller on 8001.

## Checklist

### 1. Controller stays on 8001 (internal)

- Run the controller as usual, listening on `0.0.0.0:8001` (or `127.0.0.1:8001` if only Nginx should reach it).
- No TLS on the controller itself; Nginx terminates HTTPS.

### 2. Nginx in front (port 443)

- Install Nginx and get a TLS certificate (e.g. Let’s Encrypt with certbot).
- Configure a server block that:
  - Listens on `443` with `ssl`.
  - Proxies to `http://127.0.0.1:8001` (or your controller host/port).
  - Preserves `Host` and `X-Forwarded-*` if the app needs them.

Example (minimal):

```nginx
server {
    listen 443 ssl;
    server_name your-controller.example.com;

    ssl_certificate     /etc/letsencrypt/live/your-controller.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-controller.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Reload Nginx after changes.

### 3. Agent: use HTTPS and port 443

On each agent, set the controller URL to **https** and port **443** (no port = 443 for HTTPS):

- **Agent env** (e.g. `/etc/nekoproxy/agent.env`):
  - `NEKO_AGENT_CONTROLLER_URL=https://your-controller.example.com`
  - or `NEKO_AGENT_CONTROLLER_URL=https://10.40.40.1` if you use the WireGuard IP and Nginx listens on 443 there.

If the controller is only reachable via WireGuard, use the controller’s WireGuard hostname or IP in that URL.

### 4. Optional: restrict controller to localhost

If only Nginx should talk to the controller:

- Set controller listen to `127.0.0.1:8001` (e.g. `NEKO_HOST=127.0.0.1`).
- Ensure Nginx and the controller run on the same host or that Nginx proxies to the host where the controller listens.

### 5. Verify

- Open `https://your-controller.example.com` in a browser → Web UI loads.
- Restart an agent with `NEKO_AGENT_CONTROLLER_URL=https://...` → it registers and receives config; traffic between agent and controller is encrypted over HTTPS.

## Summary

| Component    | Before HTTPS     | After HTTPS                          |
|-------------|-------------------|--------------------------------------|
| Controller  | Listen 0.0.0.0:8001 | Listen 0.0.0.0:8001 (unchanged)     |
| Nginx       | —                 | Listen 443, proxy to 127.0.0.1:8001 |
| Agent URL   | `http://...:8001` | `https://...` (port 443 implied)     |

Agent↔controller traffic is then encrypted; the controller itself still runs on 8001 and Nginx forwards the reply.
