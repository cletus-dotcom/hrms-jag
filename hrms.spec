# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for HRMS executable
# Build: pyinstaller hrms.spec
# Output: dist/hrms.exe (single executable)

import os

block_cipher = None

# Bundle Flask templates and static files (PyInstaller copies directories recursively)
_spec_dir = os.path.dirname(os.path.abspath(SPEC))
_datas = [
    (os.path.join(_spec_dir, 'app', 'templates'), 'app/templates'),
    (os.path.join(_spec_dir, 'app', 'static'), 'app/static'),
]

a = Analysis(
    ['run.py'],
    pathex=[_spec_dir],
    binaries=[],
    datas=_datas,
    hiddenimports=[
        'flask',
        'flask_sqlalchemy',
        'flask_login',
        'waitress',
        'jinja2',
        'werkzeug.security',
        'werkzeug.utils',
        'sqlalchemy',
        'sqlalchemy.sql.default_comparator',
        'psycopg2',
        'app',
        'app.routes',
        'app.models',
        'app.config',
        'app.__init__',
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
    name='hrms',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # Set to False to hide console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
