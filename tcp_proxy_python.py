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
    {'listen_port': 3724, 'backend_host': '192.168.0.85', 'backend_port': 3724, 'name': 'AuthServer'},
    {'listen_port': 8085, 'backend_host': '192.168.0.85', 'backend_port': 8085, 'name': 'WorldServer'},
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
