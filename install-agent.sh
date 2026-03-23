#!/bin/bash
#
# NekoProxy Agent Installer
#
# This script installs the NekoProxy agent as a systemd service.
# It will prompt for configuration options and set everything up.
#
# Usage: sudo ./install-agent.sh
#

set -e

# Configuration
INSTALL_DIR="/opt/nekoproxy"
CONFIG_FILE="/etc/nekoproxy/agent.env"
SERVICE_NAME="nekoproxy-agent"
BINARY_NAME="nekoproxy-agent"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

print_header() {
    echo ""
    echo -e "${CYAN}============================================================${NC}"
    echo -e "${CYAN}$1${NC}"
    echo -e "${CYAN}============================================================${NC}"
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}! $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

# Detect init system: systemd (Ubuntu/Debian) or OpenRC (Alpine)
use_systemd() {
    command -v systemctl >/dev/null 2>&1 && systemctl list-units --type=service >/dev/null 2>&1
}

# Check if running as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        print_error "This script must be run as root (use sudo)"
        exit 1
    fi
}

# Detect the script directory and binary
find_binary() {
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    # Look for binary in common locations
    if [[ -f "$SCRIPT_DIR/$BINARY_NAME" ]]; then
        BINARY_PATH="$SCRIPT_DIR/$BINARY_NAME"
    elif [[ -f "./$BINARY_NAME" ]]; then
        BINARY_PATH="$(pwd)/$BINARY_NAME"
    else
        print_error "Cannot find $BINARY_NAME binary"
        echo "Please run this script from the directory containing $BINARY_NAME"
        exit 1
    fi

    print_success "Found binary: $BINARY_PATH"
}

# Get current hostname
get_default_hostname() {
    hostname -f 2>/dev/null || hostname
}

# Try to detect WireGuard IP
get_default_wireguard_ip() {
    # Try to get IP from wg0 interface
    ip addr show wg0 2>/dev/null | grep -oP 'inet \K[\d.]+' | head -1 || echo ""
}

