#!/bin/bash
#
# NekoProxy Build Script for Linux (Ubuntu)
#
# This script builds the agent and controller for Linux.
# Agent: Linux only
# Controller: Linux (run build.py on Windows for Windows build)
#
# Usage:
#   ./build.sh [agent|controller|all] [--clean]
#
# Examples:
#   ./build.sh all         # Build both agent and controller
#   ./build.sh agent       # Build agent only
#   ./build.sh controller  # Build controller only
#   ./build.sh --clean     # Clean build artifacts
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Virtual environment
VENV_DIR="$SCRIPT_DIR/.venv"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

print_header() {
    echo ""
    echo "============================================================"
    echo -e "${GREEN}$1${NC}"
    echo "============================================================"
}

print_warning() {
    echo -e "${YELLOW}Warning: $1${NC}"
}

print_error() {
    echo -e "${RED}Error: $1${NC}"
}

# Check if running on Linux
check_platform() {
    if [[ "$OSTYPE" != "linux-gnu"* ]]; then
        print_error "This script is intended for Linux (Ubuntu)."
        print_warning "For Windows builds, use: python build.py controller"
        exit 1
    fi
}

# Setup virtual environment
setup_venv() {
    print_header "Setting up virtual environment..."

    # Check for Python 3
    if ! command -v python3 &> /dev/null; then
        print_error "Python 3 is required. Install with: sudo apt install python3 python3-pip python3-venv"
        exit 1
    fi

    # Check for venv module
    if ! python3 -m venv --help &> /dev/null; then
        print_warning "python3-venv not found. Installing..."
        sudo apt-get update
        sudo apt-get install -y python3-venv
    fi

    # Create venv if it doesn't exist or is broken
    if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
        echo "Creating virtual environment at $VENV_DIR..."
        rm -rf "$VENV_DIR" 2>/dev/null || true
        python3 -m venv "$VENV_DIR"
        if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
            print_error "Failed to create virtual environment"
            exit 1
        fi
    fi

    # Activate venv
    source "$VENV_DIR/bin/activate"
    echo "Virtual environment activated."

    # Upgrade pip
    pip install --upgrade pip -q

    # Install PyInstaller if not present
    if ! python -c "import PyInstaller" &> /dev/null; then
        echo "Installing PyInstaller..."
        pip install pyinstaller
    fi

    # Install project dependencies
    echo "Installing project dependencies..."
    pip install -r requirements.txt -q

    echo "Dependencies ready."
}

# Clean build artifacts
clean_build() {
    print_header "Cleaning build artifacts..."
    rm -rf dist/
    rm -rf build/agent/
    rm -rf build/controller/
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true
    echo "Clean complete."
}

# Build agent
build_agent() {
    print_header "Building Agent for Linux..."

    python -m PyInstaller \
        --clean \
        --noconfirm \
        --distpath dist/linux \
        --workpath build/agent \
        build/agent.spec

    if [[ -f "dist/linux/nekoproxy-agent" ]]; then
        chmod +x dist/linux/nekoproxy-agent
        SIZE=$(du -h dist/linux/nekoproxy-agent | cut -f1)
        echo ""
        echo -e "${GREEN}Agent built successfully!${NC}"
        echo "Output: dist/linux/nekoproxy-agent ($SIZE)"
    else
        print_error "Agent build failed!"
        return 1
    fi
}

# Build controller
build_controller() {
    print_header "Building Controller for Linux..."

    python -m PyInstaller \
        --clean \
        --noconfirm \
        --distpath dist/linux \
        --workpath build/controller \
        build/controller.spec

    if [[ -f "dist/linux/nekoproxy-controller" ]]; then
        chmod +x dist/linux/nekoproxy-controller
        SIZE=$(du -h dist/linux/nekoproxy-controller | cut -f1)
        echo ""
        echo -e "${GREEN}Controller built successfully!${NC}"
        echo "Output: dist/linux/nekoproxy-controller ($SIZE)"
    else
        print_error "Controller build failed!"
        return 1
    fi
}

# Show usage
show_usage() {
    echo "NekoProxy Build Script for Linux (Ubuntu)"
    echo ""
    echo "Usage: $0 [agent|controller|all] [--clean]"
    echo ""
    echo "Components:"
    echo "  agent       Build the agent (Linux only)"
    echo "  controller  Build the controller (Linux)"
    echo "  all         Build both agent and controller"
    echo ""
    echo "Options:"
    echo "  --clean     Clean build artifacts before building"
    echo ""
    echo "Examples:"
    echo "  $0 all             # Build everything"
    echo "  $0 agent           # Build agent only"
    echo "  $0 --clean all     # Clean and rebuild everything"
    echo ""
    echo "Note: For Windows controller build, run on Windows:"
    echo "  python build.py controller"
}

# Main
main() {
    check_platform

    COMPONENT=""
    CLEAN=false

    # Parse arguments
    for arg in "$@"; do
        case $arg in
            agent|controller|all)
                COMPONENT="$arg"
                ;;
            --clean)
                CLEAN=true
                ;;
            -h|--help)
                show_usage
                exit 0
                ;;
            *)
                print_error "Unknown argument: $arg"
                show_usage
                exit 1
                ;;
        esac
    done

    if [[ -z "$COMPONENT" && "$CLEAN" == false ]]; then
        show_usage
        exit 1
    fi

    print_header "NekoProxy Build System"
    echo "Platform: Linux"
    echo "Python: $(python3 --version)"
    echo "Project: $SCRIPT_DIR"

    if $CLEAN; then
        clean_build
    fi

    if [[ -z "$COMPONENT" ]]; then
        exit 0
    fi

    setup_venv

    case $COMPONENT in
        agent)
            build_agent
            ;;
        controller)
            build_controller
            ;;
        all)
            build_agent
            build_controller
            ;;
    esac

    print_header "Build Complete!"
    echo "Output directory: dist/linux/"
    ls -lh dist/linux/ 2>/dev/null || true
}

main "$@"
