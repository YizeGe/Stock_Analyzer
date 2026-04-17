#!/usr/bin/env python3
"""
同花顺模拟比赛辅助工具
- 持仓管理 + CSV 导入
- 技术指标计算 + 买卖推荐
- 热门股票 + 选股推荐（打开即看）
"""

import os
# 禁用代理，让 akshare 直连（避免公司/VPN 代理拦截）
os.environ["NO_PROXY"]  = "*"
os.environ["no_proxy"] = "*"

# 强制 requests 会话不走代理
import requests
requests.Session.trust_env = False

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import pandas as pd
import csv
import datetime
import json
try:
    from openai import OpenAI
except ImportError:
    pass

# ============================================================
# TODO: 持仓数据接入
# ============================================================
def get_holdings():
    """返回格式: [{"symbol": "000001", "name": "平安银行", "shares": 1000, "cost": 12.50}, ...]"""
    raise NotImplementedError("请使用界面「📂 导入CSV」加载同花顺持仓文件！")


# ============================================================
# CSV 解析
# ============================================================
def parse_ths_csv(filepath):
    col_choices = {
        "symbol": ["股票代码", "代码", "symbol", "证券代码", "code"],
        "name":   ["股票名称", "名称", "name", "证券名称", "证券简称"],
        "shares": ["持仓数量", "持股数量", "数量", "shares", "volume", "持股数"],
        "cost":   ["成本价", "买入成本", "cost", "单价", "cost_price"],
    }
    for enc in ("utf-8-sig", "gbk", "gb2312", "utf-8"):
        try:
            with open(filepath, encoding=enc) as f:
                rows = list(csv.DictReader(f))
                break
        except UnicodeError:
            continue
    else:
        raise ValueError("CSV 编码无法识别，请另存为 UTF-8")

    if not rows:
        raise ValueError("CSV 文件为空")

    sample = rows[0]
    col_map = {}
    for key, candidates in col_choices.items():
        for c in candidates:
            if c in sample:
                col_map[key] = c
                break

    missing = [k for k in ("symbol", "shares", "cost") if k not in col_map]
    if missing:
        raise ValueError(f"未找到必需列：{missing}\n当前列名：{list(sample.keys())}")

    holdings = []
    for i, row in enumerate(rows, 1):
        try:
            sym  = row.get(col_map["symbol"], "").strip().lstrip("'")
            name = row.get(col_map["name"], "").strip() if "name" in col_map else sym
            sh   = float(row.get(col_map["shares"], "").replace(",", "").replace("，", ""))
            cost = float(row.get(col_map["cost"], "").replace(",", "").replace("，", ""))
            if not sym:
                continue
            holdings.append({"symbol": sym, "name": name, "shares": sh, "cost": cost})
        except Exception as e:
            print(f"第{i}行解析失败: {e}")
            continue

    if not holdings:
        raise ValueError("CSV 中未找到有效持仓数据")
    return holdings


# ============================================================
# 数据获取（Sina 实时 + Sina 历史K线，绕过东方财富封锁）
# ============================================================

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn",
})


def _sina_symbol(raw_symbol):
    """转换股票代码为新浪/腾讯格式：000001 -> sz000001，600519 -> sh600519"""
    s = raw_symbol.strip().lstrip("sh").lstrip("sz")
    if s.startswith(("6", "5")):
        return f"sh{s}"    # 上海A股
    elif s.startswith(("0", "3")):
        return f"sz{s}"    # 深圳A股（含创业板、科创板）
    return f"sh{s}"        # 未知默认当沪市


def fetch_realtime_quote(symbol):
    """用腾讯财经接口获取实时行情（akshare股票实时报价接口被封时使用）"""
    # 禁用过慢的全部A股循环拉取（单次调用拿全A股数据太消耗性能且akshare已将'现价'更名为'最新价'导致查不到）
    # 直接使用腾讯财经接口
    # 备选：腾讯财经接口
    try:
        sina_sym = _sina_symbol(symbol)
        url = f"https://qt.gtimg.cn/q={sina_sym}"
        resp = SESSION.get(url, timeout=5)
        resp.encoding = "gbk"
        text = resp.text
        # 格式: v_sh600519="1~名称~代码~现价~...~涨跌幅~..."
        if "=" in text and "~" in text:
            parts = text.split("~")
            if len(parts) > 32:
                try:
                    price = float(parts[3]) if parts[3] else 0.0
                except ValueError:
                    price = 0.0
                try:
                    change = float(parts[32]) if parts[32] else 0.0
                except ValueError:
                    change = 0.0
                return {
                    "symbol": symbol,
                    "name":   parts[1],
                    "price":  price,
                    "change": change,
                }
    except Exception as e:
        print(f"腾讯实时行情失败 {symbol}: {e}")
    return None


def fetch_historical_data(symbol, period="daily", start_date=None, end_date=None):
    """用新浪财经接口获取历史K线"""
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist(symbol=symbol, period=period,
                               start_date=start_date, end_date=end_date, adjust="")
        if df is not None and not df.empty:
            df.columns = [c.lower() for c in df.columns]
            return df
    except Exception:
        pass

    # 备选：新浪财经历史K线 API（英文列名，避免中文列名问题）
    try:
        sina_sym = _sina_symbol(symbol)
        scale_map = {"daily": 240, "weekly": 240, "monthly": 240}
        scale = scale_map.get(period, 240)
        datalen = 60

        url = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php"
               f"/var%20_{sina_sym}=/CN_MarketDataService.getKLineData"
               f"?symbol={sina_sym}&scale={scale}&datalen={datalen}")

        resp = SESSION.get(url, timeout=5)
        text = resp.text
        # 格式: /*...*/\nvar _sh600519=([{"day":"2026-03-25",...}])
        marker = "=("
        idx = text.index(marker)
        json_part = text[idx + len(marker):].rstrip(";").rstrip(")")
        import json
        data = json.loads(json_part)

        if data is None:  # Sina返回null（如涨停股限制）
            return None
        if not data:  # 空数组也返回None
            return None

        df = pd.DataFrame(data)
        # Sina API 列名：day, open, high, low, close, volume
        # 直接用英文列名，兼容 analyze_signal
        df.rename(columns={
            "day":    "date",
            "open":   "open",
            "high":   "high",
            "low":    "low",
            "close":  "close",
            "volume": "volume",
        }, inplace=True)
        df.columns = [c.lower() for c in df.columns]
        # 转数字类型（Sina 返回的是字符串）
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if start_date:
            # 标准化日期比较：Sina 返回 YYYY-MM-DD，过滤字符串须一致
            start_str = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
            df = df[df["date"] >= start_str]
        if end_date:
            end_str = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
            df = df[df["date"] <= end_str]
        return df
    except Exception as e:
        print(f"新浪历史K线失败 {symbol}: {e}")
    return None


# ============================================================
# 技术指标
# ============================================================
def calc_ma(series, window):
    return series.rolling(window=window).mean()

def calc_rsi(series, window=14):
    delta = series.diff()
    gain  = delta.where(delta > 0, 0).rolling(window=window).mean()
    loss  = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs    = gain / loss.replace(0, float('nan'))
    return 100 - (100 / (1 + rs))

def calc_macd(series, fast=12, slow=26, signal=9):
    ema_fast    = series.ewm(span=fast, adjust=False).mean()
    ema_slow    = series.ewm(span=slow, adjust=False).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line

def detect_cross(s1, s2):
    if s1 is None or s2 is None or len(s1) < 2:
        return None
    s1, s2 = s1.dropna(), s2.dropna()
    if len(s1) < 2 or len(s2) < 2:
        return None
    idx = s1.index[-2:]
    p1, c1 = s1.loc[idx].iloc[0], s1.loc[idx].iloc[-1]
    p2, c2 = s2.loc[idx].iloc[0], s2.loc[idx].iloc[-1]
    if p1 <= p2 and c1 > c2:
        return "golden"
    if p1 >= p2 and c1 < c2:
        return "dead"
    return None

def detect_price_cross_ma(price_series, ma_series):
    """价格上穿/下穿 MA"""
    if price_series is None or ma_series is None or len(price_series) < 2:
        return None
    p = price_series.dropna()
    m = ma_series.dropna()
    if len(p) < 2 or len(m) < 2:
        return None
    idx = p.index[-2:]
    pp, pc = p.loc[idx].iloc[0], p.loc[idx].iloc[-1]
    mp, mc = m.loc[idx].iloc[0], m.loc[idx].iloc[-1]
    if pp <= mp and pc > mc:
        return "golden"
    if pp >= mp and pc < mc:
        return "dead"
    return None


def calc_volume_ratio(df):
    """
    计算量比：今日成交量 / 前5日平均成交量。
    返回 (ratio, signal)，ratio>1.3 表示放量，signal='up'/'down'/'normal'
    """
    if df is None or "volume" not in df.columns or len(df) < 6:
        return None, "normal"
    volumes = df["volume"].dropna()
    if len(volumes) < 6:
        return None, "normal"
    # 取最近6天，第一天是今天，其余5天是历史
    today_vol  = float(volumes.iloc[-1])
    hist_vols  = volumes.iloc[-6:-1]
    hist_avg   = hist_vols.mean()
    if hist_avg == 0 or today_vol == 0:
        return None, "normal"
    ratio = today_vol / hist_avg
    if ratio >= 1.3:
        return round(ratio, 2), "up"
    elif ratio <= 0.7:
        return round(ratio, 2), "down"
    return round(ratio, 2), "normal"


# ============================================================
# 大盘环境判断
# ============================================================
def get_market_status():
    """
    获取上证指数当前状态。
    返回: {"name": str, "price": float, "change": float,
           "ma20": float, "above_ma20": bool, "trend": str}
    """
    # 用沪深300指数（1分钟K线不行，用日K）
    # 动态计算起始日期（当前年份，往前取足够多交易日）
    today = datetime.date.today()
    start_year = str(today.year - 1)  # 去年开始，保证有足够数据算MA20
    df = fetch_historical_data("000300", start_date=start_year + "0101")
    if df is None or len(df) < 20:
        # 备选：直接用上证指数代码
        df = fetch_historical_data("000001", start_date=start_year + "0101")

    if df is None or len(df) < 20:
        return None

    close = df["close"].dropna()
    if len(close) < 20:
        return None

    ma20   = calc_ma(close, 20).iloc[-1]
    price  = float(close.iloc[-1])
    change = float(df["close"].iloc[-1] - df["close"].iloc[-2]) / float(df["close"].iloc[-2]) * 100
    above  = price > ma20

    trend = "📈 上升趋势" if above and change > 0 else \
            "📉 下降趋势" if not above and change < 0 else \
            "📊 震荡偏多" if above else "📊 震荡偏空"

    return {
        "name":       "上证指数",
        "price":      round(price, 2),
        "change":     round(change, 2),
        "ma20":       round(ma20, 2),
        "above_ma20": above,
        "trend":      trend,
    }


