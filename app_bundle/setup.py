"""
py2app setup — 打包 股票分析助手 为原生 macOS .app
"""
import sys
import os

# ── DATA_FILES ──
data_files = [
    ('backend', ['../backend/main.py', '../backend/data_api.py', '../backend/storage.py']),
    ('frontend', ['../frontend/水月.png']),
]

iconfile = 'icon.icns'
if not os.path.exists(iconfile):
    iconfile = None

# ── py2app 选项 ──
py2app_options = {
    'argv_emulation': False,
    'strip': True,
    'iconfile': iconfile,
    'plist': {
        'CFBundleName': '股票分析助手',
        'CFBundleDisplayName': '股票分析助手',
        'CFBundleIdentifier': 'com.yizege.stock-analyzer',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundlePackageType': 'APPL',
        'NSHighResolutionCapable': True,
        'LSUIElement': False,
    },
    'includes': [
        'uvicorn', 'uvicorn.logging', 'uvicorn.loops.auto',
        'uvicorn.protocols.http.auto', 'uvicorn.protocols.websocket.auto',
        'fastapi', 'starlette', 'starlette.middleware.sessions',
        'webview', 'webview.platforms.cocoa',
        'akshare', 'pandas', 'numpy',
        'openai', 'requests',
        'itsdangerous', 'pydantic', 'bottle', 'proxy_tools',
        'storage', 'data_api',
        'typing_extensions',
        'multipart',
        'httptools',
        'websockets',
        'bs4',  # akshare 依赖
        'lxml',
    ],
    'excludes': [
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'tkinter', 'matplotlib',
        'setuptools', 'pip', 'wheel',
        'sphinx', 'docutils',
        'PyInstaller',  # 必须排除，否则模块解析会出错
    ],
    'packages': [
        'uvicorn', 'fastapi', 'starlette', 'pydantic',
        'webview',
        'akshare', 'pandas', 'numpy',
        'openai', 'requests',
        'itsdangerous', 'bottle',
        'objc',  # pyobjc
        'lxml',
    ],
    'optimize': 1,

}

from setuptools import setup

setup(
    app=['launcher.py'],
    name='股票分析助手',
    data_files=data_files,
    options={'py2app': py2app_options},
    setup_requires=['py2app'],
)
