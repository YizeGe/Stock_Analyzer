"""
FastAPI 后端 — 提供所有 REST API + HTMX 前端静态文件服务
"""
import os
import sys
import json
import threading
import datetime
from pathlib import Path

# 确保 backend/ 在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware
import uvicorn

from data_api import (
    get_market_status, get_hot_stocks, get_broad_stock_pool,
    get_recommended_stocks, analyze_signal, fetch_realtime_quote,
    fetch_batch_quotes, fetch_historical_data,
)
from storage import (
    load_config, save_config,
    load_holdings, save_holdings,
    load_trade_history, save_trade_history, record_trade,
    load_ai_history, save_ai_history,
)

import json as _json
import numpy as np


class NumpyEncoder(_json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, (np.ndarray,)):
            return obj.tolist()
        return super().default(obj)


def dumps(obj):
    return _json.dumps(obj, cls=NumpyEncoder, ensure_ascii=False)


# ⭐ 全局覆写 json.JSONEncoder.default，让 numpy 类型在 FastAPI/Starlette 中自动处理
_numpy_patched = False


def _ensure_numpy_patch():
    global _numpy_patched
    if _numpy_patched:
        return
    _original_default = _json.JSONEncoder.default

    def _numpy_default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, (np.ndarray,)):
            return obj.tolist()
        return _original_default(self, obj)

    _json.JSONEncoder.default = _numpy_default
    _numpy_patched = True


_ensure_numpy_patch()

app = FastAPI(title="📈 模拟比赛辅助工具 - Web 版")

# 挂载前端静态文件
from starlette.staticfiles import StaticFiles
if getattr(sys, 'frozen', False):
    frontend_dir = os.path.join(sys._MEIPASS, "frontend")
else:
    frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
os.makedirs(frontend_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

# Session 中间件（用于登录状态）
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", "stock-analyzer-secret-key-2026"))


# ── 辅助函数 ────────────────────────────────────────────

def get_current_user(request: Request) -> str | None:
    """从 session 获取当前用户名"""
    return request.session.get("user")


def login_required(request: Request):
    """检查是否已登录，未登录则返回 None"""
    user = get_current_user(request)
    if not user:
        return None
    return user


# ════════════════════════════════════════════════════════════
# 用户认证路由
# ════════════════════════════════════════════════════════════

@app.get("/login")
async def login_page(request: Request):
    """登录页面"""
    if get_current_user(request):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/")
    return HTMLResponse(content=LOGIN_HTML)


@app.get("/register")
async def register_page(request: Request):
    """注册页面"""
    if get_current_user(request):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/")
    return HTMLResponse(content=REGISTER_HTML)


@app.post("/api/auth/login")
async def api_login(request: Request):
    data = await request.json()
    username = data.get("username", "").strip()
    password = data.get("password", "")

    from storage import verify_user
    if verify_user(username, password):
        request.session["user"] = username
        return {"ok": True, "username": username}
    return {"ok": False, "error": "用户名或密码错误"}


@app.get("/api/auth/captcha")
async def api_captcha(request: Request):
    from storage import generate_captcha
    q, ans = generate_captcha()
    request.session["captcha_ans"] = ans
    return {"question": q}


@app.post("/api/auth/register")
async def api_register(request: Request):
    data = await request.json()
    username = data.get("username", "").strip()
    password = data.get("password", "")
    captcha = data.get("captcha", "").strip()

    # 验证人机验证
    correct_ans = request.session.get("captcha_ans", "")
    if captcha != correct_ans:
        return {"ok": False, "error": "验证码错误"}
    request.session["captcha_ans"] = ""  # 用完即弃

    from storage import register_user
    err = register_user(username, password)
    if err:
        return {"ok": False, "error": err}
    # 注册后自动登录
    request.session["user"] = username
    return {"ok": True, "username": username}


@app.post("/api/auth/logout")
async def api_logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/auth/me")
async def api_auth_me(request: Request):
    user = get_current_user(request)
    if user:
        return {"logged_in": True, "username": user}
    return {"logged_in": False}


# ════════════════════════════════════════════════════════════
# API 路由
# ════════════════════════════════════════════════════════════

# ── 大盘状态 ────────────────────────────────────────────

@app.get("/api/market/status")
def api_market_status():
    mkt = get_market_status()
    if mkt is None:
        return {"error": "大盘数据获取失败"}
    return mkt


# ── 热门股票 / 市场概览 ─────────────────────────────────

@app.get("/api/market/overview")
def api_market_overview():
    """快速市场概览：大盘 + 涨停池 + 行业股（不含全量信号分析）"""
    mkt = get_market_status()
    pools = get_hot_stocks(limit=60)
    zt_list = pools.get("zt_stocks", [])
    sector_list = pools.get("sector_hot", [])

    return {
        "market_status": mkt,
        "zt_stocks": zt_list,
        "sector_hot": sector_list,
        "timestamp": datetime.datetime.now().isoformat(),
    }


# ── 单股行情 ├───────────────────────────────────────────

@app.get("/api/quote/{symbol}")
def api_quote(symbol: str):
    q = fetch_realtime_quote(symbol)
    if q is None:
        return {"error": f"股票 {symbol} 行情获取失败"}
    return q


@app.get("/api/quote/batch")
def api_batch_quote(symbols: str):
    """symbols: 逗号分隔的股票代码，如 '600519,000001'"""
    sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
    return fetch_batch_quotes(sym_list)


# ── 股票搜索 ────────────────────────────────────────────

@app.get("/api/search")
def api_search(q: str = ""):
    from data_api import search_stocks
    results = search_stocks(q, limit=10)
    return {"results": results, "query": q}


# ── 单股分析 ────────────────────────────────────────────

@app.get("/api/analyze/{symbol}")
def api_analyze(symbol: str):
    res = analyze_signal(symbol)
    if res is None:
        return {"error": f"股票 {symbol} 分析失败（K线数据不足）"}
    return res


# ── 选股推荐 ────────────────────────────────────────────

@app.get("/api/recommend")
def api_recommend():
    """快速推荐：只分析涨停池 + 行业股的前20只，减少耗时"""
    mkt = get_market_status()
    pools = get_hot_stocks(limit=30)
    broad = get_broad_stock_pool(limit_per_cat=10)

    all_stocks = pools.get("zt_stocks", [])[:10] + pools.get("sector_hot", [])[:20] + broad[:10]
    seen = set()
    deduped = []
    for s in all_stocks:
        if s["symbol"] not in seen:
            seen.add(s["symbol"])
            deduped.append(s)

    symbols = [s["symbol"] for s in deduped]
    name_map = {s["symbol"]: s.get("name", s["symbol"]) for s in deduped}
    mkt_above = bool(mkt["above_ma20"]) if mkt else True
    rec, zt_p, watch_p = get_recommended_stocks(
        symbols, market_above_ma20=mkt_above,
        max_count=20, min_watch_score=0.3, name_map=name_map)

    return {
        "market_status": mkt,
        "recommended": rec,
        "watching": watch_p,
        "zt_pool": zt_p,
    }


# ── 配置 ────────────────────────────────────────────────

@app.get("/api/config")
async def api_get_config(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "未登录"}, status_code=401)
    cfg = load_config(username=user)
    # 隐藏完整 API Key
    safe = cfg.copy()
    if safe.get("api_key"):
        k = safe["api_key"]
        safe["api_key"] = k[:6] + "…" + k[-4:] if len(k) > 10 else k[:4] + "…"
    return safe


@app.post("/api/config")
async def api_save_config(req: Request):
    user = get_current_user(req)
    if not user:
        return JSONResponse({"error": "未登录"}, status_code=401)
    data = await req.json()
    cfg = load_config(username=user)
    if "api_key" in data:
        cfg["api_key"] = data["api_key"]
    if "total_cash" in data:
        cfg["total_cash"] = float(data["total_cash"])
    if "avail_cash" in data:
        cfg["avail_cash"] = float(data["avail_cash"])
    save_config(cfg, username=user)
    return {"ok": True}


# ── 持仓 ────────────────────────────────────────────────

