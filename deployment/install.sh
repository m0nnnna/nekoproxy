#!/bin/bash
# NekoProxy Service Installation Script
# Usage: ./install.sh [controller|agent]

set -e

INSTALL_DIR="/opt/nekoproxy"
DATA_DIR="/var/lib/nekoproxy"
LOG_DIR="/var/log/nekoproxy"
SERVICE_USER="nekoproxy"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root"
        exit 1
    fi
}

install_dependencies() {
    log_info "Installing system dependencies..."
    if command -v apt-get &> /dev/null; then
        apt-get update
        apt-get install -y python3 python3-pip python3-venv
    elif command -v dnf &> /dev/null; then
        dnf install -y python3 python3-pip
    elif command -v yum &> /dev/null; then
        yum install -y python3 python3-pip
    else
        log_error "Unsupported package manager"
        exit 1
    fi
}

create_user() {
    if ! id "$SERVICE_USER" &>/dev/null; then
        log_info "Creating service user: $SERVICE_USER"
        useradd --system --no-create-home --shell /bin/false "$SERVICE_USER"
    fi
}

create_directories() {
    log_info "Creating directories..."
    mkdir -p "$INSTALL_DIR"
    mkdir -p "$DATA_DIR"
    mkdir -p "$LOG_DIR"

    chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"
    chown -R "$SERVICE_USER:$SERVICE_USER" "$LOG_DIR"
}

install_code() {
    log_info "Installing NekoProxy service code..."

    # Copy code
    cp -r controller "$INSTALL_DIR/"
    cp -r agent "$INSTALL_DIR/"
    cp -r shared "$INSTALL_DIR/"
    cp requirements.txt "$INSTALL_DIR/"

    # Create virtual environment
    log_info "Creating Python virtual environment..."
    python3 -m venv "$INSTALL_DIR/venv"

    # Install dependencies
    log_info "Installing Python dependencies..."
    "$INSTALL_DIR/venv/bin/pip" install --upgrade pip
    "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

    chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
}

install_controller() {
    log_info "Installing NekoProxy controller service..."

    # Copy systemd service
    cp deployment/controller.service /etc/systemd/system/nekoproxy-controller.service

    # Reload systemd
    systemctl daemon-reload

    # Enable service
    systemctl enable nekoproxy-controller

    log_info "Controller installed. Configure settings in /etc/systemd/system/nekoproxy-controller.service"
    log_info "Start with: systemctl start nekoproxy-controller"
}

install_agent() {
    log_info "Installing NekoProxy agent service..."

    # Copy systemd service
    cp deployment/agent.service /etc/systemd/system/nekoproxy-agent.service

    # Reload systemd
    systemctl daemon-reload

    # Enable service
    systemctl enable nekoproxy-agent

    log_info "Agent installed."
    log_info ""
    log_info "Configure the agent by editing /etc/systemd/system/nekoproxy-agent.service"
    log_info "Set NEKO_AGENT_CONTROLLER_URL to your controller's WireGuard IP"
    log_info "Set NEKO_AGENT_WIREGUARD_IP to this server's WireGuard IP"
    log_info ""
    log_info "Start with: systemctl start nekoproxy-agent"
}

show_usage() {
    echo "Usage: $0 [controller|agent|both]"
    echo ""
    echo "Options:"
    echo "  controller  Install the NekoProxy controller service"
    echo "  agent       Install the NekoProxy agent service"
    echo "  both        Install both controller and agent"
    echo ""
    echo "Examples:"
    echo "  $0 controller    # Install on central server"
    echo "  $0 agent         # Install on edge proxy nodes"
}

main() {
    if [[ $# -lt 1 ]]; then
        show_usage
        exit 1
    fi

    check_root

    case "$1" in
        controller)
            install_dependencies
            create_user
            create_directories
            install_code
            install_controller
            ;;
        agent)
            install_dependencies
            create_directories
            install_code
            install_agent
            ;;
        both)
            install_dependencies
            create_user
            create_directories
            install_code
            install_controller
            install_agent
            ;;
        *)
            show_usage
            exit 1
            ;;
    esac

    log_info "Installation complete!"
}

main "$@"
