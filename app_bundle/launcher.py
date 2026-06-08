#!/usr/bin/env python3
"""
股票分析助手 — PyInstaller 桌面应用入口
启动 FastAPI 后端 + pywebview 原生窗口
"""
import sys
import os
import threading
import time
import json
import traceback

# ── 强制 UTF-8 编码 ──────────────────────────────────────
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
os.environ.setdefault('LANG', 'zh_CN.UTF-8')

# ── 日志 ──────────────────────────────────────────────────
_LOG = os.path.join(os.path.expanduser('~'), 'sa_debug.log')


def log(msg):
    try:
        with open(_LOG, 'a', encoding='utf-8') as f:
            f.write(f'{time.strftime("%H:%M:%S")} {msg}\n')
    except Exception:
        pass


log('=== 启动 ===')

# ── 确定资源目录 ──────────────────────────────────────────
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    # PyInstaller 打包模式
    _RES = sys._MEIPASS
    log(f'PyInstaller 模式, _MEIPASS={_RES}')
else:
    # 开发模式 — 项目根目录
    _RES = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log(f'开发模式, RES={_RES}')

_BACKEND = os.path.join(_RES, 'backend')

# ── 加入搜索路径 ──────────────────────────────────────────
for p in [_RES, _BACKEND]:
    if os.path.isdir(p) and p not in sys.path:
        sys.path.insert(0, p)
    log(f'path: {p} exists={os.path.isdir(p)}')

# ── 用户初始化 ────────────────────────────────────────────
try:
    from storage import register_user, get_base_dir
    bd = get_base_dir()
    log(f'用户数据目录: {bd}')

    users_file = os.path.join(bd, 'users.json')
    users = {}
    if os.path.exists(users_file):
        with open(users_file, encoding='utf-8') as f:
            users = json.load(f)

    if 'local' not in users:
        log('创建本地用户...')
        err = register_user('local', 'Local@Pass123')
        log(f'创建用户: {err or "OK"}')

    os.environ['AUTO_LOGIN'] = 'local'
except Exception as e:
    log(f'用户初始化失败: {e}')
    log(traceback.format_exc())

# ── 后端服务器线程 ────────────────────────────────────────
def run_server():
    try:
        import uvicorn
        os.chdir(_RES)
        log('启动 uvicorn...')
        uvicorn.run('backend.main:app', host='127.0.0.1', port=8000, log_level='warning')
    except Exception as e:
        log(f'服务器错误: {e}')
        log(traceback.format_exc())


log('启动后端线程')
threading.Thread(target=run_server, daemon=True).start()

# ── 等待服务器就绪 ────────────────────────────────────────
import urllib.request

for i in range(100):
    try:
        r = urllib.request.urlopen('http://127.0.0.1:8000', timeout=1)
        log(f'服务器就绪 (status={r.status})')
        break
    except Exception:
        if i == 0:
            log('等待服务器启动...')
        time.sleep(0.3)
else:
    log('服务器启动超时!')

# ── 打开原生窗口 ──────────────────────────────────────────
try:
    import webview
    log('启动 webview 窗口')
    webview.create_window(
        '📈 股票分析助手',
        'http://127.0.0.1:8000',
        width=1250,
        height=850,
        resizable=True,
    )
    webview.start(private_mode=False)
    log('窗口已关闭')
except Exception as e:
    log(f'webview 错误: {e}')
    log(traceback.format_exc())
    # 如果 webview 失败，用浏览器打开
    import webbrowser
    webbrowser.open('http://127.0.0.1:8000')
    log('已用浏览器打开，按 Ctrl+C 退出')
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
