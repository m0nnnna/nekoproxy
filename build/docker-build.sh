#!/bin/bash
set -e

COMPONENT="${1:-all}"

echo "============================================================"
echo "NekoProxy Docker Build (Ubuntu 20.04 target)"
echo "============================================================"
echo "Building: $COMPONENT"
echo ""

cd /build

build_agent() {
    echo "Building Agent..."
    python -m PyInstaller \
        --clean \
        --noconfirm \
        --distpath /output \
        --workpath /tmp/build-agent \
        build/agent.spec

    chmod +x /output/nekoproxy-agent

    # Copy install and update scripts
    cp /build/install-agent.sh /output/install-agent.sh
    cp /build/update-agent.sh /output/update-agent.sh
    chmod +x /output/install-agent.sh /output/update-agent.sh

    echo "Agent built: /output/nekoproxy-agent"
    echo "Install script: /output/install-agent.sh"
    echo "Update script:  /output/update-agent.sh"
}

build_controller() {
    echo "Building Controller..."
    python -m PyInstaller \
        --clean \
        --noconfirm \
        --distpath /output \
        --workpath /tmp/build-controller \
        build/controller.spec

    chmod +x /output/nekoproxy-controller

    # Copy install and update scripts
    cp /build/install-controller.sh /output/install-controller.sh
    cp /build/update-controller.sh /output/update-controller.sh
    chmod +x /output/install-controller.sh /output/update-controller.sh

    echo "Controller built: /output/nekoproxy-controller"
    echo "Install script: /output/install-controller.sh"
    echo "Update script:  /output/update-controller.sh"
}

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
    *)
        echo "Unknown component: $COMPONENT"
        echo "Usage: docker-build.sh [agent|controller|all]"
        exit 1
        ;;
esac

echo ""
echo "============================================================"
echo "Build complete!"
echo "============================================================"
ls -lh /output/
