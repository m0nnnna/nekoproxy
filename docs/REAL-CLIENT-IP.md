# Forwarding the real client IP

By default every connection a backend receives through NekoProxy appears to come from the
agent's own address (usually its WireGuard IP, e.g. `10.40.40.2`). The agent is a userspace
proxy: it accepts the client's connection on the VPS and opens a **new** connection to the
backend, so the backend's view of the source address is the agent.

To pass the real address along, turn on **PROXY protocol** for the rule. The agent then
prepends one line to each backend connection before any client data:

```
PROXY TCP4 203.0.113.7 198.51.100.1 51234 443\r\n
```

The backend reads that line and uses `203.0.113.7` as the client address.

> **The backend must expect the header.** A backend that is not set up for PROXY protocol
> sees `PROXY TCP4 ...` as garbage and fails the connection (TLS handshake errors, `400 Bad
> Request`, etc.). Configure the backend first, then turn the toggle on.

PROXY protocol is TCP only. UDP rules are unaffected.

## 1. Enable it on the rule

- **New rule:** tick *Forward real client IP (PROXY protocol)* on the Rules page.
- **Existing rule:** click the **PROXY** badge next to the TCP badge in the rules table. It
  goes solid when on. Agents pick the change up on their next config sync, or immediately
  if you click *Apply to Agents*. The listener is not restarted. Connections already open
  keep their old behaviour, and new connections use the new setting.
- **API:** `"proxy_protocol": true` on `POST /api/v1/services` or `PUT /api/v1/services/{id}`.

## 2. nginx

Once `proxy_protocol` is on a `listen` socket, **every** connection to that address:port
must send the header, including LAN clients connecting directly. The cleanest setup is a
**dedicated port for NekoProxy traffic** and pointing the rule's backend port at it:

```nginx
# Real-IP module: trust the header only from the NekoProxy agents' WireGuard addresses
set_real_ip_from 10.40.40.0/24;
real_ip_header   proxy_protocol;

server {
    listen 443  ssl;                  # direct / LAN clients (no header)
    listen 8443 ssl proxy_protocol;   # NekoProxy rule backend: 10.40.40.x:8443
    http2 on;
    server_name example.com;

    ssl_certificate     /etc/letsencrypt/live/example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;               # now the real client IP
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

The `listen ... proxy_protocol` line makes nginx parse the header. `set_real_ip_from` and
`real_ip_header proxy_protocol` make `$remote_addr` (access logs, `allow`/`deny`, `limit_req`,
`X-Real-IP`) show the real client instead of `10.40.40.2`. Without them the address is only
available as `$proxy_protocol_addr`.

`set_real_ip_from` must cover the address the agent connects **from**, as nginx sees it. For
a WireGuard backend that is the agent's tunnel IP. Do not set it to `0.0.0.0/0`, because
then anyone could spoof their address on that port.

If you'd rather put the header on port 443 itself, keep a single `listen 443 ssl
proxy_protocol;`. Then every client of that port has to come through NekoProxy.

### Port 80 / HTTP

Apply the same treatment to the HTTP rule if you proxy port 80 (ACME challenges, redirects):

```nginx
server {
    listen 80;
    listen 8080 proxy_protocol;       # NekoProxy rule backend: 10.40.40.x:8080
    server_name example.com;
    return 301 https://$host$request_uri;
}
```

### nginx `stream` (raw TCP passthrough)

```nginx
stream {
    server {
        listen 8443 proxy_protocol;
        set_real_ip_from 10.40.40.0/24;
        proxy_pass backend:443;
        # Optionally pass it on to the next hop too:
        # proxy_protocol on;
    }
}
```

### Docker

If nginx runs in Docker with a published port, Docker's userland proxy can itself rewrite
the source address to the bridge gateway (e.g. `172.17.0.1`). `set_real_ip_from` must then
include that address, or use `network_mode: host`.

## 3. The app behind nginx

nginx now hands the app the real IP in `X-Real-IP` / `X-Forwarded-For`. The app still has to
trust those headers from nginx (Misskey/Node `trustProxy`, Django `SECURE_PROXY_SSL_HEADER`
plus a real-IP middleware, Express `app.set('trust proxy', ...)`, and so on).

## 4. Other backends

| Backend  | Setting |
|----------|---------|
| HAProxy  | `bind :8443 accept-proxy` |
| Traefik  | `entryPoints.<name>.proxyProtocol.trustedIPs = ["10.40.40.0/24"]` |
| Caddy    | `listener_wrappers { proxy_protocol { allow 10.40.40.0/24 } tls }` |
| Postfix  | `smtpd_upstream_proxy_protocol = haproxy` (on a dedicated `master.cf` service) |
| Dovecot  | `haproxy_trusted_networks = 10.40.40.0/24` + `haproxy = yes` on the inet_listener |

## 5. Verify

- nginx access log shows public IPs instead of `10.40.40.2`.
- If connections break right after enabling: the backend isn't parsing the header. Check
  that the rule points to the port with `proxy_protocol`. The nginx error log shows
  `broken header` when a client connects to a `proxy_protocol` port *without* the header,
  which means the toggle is off or something else is hitting that port.
