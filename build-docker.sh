#!/bin/bash
#
# NekoProxy Docker Build Script
#
# Builds Linux binaries using Docker with Ubuntu 20.04 as the base.
# This ensures compatibility with Ubuntu 20.04 LTS and newer.
#
# Usage:
#   ./build-docker.sh [agent|controller|all]
#
# Requirements:
#   - Docker must be installed and running
#

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPONENT="${1:-all}"
IMAGE_NAME="nekoproxy-builder"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m'

echo "============================================================"
echo "NekoProxy Docker Build System"
echo "============================================================"
echo "Target: Ubuntu 20.04 LTS (glibc 2.31)"
echo "Component: $COMPONENT"
echo ""

# Check Docker
if ! command -v docker &> /dev/null; then
    echo -e "${RED}Error: Docker is not installed${NC}"
    echo "Install Docker: https://docs.docker.com/engine/install/"
    exit 1
fi

# Create output directory
# Handle case where dist might be a file or have wrong permissions
OUTPUT_DIR="$SCRIPT_DIR/dist/linux"
if [[ -f "$SCRIPT_DIR/dist" ]]; then
    rm -f "$SCRIPT_DIR/dist"
fi
mkdir -p "$SCRIPT_DIR/dist"
rm -rf "$OUTPUT_DIR" 2>/dev/null || true
mkdir -p "$OUTPUT_DIR"

# Build Docker image
echo "Building Docker image..."
docker build \
    -t "$IMAGE_NAME" \
    -f build/Dockerfile.linux \
    .

# Run build with proper volume handling for WSL2/Docker Desktop
echo ""
echo "Running build inside container..."

# Detect if running in WSL and use appropriate path handling
if grep -qi microsoft /proc/version 2>/dev/null; then
    echo -e "${YELLOW}WSL detected - using workaround for Docker volume mounts${NC}"
    # For WSL2 + Docker Desktop, use a named volume approach
    VOLUME_NAME="nekoproxy-build-output"

    # Create/clean the volume
    docker volume rm "$VOLUME_NAME" 2>/dev/null || true
    docker volume create "$VOLUME_NAME" >/dev/null

    # Run the build with named volume
    docker run --rm \
        -v "$VOLUME_NAME:/output" \
        "$IMAGE_NAME" \
        "$COMPONENT"

    # Copy files from volume to local directory using docker cp
    # (avoids WSL2 volume mount issues with Windows paths)
    echo "Copying build artifacts..."
    TEMP_CONTAINER=$(docker create -v "$VOLUME_NAME:/output" alpine)
    docker cp "$TEMP_CONTAINER:/output/." "$OUTPUT_DIR/"
    docker rm "$TEMP_CONTAINER" >/dev/null

    # Cleanup volume
    docker volume rm "$VOLUME_NAME" >/dev/null 2>&1 || true
else
    # Standard Linux/macOS - direct volume mount works
    docker run --rm \
        -v "$OUTPUT_DIR:/output" \
        "$IMAGE_NAME" \
        "$COMPONENT"
fi

echo ""
echo -e "${GREEN}Build complete!${NC}"
echo "Output directory: $OUTPUT_DIR"
ls -lh "$OUTPUT_DIR" 2>/dev/null || echo "Check $OUTPUT_DIR for output files"
