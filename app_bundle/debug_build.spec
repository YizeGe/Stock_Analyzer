# -*- mode: python ; coding: utf-8 -*-
"""Debug build - console=True to see errors"""
from PyInstaller.utils.hooks import collect_all

datas = [('../backend', 'backend'), ('../frontend', 'frontend')]
binaries = []
hiddenimports = ['openai', 'akshare', 'requests', 'itsdangerous']

for mod in ['fastapi', 'uvicorn', 'starlette', 'pandas', 'webview']:
    tmp_ret = collect_all(mod)
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hiddenimports += tmp_ret[2]

a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='股票分析助手_debug',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.icns'],
)