@app.get("/api/holdings")
def api_get_holdings(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "未登录"}, status_code=401)
    holdings = load_holdings(username=user)
    # 拉实时行情
    symbols = [h["symbol"] for h in holdings if h.get("symbol")]
    quotes = {}
    if symbols:
        for q in fetch_batch_quotes(symbols):
            quotes[q["symbol"]] = q

    result = []
    for h in holdings:
        sym = h["symbol"]
        q = quotes.get(sym, {})
        current_price = q.get("price", h.get("price", 0))
        change = q.get("change", 0)
        cost_price = h.get("cost", 0)
        shares = h.get("shares", 0)
        market_value = current_price * shares
        cost_value = cost_price * shares
        pnl = market_value - cost_value
        pnl_pct = (pnl / cost_value * 100) if cost_value > 0 else 0
        result.append({
            "symbol": sym,
            "name": q.get("name", h.get("name", sym)),
            "shares": shares,
            "cost": cost_price,
            "price": current_price,
            "change": change,
            "market_value": round(market_value, 2),
            "cost_value": round(cost_value, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
        })

    cfg = load_config(username=user)
    return {
        "holdings": result,
        "avail_cash": cfg.get("avail_cash", cfg.get("total_cash", 1000000.0)),
        "total_cash": cfg.get("total_cash", 1000000.0),
    }


@app.post("/api/holdings/add")
async def api_add_holding(req: Request):
    user = get_current_user(req)
    if not user:
        return JSONResponse({"error": "未登录"}, status_code=401)
    data = await req.json()
    symbol = data.get("symbol", "").strip()
    name = data.get("name", symbol).strip()
    shares = float(data.get("shares", 0))
    cost = float(data.get("cost", 0))
    if not symbol or shares <= 0 or cost <= 0:
        raise HTTPException(400, "缺少必要的参数")

    holdings = load_holdings(username=user)
    found = False
    for h in holdings:
        if h["symbol"] == symbol:
            old_total = h["cost"] * h["shares"] + cost * shares
            h["shares"] += shares
            h["cost"] = round(old_total / h["shares"], 3)
            if not h.get("name") and name:
                h["name"] = name
            found = True
            break
    if not found:
        holdings.append({"symbol": symbol, "name": name, "shares": shares, "cost": cost})

    save_holdings(holdings, username=user)

    cfg = load_config(username=user)
    cfg["avail_cash"] = cfg.get("avail_cash", cfg.get("total_cash", 1000000.0)) - cost * shares
    save_config(cfg, username=user)
    record_trade("BUY", symbol, name, cost, shares, cost, cfg.get("avail_cash", 0), username=user)

    return {"ok": True}


@app.post("/api/holdings/update")
async def api_update_holding(req: Request):
    user = get_current_user(req)
    if not user:
        return JSONResponse({"error": "未登录"}, status_code=401)
    data = await req.json()
    symbol = data.get("symbol", "").strip()
    holdings = load_holdings(username=user)
    for h in holdings:
        if h["symbol"] == symbol:
            if "shares" in data:
                h["shares"] = float(data["shares"])
            if "cost" in data:
                h["cost"] = float(data["cost"])
            if "name" in data and data["name"]:
                h["name"] = data["name"].strip()
            break
    save_holdings(holdings, username=user)
    return {"ok": True}


@app.post("/api/holdings/remove")
async def api_remove_holding(req: Request):
    user = get_current_user(req)
    if not user:
        return JSONResponse({"error": "未登录"}, status_code=401)
    """删除 / 清仓某持仓"""
    data = await req.json()
    symbol = data.get("symbol", "").strip()
    sell_price = float(data.get("price", 0))
    sell_shares_input = float(data.get("shares", 0))

    holdings = load_holdings(username=user)
    removed_shares = 0
    cost_price = 0
    h_name = symbol
    new_holdings = []
    for h in holdings:
        if h["symbol"] == symbol:
            h_name = h.get("name", symbol)
            cost_price = h.get("cost", 0)
            current_shares = h.get("shares", 0)
            actual_shares = min(sell_shares_input, current_shares) if sell_shares_input > 0 else current_shares
            if actual_shares >= current_shares:
                # 清仓
                removed_shares = current_shares
            else:
                # 减仓
                h["shares"] = current_shares - actual_shares
                removed_shares = actual_shares
                new_holdings.append(h)
        else:
            new_holdings.append(h)

    save_holdings(new_holdings, username=user)

    if removed_shares > 0 and sell_price > 0:
        pnl = (sell_price - cost_price) * removed_shares
        cfg = load_config(username=user)
        cfg["avail_cash"] = cfg.get("avail_cash", cfg.get("total_cash", 1000000.0)) + sell_price * removed_shares
        save_config(cfg, username=user)
        record_trade("SELL", symbol, h_name, sell_price, removed_shares, cost_price, cfg.get("avail_cash", 0), username=user)

    return {"ok": True, "message": f"已处理 {symbol} 卖出 {removed_shares}股"}


# ── 交易流水 ────────────────────────────────────────────

@app.get("/api/trades")
def api_get_trades(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "未登录"}, status_code=401)
    return load_trade_history(username=user)


# ── AI 顾问 ─────────────────────────────────────────────

@app.get("/api/ai/history")
def api_ai_history(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "未登录"}, status_code=401)
    return load_ai_history(username=user)


@app.post("/api/ai/history/clear")
def api_ai_history_clear(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "未登录"}, status_code=401)
    save_ai_history([], username=user)
    return {"ok": True}


