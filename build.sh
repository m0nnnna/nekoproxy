#!/usr/bin/env bash
#
# NekoProxy Build Script – Linux (Ubuntu/Debian)
#
# Builds the agent and/or controller binaries using PyInstaller.
# Agent:    Linux only
# Controller: Linux build here (for Windows controller → use build.py on Windows)
#
# Usage:
#   ./build.sh [all|agent|controller] [--clean] [--help]
#
# Examples:
#   ./build.sh all                # Build both
#   ./build.sh agent              # Agent only
#   ./build.sh controller --clean # Clean + build controller
#   ./build.sh --clean            # Just clean
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# ────────────────────────────────────────────────
#  Colors & Output helpers
# ────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

die()       { echo -e "${RED}Error:${NC} $*" >&2; exit 1; }
warn()      { echo -e "${YELLOW}Warning:${NC} $*" >&2; }
success()   { echo -e "${GREEN}$*${NC}"; }
header()    { echo ""; echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; echo "$*"; echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; }

# ────────────────────────────────────────────────
#  Configuration
# ────────────────────────────────────────────────

VENV_DIR="$SCRIPT_DIR/.venv"
DIST_DIR="$SCRIPT_DIR/dist/linux"

# ────────────────────────────────────────────────
#  Functions
# ────────────────────────────────────────────────

check_requirements() {
    command -v python3 >/dev/null || die "python3 not found. Install it: sudo apt install python3 python3-venv python3-pip"
}

create_or_fix_venv() {
    header "Preparing virtual environment"

    if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
        echo "Creating fresh virtual environment → $VENV_DIR"
        rm -rf "$VENV_DIR" 2>/dev/null || true
        python3 -m venv "$VENV_DIR" || die "Failed to create venv"
    fi

    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate" || die "Cannot activate venv"

    python -m pip install --quiet --upgrade pip || die "pip upgrade failed"

    # Install PyInstaller if missing
    python -c "import PyInstaller" 2>/dev/null || {
        echo "Installing PyInstaller..."
        pip install --quiet pyinstaller || die "Failed to install PyInstaller"
    }

    # Install project dependencies
    if [[ -f "requirements.txt" ]]; then
        echo "Installing requirements.txt..."
        pip install --quiet -r requirements.txt || die "Failed to install dependencies"
    else
        warn "No requirements.txt found — skipping dependency installation"
    fi

    success "Virtual environment ready."
}

clean_build_artifacts() {
    header "Cleaning previous build artifacts"

    rm -rf dist/ build/ __pycache__ *.pyc *.pyo .pytest_cache .coverage htmlcov 2>/dev/null || true
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name "build"       -exec rm -rf {} + 2>/dev/null || true
    find . -type d -name "dist"        -exec rm -rf {} + 2>/dev/null || true

    success "Clean finished."
}

prepare_dist() {
    mkdir -p "$DIST_DIR" || die "Cannot create $DIST_DIR"
}

build_component() {
    local component="$1"
    local spec_file="build/${component}.spec"
    local output_name="nekoproxy-${component}"

    [[ -f "$spec_file" ]] || die "Spec file not found: $spec_file"

    header "Building $component for Linux"

    prepare_dist

    python -m PyInstaller \
        --clean \
        --noconfirm \
        --distpath "$DIST_DIR" \
        --workpath "build/$component" \
        "$spec_file" || die "PyInstaller failed for $component"

    local binary="$DIST_DIR/$output_name"

    if [[ -f "$binary" ]]; then
        chmod +x "$binary"
        local size
        size=$(du -h "$binary" | cut -f1)
        success "$component built successfully!"
        echo "  → $binary ($size)"
    else
        die "$component binary not found after build: $binary"
    fi
}

show_help() {
    cat <<'EOF'
NekoProxy Linux Build Script

Usage:
  ./build.sh [all|agent|controller] [--clean] [--help]

Options:
  all          Build both agent and controller
  agent        Build Linux agent only
  controller   Build Linux controller only
  --clean      Remove build/dist artifacts first
  --help       Show this help

Note:
  → Windows controller build: run `python build.py controller` **on Windows**
EOF
}

# ────────────────────────────────────────────────
#  Argument parsing
# ────────────────────────────────────────────────

BUILD_TARGET=""
DO_CLEAN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        all|agent|controller)
            [[ -n "$BUILD_TARGET" ]] && die "Only one build target allowed"
            BUILD_TARGET="$1"
            ;;
        --clean)
            DO_CLEAN=true
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            die "Unknown argument: $1"
            ;;
    esac
    shift
done

# Default to showing help if nothing selected
if [[ -z "$BUILD_TARGET" && "$DO_CLEAN" = false ]]; then
    show_help
    exit 1
fi

# ────────────────────────────────────────────────
#  Main execution
# ────────────────────────────────────────────────

header "NekoProxy Linux Build"
echo "Directory : $SCRIPT_DIR"
echo "Python    : $(python3 --version 2>&1 | head -n1)"
echo "Target    : ${BUILD_TARGET:-<clean only>}"

check_requirements

$DO_CLEAN && clean_build_artifacts

create_or_fix_venv

case "$BUILD_TARGET" in
    agent)
        build_component "agent"
        ;;
    controller)
        build_component "controller"
        ;;
    all)
        build_component "agent"
        build_component "controller"
        ;;
    "")
        # only --clean was used → already done
        ;;
esac

if [[ -n "$BUILD_TARGET" ]]; then
    header "Build Complete"
    echo "Output directory:"
    ls -lh "$DIST_DIR" 2>/dev/null || echo "(directory is empty)"
fi

success "Done."