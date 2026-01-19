#!/bin/bash
# NekoProxy Installer
# Automated installation script for TCP proxy with IP logging

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration - EDIT THESE VALUES
INTERNAL_HOST="${INTERNAL_HOST:-192.168.0.85}"
WIREGUARD_INTERFACE="${WIREGUARD_INTERFACE:-wg0}"
EXTERNAL_INTERFACE="${EXTERNAL_INTERFACE:-}"  # Auto-detect if empty

# Ports to forward
declare -a FORWARD_PORTS=(
    "8085:tcp:WorldServer"
    "3724:tcp:AuthServer"
)

echo -e "${GREEN}=====================================================================${NC}"
echo -e "${GREEN}NekoProxy Installer${NC}"
echo -e "${GREEN}=====================================================================${NC}"
echo ""

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}Error: This script must be run as root${NC}"
   exit 1
fi

# Prompt for configuration if not set
if [ -z "$INTERNAL_HOST" ]; then
    read -p "Enter internal server IP (e.g., 192.168.0.85): " INTERNAL_HOST
fi

if [ -z "$WIREGUARD_INTERFACE" ]; then
    read -p "Enter WireGuard interface name (default: wg0): " WIREGUARD_INTERFACE
    WIREGUARD_INTERFACE=${WIREGUARD_INTERFACE:-wg0}
fi

# Auto-detect external interface if not set
if [ -z "$EXTERNAL_INTERFACE" ]; then
    EXTERNAL_INTERFACE=$(ip route | grep default | awk '{print $5}' | head -n1)
    echo -e "${YELLOW}Auto-detected external interface: $EXTERNAL_INTERFACE${NC}"
    read -p "Is this correct? (y/n): " confirm
    if [[ ! $confirm =~ ^[Yy]$ ]]; then
        read -p "Enter external interface name: " EXTERNAL_INTERFACE
    fi
fi

echo ""
echo -e "${GREEN}Configuration:${NC}"
echo "  Internal host: $INTERNAL_HOST"
echo "  WireGuard interface: $WIREGUARD_INTERFACE"
echo "  External interface: $EXTERNAL_INTERFACE"
echo ""
read -p "Continue with installation? (y/n): " confirm
if [[ ! $confirm =~ ^[Yy]$ ]]; then
    echo "Installation cancelled."
    exit 0
fi

echo ""
echo -e "${GREEN}[1/7] Installing dependencies...${NC}"
apt update
apt install -y python3 python3-pip jq iptables-persistent

echo ""
echo -e "${GREEN}[2/7] Creating directories...${NC}"
mkdir -p /var/log/nekoproxy
mkdir -p /etc/nekoproxy
touch /etc/nekoproxy/blocklist.txt

echo ""
echo -e "${GREEN}[3/7] Installing Python proxy script...${NC}"
cat > /usr/local/bin/nekoproxy.py <<'EOFPYTHON'
#!/usr/bin/env python3
"""
TCP Proxy with IP logging and optional blocking for NekoProxy.
Logs all connections and can block IPs based on a blocklist.
"""

import socket
import select
import threading
import logging
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Tuple, Set

# Configuration
PROXY_CONFIG = [
    {'listen_port': 3724, 'backend_host': 'INTERNAL_HOST_PLACEHOLDER', 'backend_port': 3724, 'name': 'AuthServer'},
    {'listen_port': 8085, 'backend_host': 'INTERNAL_HOST_PLACEHOLDER', 'backend_port': 8085, 'name': 'WorldServer'},
]

BUFFER_SIZE = 8192
LISTEN_IP = '0.0.0.0'
LOG_DIR = '/var/log/nekoproxy'
BLOCKLIST_FILE = '/etc/nekoproxy/blocklist.txt'

# In-memory blocklist (reloaded periodically)
blocked_ips: Set[str] = set()
blocklist_lock = threading.Lock()

# Setup logging
Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f'{LOG_DIR}/proxy.log'),
        logging.StreamHandler()
    ]
)

