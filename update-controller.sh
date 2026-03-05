#!/bin/bash
#
# NekoProxy Controller Updater for Linux
#
# Updates the controller binary without touching config or data.
# Usage: sudo ./update-controller.sh [path/to/new/nekoproxy-controller]
#        If no path is given, uses ./nekoproxy-controller in the script directory.
#

set -e

INSTALL_DIR="/opt/nekoproxy"
SERVICE_NAME="nekoproxy-controller"
BINARY_NAME="nekoproxy-controller"
DATA_DIR="/var/lib/nekoproxy"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

print_success() { echo -e "${GREEN}✓ $1${NC}"; }
print_warning() { echo -e "${YELLOW}! $1${NC}"; }
print_error() { echo -e "${RED}✗ $1${NC}"; }

# Resolve path to new binary
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "$1" ]]; then
    NEW_BINARY="$1"
else
    NEW_BINARY="$SCRIPT_DIR/$BINARY_NAME"
fi

if [[ ! -f "$NEW_BINARY" ]]; then
    print_error "New binary not found: $NEW_BINARY"
    echo "Usage: sudo ./update-controller.sh [path/to/nekoproxy-controller]"
    exit 1
fi

# Detect init: systemd or OpenRC (Alpine)
use_systemd() {
    command -v systemctl >/dev/null 2>&1 && systemctl list-units --type=service >/dev/null 2>&1
}

# Find current install: systemd ExecStart, OpenRC, or default path
CURRENT_BINARY="$INSTALL_DIR/$BINARY_NAME"
if use_systemd && (systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null || systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null); then
    EXEC_START=$(systemctl show -p ExecStart --value "$SERVICE_NAME" 2>/dev/null | sed 's/^ *//; s/ .*//')
    if [[ -n "$EXEC_START" && -f "$EXEC_START" ]]; then
        CURRENT_BINARY="$EXEC_START"
    fi
elif [[ -x "/etc/init.d/$SERVICE_NAME" ]]; then
    # OpenRC: binary is at standard path
    CURRENT_BINARY="$INSTALL_DIR/$BINARY_NAME"
fi

if [[ ! -f "$CURRENT_BINARY" ]]; then
    print_error "Current installation not found at $CURRENT_BINARY"
    echo "Install the controller first with install-controller.sh"
    exit 1
fi

BACKUP_PATH="${CURRENT_BINARY}.backup"

echo ""
echo -e "${CYAN}============================================================${NC}"
echo -e "${CYAN}  NekoProxy Controller Updater (Linux)${NC}"
echo -e "${CYAN}============================================================${NC}"
echo ""
echo "  Current: $CURRENT_BINARY"
echo "  New:     $NEW_BINARY"
echo ""

# Stop service
echo "Stopping controller..."
if use_systemd && systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    systemctl stop "$SERVICE_NAME"
    print_success "Stopped $SERVICE_NAME"
elif [[ -x "/etc/init.d/$SERVICE_NAME" ]]; then
    "/etc/init.d/$SERVICE_NAME" stop 2>/dev/null && print_success "Stopped $SERVICE_NAME" || print_warning "Controller was not running"
else
    print_warning "Controller was not running"
fi

# Wait for process to exit
for i in {1..10}; do
    if ! pgrep -f "$BINARY_NAME" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Backup and install
print_success "Backing up current binary to $BACKUP_PATH"
cp -f "$CURRENT_BINARY" "$BACKUP_PATH"

print_success "Installing new binary"
cp -f "$NEW_BINARY" "$CURRENT_BINARY"
chmod +x "$CURRENT_BINARY"

# Start service
echo "Starting controller..."
if use_systemd; then
    systemctl start "$SERVICE_NAME"
else
    "/etc/init.d/$SERVICE_NAME" start
fi
sleep 2
RUNNING=false
if use_systemd && systemctl is-active --quiet "$SERVICE_NAME"; then
    RUNNING=true
elif [[ -x "/etc/init.d/$SERVICE_NAME" ]] && "/etc/init.d/$SERVICE_NAME" status >/dev/null 2>&1; then
    RUNNING=true
fi
if $RUNNING; then
    print_success "Controller is running!"
else
    print_error "Controller failed to start"
    read -p "Rollback to previous version? (y/n): " rollback
    if [[ "$rollback" == "y" || "$rollback" == "Y" ]]; then
        cp -f "$BACKUP_PATH" "$CURRENT_BINARY"
        if use_systemd; then systemctl start "$SERVICE_NAME"; else "/etc/init.d/$SERVICE_NAME" start; fi
        print_success "Rolled back"
    fi
    exit 1
fi

echo ""
print_success "Update complete. Config and data preserved."
echo "  Backup:  $BACKUP_PATH"
if use_systemd; then
    echo "  Rollback: cp '$BACKUP_PATH' '$CURRENT_BINARY' && systemctl restart $SERVICE_NAME"
else
    echo "  Rollback: cp '$BACKUP_PATH' '$CURRENT_BINARY' && /etc/init.d/$SERVICE_NAME restart"
fi
echo ""
