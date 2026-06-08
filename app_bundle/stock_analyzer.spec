# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec — 打包股票分析助手为 macOS .app
用法:  pyinstaller stock_analyzer.spec
"""
import sys
import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# ── 收集复杂包的隐式导入 ────────────────────────────────
hiddenimports = []
hiddenimports += collect_submodules('uvicorn')
hiddenimports += collect_submodules('fastapi')
hiddenimports += collect_submodules('starlette')
hiddenimports += collect_submodules('pydantic')
hiddenimports += collect_submodules('pydantic_core')
hiddenimports += collect_submodules('webview')
hiddenimports += collect_submodules('akshare')
hiddenimports += collect_submodules('openai')
hiddenimports += collect_submodules('requests')
hiddenimports += collect_submodules('itsdangerous')
hiddenimports += collect_submodules('numpy')
hiddenimports += collect_submodules('pandas')

# 额外手动添加常遗漏的
hiddenimports += [
    'multipart',
    'python_multipart',
    'email.mime.multipart',
    'email.mime.text',
    'httptools',
    'httptools.parser',
    'websockets',
    'h11',
    'httpcore',
    'httpx',
    'anyio',
    'anyio._backends._asyncio',
    'sniffio',
    'typing_extensions',
    'annotated_types',
    'certifi',
    'charset_normalizer',
    'idna',
    'urllib3',
    'bs4',
    'lxml',
    'lxml.etree',
    'bottle',
    'proxy_tools',
    'objc',
    'Foundation',
    'AppKit',
    'WebKit',
]

# ── 收集数据文件 ─────────────────────────────────────────
datas = [
    ('../backend', 'backend'),
    ('../frontend', 'frontend'),
]
datas += collect_data_files('certifi')
datas += collect_data_files('akshare')

# ── Analysis ─────────────────────────────────────────────
a = Analysis(
    ['launcher.py'],
    pathex=[os.path.abspath('.'), os.path.abspath('../backend')],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'scipy', 'PIL',
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'setuptools', 'pip', 'wheel',
        'sphinx', 'docutils', 'pytest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── 使用 onedir 模式（.app 需要） ────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='stock_analyzer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch='arm64',
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
    name='stock_analyzer',
)

# ── macOS .app Bundle ────────────────────────────────────
app = BUNDLE(
    coll,
    name='股票分析助手.app',
    icon='icon.icns',
    bundle_identifier='com.yizege.stock-analyzer',
    info_plist={
        'CFBundleName': '股票分析助手',
        'CFBundleDisplayName': '股票分析助手',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1.0.0',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '11.0',
        'CFBundlePackageType': 'APPL',
        'LSUIElement': False,
    },
)