@app.post("/api/ai/chat")
async def api_ai_chat(req: Request):
    data = await req.json()
    msg = data.get("message", "").strip()
    if not msg:
        raise HTTPException(400, "消息不能为空")

    user = get_current_user(req)
    if not user:
        return JSONResponse({"error": "未登录"}, status_code=401)

    cfg = load_config(username=user)
    api_key = cfg.get("api_key", "")
    avail_cash = cfg.get("avail_cash", cfg.get("total_cash", 1000000.0))
    holdings = load_holdings(username=user)

    from data_api import get_market_status

    # 获取当前市场状态（快速）
    mkt = get_market_status()
    mkt_desc = f"大盘MA20状态: {'多头' if mkt and mkt.get('above_ma20') else '空头'}" if mkt else "大盘数据暂缺"
    # 不在这里做全量分析，用已有数据即可

    ai_history = load_ai_history(username=user)

    system_prompt = f'''你是一个模拟炒股交易员与智能记账助手。你非常善于理解用户的自然语言指令。

【当前系统状态】
可用现金: {avail_cash:.2f} 元
持仓: {json.dumps(holdings, ensure_ascii=False)}
今日大盘环境: {mkt_desc}
今日系统底池推荐: 暂无（点击「求建议」可获取最新推荐）

【你的职责】
1. 用户可能用非常口语化的方式告诉你买卖操作，你需要智能提取
2. 如果用户一句话包含多只股票的操作，全部提取
3. 如果用户说的是股票名称而非代码，根据持仓或常识推断6位代码
4. 如果用户没有具体的买卖操作，只是咨询，则 operations 为空数组
5. 卖出时如果用户没说价格，优先用现价，没有则用成本价
6. 买入时如果用户没说价格，提醒用户补充

【输出格式】严格返回合法 JSON：
{{
  "operations": [
    {{{{
      "action": "BUY" 或 "SELL",
      "symbol": "6位股票代码",
      "name": "股票名称",
      "shares": 交易股数(整数),
      "price": 交易单价(数字)
    }}}}
  ],
  "reply": "你对用户的回话或操作建议分析，请用 Markdown 格式，生动且专业。"
}}

如果没有交易操作（纯咨询/建议），operations 返回空数组 []。
注意：你的回复只能是一段纯 JSON，不要有任何其他内容。'''

    messages = [{"role": "system", "content": system_prompt}]
    for m in ai_history[-15:]:
        messages.append(m)
    messages.append({"role": "user", "content": msg})

    # 调用 DeepSeek
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com/v1"
        )
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            temperature=0.1,
        )
        content = resp.choices[0].message.content
        if not content:
            raise ValueError("模型返回空内容")
        content = content.strip()

        # 处理模型可能返回的各种代码块包裹格式
        import re
        json_match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)```', content)
        if json_match:
            content = json_match.group(1).strip()

        result = json.loads(content)
    except Exception as e:
        import traceback
        print(f"[AI ERROR] {traceback.format_exc()}", flush=True)
        _c = locals().get('content', None)
        if _c and isinstance(_c, str) and not _c.startswith('{'):
            result = {"operations": [], "reply": _c}
        else:
            result = {"operations": [], "reply": f"❌ AI 调用失败: {str(e)}"}

    reply = result.get("reply", "")
    if reply:
        ai_history.append({"role": "user", "content": msg})
        ai_history.append({"role": "assistant", "content": reply})
        save_ai_history(ai_history, username=user)

    # 处理操作
    operations = result.get("operations", [])
    trade_results = []
    for op in operations:
        action = op.get("action")
        sym = str(op.get("symbol", "")).strip()
        sh = op.get("shares", 0)
        pr = op.get("price", 0)
        op_name = op.get("name", sym)

        if not sym or not action or sh <= 0 or pr <= 0:
            continue

        h_list = load_holdings(username=user)
        if action == "BUY":
            found = False
            for h in h_list:
                if h["symbol"] == sym:
                    old_total = h["cost"] * h["shares"] + sh * pr
                    h["shares"] += sh
                    h["cost"] = round(old_total / h["shares"], 3)
                    found = True
                    break
            if not found:
                h_list.append({"symbol": sym, "name": op_name, "shares": sh, "cost": pr})
            save_holdings(h_list, username=user)
            cfg["avail_cash"] = cfg.get("avail_cash", cfg.get("total_cash", 1000000.0)) - pr * sh
            save_config(cfg, username=user)
            record_trade("BUY", sym, op_name, pr, sh, pr, cfg.get("avail_cash", 0), username=user)
            trade_results.append(f"✅ 买入 {op_name}({sym}) {int(sh)}股 @ {pr}")

        elif action == "SELL":
            new_hl = []
            for h in h_list:
                if h["symbol"] == sym:
                    cost_p = h.get("cost", 0)
                    actual_sh = min(sh, h["shares"])
                    if actual_sh >= h["shares"]:
                        trade_results.append(f"✅ 清仓 {op_name}({sym}) {int(actual_sh)}股")
                    else:
                        h["shares"] -= actual_sh
                        new_hl.append(h)
                        trade_results.append(f"✅ 减仓 {op_name}({sym}) {int(actual_sh)}股")
                    cfg["avail_cash"] = cfg.get("avail_cash", cfg.get("total_cash", 1000000.0)) + pr * actual_sh
                    save_config(cfg, username=user)
                    record_trade("SELL", sym, op_name, pr, actual_sh, cost_p, cfg.get("avail_cash", 0), username=user)
                else:
                    new_hl.append(h)
            save_holdings(new_hl, username=user)

    result["trade_results"] = trade_results
    return result


# ── HTMX 前端页面 ───────────────────────────────────────

@app.get("/")
async def index(request: Request):
    """首页（需要登录）"""
    user = get_current_user(request)
    if not user:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/login")
    return HTMLResponse(content=INDEX_HTML)


# 内联 HTML（单文件前端）


LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>登录 - 模拟比赛辅助工具</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: linear-gradient(135deg, #1565c0, #1976d2);
            min-height: 100vh; display: flex; align-items: center; justify-content: center;
        }
        .card {
            background: white; border-radius: 16px; padding: 40px;
            width: 400px; max-width: 90vw;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }
        .card h1 { font-size: 24px; text-align: center; margin-bottom: 4px; color: #1565c0; }
        .card .subtitle { text-align: center; color: #888; font-size: 13px; margin-bottom: 28px; }
        .card .field { margin-bottom: 16px; }
        .card .field label { display: block; font-size: 13px; color: #666; margin-bottom: 4px; }
        .card .field input {
            width: 100%; padding: 10px 14px; border: 1px solid #ddd;
            border-radius: 8px; font-size: 14px; outline: none;
            transition: border-color 0.2s;
        }
        .card .field input:focus { border-color: #1565c0; box-shadow: 0 0 0 3px rgba(21,101,192,0.15); }
        .card .btn {
            width: 100%; padding: 11px; border: none; border-radius: 8px;
            font-size: 15px; font-weight: 600; cursor: pointer;
            background: #1565c0; color: white; transition: 0.2s; margin-top: 4px;
        }
        .card .btn:hover { background: #0d47a1; }
        .card .link { text-align: center; margin-top: 16px; font-size: 13px; }
        .card .link a { color: #1565c0; text-decoration: none; }
        .card .link a:hover { text-decoration: underline; }
        .card .error { color: #c62828; font-size: 13px; margin-top: 10px; text-align: center; display: none; }
    </style>
</head>
<body>
    <div class="card">
        <h1>📈 模拟比赛辅助工具</h1>
        <div class="subtitle">同花顺模拟炒股 · 技术分析 · 智能顾问</div>

        <div class="field"><label>用户名</label><input id="login-username" placeholder="输入用户名" autocomplete="username"></div>
        <div class="field"><label>密码</label><input id="login-password" type="password" placeholder="输入密码" autocomplete="current-password"></div>
        <button class="btn" onclick="doLogin()">登 录</button>
        <div class="error" id="login-error"></div>
        <div class="link">还没有账号？<a href="/register">去注册 →</a></div>
    </div>

    <script>
        document.getElementById('login-username').focus();
        document.getElementById('login-password').addEventListener('keydown', function(e) {
            if (e.key === 'Enter') doLogin();
        });
        async function doLogin() {
            const u = document.getElementById('login-username').value.trim();
            const p = document.getElementById('login-password').value;
            const err = document.getElementById('login-error');
            err.style.display = 'none';
            if (!u || !p) { err.textContent = '请填写用户名和密码'; err.style.display = 'block'; return; }
            const r = await fetch('/api/auth/login', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,password:p})});
            const d = await r.json();
            if (d.ok) { window.location.href = '/'; }
            else { err.textContent = d.error || '登录失败'; err.style.display = 'block'; }
        }
    </script>
</body>
</html>
"""

REGISTER_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>注册 - 模拟比赛辅助工具</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: linear-gradient(135deg, #1565c0, #1976d2);
            min-height: 100vh; display: flex; align-items: center; justify-content: center;
        }
        .card {
            background: white; border-radius: 16px; padding: 40px;
            width: 440px; max-width: 90vw;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        }
        .card h1 { font-size: 24px; text-align: center; margin-bottom: 4px; color: #1565c0; }
        .card .subtitle { text-align: center; color: #888; font-size: 13px; margin-bottom: 24px; }
        .card .field { margin-bottom: 14px; }
        .card .field label { display: block; font-size: 13px; color: #666; margin-bottom: 4px; }
        .card .field input {
            width: 100%; padding: 10px 14px; border: 1px solid #ddd;
            border-radius: 8px; font-size: 14px; outline: none;
            transition: border-color 0.2s;
        }
        .card .field input:focus { border-color: #1565c0; box-shadow: 0 0 0 3px rgba(21,101,192,0.15); }
        .card .btn {
            width: 100%; padding: 11px; border: none; border-radius: 8px;
            font-size: 15px; font-weight: 600; cursor: pointer;
            background: #1565c0; color: white; transition: 0.2s; margin-top: 4px;
        }
        .card .btn:hover { background: #0d47a1; }
        .card .btn:disabled { background: #999; cursor: not-allowed; }
        .card .link { text-align: center; margin-top: 16px; font-size: 13px; }
        .card .link a { color: #1565c0; text-decoration: none; }
        .card .link a:hover { text-decoration: underline; }
        .card .error { color: #c62828; font-size: 13px; margin-top: 10px; text-align: center; display: none; }
        .card .success { color: #2e7d32; font-size: 13px; margin-top: 10px; text-align: center; display: none; }
        .card .rules { font-size: 12px; color: #888; margin: 4px 0 12px; line-height: 1.7; padding: 10px; background: #f8f9ff; border-radius: 8px; }
        .card .rules .ok { color: #2e7d32; }
        .card .rules .no { color: #c62828; }
        .card .captcha-row { display: flex; gap: 10px; align-items: center; }
        .card .captcha-row input { flex: 1; }
        .card .captcha-q { font-size: 16px; font-weight: 600; color: #1565c0; white-space: nowrap; }
        .card .captcha-refresh { cursor: pointer; color: #1565c0; font-size: 20px; }
    </style>
</head>
<body>
    <div class="card">
        <h1>📈 注册新账号</h1>
        <div class="subtitle">模拟比赛辅助工具</div>

        <div class="field"><label>用户名</label><input id="reg-username" placeholder="2 个字符以上"></div>

        <div class="field"><label>密码</label><input id="reg-password" type="password" placeholder="至少 10 位" oninput="checkPassword()"></div>
        <div class="field"><label>确认密码</label><input id="reg-password2" type="password" placeholder="再次输入密码"></div>

        <div class="rules" id="pw-rules">
            <div id="rule-len">🔴 至少 10 位</div>
            <div id="rule-upper">🔴 包含大写字母</div>
            <div id="rule-lower">🔴 包含小写字母</div>
            <div id="rule-digit">🔴 包含数字</div>
            <div id="rule-special">🔴 包含特殊字符 (!@#$%^&amp;* 等)</div>
        </div>

        <div class="field">
            <label>人机验证</label>
            <div class="captcha-row">
                <span class="captcha-q" id="captcha-q">加载中…</span>
                <input id="reg-captcha" placeholder="计算结果" style="max-width:100px">
                <span class="captcha-refresh" onclick="loadCaptcha()" title="换一题">🔄</span>
            </div>
        </div>

        <button class="btn" id="reg-btn" onclick="doRegister()" disabled>注 册</button>
        <div class="error" id="reg-error"></div>
        <div class="success" id="reg-success"></div>
        <div class="link">已有账号？<a href="/login">去登录 →</a></div>
    </div>

    <script>
        document.getElementById('reg-username').focus();

        async function loadCaptcha() {
            const r = await fetch('/api/auth/captcha');
            const d = await r.json();
            document.getElementById('captcha-q').textContent = d.question;
        }
        loadCaptcha();

        function checkPassword() {
            const p = document.getElementById('reg-password').value;
            const ok = {len: p.length >= 10, upper: /[A-Z]/.test(p), lower: /[a-z]/.test(p), digit: /[0-9]/.test(p), special: /[!@#$%^&*()_+\-=\[\]{}|;':",.\/<>?~`]/.test(p)};
            const allOk = ok.len && ok.upper && ok.lower && ok.digit && ok.special;
            document.getElementById('reg-btn').disabled = !allOk;
            for (const [k, v] of Object.entries(ok)) {
                const el = document.getElementById('rule-' + k);
                if (el) el.innerHTML = (v ? '✅ ' : '🔴 ') + el.textContent.slice(2);
            }
        }

        async function doRegister() {
            const u = document.getElementById('reg-username').value.trim();
            const p = document.getElementById('reg-password').value;
            const p2 = document.getElementById('reg-password2').value;
            const captcha = document.getElementById('reg-captcha').value.trim();
            const err = document.getElementById('reg-error');
            const suc = document.getElementById('reg-success');
            err.style.display = 'none'; suc.style.display = 'none';

            if (!u || u.length < 2) { err.textContent = '用户名至少 2 个字符'; err.style.display = 'block'; return; }
            if (p !== p2) { err.textContent = '两次密码不一致'; err.style.display = 'block'; return; }
            if (!captcha) { err.textContent = '请完成人机验证'; err.style.display = 'block'; return; }

            document.getElementById('reg-btn').disabled = true;
            document.getElementById('reg-btn').textContent = '注册中…';

            const r = await fetch('/api/auth/register', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,password:p,captcha})});
            const d = await r.json();
            if (d.ok) { window.location.href = '/'; }
            else {
                err.textContent = d.error || '注册失败';
                err.style.display = 'block';
                document.getElementById('reg-btn').disabled = false;
                document.getElementById('reg-btn').textContent = '注 册';
                loadCaptcha();
            }
        }
    </script>