# Connection log (JSON format for easy parsing)
conn_log_file = f'{LOG_DIR}/connections.jsonl'

def log_connection(client_ip: str, client_port: int, server_name: str,
                   server_port: int, status: str, duration: float = 0,
                   bytes_sent: int = 0, bytes_recv: int = 0):
    """Log connection details in JSON format"""
    log_entry = {
        'timestamp': datetime.utcnow().isoformat(),
        'client_ip': client_ip,
        'client_port': client_port,
        'server': server_name,
        'server_port': server_port,
        'status': status,
        'duration_seconds': round(duration, 2),
        'bytes_sent': bytes_sent,
        'bytes_received': bytes_recv
    }

    with open(conn_log_file, 'a') as f:
        f.write(json.dumps(log_entry) + '\n')

def load_blocklist():
    """Load blocked IPs from file"""
    global blocked_ips
    try:
        if Path(BLOCKLIST_FILE).exists():
            with open(BLOCKLIST_FILE, 'r') as f:
                with blocklist_lock:
                    blocked_ips = {line.strip() for line in f if line.strip() and not line.startswith('#')}
            logging.info(f"Loaded {len(blocked_ips)} blocked IPs")
        else:
            logging.info(f"No blocklist found at {BLOCKLIST_FILE}")
            Path(BLOCKLIST_FILE).parent.mkdir(parents=True, exist_ok=True)
            Path(BLOCKLIST_FILE).touch()
    except Exception as e:
        logging.error(f"Error loading blocklist: {e}")

def is_blocked(ip: str) -> bool:
    """Check if IP is blocked"""
    with blocklist_lock:
        return ip in blocked_ips

def reload_blocklist_periodically():
    """Reload blocklist every 60 seconds"""
    while True:
        time.sleep(60)
        load_blocklist()

def forward_data(source: socket.socket, destination: socket.socket,
                direction: str, stats: dict):
    """Forward data between sockets and track bytes"""
    try:
        while True:
            data = source.recv(BUFFER_SIZE)
            if not data:
                break
            destination.sendall(data)

            # Track bytes
            if direction == "c2b":
                stats['bytes_sent'] += len(data)
            else:
                stats['bytes_recv'] += len(data)

    except Exception as e:
        logging.debug(f"Connection closed ({direction}): {e}")
    finally:
        try:
            source.shutdown(socket.SHUT_RDWR)
        except:
            pass
        try:
            destination.shutdown(socket.SHUT_RDWR)
        except:
            pass

def handle_client(client_sock: socket.socket, client_addr: Tuple[str, int],
                  backend_host: str, backend_port: int, server_name: str):
    """Handle a single client connection"""
    client_ip, client_port = client_addr
    start_time = time.time()
    stats = {'bytes_sent': 0, 'bytes_recv': 0}

    # Check if IP is blocked
    if is_blocked(client_ip):
        logging.warning(f"[{server_name}] BLOCKED connection from {client_ip}:{client_port}")
        log_connection(client_ip, client_port, server_name, backend_port,
                      'blocked', 0, 0, 0)
        client_sock.close()
        return

    logging.info(f"[{server_name}] Connection from {client_ip}:{client_port}")

    backend_sock = None
    try:
        # Connect to backend
        backend_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        backend_sock.settimeout(10)
        backend_sock.connect((backend_host, backend_port))
        backend_sock.settimeout(None)

        logging.info(f"[{server_name}] Forwarding {client_ip}:{client_port} -> {backend_host}:{backend_port}")

        # Create bidirectional forwarding threads
        c2b = threading.Thread(target=forward_data,
                              args=(client_sock, backend_sock, "c2b", stats))
        b2c = threading.Thread(target=forward_data,
                              args=(backend_sock, client_sock, "b2c", stats))

        c2b.daemon = True
        b2c.daemon = True

        c2b.start()
        b2c.start()

        # Wait for both directions to complete
        c2b.join()
        b2c.join()

        duration = time.time() - start_time
        log_connection(client_ip, client_port, server_name, backend_port,
                      'completed', duration, stats['bytes_sent'], stats['bytes_recv'])

    except Exception as e:
        logging.error(f"[{server_name}] Error handling connection from {client_ip}: {e}")
        duration = time.time() - start_time
        log_connection(client_ip, client_port, server_name, backend_port,
                      'error', duration, stats['bytes_sent'], stats['bytes_recv'])
    finally:
        if backend_sock:
            backend_sock.close()
        client_sock.close()
        logging.info(f"[{server_name}] Closed connection from {client_ip}:{client_port} " +
                    f"(duration: {time.time()-start_time:.2f}s, " +
                    f"sent: {stats['bytes_sent']} bytes, recv: {stats['bytes_recv']} bytes)")

