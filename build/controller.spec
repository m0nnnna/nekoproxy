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
        'controller.api',
        'controller.api.v1',
        'controller.api.v1.agents',
        'controller.api.v1.services',
        'controller.api.v1.rules',
        'controller.api.v1.stats',
        'controller.api.v1.blocklist',
        'controller.web',
        'controller.web.routes',
        'shared',
        'shared.models',
        'shared.models.common',
        'shared.models.agent',
        'shared.models.service',
        'shared.models.rule',
        'shared.models.stats',
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
    name='nekoproxy-controller',
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
