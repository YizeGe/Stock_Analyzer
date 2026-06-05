"""
股票分析数据层 — 从原始 stock_analyzer.py 提取的所有数据获取和分析函数
"""
import os
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

import requests as _requests
_requests.Session.trust_env = False

import pandas as pd
import datetime
import json


def _to_native(v):
    """将 numpy 类型和 NaN 转为 Python 原生类型"""
    if isinstance(v, (pd.Series, pd.DataFrame)):
        return v.to_dict() if isinstance(v, pd.Series) else v.to_dict(orient='records')
    # 处理 NaN / inf
    try:
        if isinstance(v, float) and (v != v or v == float('inf') or v == float('-inf')):
            return None
    except:
        pass
    if hasattr(v, 'dtype'):
        if hasattr(v, 'item'):
            return v.item()
        return float(v)
    return v


def _to_native_dict(d):
    """递归转换 dict 中的 numpy 类型"""
    return {k: _to_native_dict(v) if isinstance(v, dict) else _to_native(v) for k, v in d.items()}

SESSION = _requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn",
})


# ── 工具函数 ─────────────────────────────────────────────

def _sina_symbol(raw_symbol):
    """转换股票代码为新浪/腾讯格式"""
    s = raw_symbol.strip().lstrip("sh").lstrip("sz")
    if s.startswith(("6", "5")):
        return f"sh{s}"
    elif s.startswith(("0", "3")):
        return f"sz{s}"
    return f"sh{s}"


# ── 实时行情 ─────────────────────────────────────────────

def fetch_realtime_quote(symbol):
    """腾讯财经接口获取实时行情"""
    try:
        sina_sym = _sina_symbol(symbol)
        url = f"https://qt.gtimg.cn/q={sina_sym}"
        resp = SESSION.get(url, timeout=5)
        resp.encoding = "gbk"
        text = resp.text
        if "=" in text and "~" in text:
            parts = text.split("~")
            if len(parts) > 32:
                price = float(parts[3]) if parts[3] else 0.0
                change = float(parts[32]) if parts[32] else 0.0
                return {
                    "symbol": symbol,
                    "name": parts[1],
                    "price": price,
                    "change": change,
                }
    except Exception as e:
        print(f"腾讯实时行情失败 {symbol}: {e}")
    return None


def fetch_batch_quotes(symbols):
    """
    批量获取实时行情
    symbols: 列表，每个元素可以是 "600519" 或 "sh600519"
    返回 [{symbol, name, price, change, ...}, ...]
    """
    if not symbols:
        return []
    sina_codes = [_sina_symbol(s) for s in symbols]
    results = []
    # 腾讯批量接口，每次最多 200 个
    for i in range(0, len(sina_codes), 150):
        batch = sina_codes[i:i + 150]
        codes = ",".join(batch)
        try:
            url = f"https://qt.gtimg.cn/q={codes}"
            resp = SESSION.get(url, timeout=10)
            resp.encoding = "gbk"
            lines = resp.text.strip().split("\n")
            for line in lines:
                if "=" not in line:
                    continue
                sym_raw = line.split("=")[0].strip().split("_")[-1]
                parts = line.split("~")
                if len(parts) < 32:
                    continue
                sym = sym_raw[2:] if len(sym_raw) == 8 and sym_raw[:2] in ("sh", "sz") else ""
                if not sym:
                    continue
                price = float(parts[3]) if parts[3] else 0
                change = float(parts[32]) if parts[32] else 0
                name = parts[1].strip() if parts[1] else sym
                results.append({
                    "symbol": sym,
                    "name": name,
                    "price": price,
                    "change": change,
                })
        except Exception as e:
            print(f"批量行情获取失败: {e}")
    return results


# ── 历史 K 线 ───────────────────────────────────────────