def start_listener(listen_port: int, backend_host: str, backend_port: int, server_name: str):
    """Start listening on a port and forward connections"""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server.bind((LISTEN_IP, listen_port))
        server.listen(100)
        logging.info(f"[{server_name}] Listening on {LISTEN_IP}:{listen_port} -> {backend_host}:{backend_port}")

        while True:
            client_sock, client_addr = server.accept()
            # Handle each connection in a new thread
            thread = threading.Thread(
                target=handle_client,
                args=(client_sock, client_addr, backend_host, backend_port, server_name)
            )
            thread.daemon = True
            thread.start()

    except Exception as e:
        logging.error(f"[{server_name}] Error on port {listen_port}: {e}")
    finally:
        server.close()

def main():
    logging.info("=" * 70)
    logging.info("NekoProxy TCP Proxy with IP Logging")
    logging.info("=" * 70)
    logging.info(f"Connection log: {conn_log_file}")
    logging.info(f"Blocklist: {BLOCKLIST_FILE}")
    logging.info("")

    # Load initial blocklist
    load_blocklist()

    # Start blocklist reload thread
    reload_thread = threading.Thread(target=reload_blocklist_periodically)
    reload_thread.daemon = True
    reload_thread.start()

    # Start proxy listeners
    threads = []
    for config in PROXY_CONFIG:
        thread = threading.Thread(
            target=start_listener,
            args=(config['listen_port'], config['backend_host'],
                 config['backend_port'], config['name'])
        )
        thread.daemon = True
        thread.start()
        threads.append(thread)

    logging.info("=" * 70)
    logging.info("All proxies started. Press Ctrl+C to stop.")
    logging.info("")
    logging.info("To block an IP, add it to: " + BLOCKLIST_FILE)
    logging.info("To view connections: tail -f " + conn_log_file)
    logging.info("=" * 70)

    try:
        # Keep main thread alive
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        logging.info("\nShutting down...")

if __name__ == "__main__":
    main()
EOFPYTHON

# Replace placeholder with actual internal host
sed -i "s/INTERNAL_HOST_PLACEHOLDER/$INTERNAL_HOST/g" /usr/local/bin/nekoproxy.py

chmod +x /usr/local/bin/nekoproxy.py

echo ""
echo -e "${GREEN}[4/7] Installing management script...${NC}"
cat > /usr/local/bin/nekoproxy-manage <<'EOFMANAGE'
#!/bin/bash
# Management scripts for NekoProxy

BLOCKLIST_FILE="/etc/nekoproxy/blocklist.txt"
CONN_LOG="/var/log/nekoproxy/connections.jsonl"

# Ensure directories exist
mkdir -p /etc/nekoproxy
mkdir -p /var/log/nekoproxy
touch "$BLOCKLIST_FILE"

