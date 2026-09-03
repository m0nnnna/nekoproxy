# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for NekoProxy Agent.

Build for Linux (Ubuntu) and Windows. On Windows the exe supports install/start/stop/remove as a service.
"""

import sys
from pathlib import Path

# Get the project root
project_root = Path(SPECPATH).parent

block_cipher = None

a = Analysis(
    [str(project_root / 'agent' / 'main.py')],
    pathex=[str(project_root)],
    binaries=[],
    datas=[],
    hiddenimports=[
        'agent',
        'agent.config',
        'agent.core',
        'agent.core.tcp_proxy',
        'agent.core.udp_proxy',
        'agent.core.heartbeat',
        'agent.core.config_sync',
        'agent.core.stats_reporter',
        'agent.core.firewall',
        'agent.core.firewall_windows',
        'agent.core.control_api',
        'shared',
        'shared.tls',
        'shared.models',
        'shared.models.common',
        'shared.models.agent',
        'shared.models.service',
        'shared.models.rule',
        'shared.models.stats',
        'shared.models.firewall',
        'shared.models.email',
        'shared.models.assignment',
        'shared.models.alert',
        # cryptography (TLS cert generation)
        'cryptography',
        'cryptography.x509',
        'cryptography.x509.oid',
        'cryptography.x509.extensions',
        'cryptography.hazmat',
        'cryptography.hazmat.primitives',
        'cryptography.hazmat.primitives.asymmetric',
        'cryptography.hazmat.primitives.asymmetric.rsa',
        'cryptography.hazmat.primitives.hashes',
        'cryptography.hazmat.primitives.serialization',
        'cryptography.hazmat.backends',
        'cryptography.hazmat.backends.openssl',
        'httpx',
        'httpx._transports',
        'httpx._transports.default',
        'httpcore',
        'h11',
        'certifi',
        'idna',
        'sniffio',
        'anyio',
        'anyio._backends',
        'anyio._backends._asyncio',
        'psutil',
        'pydantic',
        'pydantic_settings',
        'aiofiles',
        # aiohttp and dependencies for control API
        'aiohttp',
        'aiohttp.web',
        'aiohttp.web_app',
        'aiohttp.web_request',
        'aiohttp.web_response',
        'aiohttp.web_runner',
        'aiohttp.web_server',
        'aiohttp.web_routedef',
        'multidict',
        'yarl',
        'async_timeout',
        'aiosignal',
        'frozenlist',
        'attrs',
        # Windows service (install/start/stop/remove)
        'win32serviceutil',
        'win32service',
        'win32event',
        'win32api',
        'win32con',
        'win32timezone',
        'servicemanager',
        'pywintypes',
        'pythoncom',
        'shared.win_service',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# onedir, not onefile: a Windows service must answer the SCM within ~30s. A
# onefile build spends that budget unpacking to a temp dir and spawning a second
# process, so the service start times out with error 1053. onedir has no unpack
# step - the process the SCM launches IS the app.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='nekoproxy-agent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='nekoproxy-agent',
)