def fetch_historical_data(symbol, period="daily", start_date=None, end_date=None):
    """获取历史K线，优先 akshare，备选新浪"""
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist(symbol=symbol, period=period,
                                start_date=start_date, end_date=end_date, adjust="")
        if df is not None and not df.empty:
            df.columns = [c.lower() for c in df.columns]
            return df
    except Exception:
        pass

    try:
        sina_sym = _sina_symbol(symbol)
        datalen = 60
        url = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php"
               f"/var%20_{sina_sym}=/CN_MarketDataService.getKLineData"
               f"?symbol={sina_sym}&scale=240&datalen={datalen}")
        resp = SESSION.get(url, timeout=5)
        text = resp.text
        marker = "=("
        idx = text.index(marker)
        json_part = text[idx + len(marker):].rstrip(";").rstrip(")")
        data = json.loads(json_part)
        if not data:
            return None
        df = pd.DataFrame(data)
        df.rename(columns={
            "day": "date", "open": "open", "high": "high",
            "low": "low", "close": "close", "volume": "volume",
        }, inplace=True)
        df.columns = [c.lower() for c in df.columns]
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if start_date:
            start_str = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
            df = df[df["date"] >= start_str]
        if end_date:
            end_str = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
            df = df[df["date"] <= end_str]
        return df
    except Exception as e:
        print(f"新浪历史K线失败 {symbol}: {e}")
    return None


def fetch_index_historical(index_code="sh000001", start_date=None, end_date=None):
    """获取指数历史K线"""
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol=index_code)
        if df is not None and not df.empty:
            df.columns = [c.lower() for c in df.columns]
            if start_date:
                start_str = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
                df = df[df["date"] >= start_str]
            if end_date:
                end_str = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
                df = df[df["date"] <= end_str]
            return df
    except Exception:
        pass

    try:
        datalen = 60
        url = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php"
               f"/var%20_{index_code}=/CN_MarketDataService.getKLineData"
               f"?symbol={index_code}&scale=240&datalen={datalen}")
        resp = SESSION.get(url, timeout=5)
        text = resp.text
        marker = "=("
        idx = text.index(marker)
        json_part = text[idx + len(marker):].rstrip(";").rstrip(")")
        data = json.loads(json_part)
        if not data:
            return None
        df = pd.DataFrame(data)
        df.rename(columns={
            "day": "date", "open": "open", "high": "high",
            "low": "low", "close": "close", "volume": "volume",
        }, inplace=True)
        df.columns = [c.lower() for c in df.columns]
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if start_date:
            start_str = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
            df = df[df["date"] >= start_str]
        if end_date:
            end_str = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
            df = df[df["date"] <= end_str]
        return df
    except Exception as e:
        print(f"指数历史K线获取失败 {index_code}: {e}")
    return None


# ── 技术指标 ─────────────────────────────────────────────

def calc_ma(series, window):
    return series.rolling(window=window).mean()