case "$1" in
    block)
        if [ -z "$2" ]; then
            echo "Usage: $0 block <IP_ADDRESS>"
            exit 1
        fi
        IP="$2"
        if grep -q "^$IP$" "$BLOCKLIST_FILE" 2>/dev/null; then
            echo "IP $IP is already blocked"
        else
            echo "$IP" >> "$BLOCKLIST_FILE"
            echo "Blocked IP: $IP"
            echo "Blocklist will reload within 60 seconds, or restart the proxy service"
        fi
        ;;

    unblock)
        if [ -z "$2" ]; then
            echo "Usage: $0 unblock <IP_ADDRESS>"
            exit 1
        fi
        IP="$2"
        if grep -q "^$IP$" "$BLOCKLIST_FILE" 2>/dev/null; then
            sed -i "/^$IP$/d" "$BLOCKLIST_FILE"
            echo "Unblocked IP: $IP"
        else
            echo "IP $IP was not in blocklist"
        fi
        ;;

    list-blocked)
        echo "=== Blocked IPs ==="
        if [ -s "$BLOCKLIST_FILE" ]; then
            cat "$BLOCKLIST_FILE" | grep -v '^#' | grep -v '^$'
        else
            echo "No IPs currently blocked"
        fi
        ;;

    recent)
        LINES="${2:-20}"
        echo "=== Recent Connections (last $LINES) ==="
        if [ -f "$CONN_LOG" ]; then
            tail -n "$LINES" "$CONN_LOG" | jq -r '. | "\(.timestamp) - \(.client_ip):\(.client_port) -> \(.server) (\(.status)) - \(.bytes_sent) bytes sent, \(.bytes_received) bytes received"'
        else
            echo "No connection log found"
        fi
        ;;

    top-ips)
        LINES="${2:-10}"
        echo "=== Top $LINES Connecting IPs ==="
        if [ -f "$CONN_LOG" ]; then
            jq -r '.client_ip' "$CONN_LOG" 2>/dev/null | sort | uniq -c | sort -rn | head -n "$LINES"
        else
            echo "No connection log found"
        fi
        ;;

    watch)
        echo "=== Watching live connections (Ctrl+C to stop) ==="
        tail -f "$CONN_LOG" | while read line; do
            echo "$line" | jq -r '. | "\(.timestamp) - \(.client_ip) -> \(.server) (\(.status))"'
        done
        ;;

    stats)
        if [ ! -f "$CONN_LOG" ]; then
            echo "No connection log found"
            exit 1
        fi

        echo "=== Connection Statistics ==="
        echo ""
        echo "Total connections:"
        wc -l < "$CONN_LOG"
        echo ""
        echo "By status:"
        jq -r '.status' "$CONN_LOG" 2>/dev/null | sort | uniq -c
        echo ""
        echo "By server:"
        jq -r '.server' "$CONN_LOG" 2>/dev/null | sort | uniq -c
        echo ""
        echo "Blocked connections:"
        grep -c '"status":"blocked"' "$CONN_LOG" 2>/dev/null || echo "0"
        ;;

    search)
        if [ -z "$2" ]; then
            echo "Usage: $0 search <IP_ADDRESS>"
            exit 1
        fi
        IP="$2"
        echo "=== Connections from $IP ==="
        if [ -f "$CONN_LOG" ]; then
            grep "\"client_ip\":\"$IP\"" "$CONN_LOG" | jq -r '. | "\(.timestamp) - \(.server) (\(.status)) - \(.duration_seconds)s"'
        else
            echo "No connection log found"
        fi
        ;;

    *)
        echo "NekoProxy Management"
        echo ""
        echo "Usage: $0 <command> [arguments]"
        echo ""
        echo "Commands:"
        echo "  block <IP>         - Block an IP address"
        echo "  unblock <IP>       - Unblock an IP address"
        echo "  list-blocked       - Show all blocked IPs"
        echo "  recent [N]         - Show last N connections (default: 20)"
        echo "  top-ips [N]        - Show top N connecting IPs (default: 10)"
        echo "  watch              - Watch live connections"
        echo "  stats              - Show connection statistics"
        echo "  search <IP>        - Search connections from specific IP"
        echo ""
        echo "Examples:"
        echo "  $0 block 1.2.3.4"
        echo "  $0 recent 50"
        echo "  $0 top-ips 20"
        echo "  $0 search 1.2.3.4"
        ;;
esac
EOFMANAGE

chmod +x /usr/local/bin/nekoproxy-manage

