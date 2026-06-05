"""
本地 JSON 文件存储 — 管理持仓、交易流水、AI 对话
"""
import json
import os
import datetime


def get_data_dir():
    """获取 userdata 目录路径（项目根目录下的 userdata/）"""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(base, "userdata")
    os.makedirs(d, exist_ok=True)
    return d


# ── 配置 ─────────────────────────────────────────────────

def load_config():
    path = os.path.join(get_data_dir(), "config.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"api_key": "", "total_cash": 1000000.0}


def save_config(cfg):
    path = os.path.join(get_data_dir(), "config.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存配置失败: {e}")
        return False


# ── 持仓 ─────────────────────────────────────────────────

def load_holdings():
    path = os.path.join(get_data_dir(), "my_holdings.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_holdings(holdings):
    path = os.path.join(get_data_dir(), "my_holdings.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(holdings, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存持仓失败: {e}")
        return False


# ── 交易流水 ─────────────────────────────────────────────

def load_trade_history():
    path = os.path.join(get_data_dir(), "trade_history.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_trade_history(history):
    path = os.path.join(get_data_dir(), "trade_history.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存交易流水失败: {e}")
        return False


def record_trade(action, symbol, name, price, shares, cost_price=0.0, avail_cash=0.0):
    """记录一笔买卖流水"""
    history = load_trade_history()
    amount = price * shares
    pnl = 0.0
    if action == "SELL":
        pnl = (price - cost_price) * shares

    record = {
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": "买入" if action == "BUY" else "卖出",
        "symbol": symbol,
        "name": name,
        "price": round(price, 3),
        "shares": int(shares),
        "amount": round(amount, 2),
        "pnl": round(pnl, 2),
    }
    history.insert(0, record)
    save_trade_history(history)
    return record


# ── AI 对话历史 ─────────────────────────────────────────

def load_ai_history():
    path = os.path.join(get_data_dir(), "ai_history.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_ai_history(messages):
    path = os.path.join(get_data_dir(), "ai_history.json")
    try:
        # 保留最近 30 条
        with open(path, "w", encoding="utf-8") as f:
            json.dump(messages[-30:], f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存AI对话记录失败: {e}")
        return False