# ============================================================
# 单股信号分析
# ============================================================
def analyze_signal(symbol, market_above_ma20=None):
    """
    综合技术面分析，给出推荐（含止损参考）。

    参数:
        market_above_ma20: 大盘是否在MA20上方，None则自动获取
    """
    # ── 第一步：拿名字（腾讯实时行情，最快最稳）──────────
    stock_name = None
    try:
        sina_sym = _sina_symbol(symbol)
        url = f"https://qt.gtimg.cn/q={sina_sym}"
        resp = SESSION.get(url, timeout=5)
        resp.encoding = "gbk"
        text = resp.text
        if "=" in text and "~" in text:
            parts = text.split("~")
            if len(parts) > 1 and parts[1].strip():
                stock_name = parts[1].strip()
    except Exception:
        pass

    df = fetch_historical_data(symbol, start_date="20240301")
    if df is None or len(df) < 30:
        return None
    close = df["close"].dropna()
    if len(close) < 30:
        return None

    rsi_series   = calc_rsi(close)
    rsi          = round(rsi_series.iloc[-1], 2) if not rsi_series.isna().all() else None
    ma5          = calc_ma(close, 5)
    ma10         = calc_ma(close, 10)
    ma20         = calc_ma(close, 20)
    macd_line, signal_line = calc_macd(close)

    price    = float(close.iloc[-1])
    ma20_val = ma20.iloc[-1] if not ma20.isna().iloc[-1] else None

    ma_cross       = detect_cross(ma5, ma10)
    macd_cross    = detect_cross(macd_line, signal_line)
    price_cross20 = detect_price_cross_ma(close, ma20)
    vol_ratio, vol_signal = calc_volume_ratio(df)

    # ── 止损参考 ─────────────────────────────────────
    recent_low   = float(df["close"].iloc[-5:].min())  # 近5日最低
    stop_loss    = round(recent_low * 0.97, 2)          # 跌破近5日最低或-3%
    stop_pct     = round((price - stop_loss) / price * 100, 1)
    stop_reason  = f"止损：{stop_loss}（-{stop_pct}%）"

    score   = 0
    reasons = []

    # ── 1. RSI ───────────────────────────────────────
    if rsi is not None:
        if rsi < 30:
            # 改进：超卖须有信号确认，否则可能是"接飞刀"
            confirmed = vol_signal == "up" or (ma20_val and price > ma20_val) or ma_cross == "golden"
            if confirmed:
                score += 1; reasons.append(f"RSI={rsi} 超卖+确认")
            else:
                reasons.append(f"RSI={rsi} 超卖（无确认，谨慎）")
        elif rsi > 70:
            score -= 1; reasons.append(f"RSI={rsi} > 70 超买")
        else:
            reasons.append(f"RSI={rsi} 中性区间")

    # ── 2. MA 交叉 ───────────────────────────────────
    if ma_cross == "golden":
        score += 1; reasons.append("MA5 上穿 MA10 金叉")
    elif ma_cross == "dead":
        score -= 1; reasons.append("MA5 下穿 MA10 死叉")

    # ── 3. 价格 vs MA20 ──────────────────────────────
    if ma20_val is not None:
        if price > ma20_val:
            score += 1; reasons.append(f"价格>{ma20_val:.2f} MA20 多头")
        else:
            score -= 1; reasons.append(f"价格<{ma20_val:.2f} MA20 空头")

    # ── 4. MACD 交叉 ────────────────────────────────
    if macd_cross == "golden":
        score += 1; reasons.append("MACD 金叉")
    elif macd_cross == "dead":
        score -= 1; reasons.append("MACD 死叉")

    # ── 5. 放量站稳MA20 ─────────────────────────────
    if vol_ratio is not None and ma20_val is not None:
        if vol_signal == "up" and price > ma20_val:
            score += 1; reasons.append(f"放量{vol_ratio}倍站稳MA20")
        elif vol_signal == "up":
            reasons.append(f"放量{vol_ratio}倍（未站上MA20）")
        elif vol_signal == "down":
            reasons.append(f"缩量{vol_ratio}倍，观望")

    # ── 6. 大盘乘数 ─────────────────────────────────
    if market_above_ma20 is None:
        mkt = get_market_status()
        market_above_ma20 = mkt["above_ma20"] if mkt else True

    raw_score = score
    if not bool(market_above_ma20) and score > 0:
        score = round(score * 0.7, 1)
        reasons.append(f"大盘弱，信号乘0.7（{raw_score}→{score}）")
        stop_reason += "（大盘弱，建议轻仓）"

    # ── 推荐等级（阈值式，兼容浮点分数）───────────
    thresholds = [
        (4,   "⭐ 强烈买入",  "#1565c0"),
        (3,   "✅ 买入",      "#2e7d32"),
        (2,   "🟡 谨慎买入",  "#f9a825"),
        (1,   "🟡 谨慎买入",  "#f9a825"),
        (0,   "⚪ 观望",      "#757575"),
        (-1,  "🟠 谨慎卖出",  "#ef6c00"),
        (-2,  "❌ 卖出",      "#c62828"),
        (-999,"🔥 强烈卖出",  "#b71c1c"),
    ]
    recommendation, rec_color = "⚪ 观望", "#757575"  # 默认值
    for threshold, rec, clr in thresholds:
        if score >= threshold:
            recommendation, rec_color = rec, clr
            break

    return dict(
        symbol=symbol, name=stock_name or symbol, price=price, rsi=rsi,
        ma_cross=ma_cross, macd_cross=macd_cross, price_cross20=price_cross20,
        price_vs_ma20=("above" if price > ma20_val else "below") if ma20_val else None,
        vol_ratio=vol_ratio, vol_signal=vol_signal,
        score=score, raw_score=raw_score,
        recommendation=recommendation, rec_color=rec_color,
        reason=reasons, stop_loss=stop_loss, stop_loss_pct=stop_pct,
        stop_reason=stop_reason,
    )



# ============================================================
# 市场数据获取（热门股票）
# ============================================================
def get_hot_stocks(limit=50):
    """
    综合选股池：涨停股池 + 各行业强势代表股。
    返回 {"zt_stocks": [...], "sector_hot": [...]}
    """
    import akshare as ak
    import requests
    zt_stocks   = []
    sector_hot  = []

    # ── 1. 东方财富涨停池 ─────────────────────────────
    try:
        today = datetime.date.today()
        # 往前尝试最近 10 天，解决周一/周末等假日导致数据为空问题
        for i in range(10):
            d_str = (today - datetime.timedelta(days=i)).strftime("%Y%m%d")
            try:
                df = ak.stock_zt_pool_em(date=d_str)
                if df is not None and not df.empty:
                    for _, row in df.iterrows():
                        sym = str(row.get("代码", "")).strip()
                        if len(sym) == 6:
                            # 最新价 字段在某些旧数据里可能会缺失或者变动
                            price = row.get("最新价", row.get("收盘价", 0))
                            zt_stocks.append({
                                "symbol": sym,
                                "name":   str(row.get("名称", sym)),
                                "price":  float(price) if price else 0.0,
                                "change": float(row.get("涨跌幅", 0)),
                            })
                    break  # 拿到了数据就退出循环
            except Exception as e:
                # 这一天出错（周末无数据等），继续尝试前一天
                continue
    except Exception as e:
        print(f"涨停池获取失败: {e}")

    # ── 2. 各行业代表性股票（腾讯批量接口，一次请求搞定）──────
    # 选取成交活跃的各行业代表股，不依赖 akshare
    SECTOR_LEADERS = [
        # 金融
        ("银行",   "sh600000"), ("银行",   "sh600036"), ("银行",   "sh601166"),
        ("保险",   "sh601318"), ("券商",   "sh600837"), ("券商",   "sh601211"),
        # 消费
        ("白酒",   "sh600519"), ("白酒",   "sz000568"), ("食品",   "sh603288"),
        ("家电",   "sh600690"), ("免税",   "sh601888"),
        ("零售",   "sh600859"), ("旅游",   "sh600054"),
        # 汽车 / 新能源车
        ("新能源车","sz002594"), ("新能源车","sh600418"), ("汽车零部件","sh600741"),
        # 医药
        ("创新药", "sh600276"), ("CXO",    "sh603259"), ("医疗器械","sz002007"),
        ("中药",   "sh600557"), ("疫苗",   "sz300015"),
        # 科技 / 半导体
        ("半导体", "sh688981"), ("半导体", "sh600584"), ("消费电子","sz000100"),
        ("苹果链", "sz002475"), ("算力",   "sh000977"), ("AI应用", "sh600745"),
        ("软件",   "sh600570"), ("游戏",   "sh603444"), ("影视",   "sz300133"),
        # 新能源 / 电力设备
        ("光伏",   "sh601012"), ("锂电",   "sh600150"),
        ("储能",   "sh600478"), ("风电",   "sh601615"),
        # 电力 / 公用事业
        ("电力",   "sh600900"), ("电力",   "sh600011"), ("核电",   "sh601985"),
        ("燃气",   "sh600903"),
        # 周期
        ("煤炭",   "sh600188"), ("钢铁",   "sh600019"), ("水泥",   "sh600801"),
        ("化工",   "sh600309"), ("石化",   "sh600028"), ("有色",   "sh600259"),
        ("黄金",   "sh601899"), ("稀土",   "sh600111"),
        # 基建 / 地产
        ("基建",   "sh601668"), ("地产",   "sz000002"), ("工程机械","sh601100"),
        # 交通运输
        ("航运",   "sh601919"), ("航空",   "sh600115"), ("物流",   "sh600233"),
        ("港口",   "sh601018"),
        # 军工 / 通信
        ("军工",   "sh600893"), ("通信设备","sh000063"),
        ("运营商", "sh601728"), ("光纤",   "sh600487"),
        # 农业 / 环保
        ("农业",   "sh600598"), ("种子",   "sh601998"), ("环保",   "sh601330"),
        # 热点题材
        ("机器人","sz300024"), ("数据要素","sh600536"), ("跨境电商","sz002491"),
    ]
    try:
        codes = ",".join(s[1] for s in SECTOR_LEADERS)
        url = f"https://qt.gtimg.cn/q={codes}"
        resp = SESSION.get(url, timeout=8)
        resp.encoding = "gbk"
        lines = resp.text.strip().split("\n")
        for i, line in enumerate(lines):
            if "=" not in line:
                continue
            sym_raw = line.split("=")[0].strip().split("_")[-1]
            parts = line.split("~")
            if len(parts) < 32:
                continue
            sym  = sym_raw[2:] if len(sym_raw) == 8 else ""
            name = parts[1].strip()
            price  = float(parts[3]) if parts[3] else 0
            change = float(parts[32]) if parts[32] else 0
            sector_name = SECTOR_LEADERS[i][0] if i < len(SECTOR_LEADERS) else "其他"
            sector_hot.append({
                "sector":  sector_name,
                "avg_chg": change,
                "symbol":  sym,
                "name":    name,
                "price":   price,
                "change":  change,
            })
    except Exception as e:
        print(f"行业代表股获取失败: {e}")

    # 排序并去重（去掉已在涨停池的）
    sector_hot.sort(key=lambda x: x["avg_chg"], reverse=True)
    zt_syms = {s["symbol"] for s in zt_stocks}
    sector_hot = [s for s in sector_hot if s["symbol"] not in zt_syms][:limit]

    return {"zt_stocks": zt_stocks, "sector_hot": sector_hot}