</body>
</html>
"""

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>📈 模拟比赛辅助工具</title>
    <script src="https://unpkg.com/htmx.org@2.0.4"></script>
    <script src="https://unpkg.com/htmx-ext-sse@2.0.0/sse.js"></script>
    <script src="https://unpkg.com/htmx-ext-loading-states@2.0.0/loading-states.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #f5f5f5; color: #333; min-height: 100vh;
        }
        .app-header {
            background: linear-gradient(135deg, #1565c0, #1976d2);
            color: white; padding: 14px 24px;
            display: flex; align-items: center; justify-content: space-between;
            box-shadow: 0 2px 8px rgba(0,0,0,0.15);
        }
        .app-header h1 { font-size: 20px; font-weight: 600; }
        .app-header .subtitle { font-size: 12px; opacity: 0.8; }

        .tabs {
            display: flex; background: #fff; border-bottom: 2px solid #e0e0e0;
            position: sticky; top: 0; z-index: 100;
        }
        .tab-btn {
            padding: 12px 24px; cursor: pointer; border: none; background: none;
            font-size: 14px; font-weight: 500; color: #666;
            border-bottom: 3px solid transparent; transition: all 0.2s;
        }
        .tab-btn:hover { background: #f0f0f0; }
        .tab-btn.active { color: #1565c0; border-bottom-color: #1565c0; background: #e3f2fd; }

        .tab-content { padding: 16px calc(37vw + 15px) 16px 16px; display: none; }
        .tab-content.active { display: block; }

        .panel { background: white; border-radius: 8px; padding: 16px; margin-bottom: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
        .panel-title { font-size: 15px; font-weight: 600; margin-bottom: 12px; color: #333; }

        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th { background: #f5f5f5; padding: 8px 10px; text-align: left; font-weight: 600; border-bottom: 2px solid #e0e0e0; white-space: nowrap; }
        td { padding: 8px 10px; border-bottom: 1px solid #f0f0f0; }
        tr:hover { background: #f8f9ff; }

        .up { color: #d32f2f; }
        .down { color: #388e3c; }
        .badge {
            display: inline-block; padding: 2px 8px; border-radius: 4px;
            font-size: 12px; font-weight: 500; color: white; white-space: nowrap;
        }

        .market-banner {
            padding: 14px 20px; border-radius: 8px; margin-bottom: 16px;
            font-size: 14px; font-weight: 500; display: flex; align-items: center; gap: 12px;
        }
        .market-banner.bull { background: #d4edda; color: #155724; }
        .market-bear { background: #f8d7da; color: #721c24; }
        .market-neutral { background: #fff3cd; color: #856404; }

        .grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }
        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }

        @media (max-width: 1024px) { .grid-3 { grid-template-columns: 1fr 1fr; } .tab-content { padding: 16px; } .app-header { padding: 14px 24px; } }
        @media (max-width: 768px) { .grid-3, .grid-2 { grid-template-columns: 1fr; } .tab-content { padding: 8px; } .app-header { padding: 10px 16px; } }

        /* ── 手机端适配 ─────────────────────────────────── */
        @media (max-width: 768px) {
            .app-header { flex-direction: column; gap: 8px; padding: 10px 16px; }
            .app-header h1 { font-size: 16px; }
            .app-header .subtitle { font-size: 10px; }
            #search-input { width: 100% !important; }
            .tabs { overflow-x: auto; -webkit-overflow-scrolling: touch; }
            .tab-btn { padding: 10px 14px; font-size: 12px; white-space: nowrap; }
            .tab-content { padding: 8px; }
            .panel { padding: 10px; }
            .panel-title { font-size: 13px; }
            .scroll-table { overflow-x: auto; -webkit-overflow-scrolling: touch; }
            .scroll-table table { font-size: 11px; }
            .scroll-table th, .scroll-table td { padding: 5px 6px; white-space: nowrap; }
            .stock-detail .detail-grid { grid-template-columns: 1fr; }
            #detail-dialog .dialog { min-width: auto !important; width: 92vw; }
            .chat-box { height: 350px; }
            .chat-msg { max-width: 95%; font-size: 12px; }
            .status-bar { font-size: 10px; flex-wrap: wrap; gap: 4px; }
            .search-dropdown { left: 8px; right: 8px; }
            .grid-3 { gap: 8px; }
            #sell-dialog .dialog { min-width: auto !important; width: 92vw; }
        }
        @media (max-width: 480px) {
            .tab-btn { padding: 8px 10px; font-size: 11px; }
            .scroll-table table { font-size: 10px; }
            .scroll-table th, .scroll-table td { padding: 4px 4px; }
            .dialog { width: 95vw !important; min-width: auto !important; padding: 16px; }
        }

        .loading { text-align: center; padding: 40px; color: #999; }

        .btn {
            display: inline-block; padding: 6px 16px; border-radius: 6px;
            border: none; cursor: pointer; font-size: 13px; font-weight: 500;
            transition: all 0.2s;
        }
        .btn-primary { background: #1565c0; color: white; }
        .btn-primary:hover { background: #0d47a1; }
        .btn-success { background: #2e7d32; color: white; }
        .btn-danger { background: #c62828; color: white; }
        .btn-sm { padding: 4px 10px; font-size: 12px; }

        .toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
        .toolbar .spacer { flex: 1; }

        input, select { padding: 6px 10px; border: 1px solid #ccc; border-radius: 6px; font-size: 13px; }
        input:focus { outline: none; border-color: #1565c0; box-shadow: 0 0 0 2px rgba(21,101,192,0.2); }

        .chat-box {
            border: 1px solid #e0e0e0; border-radius: 8px; height: 500px;
            overflow-y: auto; padding: 16px; background: #fafafa;
            display: flex; flex-direction: column; gap: 12px;
        }
        .chat-msg { padding: 10px 14px; border-radius: 8px; max-width: 80%; line-height: 1.5; font-size: 13px; }
        .chat-msg.user { background: #e3f2fd; align-self: flex-end; }
        .chat-msg.ai { background: white; border: 1px solid #e0e0e0; align-self: flex-start; }
        .chat-msg.system { background: #fff3cd; align-self: center; font-size: 12px; padding: 6px 12px; }
        .chat-input-row { display: flex; gap: 8px; margin-top: 8px; }
        .chat-input-row input { flex: 1; }

        .scroll-table { max-height: 500px; overflow-y: auto; }

        .status-bar {
            background: #333; color: #ccc; padding: 6px 16px;
            font-size: 12px; font-family: monospace; position: fixed; bottom: 0;
            left: 0; right: 0; z-index: 100; display: flex; justify-content: space-between;
        }

        /* ── Live2D 看板娘 ──────────────────────────── */
        #live2d-canvas {
            position: fixed;
            bottom: 28px;
            right: -10px;
            z-index: 150;
            cursor: pointer;
        }
        @media (max-width: 768px) {
            #live2d-canvas { right: 2px; bottom: 22px; }
        }

        /* ── 看板娘对话气泡 ──────────────────────────── */
        #live2d-bubble {
            position: fixed;
            max-width: 220px;
            padding: 12px 16px;
            background: #fff;
            border: 2px solid #1565c0;
            border-radius: 16px;
            font-size: 13px;
            line-height: 1.5;
            color: #333;
            z-index: 151;
            opacity: 0;
            transform: translate(-50%, 0) translateY(8px);
            transition: opacity 0.3s, transform 0.3s;
            pointer-events: none;
            box-shadow: 0 4px 16px rgba(0,0,0,0.12);
            left: 0;
            top: 0;
        }
        #live2d-bubble.show {
            opacity: 1;
            transform: translate(-50%, 0) translateY(0);
        }
        #live2d-bubble::after {
            content: '';
            position: absolute;
            bottom: -10px;
            left: 50%;
            transform: translateX(-50%);
            width: 0;
            height: 0;
            border-left: 10px solid transparent;
            border-right: 10px solid transparent;
            border-top: 10px solid #fff;
        }
        #live2d-bubble::before {
            content: '';
            position: absolute;
            bottom: -14px;
            left: 50%;
            transform: translateX(-50%);
            width: 0;
            height: 0;
            border-left: 12px solid transparent;
            border-right: 12px solid transparent;
            border-top: 12px solid #1565c0;
        }
        @media (max-width: 768px) {
            #live2d-bubble { max-width: 160px; font-size: 11px; }
        }

        .dialog-overlay {
            position: fixed; top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.4); z-index: 200;
            display: flex; align-items: center; justify-content: center;
        }
        .dialog {
            background: white; border-radius: 12px; padding: 24px;
            min-width: 360px; max-width: 500px; box-shadow: 0 8px 32px rgba(0,0,0,0.2);
        }
        .dialog h3 { margin-bottom: 16px; }
        .dialog .field { margin-bottom: 12px; }
        .dialog .field label { display: block; font-size: 13px; color: #666; margin-bottom: 4px; }
        .dialog .field input { width: 100%; }
        .dialog .btn-row { display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px; }

        /* 个股详情弹窗 */
        .stock-detail {
            font-size: 14px; line-height: 1.6;
        }
        .stock-detail .detail-header {
            text-align: center; margin-bottom: 16px;
        }
        .stock-detail .detail-header h2 {
            font-size: 22px; margin-bottom: 4px;
        }
        .stock-detail .detail-header .price {
            font-size: 28px; font-weight: 700;
        }
        .stock-detail .detail-grid {
            display: grid; grid-template-columns: 1fr 1fr; gap: 8px 20px;
        }
        .stock-detail .detail-item {
            display: flex; justify-content: space-between; padding: 6px 0;
            border-bottom: 1px solid #f0f0f0;
        }
        .stock-detail .detail-item .label { color: #888; }
        .stock-detail .detail-item .value { font-weight: 500; }
        .stock-detail .reasons {
            margin-top: 12px; padding: 10px; background: #f8f9ff;
            border-radius: 8px; font-size: 13px;
        }
        .stock-detail .reasons li { margin: 4px 0; }
        .stock-detail .stop-loss {
            margin-top: 8px; padding: 8px 12px; background: #fff3cd;
            border-radius: 6px; font-size: 13px;
        }

        /* 思考动画 */
        .thinking-indicator {
            display: flex; align-items: center; gap: 10px;
            align-self: flex-start;
            background: white; border: 1px solid #e0e0e0;
            border-radius: 8px; padding: 12px 16px;
            font-size: 13px; color: #666;
        }
        .thinking-dots {
            display: flex; gap: 4px;
        }
        .thinking-dots span {
            width: 7px; height: 7px; border-radius: 50%;
            background: #1565c0;
            animation: dotPulse 1.4s infinite ease-in-out both;
        }
        .thinking-dots span:nth-child(1) { animation-delay: -0.32s; }
        .thinking-dots span:nth-child(2) { animation-delay: -0.16s; }
        .thinking-dots span:nth-child(3) { animation-delay: 0s; }
        @keyframes dotPulse {
            0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
            40% { transform: scale(1); opacity: 1; }
        }
    </style>
</head>
<body>
    <div class="app-header">
        <div style="display:flex;align-items:center;gap:20px">
            <div>
                <h1>📈 模拟比赛辅助工具</h1>
                <div class="subtitle">同花顺模拟炒股 | 技术分析 · 智能顾问</div>
            </div>
            <div class="search-box" style="position:relative">
                <input id="search-input" type="text" placeholder="搜股票代码/名称…"
                       oninput="searchStocks(this.value)" onkeydown="if(event.key==='Enter')doSearch(this.value)"
                       style="padding:6px 12px;border:none;border-radius:6px;width:220px;font-size:13px;outline:none">
                <div id="search-results" class="search-dropdown" style="display:none;position:absolute"></div>
            </div>
        </div>
        <div style="display:flex;align-items:center;gap:12px">
            <span id="statusText" style="font-size:12px;opacity:0.8">就绪</span>
            <span id="userDisplay" style="font-size:12px;color:rgba(255,255,255,0.9)"></span>
            <button onclick="doLogout()" style="background:rgba(255,255,255,0.2);border:none;color:white;padding:4px 10px;border-radius:6px;cursor:pointer;font-size:12px">退出</button>
        </div>
    </div>

    <!-- Tabs -->
    <div class="tabs">
        <button class="tab-btn active" onclick="switchTab('market')">🌐 市场概览</button>
        <button class="tab-btn" onclick="switchTab('holdings')">💼 持仓分析</button>
        <button class="tab-btn" onclick="switchTab('ai')">🤖 AI 顾问</button>
        <button class="tab-btn" onclick="switchTab('settings')">⚙️ 设置</button>
    </div>

    <!-- Tab: 市场概览 -->
    <div id="tab-market" class="tab-content active">
        <div id="market-banner"></div>
        <div class="toolbar">
            <strong>🌐 市场概览</strong>
            <span class="spacer"></span>
            <button class="btn btn-primary btn-sm" onclick="loadMarketData()">🔄 刷新数据</button>
            <button class="btn btn-sm" onclick="if(recommendedData) exportCSV(recommendedData)">📤 导出推荐</button>
        </div>
        <div class="grid-3">
            <div class="panel">
                <div class="panel-title">🏭 行业代表股</div>
                <div class="scroll-table" style="max-height:450px">
                    <table><thead><tr><th>代码</th><th>名称</th><th>现价</th><th>涨跌幅</th><th>行业</th></tr></thead>
                    <tbody id="sector-table"></tbody></table>
                </div>
            </div>
            <div class="panel">
                <div class="panel-title">🔥 今日涨停</div>
                <div class="scroll-table" style="max-height:450px">
                    <table><thead><tr><th>代码</th><th>名称</th><th>现价</th><th>涨跌幅</th></tr></thead>
                    <tbody id="zt-table"></tbody></table>
                </div>
            </div>
            <div class="panel">
                <div class="panel-title">⭐ 技术面推荐</div>
                <div class="scroll-table" style="max-height:450px">
                    <table><thead><tr><th>代码</th><th>名称</th><th>评分</th><th>推荐</th><th>RSI</th></tr></thead>
                    <tbody id="rec-table"></tbody></table>
                </div>
            </div>
        </div>
    </div>

    <!-- Tab: 持仓分析 -->
    <div id="tab-holdings" class="tab-content">
        <div class="panel">
            <div class="toolbar">
                <div class="panel-title" style="margin:0">💼 当前持仓</div>
                <span class="spacer"></span>
                <span id="cash-display" style="font-size:13px;color:#666"></span>
                <button class="btn btn-success btn-sm" onclick="showAddHoldingDialog()">➕ 添加</button>
                <button class="btn btn-primary btn-sm" onclick="loadHoldings()">🔄 刷新</button>
            </div>
            <div class="scroll-table" style="max-height:400px">
                <table><thead><tr>
                    <th>代码</th><th>名称</th><th>持股</th><th>成本</th><th>现价</th>
                    <th>市值</th><th>盈亏</th><th>操作</th>
                </tr></thead>
                <tbody id="holdings-table"></tbody></table>
            </div>
        </div>
        <div class="panel">
            <div class="panel-title">📋 交易流水</div>
            <div class="scroll-table" style="max-height:300px">
                <table><thead><tr><th>时间</th><th>操作</th><th>代码</th><th>名称</th><th>价格</th><th>数量</th><th>金额</th><th>盈亏</th></tr></thead>
                <tbody id="trade-table"></tbody></table>
            </div>
        </div>
    </div>

    <!-- Tab: AI 顾问 -->
    <div id="tab-ai" class="tab-content">
        <div class="panel">
            <div class="panel-title">🤖 AI 智能交易顾问</div>
            <div id="ai-chat" class="chat-box"></div>
            <div class="chat-input-row">
                <input type="text" id="ai-input" placeholder="说点什么…（如「买了 600519 100股 1800」）"
                       onkeydown="if(event.key==='Enter') sendAiMessage()">
                <button class="btn btn-primary" onclick="sendAiMessage()">发送</button>
                <button class="btn btn-sm" onclick="openAiTradeDialog()">📝 规范记账</button>
                <button class="btn btn-sm" onclick="requestAiAdvice()">💡 求建议</button>
                <button class="btn btn-sm" onclick="clearAiChat()">清空</button>
            </div>
        </div>
    </div>

    <!-- Tab: 设置 -->
    <div id="tab-settings" class="tab-content">
        <div class="panel" style="max-width:600px">
            <div class="panel-title">⚙️ 设置</div>
            <div class="field"><label>DeepSeek API Key</label><input type="password" id="setting-apikey" style="width:100%;padding:10px 14px"></div>
            <div class="field"><label>总资金</label><input type="number" id="setting-total-cash" style="width:100%;padding:10px 14px"></div>
            <div class="field"><label>可用现金</label><input type="number" id="setting-avail-cash" style="width:100%;padding:10px 14px"></div>
            <button class="btn btn-primary" onclick="saveSettings()" style="padding:10px 24px;font-size:14px">保存设置</button>
        </div>
    </div>

    <!-- Dialog Overlay -->
    <div id="dialog-overlay" class="dialog-overlay" style="display:none" onclick="closeDialog(event)">
        <div class="dialog" onclick="event.stopPropagation()">
            <h3 id="dialog-title">添加持仓</h3>
            <div class="field"><label>股票代码</label><input id="dlg-symbol" placeholder="600519"></div>
            <div class="field"><label>股票名称</label><input id="dlg-name" placeholder="贵州茅台"></div>
            <div class="field"><label>数量（股）</label><input id="dlg-shares" type="number" placeholder="100"></div>
            <div class="field"><label>成本价</label><input id="dlg-cost" type="number" step="0.01" placeholder="1800"></div>
            <div class="btn-row">
                <button class="btn" onclick="closeDialog()">取消</button>
                <button class="btn btn-primary" id="dialog-confirm" onclick="confirmAddHolding()">确认添加</button>
            </div>
        </div>
    </div>

    <!-- 个股详情弹窗 -->
    <div id="detail-dialog" class="dialog-overlay" style="display:none" onclick="closeDetailDialog(event)">
        <div class="dialog" style="min-width:480px;max-width:560px" onclick="event.stopPropagation()">
            <div class="stock-detail" id="detail-content">
                <div class="detail-header">
                    <h2 id="detail-name">—</h2>
                    <div class="price" id="detail-price">—</div>
                    <div id="detail-rec" style="margin-top:6px"></div>
                </div>
                <div class="detail-grid">
                    <div class="detail-item"><span class="label">RSI</span><span class="value" id="detail-rsi">—</span></div>
                    <div class="detail-item"><span class="label">评分</span><span class="value" id="detail-score">—</span></div>
                    <div class="detail-item"><span class="label">MA 交叉</span><span class="value" id="detail-ma">—</span></div>
                    <div class="detail-item"><span class="label">MACD</span><span class="value" id="detail-macd">—</span></div>
                    <div class="detail-item"><span class="label">MA20 位置</span><span class="value" id="detail-ma20">—</span></div>
                    <div class="detail-item"><span class="label">量比</span><span class="value" id="detail-vol">—</span></div>
                </div>
                <div class="reasons" id="detail-reasons"></div>
                <div class="stop-loss" id="detail-stop"></div>
            </div>
            <div class="btn-row" style="margin-top:12px">
                <button class="btn" onclick="closeDetailDialog()">关闭</button>
            </div>
        </div>
    </div>

    <!-- 卖出弹窗 -->
    <div id="sell-dialog" class="dialog-overlay" style="display:none" onclick="document.getElementById('sell-dialog').style.display='none'">
        <div class="dialog" style="min-width:380px" onclick="event.stopPropagation()">
            <h3 id="sell-title">❌ 卖出</h3>
            <div class="field">
                <label>当前市价</label>
                <div id="sell-market-price" style="font-size:18px;font-weight:700;color:#1565c0">—</div>
            </div>
            <div class="field"><label>卖出价格</label><input id="sell-price" type="number" step="0.01" placeholder="输入卖出价"></div>
            <div class="field"><label>卖出数量（股）</label><input id="sell-shares" type="number" placeholder="全部"></div>
            <div style="font-size:12px;color:#888;margin-bottom:12px">
                持仓: <span id="sell-hold-shares">0</span> 股 · 
                成本: <span id="sell-cost">0.00</span>
            </div>
            <div class="btn-row">
                <button class="btn" onclick="document.getElementById('sell-dialog').style.display='none'">取消</button>
                <button class="btn btn-danger" onclick="confirmSell()" style="background:#c62828;color:white">确认卖出</button>
            </div>
            <div id="sell-error" style="color:#c62828;font-size:13px;margin-top:8px"></div>
        </div>
    </div>

    <!-- 规范记账弹窗 -->
    <div id="ai-trade-dialog" class="dialog-overlay" style="display:none" onclick="document.getElementById('ai-trade-dialog').style.display='none'">
        <div class="dialog" style="min-width:320px" onclick="event.stopPropagation()">
            <h3 style="margin-top:0">📝 规范化记录交易</h3>
            <div class="field">
                <label>操作类型</label>
                <select id="ai-dlg-action" style="width:100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px;">
                    <option value="买入">买入 (BUY)</option>
                    <option value="卖出">卖出 (SELL)</option>
                </select>
            </div>
            <div class="field">
                <label>股票代码 (6位)</label>
                <input type="text" id="ai-dlg-symbol" placeholder="例如: 600519" style="width:100%">
            </div>
            <div class="field">
                <label>数量 (股)</label>
                <input type="number" id="ai-dlg-shares" placeholder="例如: 100" style="width:100%">
            </div>
            <div class="field">
                <label>单价 (元)</label>
                <input type="number" step="0.01" id="ai-dlg-price" placeholder="例如: 1800.5" style="width:100%">
            </div>
            <div class="btn-row" style="margin-top:16px">
                <button class="btn" onclick="document.getElementById('ai-trade-dialog').style.display='none'">取消</button>
                <button class="btn btn-primary" onclick="submitAiTrade()">发送给 AI</button>
            </div>
        </div>
    </div>

    <!-- Live2D 看板娘 -->
    <canvas id="live2d-canvas"></canvas>
    <div id="live2d-bubble"></div>

    <div class="status-bar">
        <span><span id="statusBar">就绪</span> <span id="statusExtra" style="color:#888"></span></span>
        <span>
            <span id="marketTimeStatus" style="margin-right:12px"></span>
            <span id="timeDisplay"></span>
        </span>
    </div>

    <script>
        // ── Tab 切换 ──────────────────────────────────────
        function switchTab(name) {
            if (name === 'ai') {
                const apiKey = document.getElementById('setting-apikey').value.trim();
                if (!apiKey) {
                    alert('首次使用 AI 顾问需要配置 DeepSeek API Key。请在设置中配置。');
                    name = 'settings';
                }
            }
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.getElementById('tab-' + name).classList.add('active');
            document.querySelector(`.tab-btn[onclick="switchTab('${name}')"]`).classList.add('active');
            if (name === 'holdings') loadHoldings();
        }

        // ── 状态栏 ────────────────────────────────────────
        function setStatus(msg, extra) {
            const sb = document.getElementById('statusBar');
            sb.textContent = msg;
            const se = document.getElementById('statusExtra');
            if (extra) se.textContent = ' | ' + extra; else se.textContent = '';
        }

        // ── 市场概览 ──────────────────────────────────────
        let recommendedData = [];

        async function loadMarketData() {
            setStatus('正在加载市场数据…');
            document.getElementById('zt-table').innerHTML = '<tr><td colspan="4" class="loading">加载中…</td></tr>';
            document.getElementById('sector-table').innerHTML = '<tr><td colspan="5" class="loading">加载中…</td></tr>';
            document.getElementById('rec-table').innerHTML = '<tr><td colspan="5" class="loading">加载中…</td></tr>';

            try {
                // 1. 快速加载大盘+涨停池+行业股
                const resp = await fetch('/api/market/overview');
                const data = await resp.json();

                renderMarketBanner(data.market_status);
                renderTable('zt-table', (data.zt_stocks || []).slice(0, 50),
                    s => [s.symbol || '—', s.name || '—',
                          (s.price || 0).toFixed(2),
                          `<span class="${(s.change||0)>=0?'up':'down'}">${(s.change||0).toFixed(2)}%</span>`]);
                renderTable('sector-table', (data.sector_hot || []).slice(0, 50),
                    s => [s.symbol || '—', s.name || '—',
                          (s.price || 0).toFixed(2),
                          `<span class="${(s.change||0)>=0?'up':'down'}">${(s.change||0).toFixed(2)}%</span>`,
                          s.sector || '—']);

                setStatus(`行业:${data.sector_hot?.length||0} 只 | 涨停:${data.zt_stocks?.length||0} 只 | 正在计算技术面推荐…`);

                // 2. 异步加载技术面推荐（可能较慢）
                loadRecommendations();
            } catch(e) {
                setStatus('❌ 加载失败: ' + e.message);
            }
        }

        async function loadRecommendations() {
            try {
                setStatus('正在计算技术面推荐…（约30秒）');
                const resp = await fetch('/api/recommend');
                const data = await resp.json();

                recommendedData = data.recommended || [];
                renderTable('rec-table', recommendedData,
                    s => [s.symbol || '—', s.name || '—',
                          s.score ?? '—',
                          `<span class="badge" style="background:${s.rec_color||'#757575'}">${s.recommendation||'—'}</span>`,
                          s.rsi != null ? s.rsi.toFixed(1) : '—']);

                setStatus(`推荐:${(data.recommended||[]).length} 只 | 关注:${(data.watching||[]).length} 只`);
            } catch(e) {
                document.getElementById('rec-table').innerHTML =
                    '<tr><td colspan="5" class="loading">❌ 分析失败，点击 <a href="#" onclick="loadRecommendations()">重试</a></td></tr>';
                setStatus('❌ 技术分析失败: ' + e.message);
            }
        }

        function renderMarketBanner(mkt) {
            const el = document.getElementById('market-banner');
            if (!mkt) {
                el.innerHTML = '<div class="market-banner market-neutral">⚠️ 大盘数据获取失败</div>';
                return;
            }
            const cls = mkt.above_ma20 ? 'bull' : (mkt.change < 0 ? 'market-bear' : 'market-neutral');
            const emoji = mkt.above_ma20 ? '✅' : '❌';
            const advice = mkt.above_ma20 ? '可积极选股' : '谨慎操作，降低仓位';
            el.innerHTML = `<div class="market-banner ${cls}">
                <strong>${mkt.trend || '大盘'}</strong>
                <span>上证 ${mkt.price}（<span class="${mkt.change>=0?'up':'down'}">${mkt.change.toFixed(2)}%</span>）</span>
                <span>MA20=${mkt.ma20}</span>
                <span>${emoji} 20日线${mkt.above_ma20?'上':'下'}方 → ${advice}</span>
            </div>`;
        }

        function renderTable(tbodyId, data, rowFn) {
            const tbody = document.getElementById(tbodyId);
            if (!data || data.length === 0) {
                tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:#999">暂无数据</td></tr>';
                return;
            }
            tbody.innerHTML = data.map(item => {
                const cells = rowFn(item);
                const sym = item.symbol || '';
                const name = item.name || '';
                return '<tr data-symbol="' + sym + '" data-name="' + name + '" style="cursor:pointer">' + 
                       cells.map(c => '<td>' + c + '</td>').join('') + '</tr>';
            }).join('');
        }

        function exportCSV(data) {
            if (!data || data.length === 0) { alert('暂无数据可导出'); return; }
            const headers = ['代码','名称','评分','推荐','RSI','信号','止损'];
            const rows = data.map(s => [s.symbol, s.name, s.score, s.recommendation,
                s.rsi, (s.reason||[]).join('; '), s.stop_reason]);
            const csv = [headers.join(','), ...rows.map(r => r.map(v => `"${v}"`).join(','))].join('\n');
            const blob = new Blob(['\ufeff' + csv], {type: 'text/csv;charset=utf-8-sig'});
            const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
            a.download = '推荐股票_' + new Date().toISOString().slice(0,10) + '.csv';
            a.click();
        }

        // ── 持仓 ──────────────────────────────────────────
        async function loadHoldings() {
            try {
                const resp = await fetch('/api/holdings');
                const data = await resp.json();
                document.getElementById('cash-display').innerHTML =
                    `💰 可用现金: <strong>${(data.avail_cash||0).toLocaleString()}</strong> 元 | ` +
                    `总资金: ${(data.total_cash||0).toLocaleString()} 元`;

                const tbody = document.getElementById('holdings-table');
                if (!data.holdings || data.holdings.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#999">暂无持仓</td></tr>';
                } else {
                    tbody.innerHTML = data.holdings.map(h => {
                        const pnlCls = h.pnl >= 0 ? 'up' : 'down';
                        return `<tr>
                            <td>${h.symbol}</td>
                            <td>${h.name}</td>
                            <td>${h.shares}</td>
                            <td>${h.cost.toFixed(2)}</td>
                            <td class="${h.change>=0?'up':'down'}">${h.price.toFixed(2)}</td>
                            <td>${h.market_value.toLocaleString()}</td>
                            <td class="${pnlCls}">${h.pnl >= 0 ? '+' : ''}${h.pnl.toFixed(2)} (${h.pnl_pct >= 0 ? '+' : ''}${h.pnl_pct.toFixed(1)}%)</td>
                            <td>
                                <button class="btn btn-sm btn-danger" onclick="openSellDialog('${h.symbol}','${h.name}',${h.shares},${h.cost},${h.price})">卖出</button>
                            </td>
                        </tr>`;
                    }).join('');
                }
                await loadTrades();
            } catch(e) {
                setStatus('持仓加载失败: ' + e.message);
            }
        }

        async function loadTrades() {
            try {
                const resp = await fetch('/api/trades');
                const trades = await resp.json();
                const tbody = document.getElementById('trade-table');
                if (!trades || trades.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#999">暂无交易记录</td></tr>';
                } else {
                    tbody.innerHTML = trades.slice(0, 50).map(t => {
                        const pnlCls = (t.pnl||0) >= 0 ? 'up' : 'down';
                        return `<tr>
                            <td>${t.time||'—'}</td>
                            <td>${t.action||'—'}</td>
                            <td>${t.symbol||'—'}</td>
                            <td>${t.name||'—'}</td>
                            <td>${t.price||'—'}</td>
                            <td>${t.shares||'—'}</td>
                            <td>${(t.amount||0).toLocaleString()}</td>
                            <td class="${pnlCls}">${t.pnl ? (t.pnl>=0?'+':'')+t.pnl.toFixed(2) : '—'}</td>
                        </tr>`;
                    }).join('');
                }
            } catch(e) {}
        }

        // ── 添加持仓 ──────────────────────────────────────
        function showAddHoldingDialog() {
            document.getElementById('dlg-symbol').value = '';
            document.getElementById('dlg-name').value = '';
            document.getElementById('dlg-shares').value = '';
            document.getElementById('dlg-cost').value = '';
            document.getElementById('dialog-title').textContent = '➕ 添加持仓';
            document.getElementById('dialog-overlay').style.display = 'flex';
        }

        // ── 卖出弹窗 ─────────────────────────────────────
        let _sellData = {symbol:'', name:'', shares:0, cost:0, price:0};

        function openSellDialog(symbol, name, shares, cost, price) {
            _sellData = {symbol, name, shares, cost, price};
            document.getElementById('sell-title').textContent = '❌ 卖出 ' + name + ' (' + symbol + ')';
            document.getElementById('sell-market-price').textContent = '¥' + price.toFixed(2);
            document.getElementById('sell-price').value = price.toFixed(2);
            document.getElementById('sell-shares').value = shares;
            document.getElementById('sell-hold-shares').textContent = shares;
            document.getElementById('sell-cost').textContent = cost.toFixed(2);
            document.getElementById('sell-error').textContent = '';
            document.getElementById('sell-dialog').style.display = 'flex';
        }

        async function confirmSell() {
            const price = parseFloat(document.getElementById('sell-price').value);
            const shares = parseInt(document.getElementById('sell-shares').value);
            const err = document.getElementById('sell-error');
            
            if (!price || price <= 0) { err.textContent = '请输入有效价格'; return; }
            if (!shares || shares <= 0) { err.textContent = '请输入有效数量'; return; }
            if (shares > _sellData.shares) { err.textContent = '卖出数量不能超过持仓 ' + _sellData.shares + ' 股'; return; }

            document.getElementById('sell-dialog').style.display = 'none';
            setStatus('正在卖出…');

            try {
                const resp = await fetch('/api/holdings/remove', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({symbol: _sellData.symbol, shares: shares, price: price})
                });
                const d = await resp.json();
                setStatus(d.message || '卖出成功');
                loadHoldings();
            } catch(e) {
                setStatus('卖出失败: ' + e.message);
            }
        }

        function closeDialog(e) {
            document.getElementById('dialog-overlay').style.display = 'none';
            _sellData = null;
        }

        function closeDetailDialog(e) {
            document.getElementById('detail-dialog').style.display = 'none';
        }

        async function showStockDetail(symbol, name) {
            const dialog = document.getElementById('detail-dialog');
            const content = document.getElementById('detail-content');
            dialog.style.display = 'flex';
            document.getElementById('detail-name').textContent = name + ' (' + symbol + ')';
            document.getElementById('detail-price').textContent = '加载中…';
            document.getElementById('detail-rsi').textContent = '…';
            document.getElementById('detail-score').textContent = '…';
            document.getElementById('detail-ma').textContent = '…';
            document.getElementById('detail-macd').textContent = '…';
            document.getElementById('detail-ma20').textContent = '…';
            document.getElementById('detail-vol').textContent = '…';
            document.getElementById('detail-rec').innerHTML = '';
            document.getElementById('detail-reasons').innerHTML = '';
            document.getElementById('detail-stop').innerHTML = '';

            // 先拉实时行情
            try {
                const q = await fetch('/api/quote/' + symbol).then(r => r.json());
                if (q.price) {
                    document.getElementById('detail-price').textContent = q.price.toFixed(2) + ' 元';
                    if (q.change !== undefined) {
                        const cls = q.change >= 0 ? 'up' : 'down';
                        document.getElementById('detail-price').innerHTML =
                            q.price.toFixed(2) + ' 元 <span class="' + cls + '">' +
                            (q.change >= 0 ? '+' : '') + q.change.toFixed(2) + '%</span>';
                    }
                }
            } catch(e) {}

            // 技术分析
            try {
                const resp = await fetch('/api/analyze/' + symbol);
                const d = await resp.json();

                document.getElementById('detail-rsi').textContent = d.rsi != null ? d.rsi.toFixed(1) : '无数据';
                document.getElementById('detail-score').textContent = d.score ?? '—';

                const maText = d.ma_cross === 'golden' ? '✅ 金叉' : d.ma_cross === 'dead' ? '❌ 死叉' : '—';
                document.getElementById('detail-ma').textContent = maText;

                const macdText = d.macd_cross === 'golden' ? '✅ 金叉' : d.macd_cross === 'dead' ? '❌ 死叉' : '—';
                document.getElementById('detail-macd').textContent = macdText;

                const ma20Text = d.price_vs_ma20 === 'above' ? '✅ 线上' : d.price_vs_ma20 === 'below' ? '❌ 线下' : '—';
                document.getElementById('detail-ma20').textContent = ma20Text;

                document.getElementById('detail-vol').textContent =
                    d.vol_ratio != null ? d.vol_ratio + '倍 (' + (d.vol_signal === 'up' ? '放量' : d.vol_signal === 'down' ? '缩量' : '正常') + ')' : '—';

                if (d.recommendation) {
                    document.getElementById('detail-rec').innerHTML =
                        '<span class="badge" style="background:' + (d.rec_color || '#757575') + '">' +
                        d.recommendation + '</span>';
                }

                if (d.reason && d.reason.length) {
                    document.getElementById('detail-reasons').innerHTML =
                        '<strong>信号分析：</strong><ul><li>' + d.reason.join('</li><li>') + '</li></ul>';
                }

                if (d.stop_reason) {
                    document.getElementById('detail-stop').innerHTML = '⚠️ ' + d.stop_reason;
                }
            } catch(e) {
                document.getElementById('detail-rsi').textContent = '分析失败';
            }
        }

        // 双击股票行弹出详情
        document.addEventListener('dblclick', function(e) {
            const row = e.target.closest('tr[data-symbol]');
            if (row) {
                const sym = row.getAttribute('data-symbol');
                const name = row.getAttribute('data-name');
                if (sym) showStockDetail(sym, name || sym);
            }
        });

        async function confirmAddHolding() {
            const symbol = document.getElementById('dlg-symbol').value.trim();
            const name = document.getElementById('dlg-name').value.trim();
            const shares = parseFloat(document.getElementById('dlg-shares').value);
            const cost = parseFloat(document.getElementById('dlg-cost').value);
            if (!symbol || !shares || !cost) { alert('请填写完整信息'); return; }

            try {
                const resp = await fetch('/api/holdings/add', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({symbol, name, shares, cost})
                });
                const data = await resp.json();
                if (data.ok) {
                    closeDialog();
                    loadHoldings();
                    setStatus('✅ 添加成功');
                } else {
                    setStatus('❌ 添加失败');
                }
            } catch(e) {
                setStatus('❌ ' + e.message);
            }
        }

        // ── AI 顾问 ────────────────────────────────────────
        async function loadAiHistory() {
            try {
                const resp = await fetch('/api/ai/history');
                const messages = await resp.json();
                const chat = document.getElementById('ai-chat');
                chat.innerHTML = '';
                for (let i = 0; i < messages.length; i += 2) {
                    const userMsg = messages[i];
                    const aiMsg = messages[i+1];
                    if (userMsg) appendChat(userMsg.content, 'user');
                    if (aiMsg) appendChat(aiMsg.content, 'ai');
                }
            } catch(e) {}
        }

        function appendChat(content, role) {
            const chat = document.getElementById('ai-chat');
            const div = document.createElement('div');
            div.className = 'chat-msg ' + role;
            try {
                // 简单转义 HTML，避免 markdown 符号干扰
                div.textContent = content;
                // AI 消息用轻量级 markdown 转换：**加粗** *斜体*
                if (role === 'ai') {
                    // 先把 textContent 设好，再转 innerHTML
                    let html = content
                        .replace(/&/g, '&amp;')
                        .replace(/</g, '&lt;')
                        .replace(/>/g, '&gt;')
                        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
                        .replace(/\*(.+?)\*/g, '<em>$1</em>')
                        .replace(/\n/g, '<br>');
                    div.innerHTML = html;
                } else {
                    div.textContent = content;
                }
            } catch(e) {
                div.textContent = content;
            }
            chat.appendChild(div);
            chat.scrollTop = chat.scrollHeight;
        }

        async function sendAiMessage() {
            const input = document.getElementById('ai-input');
            const msg = input.value.trim();
            if (!msg) return;
            input.value = '';

            appendChat(msg, 'user');
            showThinking();

            const sendBtn = document.querySelector('.chat-input-row .btn-primary');
            sendBtn.disabled = true;
            try {
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 60000);

                const resp = await fetch('/api/ai/chat', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({message: msg}),
                    signal: controller.signal
                });
                clearTimeout(timeoutId);

                if (!resp.ok) {
                    const errText = await resp.text();
                    throw new Error(errText.slice(0,100));
                }

                const data = await resp.json();

                // 删除思考提示
                hideThinking();

                if (data.reply) appendChat(data.reply, 'ai');
                if (data.trade_results && data.trade_results.length > 0) {
                    data.trade_results.forEach(r => appendChat('📊 ' + r, 'system'));
                    loadHoldings();
                }
            } catch(e) {
                if (e.name === 'AbortError') {
                    appendChat('⏱️ 请求超时，DeepSeek API 响应较慢，请重试', 'system');
                } else {
                    appendChat('❌ ' + e.message, 'system');
                }
            }
            sendBtn.disabled = false;
        }

        async function requestAiAdvice() {
            const msg = '请结合当前市场环境给出今明两天的模拟赛交易建议。';
            document.getElementById('ai-input').value = msg;
            await sendAiMessage();
        }

        async function clearAiChat() {
            if (!confirm('确定要彻底清空 AI 的全部记忆吗？')) return;
            try {
                const resp = await fetch('/api/ai/history/clear', { method: 'POST' });
                if (resp.ok) {
                    document.getElementById('ai-chat').innerHTML = '';
                    appendChat('🧹 AI 记忆已清空', 'system');
                } else {
                    alert('清空记忆失败，请稍后重试');
                }
            } catch(e) {
                alert('清空记忆异常: ' + e.message);
            }
        }

        function openAiTradeDialog() {
            document.getElementById('ai-dlg-symbol').value = '';
            document.getElementById('ai-dlg-shares').value = '';
            document.getElementById('ai-dlg-price').value = '';
            document.getElementById('ai-trade-dialog').style.display = 'flex';
        }

        function submitAiTrade() {
            const action = document.getElementById('ai-dlg-action').value;
            const symbol = document.getElementById('ai-dlg-symbol').value.trim();
            const shares = document.getElementById('ai-dlg-shares').value;
            const price = document.getElementById('ai-dlg-price').value;
            
            if (!symbol || !shares || !price) {
                alert('请填写完整的股票代码、数量和单价！');
                return;
            }
            
            document.getElementById('ai-trade-dialog').style.display = 'none';
            const msg = `记录交易：${action} ${symbol} ${shares}股 单价${price}元`;
            document.getElementById('ai-input').value = msg;
            sendAiMessage();
        }

        // ── AI 思考动画 ────────────────────────────────────
        let _thinkingEl = null;

        function showThinking() {
            const chat = document.getElementById('ai-chat');
            const div = document.createElement('div');
            div.className = 'chat-msg';
            div.style.cssText = 'align-self:flex-start;padding:0;border:none;background:transparent;max-width:100%';
            div.innerHTML = `
                <div class="thinking-indicator">
                    <div class="thinking-dots">
                        <span></span><span></span><span></span>
                    </div>
                    <span>AI 正在回答…</span>
                </div>
            `;
            chat.appendChild(div);
            chat.scrollTop = chat.scrollHeight;
            _thinkingEl = div;
        }

        function hideThinking() {
            if (_thinkingEl) {
                _thinkingEl.remove();
                _thinkingEl = null;
            }
        }

        // ── 设置 ──────────────────────────────────────────
        async function loadSettings() {
            try {
                const resp = await fetch('/api/config');
                const cfg = await resp.json();
                document.getElementById('setting-apikey').value = cfg.api_key || '';
                document.getElementById('setting-total-cash').value = cfg.total_cash || 1000000;
                document.getElementById('setting-avail-cash').value = cfg.avail_cash || cfg.total_cash || 1000000;
            } catch(e) {}
        }

        async function saveSettings() {
            const data = {
                api_key: document.getElementById('setting-apikey').value.trim(),
                total_cash: parseFloat(document.getElementById('setting-total-cash').value) || 1000000,
                avail_cash: parseFloat(document.getElementById('setting-avail-cash').value) || 1000000,
            };
            try {
                await fetch('/api/config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(data)
                });
                setStatus('✅ 设置已保存');
            } catch(e) {
                setStatus('❌ 保存失败: ' + e.message);
            }
        }

        // ── 初始化 ─────────────────────────────────────────
        document.addEventListener('DOMContentLoaded', () => {
            loadUserInfo();
            loadMarketData();
            loadHoldings();
            loadAiHistory();
            loadSettings();
            updateTime();
            setInterval(updateTime, 1000);
            startAutoRefresh();
        });

        async function loadUserInfo() {
            try {
                const resp = await fetch('/api/auth/me');
                const data = await resp.json();
                if (data.logged_in) {
                    document.getElementById('userDisplay').textContent = '👤 ' + data.username;
                }
            } catch(e) {}
        }

        async function doLogout() {
            await fetch('/api/auth/logout', {method: 'POST'});
            window.location.href = '/login';
        }

        function updateTime() {
            const now = new Date();
            document.getElementById('timeDisplay').textContent = now.toLocaleString('zh-CN');
        }

        // ── 股票搜索 ──────────────────────────────────────
        let _searchTimer = null;

        async function searchStocks(q) {
            const dropdown = document.getElementById('search-results');
            if (q.length < 1) { dropdown.style.display = 'none'; return; }

            clearTimeout(_searchTimer);
            _searchTimer = setTimeout(async () => {
                dropdown.innerHTML = '<div class="loading-item">搜索中…</div>';
                dropdown.style.display = 'block';

                try {
                    const resp = await fetch('/api/search?q=' + encodeURIComponent(q));
                    const data = await resp.json();
                    const items = data.results || [];

                    if (items.length === 0) {
                        dropdown.innerHTML = '<div class="loading-item">未找到匹配股票</div>';
                        return;
                    }

                    dropdown.innerHTML = items.map(s =>
                        `<div class="item" onclick="selectSearch('${s.symbol}','${s.name}')">
                            <span class="sym">${s.symbol}</span>
                            <span class="nm">${s.name}</span>
                            <span class="pr ${(s.change||0)>=0?'up':'down'}">${(s.price||0).toFixed(2)}</span>
                            <span class="ch ${(s.change||0)>=0?'up':'down'}">${(s.change||0)>=0?'+':''}${(s.change||0).toFixed(2)}%</span>
                        </div>`
                    ).join('');
                } catch(e) {
                    dropdown.innerHTML = '<div class="loading-item">搜索失败</div>';
                }
            }, 200);
        }

        function doSearch(q) {
            if (!q) q = document.getElementById('search-input').value;
            if (q.length < 3 && !/^\d{6}$/.test(q)) return;
            // 直接搜索
            searchStocks(q);
        }

        function selectSearch(symbol, name) {
            document.getElementById('search-results').style.display = 'none';
            document.getElementById('search-input').value = name || symbol;
            showStockDetail(symbol, name || symbol);
        }

        // 点击页面其他地方关闭搜索下拉
        document.addEventListener('click', function(e) {
            if (!e.target.closest('.search-box')) {
                document.getElementById('search-results').style.display = 'none';
            }
        });

        // ── 自动刷新（交易时段每分钟刷新） ────────────────
        function startAutoRefresh() {
            checkMarketOpen();
            setInterval(checkMarketOpen, 60000);
        }

        function checkMarketOpen() {
            const now = new Date();
            // 转为北京时间 (GMT+8)
            const utc = now.getTime() + now.getTimezoneOffset() * 60000;
            const bj = new Date(utc + 8 * 3600000);
            const day = bj.getDay();       // 0=周日, 1-5=工作日
            const h = bj.getHours();
            const m = bj.getMinutes();
            const minutes = h * 60 + m;

            // 交易时段: 9:30-11:30 (570-690) 或 13:00-15:00 (780-900)
            const isOpen = day >= 1 && day <= 5 && (
                (minutes >= 570 && minutes < 690) ||
                (minutes >= 780 && minutes < 900)
            );

            if (isOpen) {
                autoRefresh();
                document.getElementById('statusText').textContent = '🟢 交易中·自动刷新';
                document.getElementById('marketTimeStatus').textContent = '🟢 交易中';
            } else if (day >= 1 && day <= 5 && minutes >= 570 && minutes < 780) {
                document.getElementById('statusText').textContent = '🟡 午休中·暂停刷新';
                document.getElementById('marketTimeStatus').textContent = '🟡 午休';
            } else if (day >= 1 && day <= 5 && minutes >= 900) {
                document.getElementById('statusText').textContent = '🔴 已收盘';
                document.getElementById('marketTimeStatus').textContent = '🔴 已收盘';
            } else if (day === 6 || day === 0) {
                document.getElementById('statusText').textContent = '🟤 周末休市';
                document.getElementById('marketTimeStatus').textContent = '🟤 休市';
            } else {
                document.getElementById('statusText').textContent = '⚪ 盘前';
                document.getElementById('marketTimeStatus').textContent = '⚪ 盘前';
            }
        }

        let _autoRefreshTimer = null;
        async function autoRefresh() {
            // 不重复刷新
            if (_autoRefreshTimer) return;
            _autoRefreshTimer = setTimeout(() => { _autoRefreshTimer = null; }, 55000);

            try {
                // 静默刷新市场数据（不更新UI状态，只在后台拉）
                const resp = await fetch('/api/market/overview');
                const data = await resp.json();

                // 更新大盘横幅
                if (data.market_status) {
                    renderMarketBanner(data.market_status);
                }
                // 更新涨停池和行业股
                renderTable('zt-table', (data.zt_stocks || []).slice(0, 50),
                    s => [s.symbol || '—', s.name || '—',
                          (s.price || 0).toFixed(2),
                          `<span class="${(s.change||0)>=0?'up':'down'}">${(s.change||0).toFixed(2)}%</span>`]);
                renderTable('sector-table', (data.sector_hot || []).slice(0, 50),
                    s => [s.symbol || '—', s.name || '—',
                          (s.price || 0).toFixed(2),
                          `<span class="${(s.change||0)>=0?'up':'down'}">${(s.change||0).toFixed(2)}%</span>`,
                          s.sector || '—']);
            } catch(e) {
                // 静默失败，不打扰用户
            }
        }
    </script>

    <!-- Live2D: Pixi → CubismCore → Cubism4 → Widget -->
    <script src="/static/live2d/pixi.min.js"></script>
    <script src="/static/live2d/live2dcubismcore.min.js"></script>
    <script>window.process = { env: { NODE_ENV: 'production' }, browser: true };</script>
    <script src="/static/live2d/cubism4.min.js"></script>
    <script src="/static/live2d/widget-light.js"></script>
</body>
</html>
"""


if __name__ == "__main__":
    print("📈 启动模拟比赛辅助工具 Web 版…")
    print(f"  → 打开浏览器访问 http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