# Prompt for configuration
prompt_config() {
    print_header "NekoProxy Agent Configuration"

    # Controller URL
    echo ""
    echo "Enter the Controller URL."
    echo "Use https:// if the controller has TLS enabled (recommended, e.g., https://10.0.0.1:8001)."
    echo "Use http:// only for plain HTTP (e.g., http://10.0.0.1:8001)."
    read -p "Controller URL: " CONTROLLER_URL

    if [[ -z "$CONTROLLER_URL" ]]; then
        print_error "Controller URL is required"
        exit 1
    fi

    # WireGuard IP (optional for internal agents)
    DEFAULT_WG_IP=$(get_default_wireguard_ip)
    echo ""
    echo "Enter this agent's WireGuard IP (leave blank for internal agent — no VPN, sync/push from controller need NEKO_AGENT_CONTROL_URL)"
    if [[ -n "$DEFAULT_WG_IP" ]]; then
        read -p "WireGuard IP [$DEFAULT_WG_IP]: " WIREGUARD_IP
        WIREGUARD_IP="${WIREGUARD_IP:-$DEFAULT_WG_IP}"
    else
        read -p "WireGuard IP (or Enter for internal): " WIREGUARD_IP
    fi

    # Hostname
    DEFAULT_HOSTNAME=$(get_default_hostname)
    echo ""
    echo "Enter a hostname for this agent (used for identification)"
    read -p "Hostname [$DEFAULT_HOSTNAME]: " AGENT_HOSTNAME
    AGENT_HOSTNAME="${AGENT_HOSTNAME:-$DEFAULT_HOSTNAME}"

    # Public IP (optional)
    echo ""
    echo "Enter this agent's public IP address (optional, press Enter to skip)"
    read -p "Public IP [auto-detect]: " PUBLIC_IP

    # Agent registration secret
    echo ""
    echo "If the controller requires an agent registration secret, enter it here."
    echo "Leave blank if no secret is configured on the controller."
    read -p "Agent secret [leave blank if none]: " AGENT_SECRET

    # Summary
    print_header "Configuration Summary"
    echo "  Controller URL: $CONTROLLER_URL"
    echo "  WireGuard IP:   ${WIREGUARD_IP:-<internal>}"
    echo "  Hostname:       $AGENT_HOSTNAME"
    echo "  Public IP:      ${PUBLIC_IP:-auto-detect}"
    echo "  Agent secret:   ${AGENT_SECRET:-(none)}"
    echo "  TLS:            Auto-generated self-signed cert on first start"
    if [[ "$CONTROLLER_URL" == https://* ]]; then
        echo "  Controller TLS: HTTPS — cert will be downloaded and cached on first registration (TOFU)"
    fi
    echo ""

    read -p "Is this correct? (y/n): " CONFIRM
    if [[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]]; then
        echo "Installation cancelled."
        exit 0
    fi
}

# Create directories
create_directories() {
    print_header "Creating directories..."

    mkdir -p "$INSTALL_DIR"
    mkdir -p "$(dirname "$CONFIG_FILE")"

    print_success "Created $INSTALL_DIR"
    print_success "Created $(dirname "$CONFIG_FILE")"
}

# Install binary
install_binary() {
    print_header "Installing binary..."

    cp "$BINARY_PATH" "$INSTALL_DIR/$BINARY_NAME"
    chmod +x "$INSTALL_DIR/$BINARY_NAME"

    print_success "Installed $INSTALL_DIR/$BINARY_NAME"
}

# Create configuration file
create_config() {
    print_header "Creating configuration..."

    cat > "$CONFIG_FILE" << EOF
# NekoProxy Agent Configuration
# Generated by install-agent.sh on $(date)

# Controller connection
NEKO_AGENT_CONTROLLER_URL=$CONTROLLER_URL

# Agent identification
NEKO_AGENT_HOSTNAME=$AGENT_HOSTNAME
EOF

    if [[ -n "$WIREGUARD_IP" ]]; then
        echo "NEKO_AGENT_WIREGUARD_IP=$WIREGUARD_IP" >> "$CONFIG_FILE"
    fi
    if [[ -n "$PUBLIC_IP" ]]; then
        echo "NEKO_AGENT_PUBLIC_IP=$PUBLIC_IP" >> "$CONFIG_FILE"
    fi
    if [[ -n "$AGENT_SECRET" ]]; then
        echo "" >> "$CONFIG_FILE"
        echo "# Registration secret (must match NEKO_AGENT_SECRET on controller)" >> "$CONFIG_FILE"
        echo "NEKO_AGENT_AGENT_SECRET=$AGENT_SECRET" >> "$CONFIG_FILE"
    fi

    # Secure the config file
    chmod 600 "$CONFIG_FILE"

    print_success "Created $CONFIG_FILE"
}

# Create systemd or OpenRC service (Alpine uses OpenRC)
create_service() {
    if use_systemd; then
        print_header "Creating systemd service..."

        cat > "/etc/systemd/system/$SERVICE_NAME.service" << EOF
[Unit]
Description=NekoProxy Agent
Documentation=https://github.com/your-repo/nekoproxy
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
EnvironmentFile=$CONFIG_FILE
ExecStart=$INSTALL_DIR/$BINARY_NAME
Restart=always
RestartSec=5
StartLimitIntervalSec=60
StartLimitBurst=3

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=$SERVICE_NAME

# Security hardening
NoNewPrivileges=false
ProtectSystem=false
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

        print_success "Created /etc/systemd/system/$SERVICE_NAME.service"
        systemctl daemon-reload
        print_success "Reloaded systemd"
    else
        print_header "Creating OpenRC service (Alpine)..."

        cat > "/etc/init.d/$SERVICE_NAME" << INITD
#!/sbin/openrc-run

description="NekoProxy Agent"
command="/bin/sh"
command_args="-c 'set -a; [ -r $CONFIG_FILE ] && . $CONFIG_FILE; set +a; exec $INSTALL_DIR/nekoproxy-agent'"
command_background="yes"
pidfile="/run/nekoproxy-agent.pid"
output_log="/var/log/nekoproxy-agent.log"
error_log="/var/log/nekoproxy-agent.log"

depend() {
    need net
}
INITD
        chmod +x "/etc/init.d/$SERVICE_NAME"
        print_success "Created /etc/init.d/$SERVICE_NAME"
    fi
}

# Enable and start service
start_service() {
    print_header "Starting service..."

    if use_systemd; then
        systemctl enable "$SERVICE_NAME"
        print_success "Enabled $SERVICE_NAME to start on boot"
        systemctl start "$SERVICE_NAME"
        print_success "Started $SERVICE_NAME"
        sleep 2
        if systemctl is-active --quiet "$SERVICE_NAME"; then
            print_success "Service is running!"
        else
            print_warning "Service may have issues. Check: journalctl -u $SERVICE_NAME -f"
        fi
    else
        rc-update add "$SERVICE_NAME" default 2>/dev/null || true
        print_success "Enabled $SERVICE_NAME to start on boot"
        "/etc/init.d/$SERVICE_NAME" start
        print_success "Started $SERVICE_NAME"
        sleep 2
        if "/etc/init.d/$SERVICE_NAME" status >/dev/null 2>&1; then
            print_success "Service is running!"
        else
            print_warning "Service may have issues. Check: /var/log/nekoproxy-agent.log"
        fi
    fi
}

# Show final instructions
show_instructions() {
    print_header "Installation Complete!"

    echo ""
    echo "The NekoProxy agent has been installed and started."
    echo ""
    echo "Useful commands:"
    if use_systemd; then
        echo "  ${CYAN}systemctl status $SERVICE_NAME${NC}    - Check service status"
        echo "  ${CYAN}systemctl restart $SERVICE_NAME${NC}   - Restart the agent"
        echo "  ${CYAN}systemctl stop $SERVICE_NAME${NC}      - Stop the agent"
        echo "  ${CYAN}journalctl -u $SERVICE_NAME -f${NC}    - View live logs"
    else
        echo "  ${CYAN}rc-service $SERVICE_NAME status${NC}   - Check service status"
        echo "  ${CYAN}rc-service $SERVICE_NAME restart${NC}  - Restart the agent"
        echo "  ${CYAN}rc-service $SERVICE_NAME stop${NC}      - Stop the agent"
        echo "  ${CYAN}tail -f /var/log/nekoproxy-agent.log${NC} - View logs"
    fi
    echo ""
    echo "Configuration file: ${CYAN}$CONFIG_FILE${NC}"
    echo "Binary location:    ${CYAN}$INSTALL_DIR/$BINARY_NAME${NC}"
    echo ""
    echo "To reconfigure, edit $CONFIG_FILE and restart the service."
    echo ""
}

# Uninstall function
uninstall() {
    print_header "Uninstalling NekoProxy Agent..."

    if use_systemd; then
        if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
            systemctl stop "$SERVICE_NAME"
            print_success "Stopped service"
        fi
        if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
            systemctl disable "$SERVICE_NAME"
            print_success "Disabled service"
        fi
        rm -f "/etc/systemd/system/$SERVICE_NAME.service"
        systemctl daemon-reload
    else
        "/etc/init.d/$SERVICE_NAME" stop 2>/dev/null || true
        rc-update del "$SERVICE_NAME" default 2>/dev/null || true
        rm -f "/etc/init.d/$SERVICE_NAME"
        print_success "Stopped and removed OpenRC service"
    fi

    rm -f "$CONFIG_FILE"
    rm -rf "$INSTALL_DIR"

    print_success "NekoProxy Agent has been uninstalled"
}

# Main
main() {
    echo ""
    echo -e "${GREEN}╔═══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║           NekoProxy Agent Installer                       ║${NC}"
    echo -e "${GREEN}╚═══════════════════════════════════════════════════════════╝${NC}"

    # Check for uninstall flag
    if [[ "$1" == "--uninstall" || "$1" == "-u" ]]; then
        check_root
        uninstall
        exit 0
    fi

    # Check for help
    if [[ "$1" == "--help" || "$1" == "-h" ]]; then
        echo ""
        echo "Usage: sudo ./install-agent.sh [OPTIONS]"
        echo ""
        echo "Options:"
        echo "  -h, --help       Show this help message"
        echo "  -u, --uninstall  Uninstall the agent"
        echo ""
        exit 0
    fi

    check_root
    find_binary
    prompt_config
    create_directories
    install_binary
    create_config
    create_service
    start_service
    show_instructions
}

main "$@"