def calc_rsi(series, window=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss.replace(0, float('nan'))
    return 100 - (100 / (1 + rs))


def calc_macd(series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
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
    """量比：今日成交量 / 前5日平均成交量"""
    if df is None or "volume" not in df.columns or len(df) < 6:
        return None, "normal"
    volumes = df["volume"].dropna()
    if len(volumes) < 6:
        return None, "normal"
    today_vol = float(volumes.iloc[-1])
    hist_vols = volumes.iloc[-6:-1]
    hist_avg = hist_vols.mean()
    if hist_avg == 0 or today_vol == 0:
        return None, "normal"
    ratio = today_vol / hist_avg
    if ratio >= 1.3:
        return round(ratio, 2), "up"
    elif ratio <= 0.7:
        return round(ratio, 2), "down"
    return round(ratio, 2), "normal"


# ── 大盘环境判断 ────────────────────────────────────────

def get_market_status():
    """获取上证指数当前状态"""
    today = datetime.date.today()
    start_year = str(today.year - 1)

    df = fetch_index_historical("sh000001", start_date=start_year + "0101")
    if df is None or len(df) < 20:
        df = fetch_index_historical("sh000300", start_date=start_year + "0101")
    if df is None or len(df) < 20:
        return None

    close = df["close"].dropna()
    if len(close) < 2:
        return None

    ma20 = calc_ma(close, 20).iloc[-1]
    price = float(close.iloc[-1])
    change = float(df["close"].iloc[-1] - df["close"].iloc[-2]) / float(df["close"].iloc[-2]) * 100
    above = price > ma20

    trend = "📈 上升趋势" if above and change > 0 else \
            "📉 下降趋势" if not above and change < 0 else \
            "📊 震荡偏多" if above else "📊 震荡偏空"

    result = {
        "name": "上证指数",
        "price": round(price, 2),
        "change": round(change, 2),
        "ma20": round(ma20, 2),
        "above_ma20": bool(above),
        "trend": trend,
    }
    return _to_native_dict(result)


# ── 单股信号分析 ────────────────────────────────────────

def analyze_signal(symbol, market_above_ma20=None):
    """综合技术面分析，给出推荐"""
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

    rsi_series = calc_rsi(close)
    rsi = round(rsi_series.iloc[-1], 2) if not rsi_series.isna().all() else None
    ma5 = calc_ma(close, 5)
    ma10 = calc_ma(close, 10)
    ma20 = calc_ma(close, 20)
    macd_line, signal_line = calc_macd(close)

    price = float(close.iloc[-1])
    ma20_val = ma20.iloc[-1] if not ma20.isna().iloc[-1] else None

    ma_cross = detect_cross(ma5, ma10)
    macd_cross = detect_cross(macd_line, signal_line)
    price_cross20 = detect_price_cross_ma(close, ma20)
    vol_ratio, vol_signal = calc_volume_ratio(df)

    recent_low = float(df["close"].iloc[-5:].min())
    stop_loss = round(recent_low * 0.97, 2)
    stop_pct = round((price - stop_loss) / price * 100, 1)
    stop_reason = f"止损：{stop_loss}（-{stop_pct}%）"

    score = 0
    reasons = []

    if rsi is not None:
        if rsi < 30:
            confirmed = vol_signal == "up" or (ma20_val and price > ma20_val) or ma_cross == "golden"
            if confirmed:
                score += 1
                reasons.append(f"RSI={rsi} 超卖+确认")
            else:
                reasons.append(f"RSI={rsi} 超卖（无确认，谨慎）")
        elif rsi > 70:
            score -= 1
            reasons.append(f"RSI={rsi} > 70 超买")
        else:
            reasons.append(f"RSI={rsi} 中性区间")

    if ma_cross == "golden":
        score += 1
        reasons.append("MA5 上穿 MA10 金叉")
    elif ma_cross == "dead":
        score -= 1
        reasons.append("MA5 下穿 MA10 死叉")

    if ma20_val is not None:
        if price > ma20_val:
            score += 1
            reasons.append(f"价格>{ma20_val:.2f} MA20 多头")
        else:
            score -= 1
            reasons.append(f"价格<{ma20_val:.2f} MA20 空头")

    if macd_cross == "golden":
        score += 1
        reasons.append("MACD 金叉")
    elif macd_cross == "dead":
        score -= 1
        reasons.append("MACD 死叉")

    if vol_ratio is not None and ma20_val is not None:
        if vol_signal == "up" and price > ma20_val:
            score += 1
            reasons.append(f"放量{vol_ratio}倍站稳MA20")
        elif vol_signal == "up":
            reasons.append(f"放量{vol_ratio}倍（未站上MA20）")
        elif vol_signal == "down":
            reasons.append(f"缩量{vol_ratio}倍，观望")

    if market_above_ma20 is None:
        mkt = get_market_status()
        market_above_ma20 = mkt["above_ma20"] if mkt else True

    raw_score = score
    if not bool(market_above_ma20) and score > 0:
        score = round(score * 0.7, 1)
        reasons.append(f"大盘弱，信号乘0.7（{raw_score}→{score}）")
        stop_reason += "（大盘弱，建议轻仓）"

    thresholds = [
        (4, "⭐ 强烈买入", "#1565c0"),
        (3, "✅ 买入", "#2e7d32"),
        (2, "🟡 谨慎买入", "#f9a825"),
        (1, "🟡 谨慎买入", "#f9a825"),
        (0, "⚪ 观望", "#757575"),
        (-1, "🟠 谨慎卖出", "#ef6c00"),
        (-2, "❌ 卖出", "#c62828"),
        (-999, "🔥 强烈卖出", "#b71c1c"),
    ]
    recommendation, rec_color = "⚪ 观望", "#757575"
    for threshold, rec, clr in thresholds:
        if score >= threshold:
            recommendation, rec_color = rec, clr
            break

    return _to_native_dict(dict(
        symbol=symbol, name=stock_name or symbol, price=price, rsi=rsi,
        ma_cross=ma_cross, macd_cross=macd_cross, price_cross20=price_cross20,
        price_vs_ma20=("above" if price > ma20_val else "below") if ma20_val else None,
        vol_ratio=vol_ratio, vol_signal=vol_signal,
        score=score, raw_score=raw_score,
        recommendation=recommendation, rec_color=rec_color,
        reason=reasons, stop_loss=stop_loss, stop_loss_pct=stop_pct,
        stop_reason=stop_reason,
    ))


# ── 市场数据获取（热门股票）────────────────────────────

def get_hot_stocks(limit=50):
    """涨停池 + 各行业强势代表股"""
    import akshare as ak

    zt_stocks = []
    sector_hot = []

    # 涨停池
    try:
        today = datetime.date.today()
        for i in range(10):
            d_str = (today - datetime.timedelta(days=i)).strftime("%Y%m%d")
            try:
                df = ak.stock_zt_pool_em(date=d_str)
                if df is not None and not df.empty:
                    for _, row in df.iterrows():
                        sym = str(row.get("代码", "")).strip()
                        if len(sym) == 6:
                            price = row.get("最新价", row.get("收盘价", 0))
                            zt_stocks.append({
                                "symbol": sym,
                                "name": str(row.get("名称", sym)),
                                "price": float(price) if price else 0.0,
                                "change": float(row.get("涨跌幅", 0)),
                            })
                    break
            except Exception:
                continue
    except Exception as e:
        print(f"涨停池获取失败: {e}")

    # 行业代表股
    SECTOR_LEADERS = [
        ("银行", "sh600000"), ("银行", "sh600036"), ("银行", "sh601166"),
        ("保险", "sh601318"), ("券商", "sh600837"), ("券商", "sh601211"),
        ("白酒", "sh600519"), ("白酒", "sz000568"), ("食品", "sh603288"),
        ("家电", "sh600690"), ("免税", "sh601888"),
        ("零售", "sh600859"), ("旅游", "sh600054"),
        ("新能源车", "sz002594"), ("新能源车", "sh600418"),
        ("创新药", "sh600276"), ("CXO", "sh603259"), ("医疗器械", "sz002007"),
        ("中药", "sh600557"), ("疫苗", "sz300015"),
        ("半导体", "sh688981"), ("半导体", "sh600584"),
        ("苹果链", "sz002475"), ("算力", "sh000977"), ("AI应用", "sh600745"),
        ("软件", "sh600570"), ("游戏", "sh603444"),
        ("光伏", "sh601012"), ("锂电", "sh600150"),
        ("储能", "sh600478"), ("风电", "sh601615"),
        ("电力", "sh600900"), ("电力", "sh600011"), ("核电", "sh601985"),
        ("煤炭", "sh600188"), ("钢铁", "sh600019"), ("水泥", "sh600801"),
        ("化工", "sh600309"), ("有色", "sh600259"),
        ("黄金", "sh601899"), ("稀土", "sh600111"),
        ("基建", "sh601668"), ("地产", "sz000002"),
        ("航运", "sh601919"), ("航空", "sh600115"), ("物流", "sh600233"),
        ("军工", "sh600893"), ("通信设备", "sh000063"), ("运营商", "sh601728"),
        ("农业", "sh600598"), ("机器人", "sz300024"),
        ("数据要素", "sh600536"), ("跨境电商", "sz002491"),
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
            sym = sym_raw[2:] if len(sym_raw) == 8 else ""
            name = parts[1].strip()
            price = float(parts[3]) if parts[3] else 0
            change = float(parts[32]) if parts[32] else 0
            sector_name = SECTOR_LEADERS[i][0] if i < len(SECTOR_LEADERS) else "其他"
            sector_hot.append({
                "sector": sector_name,
                "avg_chg": change,
                "symbol": sym,
                "name": name,
                "price": price,
                "change": change,
            })
    except Exception as e:
        print(f"行业代表股获取失败: {e}")

    sector_hot.sort(key=lambda x: x["avg_chg"], reverse=True)
    zt_syms = {s["symbol"] for s in zt_stocks}
    sector_hot = [s for s in sector_hot if s["symbol"] not in zt_syms][:limit]

    return {"zt_stocks": zt_stocks, "sector_hot": sector_hot, "timestamp": datetime.datetime.now().isoformat()}


def get_broad_stock_pool(limit_per_cat=20):
    """更广泛的选股池"""
    dynamic_list = []
    try:
        url = 'http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page=1&num=60&sort=amount&asc=0&node=hs_a&symbol=&_s_r_a=page'
        resp = SESSION.get(url, timeout=5)
        data = resp.json()
        for v in data:
            sym = v.get('symbol')
            if sym:
                dynamic_list.append(sym)
    except Exception:
        pass

    WATCH_LIST = [
        "sh600000", "sh600036", "sh600016", "sh601166", "sh601318", "sh601398",
        "sh600519", "sh600104", "sh600690", "sh600887", "sz000568", "sh603288",
        "sh600150", "sh601012", "sh600900", "sh600703", "sh600276", "sh688981",
        "sz000001", "sz000002", "sz000333", "sz000651", "sz002594", "sz002415",
        "sh601100", "sh600862", "sh600585", "sh601668", "sh601919", "sh600309",
        "sz000661", "sz002007", "sz300760", "sz300015", "sh603259",
        "sh600745", "sh603444", "sz000977", "sz000063", "sz002230", "sh688256",
    ]

    final_watch_list = list(set(WATCH_LIST + dynamic_list))
    all_stocks = []
    seen = set()

    batch_size = 80
    for i in range(0, len(final_watch_list), batch_size):
        batch = final_watch_list[i:i + batch_size]
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
                sym = sym_raw[2:] if len(sym_raw) == 8 and sym_raw[:2] in ("sh", "sz") else ""
                name = parts[1].strip()
                price = float(parts[3]) if parts[3] else 0
                change = float(parts[32]) if len(parts) > 32 and parts[32] else 0
                if not sym or sym in seen:
                    continue
                seen.add(sym)
                all_stocks.append({
                    "symbol": sym, "name": name,
                    "price": price, "change": change,
                    "source": "关注池"
                })
        except Exception:
            continue

    return all_stocks


def get_recommended_stocks(symbols, market_above_ma20=None, max_count=20,
                           min_watch_score=0.3, name_map=None):
    """对股票列表跑策略筛选"""
    if name_map is None:
        name_map = {}
    analyzed = []
    zt_pool = []
    watch_pool = []
    for sym in symbols:
        res = analyze_signal(sym, market_above_ma20=market_above_ma20)
        score = res.get("score", 0) if res else 0
        if res and score >= 1:
            if not res.get("name"):
                res["name"] = name_map.get(sym, sym)
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
            if not res.get("name"):
                res["name"] = name_map.get(sym, sym)
            res["recommendation"] = "👀 关注"
            res["rec_color"] = "#1976d2"
            watch_pool.append(res)

    analyzed.sort(key=lambda x: x["score"], reverse=True)
    watch_pool.sort(key=lambda x: x["score"], reverse=True)
    return analyzed[:max_count], zt_pool, watch_pool


# ── 股票搜索 ─────────────────────────────────────────────

STOCK_NAME_CACHE = None


def search_stocks(query: str, limit: int = 10) -> list:
    """按代码或名称搜索股票，返回 [{symbol, name, ...}]"""
    global STOCK_NAME_CACHE
    q = query.strip().lower()
    if not q:
        return []

    results = []
    seen = set()

    # 如果输入是 6 位代码，直接查
    if q.isdigit() and len(q) <= 6:
        code = q.zfill(6)
        quote = fetch_realtime_quote(code)
        if quote and quote.get("name"):
            return [{"symbol": code, "name": quote["name"],
                     "price": quote.get("price", 0), "change": quote.get("change", 0)}]
        # 如果查不到，可能是 sh/sz 前缀问题，再试一次
        for prefix in ["sh", "sz"]:
            quote = fetch_realtime_quote(f"{prefix}{code}")
            if quote and quote.get("name"):
                return [{"symbol": code, "name": quote["name"],
                         "price": quote.get("price", 0), "change": quote.get("change", 0)}]
        return []

    # 如果是文字搜索，用已知热门股票匹配（本地缓存）
    if STOCK_NAME_CACHE is None:
        # 构建一个常见股票列表
        STOCK_NAME_CACHE = _build_common_stock_list()

    for s in STOCK_NAME_CACHE:
        if s["symbol"] in seen:
            continue
        if q in s["name"] or s["symbol"].startswith(q):
            results.append(s)
            seen.add(s["symbol"])
        if len(results) >= limit:
            break

    # 没搜到的话试着当代码搜
    if not results and len(q) <= 6:
        code = q.zfill(6)
        quote = fetch_realtime_quote(code)
        if quote and quote.get("name"):
            return [{"symbol": code, "name": quote["name"],
                     "price": quote.get("price", 0), "change": quote.get("change", 0)}]

    return results


def _build_common_stock_list():
    """构建常用股票列表（不用 akshare，避免网络封锁）"""
    COMMON_SYMBOLS = [
        ("000001", "平安银行"), ("000002", "万科A"), ("000063", "中兴通讯"),
        ("000100", "TCL科技"), ("000333", "美的集团"), ("000568", "泸州老窖"),
        ("000651", "格力电器"), ("000725", "京东方A"), ("000858", "五粮液"),
        ("000977", "浪潮信息"), ("002007", "华兰生物"), ("002230", "科大讯飞"),
        ("002415", "海康威视"), ("002475", "立讯精密"), ("002491", "通鼎互联"),
        ("002594", "比亚迪"), ("300015", "爱尔眼科"), ("300024", "机器人"),
        ("300059", "东方财富"), ("300133", "华策影视"), ("300750", "宁德时代"),
        ("300760", "迈瑞医疗"), ("300897", "山科智能"),
        ("600000", "浦发银行"), ("600011", "华能国际"), ("600016", "民生银行"),
        ("600019", "宝钢股份"), ("600028", "中国石化"), ("600036", "招商银行"),
        ("600054", "黄山旅游"), ("600104", "上汽集团"), ("600111", "北方稀土"),
        ("600115", "中国东航"), ("600150", "中国船舶"), ("600188", "兖矿能源"),
        ("600233", "圆通速递"), ("600259", "广晟有色"), ("600276", "恒瑞医药"),
        ("600309", "万华化学"), ("600418", "江淮汽车"), ("600487", "亨通光电"),
        ("600519", "贵州茅台"), ("600536", "中国软件"), ("600557", "康缘药业"),
        ("600570", "恒生电子"), ("600584", "长电科技"), ("600585", "海螺水泥"),
        ("600598", "北大荒"), ("600688", "上海石化"), ("600690", "海尔智家"),
        ("600703", "三安光电"), ("600741", "华域汽车"), ("600745", "闻泰科技"),
        ("600801", "华新水泥"), ("600837", "海通证券"), ("600859", "王府井"),
        ("600862", "中航高科"), ("600887", "伊利股份"), ("600893", "航发动力"),
        ("600900", "长江电力"), ("600903", "贵州燃气"),
        ("601012", "隆基绿能"), ("601018", "宁波港"), ("601100", "恒立液压"),
        ("601138", "工业富联"), ("601166", "兴业银行"), ("601179", "中国西电"),
        ("601211", "国泰君安"), ("601318", "中国平安"), ("601330", "绿色动力"),
        ("601398", "工商银行"), ("601615", "明阳智能"), ("601668", "中国建筑"),
        ("601728", "中国电信"), ("601888", "中国中免"), ("601899", "紫金矿业"),
        ("601919", "中远海控"), ("601985", "中国核电"), ("601998", "中信银行"),
        ("603007", "花王股份"), ("603259", "药明康德"), ("603288", "海天味业"),
        ("603444", "吉比特"), ("603501", "韦尔股份"),
        ("688256", "寒武纪"), ("688981", "中芯国际"),
    ]
    return [{"symbol": s[0], "name": s[1], "price": 0, "change": 0} for s in COMMON_SYMBOLS]
