#!/bin/bash
#
# NekoProxy Docker Build Script — Alpine (musl) target
#
# Builds Linux binaries for Alpine Linux. Use these on Alpine only.
# For Ubuntu/Debian use ./build-docker.sh instead.
#
# Usage:
#   ./build-docker-alpine.sh [agent|controller|all]
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPONENT="${1:-all}"
IMAGE_NAME="nekoproxy-builder-alpine"
OUTPUT_DIR="$SCRIPT_DIR/dist/linux-alpine"

echo "============================================================"
echo "NekoProxy Docker Build — Alpine (musl)"
echo "============================================================"
echo "Target: Alpine Linux (musl libc)"
echo "Component: $COMPONENT"
echo ""

if ! command -v docker &> /dev/null; then
    echo "Error: Docker is not installed"
    exit 1
fi

mkdir -p "$SCRIPT_DIR/dist"
rm -rf "$OUTPUT_DIR" 2>/dev/null || true
mkdir -p "$OUTPUT_DIR"

echo "Building Docker image (Alpine base)..."
docker build \
    -t "$IMAGE_NAME" \
    -f build/Dockerfile.linux.alpine \
    .

echo ""
echo "Running build inside container..."

if grep -qi microsoft /proc/version 2>/dev/null; then
    VOLUME_NAME="nekoproxy-build-output-alpine"
    docker volume rm "$VOLUME_NAME" 2>/dev/null || true
    docker volume create "$VOLUME_NAME" >/dev/null
    docker run --rm \
        -v "$VOLUME_NAME:/output" \
        "$IMAGE_NAME" \
        "$COMPONENT"
    echo "Copying build artifacts..."
    TEMP_CONTAINER=$(docker create -v "$VOLUME_NAME:/output" alpine:3.19)
    docker cp "$TEMP_CONTAINER:/output/." "$OUTPUT_DIR/"
    docker rm "$TEMP_CONTAINER" >/dev/null
    docker volume rm "$VOLUME_NAME" >/dev/null 2>&1 || true
else
    docker run --rm \
        -v "$OUTPUT_DIR:/output" \
        "$IMAGE_NAME" \
        "$COMPONENT"
fi

echo ""
echo "Build complete! Output: $OUTPUT_DIR"
ls -lh "$OUTPUT_DIR" 2>/dev/null || true
