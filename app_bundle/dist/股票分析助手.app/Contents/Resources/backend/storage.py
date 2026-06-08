"""
本地 JSON 文件存储 — 管理用户、持仓、交易流水、AI 对话
"""
import json
import os
import sys
import datetime
import hashlib
import random
import string


def validate_password(password: str) -> tuple:
    """校验密码强度，返回 (是否通过, 错误信息)"""
    if len(password) < 10:
        return False, "密码至少 10 位"
    if not any(c.isupper() for c in password):
        return False, "密码需要包含大写字母"
    if not any(c.islower() for c in password):
        return False, "密码需要包含小写字母"
    if not any(c.isdigit() for c in password):
        return False, "密码需要包含数字"
    specials = "!@#$%^&*()_+-=[]{}|;':,./<>?~`"
    if not any(c in specials for c in password):
        return False, "密码需要包含特殊字符"
    return True, ""


def generate_captcha() -> tuple:
    """生成人机验证题目，返回 (题目文本, 答案字符串)"""
    a = random.randint(1, 20)
    b = random.randint(1, 20)
    op = random.choice(["+", "-"])
    if op == "-" and a < b:
        a, b = b, a
    q = f"{a} {op} {b} = ?"
    ans = str(a + b if op == "+" else a - b)
    return q, ans


def get_base_dir():
    """项目根目录（开发模式）或用户数据目录（打包模式）"""
    if getattr(sys, 'frozen', False):
        # PyInstaller / py2app 打包模式 — 用户数据存到 home 目录
        base = os.path.join(os.path.expanduser('~'), '.stock_analyzer')
        os.makedirs(base, exist_ok=True)
        return base
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ════════════════════════════════════════════════════════════
# 用户管理
# ════════════════════════════════════════════════════════════

USERS_FILE = os.path.join(get_base_dir(), "users.json")


def _load_users():
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def register_user(username: str, password: str) -> str:
    """注册用户，成功返回 None，失败返回错误信息"""
    username = username.strip()
    if not username or len(username) < 2:
        return "用户名至少 2 个字符"
    
    ok, err = validate_password(password)
    if not ok:
        return err

    users = _load_users()
    if username in users:
        return "用户名已存在"

    users[username] = {
        "password": _hash_password(password),
        "created_at": datetime.datetime.now().isoformat(),
    }
    _save_users(users)

    # 创建用户数据目录
    user_dir = os.path.join(get_base_dir(), "userdata", username)
    os.makedirs(user_dir, exist_ok=True)

    # 初始化用户数据文件
    for fname in ("config.json", "my_holdings.json", "trade_history.json", "ai_history.json"):
        path = os.path.join(user_dir, fname)
        if not os.path.exists(path):
            if fname == "config.json":
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({"api_key": "", "total_cash": 1000000.0}, f)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump([] if fname != "config.json" else {}, f)

    return None  # 成功


def verify_user(username: str, password: str) -> bool:
    """验证用户登录"""
    users = _load_users()
    user = users.get(username)
    if not user:
        return False
    return user["password"] == _hash_password(password)


# ════════════════════════════════════════════════════════════
# 用户数据路径
# ════════════════════════════════════════════════════════════

def get_user_dir(username: str) -> str:
    d = os.path.join(get_base_dir(), "userdata", username)
    os.makedirs(d, exist_ok=True)
    return d


# ════════════════════════════════════════════════════════════
# 配置
# ════════════════════════════════════════════════════════════

def load_config(username: str = "default"):
    path = os.path.join(get_user_dir(username), "config.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"api_key": "", "total_cash": 1000000.0}


def save_config(cfg, username: str = "default"):
    path = os.path.join(get_user_dir(username), "config.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存配置失败: {e}")
        return False


# ════════════════════════════════════════════════════════════
# 持仓
# ════════════════════════════════════════════════════════════

def load_holdings(username: str = "default"):
    path = os.path.join(get_user_dir(username), "my_holdings.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_holdings(holdings, username: str = "default"):
    path = os.path.join(get_user_dir(username), "my_holdings.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(holdings, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存持仓失败: {e}")
        return False


# ════════════════════════════════════════════════════════════
# 交易流水
# ════════════════════════════════════════════════════════════

def load_trade_history(username: str = "default"):
    path = os.path.join(get_user_dir(username), "trade_history.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_trade_history(history, username: str = "default"):
    path = os.path.join(get_user_dir(username), "trade_history.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存交易流水失败: {e}")
        return False


def record_trade(action, symbol, name, price, shares, cost_price=0.0, avail_cash=0.0, username: str = "default"):
    """记录一笔买卖流水"""
    history = load_trade_history(username)
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
    save_trade_history(history, username)
    return record


# ════════════════════════════════════════════════════════════
# AI 对话历史
# ════════════════════════════════════════════════════════════

def load_ai_history(username: str = "default"):
    path = os.path.join(get_user_dir(username), "ai_history.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_ai_history(messages, username: str = "default"):
    path = os.path.join(get_user_dir(username), "ai_history.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(messages[-30:], f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存AI对话记录失败: {e}")
        return False