echo ""
echo -e "${GREEN}[5/7] Creating systemd service...${NC}"
cat > /etc/systemd/system/nekoproxy.service <<EOF
[Unit]
Description=NekoProxy TCP Proxy with IP Logging
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/python3 /usr/local/bin/nekoproxy.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable nekoproxy

echo ""
echo -e "${GREEN}[6/7] Configuring system settings...${NC}"

# Enable IP forwarding
sysctl -w net.ipv4.ip_forward=1
echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-nekoproxy.conf

# Configure UFW if it exists
if command -v ufw &> /dev/null; then
    echo -e "${YELLOW}Configuring UFW...${NC}"

    # Disable IPv6 in UFW
    sed -i 's/IPV6=yes/IPV6=no/' /etc/default/ufw

    # Check if before.rules exists
    if [ -f /etc/ufw/before.rules ]; then
        # Backup existing before.rules
        cp /etc/ufw/before.rules /etc/ufw/before.rules.backup

        # Ensure MASQUERADE is set for WireGuard
        if ! grep -q "POSTROUTING.*${WIREGUARD_INTERFACE}.*MASQUERADE" /etc/ufw/before.rules; then
            # Add nat table if not present
            if ! grep -q "^*nat" /etc/ufw/before.rules; then
                echo "*nat" >> /etc/ufw/before.rules
                echo ":PREROUTING ACCEPT [0:0]" >> /etc/ufw/before.rules
                echo ":POSTROUTING ACCEPT [0:0]" >> /etc/ufw/before.rules
                echo "# Masquerade return traffic" >> /etc/ufw/before.rules
                echo "-A POSTROUTING -o ${WIREGUARD_INTERFACE} -j MASQUERADE" >> /etc/ufw/before.rules
                echo "COMMIT" >> /etc/ufw/before.rules
            fi
        fi
    fi

    # Allow proxy ports
    for port_config in "${FORWARD_PORTS[@]}"; do
        PORT="${port_config%%:*}"
        ufw allow $PORT/tcp
    done

    ufw --force enable
    ufw reload
fi

echo ""
echo -e "${GREEN}[7/7] Starting proxy service...${NC}"
systemctl start nekoproxy

echo ""
echo -e "${GREEN}=====================================================================${NC}"
echo -e "${GREEN}Installation Complete!${NC}"
echo -e "${GREEN}=====================================================================${NC}"
echo ""
echo -e "${GREEN}Service Status:${NC}"
systemctl status nekoproxy --no-pager | head -n 10
echo ""
echo -e "${GREEN}Listening Ports:${NC}"
ss -tlnp | grep python3
echo ""
echo -e "${GREEN}Management Commands:${NC}"
echo "  nekoproxy-manage block <IP>       - Block an IP"
echo "  nekoproxy-manage recent           - View recent connections"
echo "  nekoproxy-manage watch            - Watch live connections"
echo "  nekoproxy-manage stats            - View statistics"
echo ""
echo -e "${GREEN}Service Commands:${NC}"
echo "  systemctl status nekoproxy        - Check status"
echo "  systemctl restart nekoproxy       - Restart service"
echo "  systemctl stop nekoproxy          - Stop service"
echo ""
echo -e "${GREEN}Logs:${NC}"
echo "  tail -f /var/log/nekoproxy/connections.jsonl"
echo "  journalctl -u nekoproxy -f"
echo ""
echo -e "${YELLOW}IMPORTANT: Make sure WireGuard is configured and connected!${NC}"
echo ""
echo -e "${GREEN}Configuration:${NC}"
echo "  Internal Host: $INTERNAL_HOST"
echo "  WireGuard Interface: $WIREGUARD_INTERFACE"
echo "  External Interface: $EXTERNAL_INTERFACE"
echo ""
echo -e "${GREEN}Next Steps:${NC}"
echo "1. Verify WireGuard connectivity: ping $INTERNAL_HOST"
echo "2. Test from external client to verify proxy is working"
echo "3. Monitor logs: nekoproxy-manage watch"
echo ""
