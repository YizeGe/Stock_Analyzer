#!/usr/bin/env python3
"""
股票分析助手 — macOS 桌面应用入口
"""
import sys
import os
import threading
import time
import json
import hashlib

# PyInstaller 路径
if hasattr(sys, '_MEIPASS'):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, ".."))
sys.path.insert(0, os.path.join(BASE_DIR, "..", "backend"))

# 用 storage 模块来创建用户（保证路径一致）
from storage import register_user, get_base_dir

users_file = os.path.join(get_base_dir(), "users.json")
users = {}
if os.path.exists(users_file):
    with open(users_file) as f:
        users = json.load(f)

if "local" not in users:
    print("📝 创建本地用户…")
    err = register_user("local", "Local@Pass123")
    if err:
        print(f"  创建失败: {err}")
    else:
        print("  ✅ 创建成功")

os.environ["AUTO_LOGIN"] = "local"


def start_server():
    import uvicorn
    os.chdir(BASE_DIR)
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, log_level="warning")


threading.Thread(target=start_server, daemon=True).start()

# 等服务器就绪
import urllib.request
for _ in range(100):
    try:
        resp = urllib.request.urlopen("http://127.0.0.1:8000", timeout=1)
        if resp.status == 200:
            break
    except Exception:
        time.sleep(0.3)

URL = "http://127.0.0.1:8000"

# 启动原生窗口
import webview
webview.create_window(
    title="📈 股票分析助手",
    url=URL,
    width=1250,
    height=850,
    resizable=True,
)
webview.start(private_mode=False)
