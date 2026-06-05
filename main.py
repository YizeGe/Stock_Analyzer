"""
入口文件 — 供部署平台自动发现
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.main import app
