# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for NekoProxy Controller.

Build for Linux (Ubuntu) and Windows.
"""

import sys
from pathlib import Path

# Get the project root
project_root = Path(SPECPATH).parent

block_cipher = None

# Data files to include (templates, static files)
datas = [
    (str(project_root / 'controller' / 'web' / 'templates'), 'controller/web/templates'),
    (str(project_root / 'controller' / 'web' / 'static'), 'controller/web/static'),
]

a = Analysis(
    [str(project_root / 'controller' / 'main.py')],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'controller',
        'controller.config',
        'controller.main',
        'controller.database',
        'controller.database.database',
        'controller.database.models',
        'controller.database.repositories',
        'controller.core',
        'controller.core.agent_manager',
        'controller.core.health_monitor',
        'controller.core.auth',
        'controller.core.agent_sync',
        'controller.core.email_manager',
        'controller.api',
        'controller.api.v1',
        'controller.api.v1.agents',
        'controller.api.v1.services',
        'controller.api.v1.rules',
        'controller.api.v1.stats',
        'controller.api.v1.blocklist',
        'controller.api.v1.firewall',
        'controller.api.v1.alerts',
        'controller.api.v1.assignments',
        'controller.api.v1.email',
        'controller.web',
        'controller.web.routes',
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
        # httpx (for controller→agent calls)
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
        'fastapi',
        'fastapi.staticfiles',
        'fastapi.templating',
        'fastapi.responses',
        'fastapi.middleware',
        'fastapi.middleware.cors',
        'starlette',
        'starlette.responses',
        'starlette.requests',
        'starlette.routing',
        'starlette.staticfiles',
        'starlette.templating',
        'uvicorn',
        'uvicorn.config',
        'uvicorn.main',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.http.httptools_impl',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.logging',
        'sqlalchemy',
        'sqlalchemy.orm',
        'sqlalchemy.ext.declarative',
        'sqlalchemy.dialects.sqlite',
        'pydantic',
        'pydantic_settings',
        'jinja2',
        'python_multipart',
        'aiofiles',
        'h11',
        'httptools',
        'websockets',
        'watchfiles',
        'email_validator',
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

# Optional app icon. Missing icon must not fail the build (e.g. fresh clone on a
# build box) - PyInstaller raises FileNotFoundError if icon= points at nothing.
_icon_file = project_root / 'build' / 'neko.ico'
_icon = str(_icon_file) if _icon_file.is_file() else None

# onedir, not onefile: a Windows service must answer the SCM within ~30s. A
# onefile build spends that budget unpacking ~27MB to a temp dir and spawning a
# second process (AV scanning the freshly written files makes it worse), so the
# service start times out with error 1053. onedir has no unpack step - the
# process the SCM launches IS the app.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='nekoproxy-controller',
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
    icon=_icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='nekoproxy-controller',
)