def get_sina_ranking_stocks(category="change", limit=25):
    """
    从新浪财经排行获取股票列表。
    category: change(涨跌幅) / volume(成交额) / turnover(换手率) / high(新高) / low(新低)
    返回 [{"symbol": "600000", "name": "平安银行", ...}, ...]
    """
    import json
    # Sina 排行榜 API
    rank_map = {
        "change":   ("rank_change",   "涨跌幅排行"),
        "volume":   ("rank_amount",   "成交额排行"),
        "turnover": ("rank_hs300",   "换手率排行"),
        "high":     ("rank_high52",  "52周新高"),
        "low":      ("rank_low52",   "52周新低"),
    }
    kind, label = rank_map.get(category, ("rank_change", "涨跌幅排行"))
    url = f"https://vip.stock.finance.sina.com.cn/q/view/{kind}.js"
    try:
        resp = SESSION.get(url, timeout=6)
        resp.encoding = "gbk"
        text = resp.text
        # 格式: var hq_str_rank_xxx="symbol^name^price^...^change%^..."
        marker = '="'
        idx = text.index(marker) + len(marker)
        raw = text[idx:].strip().rstrip('";')
        if not raw:
            return []
        lines = raw.split(";")
        results = []
        for line in lines[:limit]:
            parts = line.split("^")
            if len(parts) < 4:
                continue
            sym = parts[0].strip()
            if len(sym) != 6:
                continue
            # 去掉 sh/sz 前缀
            sym = sym[2:] if sym.startswith(("sh", "sz")) else sym
            name = parts[1].strip() if len(parts) > 1 else sym
            try:
                price = float(parts[3]) if parts[3] else 0
                change = float(parts[32]) if len(parts) > 32 and parts[32] else 0
            except (ValueError, IndexError):
                price, change = 0, 0
            results.append({"symbol": sym, "name": name,
                            "price": price, "change": change,
                            "source": label})
        return results
    except Exception as e:
        print(f"新浪排行获取失败({category}): {e}")
        return []


def get_broad_stock_pool(limit_per_cat=20):
    """
    获取更广泛的选股池（各行业代表性 + 当日真实成交活跃榜）。
    使用腾讯批量接口实时获取行情，不依赖 akshare。
    """
    dynamic_list = []
    try:
        import requests
        # 获取全市场成交额最大的前60名（改用新浪API，规避东方财富被本地代理阻断的问题）
        url = 'http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page=1&num=60&sort=amount&asc=0&node=hs_a&symbol=&_s_r_a=page'
        resp = requests.get(url, timeout=5)
        # 返回的是含有 symbol 字段的 dict array
        data = resp.json()
        for v in data:
            sym = v.get('symbol')
            if sym:
                dynamic_list.append(sym)
    except Exception as e:
        print("获取真实活跃榜失败:", e)

    # 各行业代表性股票（基础底座：成交活跃、有流动性的行业龙头）
    WATCH_LIST = [
        "sh600000", "sh600036", "sh600016", "sh601166", "sh601318", "sh601398", # 金融
        "sh600519", "sh600104", "sh600690", "sh600887", "sz000568", "sh603288", # 消费
        "sh600150", "sh601012", "sh600900", "sh600703", "sh600276", "sh688981", # 科技
        "sz000001", "sz000002", "sz000333", "sz000651", "sz002594", "sz002415", # 深市
        "sh601100", "sh600862", "sh600585", "sh601668", "sh601919", "sh600309", # 周期
        "sz000661", "sz002007", "sz300760", "sz300015", "sh603259",             # 医药
        "sh600745", "sh603444", "sz000977", "sz000063", "sz002230", "sh688256", # TMT
    ]

    # 合并并去重，这样就兼顾了长期的龙头底座和每天最新的成交活跃大牛股
    final_watch_list = list(set(WATCH_LIST + dynamic_list))

    all_stocks = []
    seen = set()

    # 分批从腾讯批量拉行情（每批80个，Tencent 上限约200）
    batch_size = 80
    for i in range(0, len(final_watch_list), batch_size):
        batch = final_watch_list[i:i+batch_size]
        codes = ",".join(batch)
        try:
            url = f"https://qt.gtimg.cn/q={codes}"
            resp = SESSION.get(url, timeout=8)
            resp.encoding = "gbk"
            lines = resp.text.strip().split("\n")
            for line in lines:
                if "=" not in line:
                    continue
                sym_raw = line.split("=")[0].strip().split("_")[-1]
                parts = line.split("~")
                if len(parts) < 10:
                    continue
                sym  = sym_raw[2:] if len(sym_raw) == 8 and sym_raw[:2] in ("sh","sz") else ""
                name = parts[1].strip()
                price  = float(parts[3]) if parts[3] else 0
                change = float(parts[32]) if len(parts) > 32 and parts[32] else 0
                if not sym or sym in seen:
                    continue
                seen.add(sym)
                all_stocks.append({"symbol": sym, "name": name,
                                   "price": price, "change": change,
                                   "source": "关注池"})
        except Exception as e:
            print(f"关注股批量获取失败: {e}")
            continue

    return all_stocks


def get_index_components(index_code="000300", limit=30):
    """获取指数成分股（默认沪深300）"""
    try:
        import akshare as ak
        df = ak.stock_index_zh_a_hist(adjust="", start_date="20240101")
        # 取沪深300成分股（简化：直接用成交额前排的大盘股）
        df_spot = ak.stock_zh_a_spot_em()
        if df_spot is not None:
            # 过滤沪深A股，选取成交额最大的
            df_spot = df_spot[df_spot["代码"].str.startswith(("6", "0", "3"))]
            df_spot = df_spot.sort_values("成交额", ascending=False)
            return df_spot.head(limit).apply(
                lambda r: {"symbol": str(r["代码"]).strip(),
                           "name":   str(r["名称"]),}, axis=1).tolist()
    except Exception as e:
        print(f"成分股获取失败: {e}")
    return []


def get_recommended_stocks(symbols, market_above_ma20=None, max_count=20,
                           min_watch_score=0.3, name_map=None):
    """
    对给定股票列表跑策略筛选，返回有分析结果的股票。
    返回: (推荐股票[score>=1], ZT池[无数据], 关注股票[min_watch_score<=score<1])
    """
    if name_map is None:
        name_map = {}
    analyzed   = []   # 有K线数据且score>=1
    zt_pool   = []   # ZT/K线暂无数据
    watch_pool = []  # 中等分数，值得关注
    for sym in symbols:
        res = analyze_signal(sym, market_above_ma20=market_above_ma20)
        score = res.get("score", 0) if res else 0
        if res and score >= 1:
            if not res.get("name"):
                res["name"] = name_map.get(sym, sym)
            if not res.get("name"):
                res["name"] = sym
            analyzed.append(res)
        elif res is None:
            zt_pool.append({
                "symbol": sym,
                "name": name_map.get(sym, sym),
                "score": 0,
                "recommendation": "✅ 涨停待分析", "rec_color": "#2e7d32",
                "reason": ["涨停股（K线数据暂缺）"],
                "stop_reason": "需结合大盘环境判断",
                "rsi": None, "vol_ratio": None,
            })
        elif res and min_watch_score <= score < 1:
            # 中等分，关注但不推荐
            if not res.get("name"):
                res["name"] = name_map.get(sym, sym)
            if not res.get("name"):
                res["name"] = sym
            res["recommendation"] = "👀 关注"
            res["rec_color"] = "#1976d2"
            watch_pool.append(res)

    analyzed.sort(key=lambda x: x["score"], reverse=True)
    watch_pool.sort(key=lambda x: x["score"], reverse=True)
    # 有数据的优先，最多显示max_count只；ZT池单独保存供GUI使用
    result = analyzed[:max_count]
    return result, zt_pool, watch_pool


