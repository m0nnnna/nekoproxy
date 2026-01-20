# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for NekoProxy Agent.

Build for Linux (Ubuntu) only.
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
        'agent.core.control_api',
        'shared',
        'shared.models',
        'shared.models.common',
        'shared.models.agent',
        'shared.models.service',
        'shared.models.rule',
        'shared.models.stats',
        'shared.models.firewall',
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

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='nekoproxy-agent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
