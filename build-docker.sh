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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPONENT="${1:-all}"
IMAGE_NAME="nekoproxy-builder"
OUTPUT_DIR="$SCRIPT_DIR/dist/linux"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
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
mkdir -p "$OUTPUT_DIR"

# Build Docker image
echo "Building Docker image..."
docker build \
    -t "$IMAGE_NAME" \
    -f build/Dockerfile.linux \
    .

# Run build
echo ""
echo "Running build inside container..."
docker run --rm \
    -v "$OUTPUT_DIR:/output" \
    "$IMAGE_NAME" \
    "$COMPONENT"

echo ""
echo -e "${GREEN}Build complete!${NC}"
echo "Output directory: $OUTPUT_DIR"
ls -lh "$OUTPUT_DIR"
