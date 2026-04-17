# 📈 模拟炒股比赛辅助工具

一个帮助同花顺模拟炒股比赛选手管理持仓、分析技术面、筛选推荐股票的桌面工具，集成 AI 智能交易顾问。

![Python](https://img.shields.io/badge/Python-3.8+-blue?logo=python)
![License](https://img.shields.io/badge/License-MIT-green)
![Platform](https://img.shields.io/badge/Platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey)

## ✨ 功能特性

### 🌐 市场概览
- 大盘状态实时监测（MA20 多空判断）
- 各行业代表股行情一览（60+ 行业龙头）
- 今日涨停池自动获取
- 技术面综合评分选股推荐（RSI / MA / MACD / 量比）

### 💼 持仓管理
- 支持同花顺 CSV 导入或手动添加持仓
- 实时行情刷新、盈亏计算
- 买入 / 卖出 / 修改持仓
- 交易流水记录与导出
- 可用现金自动追踪与手动校准

### 🤖 AI 交易顾问
- 接入 DeepSeek API，支持自然语言交互
- 一句话批量录入买卖操作（如"今天买了 600519 100股 1800，还有 300858 500股 17.7"）
- 智能持仓分析与操作建议
- 对话历史持久化

## 🚀 安装与运行

### 1. 克隆项目

```bash
git clone https://github.com/YizeGe/stock-sim-tool.git
cd stock-sim-tool
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置（可选）

如果需要 AI 顾问功能，复制示例配置文件并填入你的 DeepSeek API Key：

```bash
mkdir -p userdata
cp config.example.json userdata/config.json
# 编辑 userdata/config.json，填入你的 API Key
```

也可以在应用内的「🤖 AI 顾问」Tab 中直接配置。

### 4. 运行

```bash
python stock_analyzer.py
```

## 📁 项目结构

```
├── stock_analyzer.py       # 主程序（GUI + 数据 + 策略）
├── config.example.json     # 配置文件模板
├── requirements.txt        # Python 依赖
├── .gitignore
├── README.md
└── userdata/               # 个人数据目录（自动创建，已被 .gitignore 忽略）
    ├── config.json          # 配置（API Key、资金）
    ├── my_holdings.json     # 当前持仓
    ├── trade_history.json   # 交易流水
    └── ai_history.json      # AI 对话记录
```

## 📊 技术分析策略

综合评分系统，满足以下条件加分：

| 信号 | 分值 |
|------|------|
| RSI < 30（超卖 + 确认信号） | +1 |
| RSI > 70（超买） | -1 |
| MA5 金叉 MA10 | +1 |
| MA5 死叉 MA10 | -1 |
| 价格站稳 MA20 | +1 |
| 价格跌破 MA20 | -1 |
| MACD 金叉 | +1 |
| MACD 死叉 | -1 |
| 放量站稳 MA20 | +1 |
| 大盘弱势时正分 ×0.7 | 降权 |

评分 ≥ 3 推荐买入，≥ 4 强烈买入，≤ -2 建议卖出。

## 📡 数据来源

- **实时行情**：腾讯财经 API
- **历史 K 线**：akshare / 新浪财经 API（多层降级）
- **涨停池**：akshare（东方财富数据）
- **AI 顾问**：DeepSeek API

## ⚠️ 免责声明

本工具仅用于**模拟炒股比赛辅助**和学习目的，不构成任何投资建议。股市有风险，投资需谨慎。

## 📄 License

[MIT License](LICENSE)