# ============================================================
# GUI
# ============================================================
class StockAnalyzerApp:
    def __init__(self, root):
        self.root   = root
        self.root.title("📈 模拟比赛辅助工具")
        self.root.geometry("1400x850") # 稍微拉大一点给AI展示
        
        # 个人数据统一存放在 userdata/ 目录（已被 .gitignore 忽略）
        self.userdata_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "userdata")
        os.makedirs(self.userdata_dir, exist_ok=True)
        self.config_path = os.path.join(self.userdata_dir, "config.json")
        self.holdings_path = os.path.join(self.userdata_dir, "my_holdings.json")
        self.trade_history_path = os.path.join(self.userdata_dir, "trade_history.json")
        self.ai_history_path = os.path.join(self.userdata_dir, "ai_history.json")
        self.config = self.load_config()
        self.stocks = self.load_local_holdings()
        self.ai_messages = self.load_ai_history()
        # 初始化真实可用现金（如果缺失，用总资金减去当前持仓成本来推算）
        if "avail_cash" not in self.config:
            total_cost = sum(s.get("cost", 0) * s.get("shares", 0) for s in self.stocks)
            self.config["avail_cash"] = self.config.get("total_cash", 1000000.0) - total_cost
            self.save_config()

        self.analysis_results = {}
        self.csv_path = None
        self.hot_stocks = []      # 热门股票
        self.sector_stocks = []   # 行业代表股
        self.recommended = []      # 推荐股票
        self.market_status = None  # 大盘状态
        self.zt_stocks = []    # ZT暂无数据股票池
        self.broad_stocks = []  # 更广泛选股池（新高/新低/高换手等）
        self.watch_stocks = []  # 关注观察（中等分数）
        self._market_loading = False  # 防止重复加载
        self.setup_tabs()

    def load_config(self):
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"api_key": "", "total_cash": 1000000.0}

    def save_config(self):
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("保存配置失败:", e)

    def load_local_holdings(self):
        try:
            with open(self.holdings_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def save_local_holdings(self):
        try:
            with open(self.holdings_path, "w", encoding="utf-8") as f:
                json.dump(self.stocks, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("保存持仓失败:", e)

    def load_trade_history(self):
        try:
            with open(self.trade_history_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def load_ai_history(self):
        try:
            with open(self.ai_history_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def save_ai_history(self):
        try:
            with open(self.ai_history_path, "w", encoding="utf-8") as f:
                json.dump(self.ai_messages[-30:], f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("保存AI对话记录失败:", e)

    def save_trade_history(self, history):
        try:
            with open(self.trade_history_path, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("保存交易流水失败:", e)

    def record_trade(self, action, symbol, name, price, shares, cost_price=0.0):
        """记录一笔买卖流水，并更新可用现金"""
        import datetime
        history = self.load_trade_history()
        amount = price * shares
        if action == "BUY":
            pnl = 0.0  # 买入时尚未实现盈亏
            self.config["avail_cash"] = self.config.get("avail_cash", 0) - amount
        elif action == "SELL":
            pnl = (price - cost_price) * shares
            self.config["avail_cash"] = self.config.get("avail_cash", 0) + amount
        else:
            pnl = 0.0
        self.save_config()
        record = {
            "time":    datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "action":  "买入" if action == "BUY" else "卖出",
            "symbol":  symbol,
            "name":    name,
            "price":   round(price, 3),
            "shares":  int(shares),
            "amount":  round(amount, 2),
            "pnl":     round(pnl, 2),
        }
        history.insert(0, record)  # 最新记录放最前面
        self.save_trade_history(history)

    # ── Tab 布局 ───────────────────────────────────────────
    def setup_tabs(self):
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=10, pady=(10, 0))

        # Tab1: 市场概览（打开默认显示）
        self.tab_market = ttk.Frame(self.nb, padding=5)
        self.nb.add(self.tab_market, text="🌐 市场概览")
        self.setup_market_tab()

        # Tab2: 持仓分析
        self.tab_holdings = ttk.Frame(self.nb, padding=5)
        self.nb.add(self.tab_holdings, text="💼 持仓分析")
        self.setup_holdings_tab()

        # Tab3: AI 顾问
        self.tab_ai = ttk.Frame(self.nb, padding=5)
        self.nb.add(self.tab_ai, text="🤖 AI 顾问")
        self.setup_ai_tab()

        # 底部状态栏
        self.status = tk.StringVar(value="就绪")
        tk.Label(self.root, textvariable=self.status, bd=1, relief="sunken",
                 font=("Courier", 9), anchor="w", padx=10, pady=4).pack(fill="x")

        # 打开时自动加载市场数据
        self.nb.bind("<<NotebookTabChanged>>", self.on_tab_changed)

        # 首次打开自动触发市场数据加载
        self.root.after(500, self.load_market_data)

    def on_tab_changed(self, event):
        idx = self.nb.index(self.nb.select())
        if idx == 0 and not self.hot_stocks and not self._market_loading:
            self.load_market_data()

    # ── Tab1: 市场概览 ─────────────────────────────────────
    def setup_market_tab(self):
        # 工具栏
        toolbar = ttk.Frame(self.tab_market)
        toolbar.pack(fill="x", pady=(0, 5))
        ttk.Label(toolbar, text="🌐 市场概览", font=("Arial", 15, "bold")).pack(side="left")
        ttk.Button(toolbar, text="🔄 刷新热门股", command=self.load_market_data).pack(side="right", padx=4)
        ttk.Button(toolbar, text="🧠 搜索推荐",   command=self.run_stock_picker).pack(side="right", padx=4)
        ttk.Button(toolbar, text="📤 导出推荐",   command=self.export_recommended_csv).pack(side="right", padx=4)

        # ── 大盘状态横幅 ───────────────────────────────────
        self.market_frame = tk.Frame(self.tab_market, bg="#f0f0f0", height=52)
        self.market_frame.pack(fill="x", pady=(0, 6))
        self.market_frame.pack_propagate(False)

        self.market_label = tk.Label(
            self.market_frame, text="⏳ 正在加载大盘数据…",
            font=("Arial", 11, "bold"), bg="#f0f0f0", fg="#555", anchor="w"
        )
        self.market_label.pack(fill="both", expand=True, padx=12, pady=8)

        paned = ttk.PanedWindow(self.tab_market, orient="horizontal")
        paned.pack(fill="both", expand=True)

        # 左：行业代表股
        left_frame = ttk.LabelFrame(paned, text="🏭 行业代表股（各行业强势股）", padding=5)
        paned.add(left_frame, weight=1)

        cols = ["代码", "名称", "现价", "涨跌幅", "行业"]
        self.sector_tree = ttk.Treeview(left_frame, columns=cols, show="headings", height=30)
        widths = {"代码":80, "名称":80, "现价":70, "涨跌幅":80, "行业":90}
        for c in cols:
            self.sector_tree.heading(c, text=c)
            self.sector_tree.column(c, width=widths[c], anchor="center")
        sb = ttk.Scrollbar(left_frame, orient="vertical", command=self.sector_tree.yview)
        self.sector_tree.configure(yscrollcommand=sb.set)
        self.sector_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.sector_tree.bind("<Double-1>", self.on_sector_double_click)

        # 中：今日涨停
        mid_frame = ttk.LabelFrame(paned, text="🔥 今日涨停", padding=5)
        paned.add(mid_frame, weight=1)

        cols_m = ["代码", "名称", "现价", "涨跌幅", "推荐"]
        self.hot_tree = ttk.Treeview(mid_frame, columns=cols_m, show="headings", height=30)
        widths_m = {"代码":80, "名称":85, "现价":75, "涨跌幅":85, "推荐":110}
        for c in cols_m:
            self.hot_tree.heading(c, text=c)
            self.hot_tree.column(c, width=widths_m[c], anchor="center")
        sb_m = ttk.Scrollbar(mid_frame, orient="vertical", command=self.hot_tree.yview)
        self.hot_tree.configure(yscrollcommand=sb_m.set)
        self.hot_tree.pack(side="left", fill="both", expand=True)
        sb_m.pack(side="right", fill="y")
        self.hot_tree.bind("<Double-1>", self.on_hot_double_click)

        # 右：技术面推荐
        right_frame = ttk.LabelFrame(paned, text="⭐ 技术面推荐", padding=5)
        paned.add(right_frame, weight=1)

        cols2 = ["代码", "名称", "推荐", "RSI", "信号", "止损"]
        self.rec_tree = ttk.Treeview(right_frame, columns=cols2, show="headings", height=25)
        widths2 = {"代码":80, "名称":75, "推荐":100, "RSI":55, "信号":120, "止损":145}
        for c in cols2:
            self.rec_tree.heading(c, text=c)
            self.rec_tree.column(c, width=widths2[c], anchor="center")
        sb2 = ttk.Scrollbar(right_frame, orient="vertical", command=self.rec_tree.yview)
        self.rec_tree.configure(yscrollcommand=sb2.set)
        self.rec_tree.pack(side="left", fill="both", expand=True)
        sb2.pack(side="right", fill="y")
        self.rec_tree.bind("<Double-1>", self.on_rec_double_click)

        # 说明标签
        note = tk.Label(self.tab_market,
                       text="📌 推荐逻辑：RSI<30(超卖)+1 | RSI>70(超买)-1 | RSI在40-60区间+配合信号+1 | MA金叉+1 | MACD金叉+1 | 价格>MA20+1 | 放量上涨+1 | 2分以上触发推荐",
                       font=("Arial", 8), fg="#666", anchor="w")
        note.pack(fill="x", pady=(4, 0))

    def load_market_data(self):
        if self._market_loading:
            return
        self._market_loading = True
        self.status.set("正在加载热门股票，请稍候…")
        self.market_label.config(text="⏳ 正在加载大盘数据，请稍候…")

        def go():
            # 先获取大盘状态
            mkt = get_market_status()
            self.market_status = mkt
            self.root.after(0, lambda: self._update_market_banner(mkt))

            pools = get_hot_stocks(limit=60)
            zt_list     = pools.get("zt_stocks", [])
            sector_list = pools.get("sector_hot", [])

            self.hot_stocks    = zt_list
            self.sector_stocks = sector_list

            self.root.after(0, self._update_hot_tree)
            self.root.after(0, self._update_sector_tree)

            self.root.after(0, lambda: self.status.set("数据加载完毕，正在筛选推荐股票…"))

            # 补充更广泛的选股池（新高/新低/高换手/高成交等）
            self.root.after(0, lambda: self.status.set(
                "正在补充选股池（新高/新低/换手率等）…"))
            broad_stocks = get_broad_stock_pool(limit_per_cat=20)
            self.broad_stocks = broad_stocks

            all_stocks = zt_list + sector_list + broad_stocks
            # 去重（保留先出现的，即热门优先）
            seen = set()
            deduped = []
            for s in all_stocks:
                if s["symbol"] not in seen:
                    seen.add(s["symbol"])
                    deduped.append(s)
            all_stocks = deduped

            symbols = [s["symbol"] for s in all_stocks]
            name_map = {s["symbol"]: s.get("name", s["symbol"]) for s in all_stocks}
            mkt_above = bool(self.market_status["above_ma20"]) if self.market_status else True
            rec, zt_pool, watch_pool = get_recommended_stocks(
                symbols, market_above_ma20=mkt_above,
                max_count=20, min_watch_score=0.3, name_map=name_map)
            self.zt_stocks = zt_pool
            self.watch_stocks = watch_pool
            self.recommended = rec
            self.root.after(0, self._update_rec_tree)

            self.root.after(0, lambda: self.status.set(
                f"行业代表:{len(sector_list)}只 涨停:{len(zt_list)}只 "
                f"关注池:{len(broad_stocks)}只 推荐:{len(rec)}只 观察:{len(watch_pool)}只"))
            self._market_loading = False

        threading.Thread(target=go, daemon=True).start()

    def _update_market_banner(self, mkt):
        """更新大盘状态横幅"""
        if mkt is None:
            self.market_label.config(
                text="⚠️ 大盘数据获取失败（网络问题），选股信号仍可参考",
                bg="#fff3cd", fg="#856404"
            )
            self.market_frame.config(bg="#fff3cd")
            return

        change_str = f"{mkt['change']:+.2f}%"
        bg_color   = "#d4edda" if mkt["change"] >= 0 else "#f8d7da"
        fg_color   = "#155724" if mkt["change"] >= 0 else "#721c24"
        ma_color   = "#155724" if mkt["above_ma20"] else "#721c24"

        text = (
            f"大盘状态：{mkt['trend']}　"
            f"上证 {mkt['price']}（{change_str}）　"
            f"MA20={mkt['ma20']}　"
            f"当前位置：{'✅ 20日线上方' if mkt['above_ma20'] else '❌ 20日线下方'}　"
            f"→ {'可积极选股' if mkt['above_ma20'] else '谨慎操作，降低仓位'}"
        )
        self.market_label.config(text=text, bg=bg_color, fg=fg_color)
        self.market_frame.config(bg=bg_color)

    def _update_hot_tree(self):
        for item in self.hot_tree.get_children():
            self.hot_tree.delete(item)
        stocks = self.hot_stocks if isinstance(self.hot_stocks, list) else []
        if not stocks:
            self.hot_tree.insert("", "end", values=("—", "（无数据）", "—", "—", "—"))
            return
        for s in stocks:
            if not isinstance(s, dict):
                continue
            tag = "up" if s.get("change", 0) >= 0 else "down"
            self.hot_tree.insert("", "end", values=(
                s.get("symbol", ""), s.get("name", ""),
                f"{s.get('price', 0):.2f}", f"{s.get('change', 0):+.2f}%", "—",
            ), tags=(tag,))
        self.hot_tree.tag_configure("up",   foreground="#d32f2f")
        self.hot_tree.tag_configure("down", foreground="#388e3c")

    def on_sector_double_click(self, event=None):
        sel = self.sector_tree.selection()
        if not sel:
            return
        vals = self.sector_tree.item(sel[0])["values"]
        self.show_detail_window(vals[0], vals[1])


    def _update_sector_tree(self):
        """更新行业代表股列表"""
        for item in self.sector_tree.get_children():
            self.sector_tree.delete(item)
        stocks = self.sector_stocks if isinstance(self.sector_stocks, list) else []
        if not stocks:
            self.sector_tree.insert("", "end", values=("—", "（无数据）", "—", "—", "—"))
            return
        for s in stocks:
            if not isinstance(s, dict):
                continue
            tag = "up" if s.get("change", 0) >= 0 else "down"
            self.sector_tree.insert("", "end", values=(
                s.get("symbol", ""), s.get("name", ""),
                f"{s.get('price', 0):.2f}",
                f"{s.get('change', 0):+.2f}%",
                s.get("sector", ""),
            ), tags=(tag,))
        self.sector_tree.tag_configure("up",   foreground="#d32f2f")
        self.sector_tree.tag_configure("down", foreground="#388e3c")


    def _update_rec_tree(self):
        for item in self.rec_tree.get_children():
            self.rec_tree.delete(item)

        # Section 1: 有分析数据的推荐股票
        zt_stocks = getattr(self, 'zt_stocks', [])
        if self.recommended:
            # Section header
            self.rec_tree.insert("", "end", values=(
                "━━━", "技术面推荐（已分析）", "━", "━", "━", "━",
            ), tags=("header",))
            self.rec_tree.tag_configure("header", foreground="#888888", font=("Arial", 8))

            for r in self.recommended:
                sig_parts = r.get("reason", [])
                sig = sig_parts[0] if sig_parts else ""
                stop = r.get("stop_reason", "")
                self.rec_tree.insert("", "end", values=(
                    r.get("symbol", ""), r.get("name", ""),
                    r.get("recommendation", "—"),
                    f"{r.get('rsi', 0):.1f}" if r.get("rsi") else "—",
                    sig[:28] + ("…" if len(sig) > 28 else ""),
                    stop[:20] + ("…" if len(stop) > 20 else ""),
                ))

        # Section 2: ZT暂无数据
        if zt_stocks:
            self.rec_tree.insert("", "end", values=(
                "━━━", f"涨停待分析（{len(zt_stocks)}只）", "━", "━", "━", "━",
            ), tags=("header",))
            for r in zt_stocks[:20]:  # 最多显示20只
                self.rec_tree.insert("", "end", values=(
                    r.get("symbol", ""), r.get("name", ""),
                    r.get("recommendation", "—"), "—", "涨停板", "K线暂无",
                ))

        # Section 3: 关注股票（中等分数，值得观察）
        watch_stocks = getattr(self, 'watch_stocks', [])
        if watch_stocks:
            self.rec_tree.insert("", "end", values=(
                "━━━", f"👀 关注观察（{len(watch_stocks)}只）", "━", "━", "━", "━",
            ), tags=("header",))
            for r in watch_stocks[:20]:
                sig_parts = r.get("reason", [])
                sig = sig_parts[0] if sig_parts else ""
                stop = r.get("stop_reason", "")
                self.rec_tree.insert("", "end", values=(
                    r.get("symbol", ""), r.get("name", ""),
                    r.get("recommendation", "👀 关注"),
                    f"{r.get('rsi', 0):.1f}" if r.get("rsi") else "—",
                    sig[:28] + ("…" if len(sig) > 28 else ""),
                    stop[:20] + ("…" if len(stop) > 20 else ""),
                ))

        if not self.recommended and not zt_stocks:
            self.rec_tree.insert("", "end", values=("—", "（无数据）", "—", "—", "—", "—"))

    def run_stock_picker(self):
        """手动触发全市场选股"""
        self.status.set("正在从热门股中筛选推荐标的…")
        self.load_market_data()

    def on_hot_double_click(self, event):
        sel = self.hot_tree.selection()
        if not sel:
            return
        vals = self.hot_tree.item(sel[0])["values"]
        self.show_detail_window(vals[0], vals[1])

    def on_rec_double_click(self, event):
        sel = self.rec_tree.selection()
        if not sel:
            return
        vals = self.rec_tree.item(sel[0])["values"]
        self.show_detail_window(vals[0], vals[1])

    # ── Tab2: 持仓分析 ─────────────────────────────────────
    def setup_holdings_tab(self):
        toolbar = ttk.Frame(self.tab_holdings)
        toolbar.pack(fill="x", pady=(0, 5))
        ttk.Label(toolbar, text="💼 持仓分析", font=("Arial", 15, "bold")).pack(side="left")
        ttk.Button(toolbar, text="📂 导入CSV",   command=self.import_csv).pack(side="right", padx=4)
        ttk.Button(toolbar, text="🔄 刷新行情",  command=self.refresh_all).pack(side="right", padx=4)
        ttk.Button(toolbar, text="🧠 分析推荐",  command=self.run_analysis).pack(side="right", padx=4)
        ttk.Button(toolbar, text="➕ 添加",      command=self.add_holding_dialog).pack(side="right", padx=4)
        ttk.Button(toolbar, text="📝 修改选中",  command=self.edit_holding_dialog).pack(side="right", padx=4)
        ttk.Button(toolbar, text="🛒 卖出选中",  command=self.sell_holding_dialog).pack(side="right", padx=4)
        ttk.Button(toolbar, text="📜 交易流水",  command=self.show_trade_history).pack(side="right", padx=4)
        ttk.Button(toolbar, text="💰 校准现金",  command=self.edit_cash_dialog).pack(side="right", padx=4)
        ttk.Button(toolbar, text="🗑 清空",      command=self.clear_holdings).pack(side="right", padx=4)

        self.csv_hint = tk.StringVar(value="未加载 CSV，请点击「📂 导入CSV」从同花顺导入持仓")
        tk.Label(toolbar, textvariable=self.csv_hint, font=("Arial", 8),
                 fg="#888", anchor="w").place(relx=0, rely=1.0, relw=1.0, y=4)

        frm = ttk.Frame(self.tab_holdings)
        frm.pack(fill="both", expand=True, pady=(30, 0))

        cols = ["代码", "名称", "数量", "成本", "现价", "涨跌幅", "盈亏额", "盈亏率", "推荐"]
        w    = {"代码":80,"名称":95,"数量":65,"成本":72,"现价":72,
                "涨跌幅":80,"盈亏额":90,"盈亏率":80,"推荐":115}
        self.tree = ttk.Treeview(frm, columns=cols, show="headings", height=13)
        for c in cols:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w[c], anchor="center")
        sb = ttk.Scrollbar(frm, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.on_holding_double_click)
        
        # 启动时如果有本地数据，自动填充表格
        self.root.after(100, self._update_holdings_table)

    # ── 持仓操作 ──────────────────────────────────────────
    def import_csv(self):
        path = filedialog.askopenfilename(
            title="选择同花顺导出的持仓 CSV",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
            initialdir=self.csv_path or os.path.expanduser("~/Desktop"),
        )
        if not path:
            return
        self.csv_path = path
        try:
            holdings = parse_ths_csv(path)
        except Exception as e:
            messagebox.showerror("CSV 解析错误", str(e))
            return
        self.stocks = holdings
        self.save_local_holdings()
        self.analysis_results.clear()
        self.csv_hint.set(f"已加载：{os.path.basename(path)}（{len(holdings)} 只股票）")
        self.status.set("加载成功，请点击「🔄 刷新行情」")
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.refresh_ai_summary()

    def clear_holdings(self):
        if not self.stocks:
            messagebox.showinfo("提示", "当前没有持仓")
            return
        holding_cost = sum(s.get("cost", 0) * s.get("shares", 0) for s in self.stocks)
        choice = messagebox.askyesnocancel(
            "清空持仓",
            f"当前持仓成本合计 {holding_cost:,.2f} 元。\n\n"
            f"是否将持仓成本退还到可用现金？\n"
            f"• [是] 退还现金（相当于全部按成本价卖出）\n"
            f"• [否] 仅清空持仓，不动现金\n"
            f"• [取消] 不清空"
        )
        if choice is None:  # 取消
            return
        if choice:  # 是 → 退还现金
            self.config["avail_cash"] = self.config.get("avail_cash", 0) + holding_cost
            self.save_config()
        self.stocks.clear()
        self.save_local_holdings()
        self.analysis_results.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.csv_hint.set("已清空，请点击「📂 导入CSV」重新加载")
        self.refresh_ai_summary()
        self.status.set("就绪")

    def load_holdings(self):
        if self.stocks:
            return self.stocks
        try:
            return get_holdings() or []
        except NotImplementedError:
            return []

    def refresh_all(self):
        if not self.stocks:
            messagebox.showinfo("提示", "请先点击「📂 导入CSV」加载持仓")
            return
        self.status.set("正在获取实时行情…")

        def go():
            for s in self.stocks:
                q = fetch_realtime_quote(s["symbol"])
                if q:
                    s["price"]  = q.get("price", 0)
                    s["change"] = q.get("change", 0)
                    # 如果股票名称为空或者是股票代码本身，自动补充真实的名称
                    if not s.get("name") or s.get("name") == s.get("symbol"):
                        s["name"] = q.get("name", s.get("name"))
                else:
                    s.setdefault("price", 0)
                    s.setdefault("change", 0)
            
            # 保存到本地，持久化修正后的名称
            self.root.after(0, self.save_local_holdings)
            
            self.root.after(0, self._update_holdings_table)
            self.root.after(0, self.refresh_ai_summary)
            self.root.after(0, lambda: self.status.set(f"行情刷新完成，共 {len(self.stocks)} 只"))

        threading.Thread(target=go, daemon=True).start()

    def _update_holdings_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for s in self.stocks:
            p   = s.get("price", 0)
            c   = s.get("cost", 0)
            sh  = s.get("shares", 0)
            chg = s.get("change", 0)
            profit = (p - c) * sh
            rate   = (p - c) / c * 100 if c else 0
            res    = self.analysis_results.get(s["symbol"], {})
            rec    = res.get("recommendation", "—")
            tag    = "up" if profit >= 0 else "down"
            self.tree.insert("", "end", values=(
                s.get("symbol", ""), s.get("name", ""),
                f"{sh:.0f}", f"{c:.2f}", f"{p:.2f}",
                f"{chg:+.2f}%", f"{profit:+,.2f}", f"{rate:+.2f}%", rec,
            ), tags=tag)
        self.tree.tag_configure("up",   foreground="#d32f2f")
        self.tree.tag_configure("down", foreground="#388e3c")

    def run_analysis(self):
        if not self.stocks:
            messagebox.showinfo("提示", "请先导入持仓并刷新行情")
            return
        self.status.set("正在分析持仓，请稍候…")

        def go():
            results = {}
            for s in self.stocks:
                sym = s["symbol"]
                self.root.after(0, lambda sym=sym: self.status.set(f"分析 {sym}…"))
                mkt_above = self.market_status["above_ma20"] if self.market_status else True
                results[sym] = analyze_signal(sym, market_above_ma20=mkt_above) or {}
            self.analysis_results = results
            self.root.after(0, self._update_holdings_table)
            self.root.after(0, lambda: self.status.set("分析完成！"))

        threading.Thread(target=go, daemon=True).start()

    def on_holding_double_click(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        vals = self.tree.item(sel[0])["values"]
        sym = str(vals[0])
        if sym.isdigit() and len(sym) < 6:
            sym = sym.zfill(6)
        self.show_detail_window(sym, vals[1])

    # ── 详情窗口（通用）─────────────────────────────────────
    def show_detail_window(self, symbol, name):
        win = tk.Toplevel(self.root)
        win.title(f"技术分析 · {name} ({symbol})")
        win.geometry("750x620")

        tk.Label(win, text=f"{name} ({symbol})", font=("Arial", 15, "bold")).pack(pady=8)

        res = self.analysis_results.get(symbol, {})
        rec = res.get("recommendation", "—")
        rec_color_map = {
            "⭐ 强烈买入":"#1565c0","✅ 买入":"#2e7d32","🟡 谨慎买入":"#f9a825",
            "⚪ 观望":"#757575","🟠 谨慎卖出":"#ef6c00","❌ 卖出":"#c62828","🔥 强烈卖出":"#b71c1c",
        }
        bg = rec_color_map.get(rec, "#eeeeee")
        rf = tk.Frame(win, bg=bg, height=50)
        rf.pack(fill="x", padx=10, pady=(0, 10))
        rf.pack_propagate(False)
        tk.Label(rf, text=f"推荐：{rec}", font=("Arial", 14, "bold"), bg=bg, fg="white").pack(pady=10)

        reason_frame = ttk.LabelFrame(win, text="指标信号 + 止损参考", padding=10)
        reason_frame.pack(fill="x", padx=10, pady=(0, 10))
        for r in res.get("reason", []):
            color = "#2e7d32" if any(k in r for k in ["✓", "多头", "金叉", "超卖"]) else \
                    "#c62828" if any(k in r for k in ["✗", "空头", "死叉", "超买"]) else "#555555"
            tk.Label(reason_frame, text=f"• {r}", font=("Courier", 10), fg=color, anchor="w").pack(anchor="w", pady=2)
        stop = res.get("stop_reason", "")
        if stop:
            tk.Label(reason_frame, text=f"🛑 {stop}", font=("Arial", 10, "bold"),
                     fg="#c62828", anchor="w").pack(anchor="w", pady=2)

        tk.Label(win, text="近20日K线与技术指标", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        tf = ttk.Frame(win)
        tf.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        txt = tk.Text(tf, font=("Courier", 9), height=22, wrap="none")
        yscb = tk.Scrollbar(tf, orient="vertical", command=txt.yview)
        xscb = tk.Scrollbar(tf, orient="horizontal", command=txt.xview)
        txt.configure(yscrollcommand=yscb.set, xscrollcommand=xscb.set)
        txt.grid(row=0, column=0, sticky="nsew")
        yscb.grid(row=0, column=1, sticky="ns")
        xscb.grid(row=1, column=0, sticky="ew")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)

        def load():
            df = fetch_historical_data(symbol, start_date="20240101")
            if df is None:
                txt.insert("end", "数据加载失败\n")
                return
            # 统一列名（akshare用中文，Sina用英文）
            if "日期" in df.columns:
                df.rename(columns={"日期": "date"}, inplace=True)
            df["ma5"]   = calc_ma(df["close"], 5)
            df["ma10"]  = calc_ma(df["close"], 10)
            df["ma20"]  = calc_ma(df["close"], 20)
            df["rsi"]   = calc_rsi(df["close"])
            macd, sig   = calc_macd(df["close"])
            df["macd"]  = macd
            df["signal"]= sig
            recent = df.tail(20).iloc[::-1]
            hdr = (f"{'日期':<12}{'收盘':>8}{'涨跌%':>9}{'MA5':>10}{'MA10':>10}"
                   f"{'MA20':>10}{'RSI':>8}{'MACD':>10}{'信号线':>10}")
            lines = [hdr, "-" * 87]
            for i, (_, row) in enumerate(recent.iterrows()):
                dt = str(row.get("date", ""))[:10]
                cl = float(row.get("close", 0))
                # 计算涨跌幅：当前行与下一行（更早一天）的收盘价比较
                if i + 1 < len(recent):
                    prev_close = float(recent.iloc[i + 1]["close"])
                    pct = (cl - prev_close) / prev_close * 100 if prev_close else 0
                else:
                    pct = 0
                lines.append(
                    f"{dt:<12}{cl:>8.2f}{pct:>+8.2f}%"
                    f"{row.get('ma5', 0):>10.2f}{row.get('ma10', 0):>10.2f}"
                    f"{row.get('ma20', 0):>10.2f}{row.get('rsi', 0):>8.2f}"
                    f"{row.get('macd', 0):>10.4f}{row.get('signal', 0):>10.4f}"
                )
            txt.insert("end", "\n".join(lines))
            txt.config(state="disabled")

        threading.Thread(target=load, daemon=True).start()

    # ── 添加持仓弹窗 ────────────────────────────────────────
    def add_holding_dialog(self):
        d = tk.Toplevel(self.root)
        d.title("添加持仓")
        d.geometry("360x230")
        d.transient(self.root)
        ents = {}
        for i, key in enumerate(["代码", "名称", "持有数量", "成本价"]):
            tk.Label(d, text=key+":", width=10).grid(row=i, column=0, padx=10, pady=9, sticky="e")
            e = tk.Entry(d, width=22)
            e.grid(row=i, column=1, padx=10, pady=9)
            ents[key] = e

        def add():
            try:
                sym  = ents["代码"].get().strip()
                name = ents["名称"].get().strip()
                sh   = float(ents["持有数量"].get())
                cost = float(ents["成本价"].get())
                if not sym:
                    raise ValueError("代码不能为空")
                self.stocks.append({"symbol": sym, "name": name, "shares": sh, "cost": cost})
                # 记录一笔「初始导入/手动添加」的资金扣除
                avail_cash = self.config.get("avail_cash", self.config.get("total_cash", 1000000.0))
                # 保持账本资产守恒，扣除购买成本
                self.config["avail_cash"] = avail_cash - (sh * cost)
                self.save_config()
                self.save_local_holdings()
                d.destroy()
                self.refresh_all()
            except ValueError as e:
                messagebox.showerror("输入错误", e)

        f = ttk.Frame(d)
        f.grid(row=4, column=0, columnspan=2, pady=12)
        ttk.Button(f, text="添加", command=add).pack(side="left", padx=8)
        ttk.Button(f, text="取消", command=d.destroy).pack(side="left")

    # ── 卖出选中弹窗 ───────────────────────────────────────
    def sell_holding_dialog(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在表格中点选一只股票")
            return
        vals = self.tree.item(sel[0])["values"]
        sym  = str(vals[0])
        if sym.isdigit() and len(sym) < 6:
            sym = sym.zfill(6)
        stock = next((s for s in self.stocks if str(s["symbol"]) == sym), None)
        if not stock:
            messagebox.showerror("错误", "找不到该持仓数据")
            return

        d = tk.Toplevel(self.root)
        d.title(f"卖出 {stock.get('name', sym)} ({sym})")
        d.geometry("360x220")
        d.transient(self.root)
        d.grab_set()

        max_shares = int(stock.get("shares", 0))
        cost_price = stock.get("cost", 0)
        cur_price  = stock.get("price", cost_price)

        fields = [
            ("卖出数量",  str(max_shares)),
            ("卖出单价",  str(round(cur_price, 3))),
        ]
        ents = {}
        for i, (label, default) in enumerate(fields):
            tk.Label(d, text=label + ":", width=12).grid(row=i, column=0, padx=12, pady=10, sticky="e")
            e = tk.Entry(d, width=20)
            e.insert(0, default)
            e.grid(row=i, column=1, padx=12, pady=10)
            ents[label] = e

        tk.Label(d, text=f"（可用数量: {max_shares} 股，成本价: {cost_price:.3f}）",
                 font=("Arial", 8), fg="#888").grid(row=2, column=0, columnspan=2, pady=2)

        def do_sell():
            try:
                sh = float(ents["卖出数量"].get())
                pr = float(ents["卖出单价"].get())
                if sh <= 0 or pr <= 0:
                    raise ValueError("数量和单价必须大于 0")
                if sh > max_shares:
                    raise ValueError(f"卖出数量不能超过持有数量 {max_shares} 股")
            except ValueError as e:
                messagebox.showerror("输入错误", str(e))
                return

            # 计算本次盈亏
            pnl = (pr - cost_price) * sh
            pnl_str = f"+{pnl:,.2f}" if pnl >= 0 else f"{pnl:,.2f}"

            confirm = messagebox.askyesno(
                "确认卖出",
                f"卖出 {stock.get('name', sym)} ({sym})\n"
                f"数量: {int(sh)} 股  @ {pr:.3f}\n"
                f"成交额: {pr*sh:,.2f} 元\n"
                f"预计盈亏: {pnl_str} 元\n"
                f"确认卖出？"
            )
            if not confirm:
                return

            # 更新持仓
            if sh >= stock["shares"]:
                self.stocks.remove(stock)
            else:
                stock["shares"] -= sh

            # 记录流水 + 更新 avail_cash
            self.record_trade("SELL", sym, stock.get("name", sym), pr, sh, cost_price)
            self.save_local_holdings()
            self.refresh_ai_summary()
            self._update_holdings_table()
            d.destroy()
            messagebox.showinfo("卖出成功",
                f"已记录卖出 {stock.get('name', sym)} {int(sh)} 股 @ {pr:.3f}\n"
                f"实现盈亏: {pnl_str} 元")

        f = ttk.Frame(d)
        f.grid(row=3, column=0, columnspan=2, pady=14)
        ttk.Button(f, text="✅ 确认卖出", command=do_sell).pack(side="left", padx=8)
        ttk.Button(f, text="取消", command=d.destroy).pack(side="left")

    # ── 修改选中弹窗 ───────────────────────────────────────
    def edit_holding_dialog(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在表格中点选一只股票")
            return
        vals = self.tree.item(sel[0])["values"]
        sym  = str(vals[0])
        if sym.isdigit() and len(sym) < 6:
            sym = sym.zfill(6)
        stock = next((s for s in self.stocks if str(s["symbol"]) == sym), None)
        if not stock:
            messagebox.showerror("错误", "找不到该持仓数据")
            return

        d = tk.Toplevel(self.root)
        d.title(f"修改持仓 {stock.get('name', sym)} ({sym})")
        d.geometry("360x250")
        d.transient(self.root)
        d.grab_set()

        fields = [
            ("名称",   stock.get("name", "")),
            ("持有数量", str(int(stock.get("shares", 0)))),
            ("成本价",  str(round(stock.get("cost", 0), 3))),
        ]
        ents = {}
        for i, (label, default) in enumerate(fields):
            tk.Label(d, text=label + ":", width=12).grid(row=i, column=0, padx=12, pady=10, sticky="e")
            e = tk.Entry(d, width=20)
            e.insert(0, default)
            e.grid(row=i, column=1, padx=12, pady=10)
            ents[label] = e

        def do_edit():
            try:
                name  = ents["名称"].get().strip()
                sh    = float(ents["持有数量"].get())
                cost  = float(ents["成本价"].get())
                if sh <= 0 or cost <= 0:
                    raise ValueError("数量和成本价必须大于 0")
            except ValueError as e:
                messagebox.showerror("输入错误", str(e))
                return

            # 计算持仓成本差异，同步调整可用现金
            old_total_cost = stock.get("cost", 0) * stock.get("shares", 0)
            new_total_cost = cost * sh
            cash_delta = old_total_cost - new_total_cost  # 减仓则释放现金，加仓则扣除
            if abs(cash_delta) > 0.01:
                self.config["avail_cash"] = self.config.get("avail_cash", 0) + cash_delta
                self.save_config()

            stock["name"]   = name
            stock["shares"] = sh
            stock["cost"]   = cost
            self.save_local_holdings()
            self._update_holdings_table()
            self.refresh_ai_summary()
            d.destroy()

        f = ttk.Frame(d)
        f.grid(row=3, column=0, columnspan=2, pady=14)
        ttk.Button(f, text="✅ 保存修改", command=do_edit).pack(side="left", padx=8)
        ttk.Button(f, text="取消", command=d.destroy).pack(side="left")

    # ── 校准现金弹窗 ───────────────────────────────────────
    def edit_cash_dialog(self):
        avail = self.config.get("avail_cash", 0)
        total = self.config.get("total_cash", 100000.0)
        holding_cost = sum(s.get("cost", 0) * s.get("shares", 0) for s in self.stocks)
        holding_value = sum((s.get("price") or s.get("cost", 0)) * s.get("shares", 0) for s in self.stocks)

        d = tk.Toplevel(self.root)
        d.title("💰 校准可用现金")
        d.geometry("420x320")
        d.transient(self.root)
        d.grab_set()

        # 当前状态展示
        info_frame = ttk.LabelFrame(d, text="当前资金状态", padding=10)
        info_frame.pack(fill="x", padx=15, pady=(15, 5))

        info_lines = [
            f"初始总资金：  {total:,.2f} 元",
            f"系统记录现金：{avail:,.2f} 元",
            f"持仓成本合计：{holding_cost:,.2f} 元",
            f"持仓市值合计：{holding_value:,.2f} 元",
            f"系统总资产：  {avail + holding_value:,.2f} 元",
        ]
        for line in info_lines:
            tk.Label(info_frame, text=line, font=("Courier", 10), anchor="w").pack(anchor="w", pady=1)

        # 编辑区
        edit_frame = ttk.LabelFrame(d, text="手动校准", padding=10)
        edit_frame.pack(fill="x", padx=15, pady=10)

        ttk.Label(edit_frame, text="实际可用现金:").grid(row=0, column=0, sticky="e", padx=5, pady=8)
        cash_var = tk.DoubleVar(value=round(avail, 2))
        cash_entry = tk.Entry(edit_frame, textvariable=cash_var, width=18, font=("Arial", 12))
        cash_entry.grid(row=0, column=1, padx=5, pady=8)
        cash_entry.select_range(0, "end")
        cash_entry.focus_set()

        def do_save():
            new_cash = cash_var.get()
            old_cash = self.config.get("avail_cash", 0)
            self.config["avail_cash"] = new_cash
            self.save_config()
            # 同步 AI tab 的 avail_cash_var（如果已初始化）
            if hasattr(self, "avail_cash_var"):
                self.avail_cash_var.set(new_cash)
            self.refresh_ai_summary()
            d.destroy()
            messagebox.showinfo("校准成功",
                f"可用现金已从 {old_cash:,.2f} 校准为 {new_cash:,.2f} 元")

        def do_recalc():
            """根据 初始资金 - 当前持仓成本 重新推算"""
            recalc = total - holding_cost
            cash_var.set(round(recalc, 2))

        btn_frame = ttk.Frame(d)
        btn_frame.pack(fill="x", padx=15, pady=(0, 12))
        ttk.Button(btn_frame, text="🔄 按持仓成本重算", command=do_recalc).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="✅ 保存", command=do_save).pack(side="right", padx=5)
        ttk.Button(btn_frame, text="取消", command=d.destroy).pack(side="right", padx=5)

    # ── 交易流水弹窗 ───────────────────────────────────────
    def show_trade_history(self):
        history = self.load_trade_history()

        win = tk.Toplevel(self.root)
        win.title("📜 历史交易流水")
        win.geometry("860x480")
        win.grab_set()

        frm = ttk.Frame(win)
        frm.pack(fill="both", expand=True, padx=10, pady=10)

        cols = ["时间", "操作", "代码", "名称", "单价", "数量", "成交额", "盈亏"]
        widths = {"时间":140, "操作":55, "代码":70, "名称":90, "单价":75, "数量":60, "成交额":100, "盈亏":100}
        tree = ttk.Treeview(frm, columns=cols, show="headings", height=18)
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=widths[c], anchor="center")
        sb = ttk.Scrollbar(frm, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        tree.tag_configure("buy",  foreground="#1565c0")
        tree.tag_configure("sell_profit", foreground="#c62828")
        tree.tag_configure("sell_loss",   foreground="#388e3c")

        if not history:
            tree.insert("", "end", values=("—","—","暂无记录，请先完成一笔买卖","—","—","—","—","—"))
        else:
            total_pnl = 0.0
            for r in history:
                pnl = r.get("pnl", 0)
                total_pnl += pnl
                tag = "buy" if r["action"] == "买入" else ("sell_profit" if pnl >= 0 else "sell_loss")
                pnl_str = (f"+{pnl:,.2f}" if pnl > 0 else f"{pnl:,.2f}") if r["action"] == "卖出" else "—"
                tree.insert("", "end", values=(
                    r.get("time",""),
                    r.get("action",""),
                    r.get("symbol",""),
                    r.get("name",""),
                    f"{r.get('price',0):.3f}",
                    r.get("shares",""),
                    f"{r.get('amount',0):,.2f}",
                    pnl_str,
                ), tags=(tag,))

            total_str = (f"+{total_pnl:,.2f}" if total_pnl >= 0 else f"{total_pnl:,.2f}")
            color = "#c62828" if total_pnl >= 0 else "#388e3c"
            tk.Label(win, text=f"累计已实现盈亏：{total_str} 元",
                     font=("Arial", 12, "bold"), fg=color).pack(pady=(0, 8))

        btn_frame = ttk.Frame(win)
        btn_frame.pack(fill="x", padx=10, pady=(0, 8))

        def export_history():
            if not history:
                return
            path = self._get_export_path("交易流水_导出.csv")
            if not path:
                return
            import csv as csv_mod
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv_mod.DictWriter(f, fieldnames=["time","action","symbol","name","price","shares","amount","pnl"])
                writer.writeheader()
                writer.writerows(history)
            messagebox.showinfo("导出成功", f"已保存至：{path}")

        ttk.Button(btn_frame, text="📤 导出 CSV", command=export_history).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="关闭", command=win.destroy).pack(side="right", padx=5)

    # ── 导出功能 ────────────────────────────────────────
    def _get_export_path(self, default_name):
        """弹出保存对话框，返回用户选择的路径"""
        path = filedialog.asksaveasfilename(
            title="保存 CSV 文件",
            defaultextension=".csv",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
            initialfile=default_name,
            initialdir=os.path.expanduser("~/Desktop"),
        )
        return path

    def export_recommended_csv(self):
        """导出推荐股票列表（含关注股票）为 CSV"""
        if not self.recommended and not getattr(self, 'watch_stocks', []):
            messagebox.showinfo("提示", "暂无推荐股票数据，请先点击「🧠 搜索推荐」")
            return
        path = self._get_export_path("推荐股票_导出.csv")
        if not path:
            return
        try:
            rows = []
            for r in self.recommended:
                rows.append({
                    "代码":       r.get("symbol", ""),
                    "名称":       r.get("name", ""),
                    "推荐":       r.get("recommendation", ""),
                    "评分":       r.get("score", ""),
                    "RSI":        r.get("rsi", ""),
                    "信号":       " | ".join(r.get("reason", [])),
                    "止损参考":   r.get("stop_reason", ""),
                    "量比":       r.get("vol_ratio", ""),
                    "MA状态":     r.get("price_vs_ma20", ""),
                })
            for r in getattr(self, 'watch_stocks', []):
                rows.append({
                    "代码":       r.get("symbol", ""),
                    "名称":       r.get("name", ""),
                    "推荐":       r.get("recommendation", ""),
                    "评分":       r.get("score", ""),
                    "RSI":        r.get("rsi", ""),
                    "信号":       " | ".join(r.get("reason", [])),
                    "止损参考":   r.get("stop_reason", ""),
                    "量比":       r.get("vol_ratio", ""),
                    "MA状态":     r.get("price_vs_ma20", ""),
                })
            df = pd.DataFrame(rows)
            df.to_csv(path, index=False, encoding="utf-8-sig")
            messagebox.showinfo("导出成功", "已保存至：\n" + path)
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def export_holdings_csv(self):
        """导出持仓分析为 CSV（含盈亏和分析）"""
        if not self.stocks:
            messagebox.showinfo("提示", "暂无持仓数据，请先导入 CSV")
            return
        path = self._get_export_path("持仓分析_导出.csv")
        if not path:
            return
        try:
            rows = []
            for s in self.stocks:
                p    = s.get("price", 0)
                c    = s.get("cost", 0)
                sh   = s.get("shares", 0)
                chg  = s.get("change", 0)
                profit  = (p - c) * sh
                rate    = (p - c) / c * 100 if c else 0
                res  = self.analysis_results.get(s["symbol"], {})
                rec  = res.get("recommendation", "—")
                rows.append({
                    "代码":       s.get("symbol", ""),
                    "名称":       s.get("name", ""),
                    "持有数量":   sh,
                    "成本价":     c,
                    "现价":       p,
                    "涨跌幅(%)": chg,
                    "盈亏额":     round(profit, 2),
                    "盈亏率(%)": round(rate, 2),
                    "推荐":       rec,
                    "技术评分":   res.get("score", ""),
                    "RSI":        res.get("rsi", ""),
                    "信号":       " | ".join(res.get("reason", [])),
                    "止损参考":   res.get("stop_reason", ""),
                })
            df = pd.DataFrame(rows)
            df.to_csv(path, index=False, encoding="utf-8-sig")
            messagebox.showinfo("导出成功", "已保存至：\n" + path)
        except Exception as e:
            messagebox.showerror("导出失败", str(e))


    # ── Tab3: AI 顾问 ──────────────────────────────────────────
    def setup_ai_tab(self):
        # 顶部配置区
        top_frame = ttk.LabelFrame(self.tab_ai, text="⚙️ AI 配置与资金核算", padding=10)
        top_frame.pack(fill="x", padx=10, pady=5)
        
        ttk.Label(top_frame, text="DeepSeek API Key:").grid(row=0, column=0, sticky="e", padx=5)
        self.api_key_var = tk.StringVar(value=self.config.get("api_key", ""))
        tk.Entry(top_frame, textvariable=self.api_key_var, width=50, show="*").grid(row=0, column=1, padx=5)
        
        ttk.Label(top_frame, text="初始总资金:").grid(row=1, column=0, sticky="e", padx=5, pady=5)
        self.total_cash_var = tk.DoubleVar(value=self.config.get("total_cash", 1000000.0))
        tk.Entry(top_frame, textvariable=self.total_cash_var, width=15).grid(row=1, column=1, sticky="w", padx=5, pady=5)
        
        ttk.Label(top_frame, text="可用现金:").grid(row=2, column=0, sticky="e", padx=5, pady=5)
        self.avail_cash_var = tk.DoubleVar(value=self.config.get("avail_cash", 0))
        tk.Entry(top_frame, textvariable=self.avail_cash_var, width=15).grid(row=2, column=1, sticky="w", padx=5, pady=5)
        
        def save_cfg():
            self.config["api_key"] = self.api_key_var.get().strip()
            self.config["total_cash"] = self.total_cash_var.get()
            self.config["avail_cash"] = self.avail_cash_var.get()
            self.save_config()
            self.refresh_ai_summary()
            messagebox.showinfo("成功", "配置已保存（含可用现金校准）")
        ttk.Button(top_frame, text="保存配置", command=save_cfg).grid(row=0, column=2, rowspan=3, padx=15)
        
        # 资金面板
        self.fund_summary_var = tk.StringVar(value="资金加载中...")
        tk.Label(top_frame, textvariable=self.fund_summary_var, font=("Arial", 11, "bold"), fg="#1565c0").grid(row=3, column=0, columnspan=3, pady=5, sticky="w", padx=5)
        
        # 聊天显示区
        chat_frame = ttk.LabelFrame(self.tab_ai, text="💬 交易决策台", padding=10)
        chat_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        self.chat_text = tk.Text(chat_frame, font=("Courier", 11), wrap="word")
        sb = ttk.Scrollbar(chat_frame, orient="vertical", command=self.chat_text.yview)
        self.chat_text.configure(yscrollcommand=sb.set)
        self.chat_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.chat_text.tag_config("user", foreground="#0d47a1")
        self.chat_text.tag_config("ai", foreground="#2e7d32")
        self.chat_text.tag_config("system", foreground="#ed6c02")
        self.chat_text.insert("end", "【系统】输入操作指令（例如：以10元买入100股600519）或者直接咨询操作建议。\n", "system")
        self.chat_text.config(state="disabled")
        
        # 恢复历史聊天
        for m in self.ai_messages:
            if m["role"] == "user":
                self.append_chat(f"👤 我/系统请求: {m['content']}", "user")
            elif m["role"] == "assistant":
                self.append_chat(f"🤖 AI: {m['content']}\n", "ai")
        
        # 向下发送区
        input_frame = ttk.Frame(self.tab_ai)
        input_frame.pack(fill="x", padx=10, pady=5)
        self.chat_entry = tk.Entry(input_frame, font=("Arial", 12))
        self.chat_entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.chat_entry.bind("<Return>", lambda e: self.send_ai_msg())
        ttk.Button(input_frame, text="发送指令", command=self.send_ai_msg).pack(side="right", padx=5)
        
        # 底部快捷键
        btn_frame = ttk.Frame(self.tab_ai)
        btn_frame.pack(fill="x", padx=10, pady=5)
        ttk.Button(btn_frame, text="💡 请求操作建议 (系统持仓与推荐注入)", command=self.request_ai_advice).pack(side="left", padx=5)
        
        def save_chat():
            txt = self.chat_text.get("1.0", "end")
            path = self._get_export_path("AI交易建议记录.txt")
            if path:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(txt)
                messagebox.showinfo("成功", f"文本已保存至 {path}")
        ttk.Button(btn_frame, text="💾 保存对话记录", command=save_chat).pack(side="right", padx=5)

        def clear_chat():
            from tkinter import messagebox
            if messagebox.askyesno("确认清空", "确定要清空并重置所有 AI 通讯和记忆吗？"):
                self.ai_messages = []
                self.save_ai_history()
                self.chat_text.config(state="normal")
                self.chat_text.delete("1.0", "end")
                self.chat_text.insert("end", "【系统】对话记忆已完全清空，可以直接输入新的查询。\n", "system")
                self.chat_text.config(state="disabled")
        ttk.Button(btn_frame, text="🗑️ 清空对话", command=clear_chat).pack(side="right", padx=5)
        
        self.root.after(100, self.refresh_ai_summary)

    def refresh_ai_summary(self):
        avail_cash = self.config.get("avail_cash", self.config.get("total_cash", 1000000.0))
        # 总市值 = 持仓股票现值（若有实时价则用现价，否则用成本）
        market_value = sum(
            (s.get("price") or s.get("cost", 0)) * s.get("shares", 0)
            for s in self.stocks
        )
        total_assets = avail_cash + market_value
        total_init   = self.config.get("total_cash", 1000000.0)
        total_pnl    = total_assets - total_init
        pnl_str = (f"+{total_pnl:,.2f}" if total_pnl >= 0 else f"{total_pnl:,.2f}")
        self.fund_summary_var.set(
            f"📊 初始资金: {total_init:,.2f}  "
            f"| 💰 可用现金: {avail_cash:,.2f}  "
            f"| 📈 持仓市值: {market_value:,.2f}  "
            f"| 🏦 总资产: {total_assets:,.2f}  "
            f"| {'🟢' if total_pnl >= 0 else '🔴'} 总盈亏: {pnl_str}"
        )
        # 同步 AI 配置面板中的可用现金数值
        if hasattr(self, "avail_cash_var"):
            self.avail_cash_var.set(round(avail_cash, 2))

    def append_chat(self, msg, tag):
        self.chat_text.config(state="normal")
        self.chat_text.insert("end", msg + "\n", tag)
        self.chat_text.see("end")
        self.chat_text.config(state="disabled")

    def _call_deepseek(self, messages):
        if not self.config.get("api_key"):
            self.root.after(0, lambda: self.append_chat("【系统】请先在上方配置 DeepSeek API Key。", "system"))
            return None
        try:
            from openai import OpenAI
        except ImportError:
            self.root.after(0, lambda: self.append_chat("【系统】缺少库，请运行 pip install openai。", "system"))
            return None
            
        client = OpenAI(api_key=self.config["api_key"], base_url="https://api.deepseek.com", timeout=30.0)
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=messages,
                response_format={"type": "json_object"}
            )
            return json.loads(resp.choices[0].message.content)
        except Exception as e:
            self.root.after(0, lambda err=str(e): self.append_chat(f"【系统】AI 请求失败: {err}", "system"))
            return None

    def send_ai_msg(self, prompt=None):
        msg = prompt or self.chat_entry.get().strip()
        if not prompt:
            self.chat_entry.delete(0, 'end')
        if not msg:
            return
        
        if not prompt:
            self.append_chat(f"👤 我: {msg}", "user")
        
        self.append_chat(f"🤖 AI 在思考中...", "system")
        def go():
            avail_cash = self.config.get("avail_cash", self.config.get("total_cash", 1000000.0))
            
            system_prompt = f'''你是一个模拟炒股交易员与智能记账助手。你非常善于理解用户的自然语言指令。

【当前系统状态】
可用现金: {avail_cash:.2f} 元
持仓: {json.dumps(self.stocks, ensure_ascii=False)}
今日大盘环境: {getattr(self, "market_status", "无")}
今日系统底池推荐: {json.dumps(getattr(self, "recommended", [])[:10], ensure_ascii=False)}

【你的职责】
1. 用户可能用非常口语化的方式告诉你买卖操作，你需要智能提取。比如：
   - "我买了点平安银行，500股，大概10块钱" → 提取买入 000001 500股 10元
   - "今天入了两只，600519 买了100股 1800，还有300858 500股 17块7" → 提取两笔买入
   - "把美邦服饰清了" → 查找持仓中的美邦服饰，提取卖出，数量=全部持仓数量
   - "昨天卖掉了招商银行200股，39.5" → 提取卖出
   - "减半润泽科技" → 查找持仓，卖出一半数量
2. 用户可能一句话包含多只股票的操作，你必须全部提取。
3. 如果用户说的是股票名称而非代码，你需要根据已知信息（持仓列表或常识）推断6位代码。
4. 如果用户没有具体的买卖操作，只是咨询或要建议，则 operations 为空数组。
5. 卖出时，如果用户没说价格，优先使用持仓中的现价(price字段)，如果没有则用成本价。
6. 买入时，如果用户没说价格，答复中应该提醒用户补充价格信息，不要自己编造。

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
            
            # 把当前输入记录到内存上下文
            self.ai_messages.append({"role": "user", "content": msg})
            
            # 取最近的记录发送给 AI (保障上下文存在，且携带最新的 system_prompt 状态)
            messages = [{"role": "system", "content": system_prompt}] + self.ai_messages[-20:]
            
            res = self._call_deepseek(messages)
            if not res:
                self.ai_messages.pop()  # 防止失败的 prompt 污染内存
                self.root.after(0, lambda: self._process_ai_result(None)) # trigger cleanup
                return
            
            # 删除此前的思考提示
            self.root.after(0, lambda: self._process_ai_result(res))
            
            reply = res.get("reply", "")
            if reply:
                self.ai_messages.append({"role": "assistant", "content": reply})
                self.save_ai_history()

        threading.Thread(target=go, daemon=True).start()

    def _process_ai_result(self, res):
        self.chat_text.config(state="normal")
        self.chat_text.delete("end-2l", "end-1l") # 删掉思考中
        self.chat_text.config(state="disabled")
        
        if not res:
            return

        reply = res.get("reply", "")
        self.append_chat(f"🤖 AI: {reply}\n", "ai")

        # 兼容旧格式（单个 action）和新格式（operations 数组）
        operations = res.get("operations", [])
        if not operations and res.get("action") in ["BUY", "SELL"]:
            # 向后兼容旧的单操作格式
            operations = [{
                "action": res["action"],
                "symbol": res.get("symbol", ""),
                "name":   res.get("name", ""),
                "shares": res.get("shares", 0),
                "price":  res.get("price", 0),
            }]

        if not operations:
            return

        has_trade = False
        for op in operations:
            action = op.get("action")
            sym = str(op.get("symbol", "")).strip()
            sh = op.get("shares", 0)
            pr = op.get("price", 0)
            op_name = op.get("name", sym)

            if not sym or not action or sh <= 0 or pr <= 0:
                if sym and action:
                    self.append_chat(
                        f"【系统】⚠️ 操作 {action} {op_name}({sym}) 缺少有效的数量或价格，已跳过。",
                        "system")
                continue

            has_trade = True
            if action == "BUY":
                found = False
                name_used = op_name or sym
                for s in self.stocks:
                    if s["symbol"] == sym:
                        name_used = s.get("name", name_used)
                        old_total = s["cost"] * s["shares"] + sh * pr
                        s["shares"] += sh
                        s["cost"] = round(old_total / s["shares"], 3)
                        found = True
                        break
                if not found:
                    self.stocks.append({"symbol": sym, "name": name_used, "shares": sh, "cost": pr})
                self.record_trade("BUY", sym, name_used, pr, sh, pr)
                self.append_chat(
                    f"【系统】✅ 买入 {name_used}({sym}) {int(sh)}股 @ {pr}，"
                    f"花费 {pr*sh:,.2f} 元，剩余现金 {self.config.get('avail_cash',0):,.2f} 元。",
                    "system")

            elif action == "SELL":
                sold = False
                for s in self.stocks[:]:
                    if s["symbol"] == sym:
                        cost_p = s.get("cost", 0)
                        name_used = s.get("name", op_name or sym)
                        actual_sh = min(sh, s["shares"])  # 防止卖出超过持有
                        pnl = (pr - cost_p) * actual_sh
                        if actual_sh >= s["shares"]:
                            self.stocks.remove(s)
                            self.append_chat(f"【系统】✅ 清仓 {name_used}({sym}) {int(actual_sh)}股。", "system")
                        else:
                            s["shares"] -= actual_sh
                            self.append_chat(f"【系统】✅ 减仓 {name_used}({sym}) {int(actual_sh)}股，剩余 {int(s['shares'])}股。", "system")
                        self.record_trade("SELL", sym, name_used, pr, actual_sh, cost_p)
                        pnl_str = f"+{pnl:,.2f}" if pnl >= 0 else f"{pnl:,.2f}"
                        self.append_chat(
                            f"【系统】卖出到账 {pr*actual_sh:,.2f} 元，盈亏 {pnl_str} 元，"
                            f"现金 {self.config.get('avail_cash',0):,.2f} 元。",
                            "system")
                        sold = True
                        break
                if not sold:
                    self.append_chat(
                        f"【系统】⚠️ 未找到持仓 {op_name}({sym})，卖出操作已跳过。",
                        "system")

        if has_trade:
            self.save_local_holdings()
            self.refresh_ai_summary()
            self.refresh_all()

    def request_ai_advice(self):
        mkt = self.market_status
        mkt_desc = f"大盘MA20状态: {'多头' if mkt and mkt.get('above_ma20') else '空头'}"
        rec_list = [f"{r['name']}({r['symbol']}) 信号:{'||'.join(r['reason'])}" for r in self.recommended[:5]]
        
        prompt = f"请结合以下环境给我今明两天的模拟赛交易建议。1.当前环境:{mkt_desc}。2.系统为你推荐了今日热股：{json.dumps(rec_list, ensure_ascii=False)}。请指出我要不要动现在的持仓，以及要不要买新股。返回的 action 设为 'ADVICE'。"
        self.append_chat("【系统】正在把当前持仓数据、大盘状态与当晚推荐股池发送给 AI 获取最新意见...", "system")
        self.send_ai_msg(prompt)

# ============================================================
# 启动
# ============================================================
if __name__ == "__main__":
    try:
        import akshare
    except ImportError:
        print("缺少 akshare，请先运行: pip install akshare")
        exit(1)

    root = tk.Tk()
    app  = StockAnalyzerApp(root)
    root.mainloop()
