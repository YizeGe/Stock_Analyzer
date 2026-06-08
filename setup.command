#!/bin/bash
# 安装脚本 — 只需运行一次
DIR="$(cd "$(dirname "$0")" && pwd)"
echo "📦 正在安装依赖…"

# 创建 venv（如果不存在）
if [ ! -d "$DIR/venv" ]; then
    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 -m venv "$DIR/venv"
fi

# 安装
"$DIR/venv/bin/pip" install -q -r "$DIR/requirements.txt" 2>&1 | tail -1

echo "✅ 安装完成！现在可以双击 股票分析助手.app 启动了"
echo "   或双击 start.command"
open "$DIR/../股票分析助手.app"
