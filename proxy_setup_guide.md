# NekoProxy Installation Guide

## Quick Install (Interactive)

On your new proxy server:

```bash
# Download the installer
wget https://github.com/m0nnnna/wow/blob/main/proxy/proxy_installer.sh

# Make it executable
chmod +x nekoproxy-install.sh

# Run the installer
sudo ./nekoproxy-install.sh
```

The installer will prompt you for:
- Internal server IP (e.g., 192.168.0.85)
- WireGuard interface name (default: wg0)
- External interface name (auto-detected)

## Automated Install (Non-Interactive)

For automated deployment, set environment variables:

```bash
# Set configuration
export INTERNAL_HOST="192.168.0.85"
export WIREGUARD_INTERFACE="wg0"
export EXTERNAL_INTERFACE="enp1s0"

# Run installer
sudo -E ./nekoproxy-install.sh
```

## What the Installer Does

1. ✅ Installs Python3, jq, and iptables-persistent
2. ✅ Creates logging directories
3. ✅ Installs the Python proxy script
4. ✅ Installs the management tools
5. ✅ Creates systemd service
6. ✅ Configures system settings (IP forwarding)
7. ✅ Configures UFW (if present)
8. ✅ Starts the proxy service

## Post-Installation

### Verify Installation

```bash
# Check service status
sudo systemctl status nekoproxy

# Check if ports are listening
sudo ss -tlnp | grep -E ':(3724|8085)'

# Test connectivity to internal server
ping 192.168.0.85
```

### View Logs

```bash
# Watch live connections
nekoproxy-manage watch

# View recent connections
nekoproxy-manage recent 50

# View statistics
nekoproxy-manage stats
```

### Management

```bash
# Block an IP
nekoproxy-manage block 1.2.3.4

# Unblock an IP
nekoproxy-manage unblock 1.2.3.4

# List blocked IPs
nekoproxy-manage list-blocked

# Search for specific IP
nekoproxy-manage search 1.2.3.4

# Show top connecting IPs
nekoproxy-manage top-ips 20
```

## Load Balancing Setup

### Using DNS Round-Robin

Point your game server domain to multiple proxy IPs:

```
wow.yourdomain.com.  A  222.222.22.222  # Proxy 1
wow.yourdomain.com.  A  223.223.33.333  # Proxy 2
wow.yourdomain.com.  A  224.224.44.444  # Proxy 3
```

### Using HAProxy/Nginx (Advanced)

For more control, use a load balancer in front of your proxies.

## Troubleshooting

### Proxy won't start

```bash
# Check for errors
sudo journalctl -u nekoproxy -n 50

# Check if ports are already in use
sudo ss -tlnp | grep -E ':(3724|8085)'

# Manually test
sudo python3 /usr/local/bin/nekoproxy.py
```

### No connections logged

```bash
# Check iptables for DNAT rules (should be none)
sudo iptables -t nat -L PREROUTING -n -v

# If DNAT rules exist, remove them
sudo iptables -t nat -D PREROUTING -i enp1s0 -p tcp --dport 3724 -j DNAT --to-destination 192.168.0.85:3724
sudo iptables -t nat -D PREROUTING -i enp1s0 -p tcp --dport 8085 -j DNAT --to-destination 192.168.0.85:8085
```

### Can't reach internal server

```bash
# Check WireGuard status
sudo wg show

# Check routes
ip route show

# Test connectivity
ping 192.168.0.85
telnet 192.168.0.85 3724
```

## Configuration Files

- **Proxy script**: `/usr/local/bin/nekoproxy.py`
- **Management script**: `/usr/local/bin/nekoproxy-manage`
- **Systemd service**: `/etc/systemd/system/nekoproxy.service`
- **Connection logs**: `/var/log/nekoproxy/connections.jsonl`
- **Service logs**: `/var/log/nekoproxy/proxy.log`
- **Blocklist**: `/etc/nekoproxy/blocklist.txt`

## Uninstalling

```bash
# Stop and disable service
sudo systemctl stop nekoproxy
sudo systemctl disable nekoproxy

# Remove files
sudo rm /usr/local/bin/nekoproxy.py
sudo rm /usr/local/bin/nekoproxy-manage
sudo rm /etc/systemd/system/nekoproxy.service
sudo rm -rf /var/log/nekoproxy
sudo rm -rf /etc/nekoproxy

# Reload systemd
sudo systemctl daemon-reload
```

## Security Notes

- The proxy logs real client IPs - ensure logs are protected
- Blocklist is reloaded every 60 seconds automatically
- UFW should be configured to only allow necessary ports
- WireGuard connection should use strong encryption
