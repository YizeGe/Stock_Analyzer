#!/bin/bash
DIR="$HOME/desktop/program_project/stock_analyzer"
cd "$DIR"

# 检查 venv
if [ ! -f "venv/bin/python3" ]; then
    echo "📦 首次使用，正在安装依赖（约 1-2 分钟）…"
    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 -m venv venv
    venv/bin/pip install -q fastapi uvicorn pandas requests openai akshare numpy python-multipart starlette itsdangerous
    if [ $? -ne 0 ]; then
        echo "❌ 安装失败，请检查网络后重试"
        read -p "按 Enter 退出…"
        exit 1
    fi
    echo "✅ 安装完成"
fi

echo "📈 股票分析助手启动中…"
echo "   服务地址: http://127.0.0.1:8000"
echo "   按 Ctrl+C 停止"
echo ""

venv/bin/python3 -m backend.main

echo ""
echo "服务已停止"
read -p "按 Enter 关闭…"
