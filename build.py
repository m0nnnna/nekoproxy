#!/usr/bin/env python3
"""
NekoProxy Build Script

Builds the agent and controller as standalone executables.

Build targets:
  - Agent: Linux (Ubuntu) only
  - Controller: Linux (Ubuntu) and Windows

Usage:
    python build.py [component] [--platform PLATFORM]

Examples:
    python build.py agent           # Build agent (Linux only)
    python build.py controller      # Build controller for current platform
    python build.py all             # Build all components for current platform
    python build.py --clean         # Clean build artifacts
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


# Project paths
PROJECT_ROOT = Path(__file__).parent
BUILD_DIR = PROJECT_ROOT / "build"
DIST_DIR = PROJECT_ROOT / "dist"
SPEC_DIR = BUILD_DIR


def get_platform():
    """Get the current platform."""
    system = platform.system().lower()
    if system == "linux":
        return "linux"
    elif system == "windows":
        return "windows"
    elif system == "darwin":
        return "macos"
    return system


def check_pyinstaller():
    """Check if PyInstaller is installed."""
    try:
        import PyInstaller
        return True
    except ImportError:
        return False


def install_pyinstaller():
    """Install PyInstaller."""
    print("Installing PyInstaller...")
    subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)


def clean_build():
    """Clean build artifacts."""
    print("Cleaning build artifacts...")

    dirs_to_clean = [
        DIST_DIR,
        PROJECT_ROOT / "__pycache__",
        BUILD_DIR / "agent",
        BUILD_DIR / "controller",
    ]

    for d in dirs_to_clean:
        if d.exists():
            print(f"  Removing {d}")
            shutil.rmtree(d)

    # Clean __pycache__ in subdirectories
    for pycache in PROJECT_ROOT.rglob("__pycache__"):
        if pycache.exists():
            shutil.rmtree(pycache)

    print("Clean complete.")


def build_component(component: str, current_platform: str):
    """Build a component using PyInstaller."""
    spec_file = SPEC_DIR / f"{component}.spec"

    if not spec_file.exists():
        print(f"Error: Spec file not found: {spec_file}")
        return False

    print(f"\nBuilding {component} for {current_platform}...")
    print("=" * 60)

    # Create output directory
    output_dir = DIST_DIR / current_platform
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build command
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--clean",
        "--noconfirm",
        "--distpath", str(output_dir),
        "--workpath", str(BUILD_DIR / component),
        str(spec_file)
    ]

    print(f"Running: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), check=True)
        print(f"\n{component} built successfully!")

        # Show output location
        if current_platform == "windows":
            exe_name = f"nekoproxy-{component}.exe"
        else:
            exe_name = f"nekoproxy-{component}"

        output_path = output_dir / exe_name
        if output_path.exists():
            size_mb = output_path.stat().st_size / (1024 * 1024)
            print(f"Output: {output_path} ({size_mb:.1f} MB)")

        return True
    except subprocess.CalledProcessError as e:
        print(f"Error building {component}: {e}")
        return False


def build_agent(current_platform: str):
    """Build the agent."""
    if current_platform != "linux":
        print(f"\nWarning: Agent is only supported on Linux (Ubuntu).")
        print(f"Current platform: {current_platform}")
        print("To build the agent, run this script on a Linux system.")
        return False

    return build_component("agent", current_platform)


def build_controller(current_platform: str):
    """Build the controller."""
    if current_platform not in ("linux", "windows"):
        print(f"\nWarning: Controller is only supported on Linux and Windows.")
        print(f"Current platform: {current_platform}")
        return False

    return build_component("controller", current_platform)


def main():
    parser = argparse.ArgumentParser(
        description="Build NekoProxy components",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Build Targets:
  agent       - Build agent (Linux/Ubuntu only)
  controller  - Build controller (Linux/Ubuntu and Windows)
  all         - Build all components for current platform

Examples:
  python build.py agent        # Build agent on Linux
  python build.py controller   # Build controller
  python build.py all          # Build everything for current platform
  python build.py --clean      # Clean build artifacts
        """
    )

    parser.add_argument(
        "component",
        nargs="?",
        choices=["agent", "controller", "all"],
        help="Component to build"
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean build artifacts"
    )
    parser.add_argument(
        "--platform",
        choices=["linux", "windows"],
        help="Target platform (default: current platform)"
    )

    args = parser.parse_args()

    if args.clean:
        clean_build()
        if not args.component:
            return 0

    if not args.component:
        parser.print_help()
        return 1

    # Detect or use specified platform
    current_platform = args.platform or get_platform()

    print("=" * 60)
    print("NekoProxy Build System")
    print("=" * 60)
    print(f"Platform: {current_platform}")
    print(f"Python: {sys.version}")
    print(f"Project: {PROJECT_ROOT}")

    # Check PyInstaller
    if not check_pyinstaller():
        install_pyinstaller()

    # Build components
    success = True

    if args.component in ("agent", "all"):
        if current_platform == "linux":
            if not build_agent(current_platform):
                success = False
        else:
            print(f"\nSkipping agent build - only supported on Linux")
            print("Run this script on an Ubuntu system to build the agent.")

    if args.component in ("controller", "all"):
        if not build_controller(current_platform):
            success = False

    print("\n" + "=" * 60)
    if success:
        print("Build completed successfully!")
        print(f"Outputs are in: {DIST_DIR}")
    else:
        print("Build completed with errors.")
    print("=" * 60)

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
