#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small Eastmoney-backed research API for the first frontend version."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from urllib.error import URLError, HTTPError
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import combinations
import json, math, statistics, time, subprocess, re, threading, os, gzip, hashlib
from multi_agent import load_latest as read_multi_agent_run, run_research_council

HOST, PORT = "127.0.0.1", 4173
GUPIAO_UNIVERSE_FILE = Path("/Users/pozansky/Documents/gupiao/fixed_universe_codes.json")
HOT_HISTORY_FILE = Path(__file__).with_name("hot_rank_history.json")
CACHE_DIR = Path(__file__).with_name(".cache")
DASHBOARD_CACHE_FILE = CACHE_DIR / "dashboard.json"
KLINE_CACHE_DIR = CACHE_DIR / "klines"
KLINE_MANIFEST_FILE = CACHE_DIR / "historical_data_manifest.json"
FACTOR_AGENT_RUN_FILE = CACHE_DIR / "factor_agent_last_run.json"
BACKTEST_RUN_FILE = CACHE_DIR / "backtest_factor_last_run.json"
TECH_UNIVERSE_FILE = Path(__file__).with_name("tech_universe.json")
TECH_CONFIG_FILE = CACHE_DIR / "tech_universe_config.json"
DATA_DIR = Path(__file__).with_name("data")
TECH_HISTORY_FILE = DATA_DIR / "tech_history.json.gz"
TECH_HISTORY_MANIFEST_FILE = DATA_DIR / "tech_history_manifest.json"
FIXED_TECH_UNIVERSE_FILE = DATA_DIR / "fixed_tech_universe.json"
CACHE_LOCK = threading.Lock()
REFRESH_THREAD = None
MULTI_AGENT_THREAD = None
MULTI_AGENT_LOCK = threading.Lock()
# A complete 930-name refresh is expensive; stale-while-revalidate on a
# 15-minute cadence prevents permanent refresh loops during market hours.
REFRESH_TTL_SECONDS = 15 * 60
FACTOR_ENGINE_VERSION = "tech-factor-v4-shared-top4-backtests"
BACKTEST_ENGINE_VERSION = "tech-top3-v8-liquidity-screen"

TECH_SECTOR_DEFINITIONS = {
    "semiconductor": {
        "name": "半导体与芯片",
        "industries": {"半导体"},
        "concepts": {"芯片概念", "先进封装", "第三代半导体", "汽车芯片", "存储芯片"},
    },
    "electronic_components": {
        "name": "电子元件与硬件",
        "industries": {"元件", "消费电子", "电子化学品Ⅱ", "电子化学品II", "其他电子Ⅱ", "其他电子II"},
        "concepts": {"PCB", "被动元件", "先进封装", "苹果概念"},
    },
    "optical_electronics": {
        "name": "光学光电子",
        "industries": {"光学光电子"},
        "concepts": {"MiniLED", "MicroLED", "OLED", "激光雷达"},
    },
    "glass_substrate": {
        "name": "玻璃基板与电子玻璃",
        "industries": set(),
        "concepts": {"玻璃基板", "TGV", "电子玻璃"},
    },
    "communication_compute": {
        "name": "通信与算力",
        "industries": {"通信设备", "通信服务", "计算机设备"},
        "concepts": {"CPO概念", "光通信模块", "算力概念", "数据中心", "液冷概念", "6G概念", "5G概念"},
    },
    "software_ai": {
        "name": "软件、信创与 AI",
        "industries": {"软件开发", "IT服务"},
        "concepts": {"人工智能", "AIGC概念", "ChatGPT概念", "多模态AI", "AI智能体", "DeepSeek概念", "信创"},
    },
    "robotics_auto": {
        "name": "机器人与智能汽车",
        "industries": {"自动化设备"},
        "concepts": {"机器人概念", "人形机器人", "减速器", "机器视觉", "自动驾驶", "激光雷达", "汽车芯片"},
    },
    "advanced_manufacturing": {
        "name": "高端科技制造",
        "industries": {"自动化设备", "航空装备Ⅱ", "航空装备II", "航天装备Ⅱ", "航天装备II", "军工电子Ⅱ", "军工电子II", "仪器仪表"},
        "concepts": {"工业母机", "3D打印", "商业航天", "卫星互联网", "工业互联网"},
    },
    "internet_media": {
        "name": "科技互联网与数字内容",
        "industries": {"游戏Ⅱ", "游戏II", "数字媒体", "互联网电商"},
        "concepts": {"云游戏", "网络游戏", "数字经济", "数据要素", "区块链"},
    },
}
DEFAULT_TECH_SECTORS = [key for key in TECH_SECTOR_DEFINITIONS if key != "internet_media"]

# Verified against Tonghuashun's electronic glass-substrate definition and
# company disclosure references. 688/689 names are intentionally omitted.
GLASS_SUBSTRATE_MEMBERS = {
    "000725", "000988", "002106", "002156", "002436", "300088",
    "300162", "300219", "300296", "300456", "300776", "301200",
    "301338", "600367", "600552", "600707", "600714", "603773",
}

# Concept membership is useful for these hardware chains because suppliers can
# sit outside the narrow exchange industry label. Broad AI/robot concepts stay
# industry-gated to avoid pulling unrelated consumer and cyclical companies in.
FIXED_CONCEPT_SECTORS = {
    "semiconductor", "electronic_components", "optical_electronics",
    "communication_compute",
}

DIGITAL_TECH_INDUSTRIES = {"半导体", "元件", "消费电子", "光学光电子", "电子化学品", "其他电子", "通信设备", "通信服务", "计算机设备", "软件开发", "IT服务", "游戏", "数字媒体", "互联网电商", "影视院线", "出版", "广告营销"}
TECH_MANUFACTURING_INDUSTRIES = {"自动化设备", "通用设备", "专用设备", "仪器仪表", "航空装备", "航天装备", "军工电子", "地面兵装", "汽车零部件", "乘用车", "工程机械", "电机", "电网设备"}
TECH_CONCEPT_ALLOWED_INDUSTRIES = {
    "semiconductor": DIGITAL_TECH_INDUSTRIES | {"专用设备", "自动化设备", "非金属材料", "化学制品"},
    "electronic_components": DIGITAL_TECH_INDUSTRIES | {"专用设备", "自动化设备"},
    "optical_electronics": DIGITAL_TECH_INDUSTRIES | {"汽车零部件", "仪器仪表"},
    "glass_substrate": DIGITAL_TECH_INDUSTRIES | {"非金属材料", "化学原料", "专用设备", "自动化设备"},
    "communication_compute": DIGITAL_TECH_INDUSTRIES | {"自动化设备", "专用设备", "电网设备"},
    "software_ai": DIGITAL_TECH_INDUSTRIES | {"自动化设备", "仪器仪表"},
    "robotics_auto": DIGITAL_TECH_INDUSTRIES | TECH_MANUFACTURING_INDUSTRIES,
    "advanced_manufacturing": TECH_MANUFACTURING_INDUSTRIES | {"半导体", "元件", "消费电子"},
    "internet_media": DIGITAL_TECH_INDUSTRIES,
}

MINING_CANDIDATES = [
    ("momentum5", "5 日动量", "close / Ref(close, 5) - 1"),
    ("momentum10", "10 日动量", "close / Ref(close, 10) - 1"),
    ("momentum20", "20 日动量", "close / Ref(close, 20) - 1"),
    ("momentum60", "60 日动量", "close / Ref(close, 60) - 1"),
    ("ma5_20", "5/20 日均线差", "Mean(close, 5) / Mean(close, 20) - 1"),
    ("ma10_20", "10/20 日均线差", "Mean(close, 10) / Mean(close, 20) - 1"),
    ("breakout20", "20 日前高距离", "close / Max(close[-20:-1]) - 1"),
    ("drawdown20", "20 日高点回撤", "close / Max(close, 20) - 1"),
    ("range_position20", "20 日区间位置", "(close - Min(low, 20)) / (Max(high, 20) - Min(low, 20))"),
    ("volume_ratio5_20", "5/20 日量比", "Mean(volume, 5) / Mean(volume, 20)"),
    ("volume_surprise20", "当日量能异常", "volume / Mean(volume, 20)"),
    ("up_ratio20", "20 日上涨日占比", "Count(Return(close) > 0, 20) / 20"),
    ("volatility20", "20 日波动率", "Std(Return(close), 20)"),
    ("downside_vol20", "20 日下行波动", "Std(min(Return(close), 0), 20)"),
    ("atr14", "14 日 ATR", "Mean(TrueRange, 14) / close"),
    # These candidates are generated from the Codex research hypotheses below,
    # then executed by the local validator. The model never supplies metrics.
    ("reversal5", "5 日短线反转", "-Return(close, 5)"),
    ("overnight_gap", "隔夜跳空", "open / Ref(close, 1) - 1"),
    ("intraday_return", "日内收益", "close / open - 1"),
    ("close_location", "收盘位置", "(close - low) / (high - low)"),
    ("volume_trend5_60", "5/60 日量能趋势", "Mean(volume, 5) / Mean(volume, 60)"),
    ("range_expansion20", "波动扩张", "TrueRange / Mean(TrueRange, 20)"),
    ("price_volume5", "量价协同", "Return(close, 5) * Mean(volume, 5) / Mean(volume, 20)"),
]

# Research Agent output: hypotheses are explicit, auditable and persisted with
# each run. This is the seed set produced by Codex; the validator below is the
# only component allowed to decide whether a factor has signal.
FACTOR_RESEARCH_PROPOSALS = [
    {"id":"R01", "name":"短线反转", "hypothesis":"短期过度上涨后可能出现均值回归。", "formula":"-Return(close, 5)", "candidate":"reversal5"},
    {"id":"R02", "name":"隔夜跳空", "hypothesis":"隔夜信息冲击的方向可能延续或反转，需由样本外数据决定。", "formula":"open / Ref(close, 1) - 1", "candidate":"overnight_gap"},
    {"id":"R03", "name":"日内收益", "hypothesis":"开盘到收盘的买卖压力可能包含次日横截面信息。", "formula":"close / open - 1", "candidate":"intraday_return"},
    {"id":"R04", "name":"收盘位置", "hypothesis":"收盘接近当日高位可能代表买方占优，接近低位可能代表卖方占优。", "formula":"(close - low) / (high - low)", "candidate":"close_location"},
    {"id":"R05", "name":"量能趋势", "hypothesis":"短期成交量持续高于中期均值可能确认价格信号，但也可能代表拥挤。", "formula":"Mean(volume, 5) / Mean(volume, 60)", "candidate":"volume_trend5_60"},
    {"id":"R06", "name":"波动扩张", "hypothesis":"当前真实波幅相对历史波幅的扩张可能捕捉状态切换。", "formula":"TrueRange / Mean(TrueRange, 20)", "candidate":"range_expansion20"},
    {"id":"R07", "name":"量价协同", "hypothesis":"短期收益与成交量放大结合，可能区分有效突破与无量上涨。", "formula":"Return(close, 5) * Mean(volume, 5) / Mean(volume, 20)", "candidate":"price_volume5"},
]

def build_factor_search_space():
    """Generate a broad but auditable formula library before validation."""
    windows = [3, 5, 10, 20, 40, 60, 120]
    candidates = list(MINING_CANDIDATES)
    families = [
        ("momentum", "动量", "close / Ref(close, {w}) - 1"),
        ("reversal", "反转", "-(close / Ref(close, {w}) - 1)"),
        ("breakout", "前高距离", "close / Max(close[-{w}: -1]) - 1"),
        ("drawdown", "高点回撤", "close / Max(close, {w}) - 1"),
        ("volatility", "收益波动", "Std(Return(close), {w})"),
        ("downside_vol", "下行波动", "Std(min(Return(close), 0), {w})"),
        ("up_ratio", "上涨占比", "Count(Return(close) > 0, {w}) / {w}"),
    ]
    for prefix, label, formula in families:
        for window in windows:
            candidates.append((f"{prefix}_{window}", f"{window} 日{label}", formula.format(w=window)))
    for fast in [3, 5, 10, 20]:
        for slow in [20, 40, 60, 120]:
            if fast >= slow:
                continue
            candidates.append((f"ma_{fast}_{slow}", f"{fast}/{slow} 日均线差", f"Mean(close, {fast}) / Mean(close, {slow}) - 1"))
            candidates.append((f"volume_ratio_{fast}_{slow}", f"{fast}/{slow} 日量比", f"Mean(volume, {fast}) / Mean(volume, {slow})"))
    for window in windows:
        candidates.append((f"range_position_{window}", f"{window} 日区间位置", f"(close - Min(low, {window})) / (Max(high, {window}) - Min(low, {window}))"))
        candidates.append((f"atr_{window}", f"{window} 日 ATR", f"Mean(TrueRange, {window}) / close"))
    # Preserve the seed candidates and cap the deterministic search space at 100.
    unique = []
    seen = set()
    for candidate in candidates:
        if candidate[0] not in seen:
            unique.append(candidate); seen.add(candidate[0])
    return unique[:100]

def _factor_value(rows, index, key):
    # No factor in the audited library needs more than 120 lookback bars.
    # Bounding the slice avoids repeatedly scanning a stock's full 1000-bar history.
    factor_rows = rows[max(0, index - 120):index + 1]
    closes = [float(row["close"]) for row in factor_rows]
    highs = [float(row["high"]) for row in factor_rows]
    lows = [float(row["low"]) for row in factor_rows]
    volumes = [float(row.get("volume", 0) or 0) for row in factor_rows]
    def avg(values): return mean(values)
    def ret(window): return closes[-1] / closes[-window-1] - 1 if len(closes) > window and closes[-window-1] else None
    if re.match(r"^(momentum|reversal|breakout|drawdown|volatility|downside_vol|up_ratio)_\d+$", key):
        prefix, window_text = key.rsplit("_", 1); window = int(window_text)
        if prefix == "momentum": return ret(window)
        if prefix == "reversal": return -ret(window) if ret(window) is not None else None
        if prefix == "breakout":
            high = max(closes[-window-1:-1]) if len(closes) > window else None
            return closes[-1] / high - 1 if high else None
        if prefix == "drawdown":
            high = max(closes[-window:]) if len(closes) >= window else None
            return closes[-1] / high - 1 if high else None
        returns = [(closes[pos] / closes[pos - 1] - 1) for pos in range(1, len(closes)) if closes[pos - 1]]
        if prefix == "volatility": return statistics.pstdev(returns[-window:]) if len(returns) >= window else None
        if prefix == "downside_vol":
            downside = [value for value in returns[-window:] if value < 0]
            return statistics.pstdev(downside) if len(downside) >= 2 else None
        if prefix == "up_ratio": return sum(value > 0 for value in returns[-window:]) / min(window, len(returns)) if returns else None
    if key.startswith("ma_") or key.startswith("volume_ratio_"):
        parts = key.split("_"); fast, slow = int(parts[-2]), int(parts[-1])
        short, long = closes if key.startswith("ma_") else volumes, closes if key.startswith("ma_") else volumes
        return avg(short[-fast:]) / avg(long[-slow:]) - (1 if key.startswith("ma_") else 0) if len(long) >= slow and avg(long[-slow:]) else None
    if key.startswith("range_position_") or key.startswith("atr_"):
        window = int(key.rsplit("_", 1)[1])
        if key.startswith("range_position_"):
            if len(lows) < window:
                return None
            high, low = max(highs[-window:]), min(lows[-window:])
            return (closes[-1] - low) / (high - low) if low is not None and high != low else None
        if len(closes) < window + 1: return None
        ranges = [max(highs[pos] - lows[pos], abs(highs[pos] - closes[pos - 1]), abs(lows[pos] - closes[pos - 1])) for pos in range(1, len(closes))]
        return avg(ranges[-window:]) / closes[-1] if ranges and closes[-1] else None
    if key.startswith("momentum"):
        return ret(int(key.replace("momentum", "")))
    if key == "ma5_20": return avg(closes[-5:]) / avg(closes[-20:]) - 1 if len(closes) >= 20 and avg(closes[-20:]) else None
    if key == "ma10_20": return avg(closes[-10:]) / avg(closes[-20:]) - 1 if len(closes) >= 20 and avg(closes[-20:]) else None
    if key == "breakout20":
        high = max(closes[-21:-1]) if len(closes) >= 21 else None
        return closes[-1] / high - 1 if high else None
    if key == "drawdown20":
        high = max(closes[-20:]) if len(closes) >= 20 else None
        return closes[-1] / high - 1 if high else None
    if key == "range_position20":
        if len(lows) < 20:
            return None
        high, low = max(highs[-20:]), min(lows[-20:])
        return (closes[-1] - low) / (high - low) if low is not None and high != low else None
    if key == "volume_ratio5_20": return avg(volumes[-5:]) / avg(volumes[-20:]) if len(volumes) >= 20 and avg(volumes[-20:]) else None
    if key == "volume_surprise20": return volumes[-1] / avg(volumes[-20:]) if len(volumes) >= 20 and avg(volumes[-20:]) else None
    returns = [(closes[pos] / closes[pos - 1] - 1) for pos in range(1, len(closes)) if closes[pos - 1]]
    if key == "up_ratio20": return sum(value > 0 for value in returns[-20:]) / min(20, len(returns)) if returns else None
    if key == "volatility20": return statistics.pstdev(returns[-20:]) if len(returns) >= 20 else None
    if key == "downside_vol20":
        downside = [value for value in returns[-20:] if value < 0]
        return statistics.pstdev(downside) if len(downside) >= 2 else None
    if key == "atr14":
        ranges = []
        for pos in range(1, len(closes)):
            ranges.append(max(highs[pos] - lows[pos], abs(highs[pos] - closes[pos - 1]), abs(lows[pos] - closes[pos - 1])))
        return avg(ranges[-14:]) / closes[-1] if len(ranges) >= 14 and closes[-1] else None
    if key == "reversal5":
        value = ret(5)
        return -value if value is not None else None
    if key == "overnight_gap": return closes[-1] * 0 + (float(rows[index].get("open", 0)) / closes[-2] - 1) if len(closes) >= 2 and closes[-2] else None
    if key == "intraday_return": return closes[-1] / float(rows[index].get("open", 0)) - 1 if rows[index].get("open") else None
    if key == "close_location":
        span = highs[-1] - lows[-1]
        return (closes[-1] - lows[-1]) / span if span else None
    if key == "volume_trend5_60": return avg(volumes[-5:]) / avg(volumes[-60:]) if len(volumes) >= 60 and avg(volumes[-60:]) else None
    if key == "range_expansion20":
        if len(closes) < 21: return None
        true_ranges = [max(highs[pos] - lows[pos], abs(highs[pos] - closes[pos - 1]), abs(lows[pos] - closes[pos - 1])) for pos in range(1, len(closes))]
        return true_ranges[-1] / avg(true_ranges[-20:]) if true_ranges[-20:] and avg(true_ranges[-20:]) else None
    if key == "price_volume5":
        ratio = avg(volumes[-5:]) / avg(volumes[-20:]) if len(volumes) >= 20 and avg(volumes[-20:]) else None
        return ret(5) * ratio if ratio is not None else None
    return None

def latest_mined_factor_values(rows):
    """Materialize latest values once so dashboard requests never reread 930 files."""
    if not rows:
        return {}
    index = len(rows) - 1
    values = {}
    for key, _, _ in build_factor_search_space():
        value = _factor_value(rows, index, key)
        if value is not None and math.isfinite(value):
            values[key] = value
    return values

def completed_factor_snapshot(rows):
    completed = completed_daily_rows(rows)
    return {
        "completed_factor_date": completed[-1].get("date") if completed else None,
        "completed_history_bars": len(completed),
        "completed_mined_factors": latest_mined_factor_values(completed),
    }

def _rank(values):
    ordered = sorted((value, index) for index, value in enumerate(values))
    ranks = [0.0] * len(values)
    for rank, (_, index) in enumerate(ordered): ranks[index] = rank + 1
    return ranks

def completed_daily_rows(rows, now=None):
    """Exclude today's still-forming daily bar before the A-share close settles."""
    if not rows:
        return rows
    now = now or time.localtime()
    today = time.strftime("%Y-%m-%d", now)
    before_settlement = now.tm_hour * 100 + now.tm_min < 1510
    return rows[:-1] if before_settlement and rows[-1].get("date") == today else rows

def latest_completed_data_date(stocks):
    return max((rows[-1].get("date") for stock in stocks for rows in [completed_daily_rows(stock.get("klines", []))] if rows), default=None)

def liquidity_value(rows, index):
    window = rows[max(0, index - 19):index + 1]
    values = [float(row.get("close") or 0) * float(row.get("volume") or 0) for row in window]
    return mean(values) if len(values) >= 10 else 0.0

def liquid_codes_from_stocks(stocks, keep_ratio=0.70):
    values = []
    for stock in stocks:
        rows = completed_daily_rows(stock.get("klines", []))
        if rows:
            values.append((stock.get("code", ""), liquidity_value(rows, len(rows) - 1)))
    ordered = sorted(value for _, value in values if value > 0)
    cutoff = ordered[max(0, int(len(ordered) * (1 - keep_ratio)) - 1)] if ordered else 0
    return {code for code, value in values if value >= cutoff and value > 0}

def _correlation(left, right):
    if len(left) < 3 or len(left) != len(right): return 0.0
    left_mean, right_mean = mean(left), mean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    denominator = math.sqrt(sum((a - left_mean) ** 2 for a in left) * sum((b - right_mean) ** 2 for b in right))
    return numerator / denominator if denominator else 0.0

def mine_factor_candidates(stocks, candidates=None):
    usable = []
    for stock in stocks:
        history = completed_daily_rows(read_kline_cache(stock.get("code", "")) or stock.get("klines", []))
        if len(history) >= 30:
            usable.append({"stock": stock, "rows": history, "indexes": {row.get("date"): index for index, row in enumerate(history)}})
    results = []
    available_dates = sorted(set().union(*(set(item["indexes"]) for item in usable))) if usable else []
    sample_dates = available_dates[-83:-2]
    for key, name, formula in candidates or MINING_CANDIDATES:
        daily_ic, long_short, factor_series = [], [], []
        # Use actual trading dates, never positional row alignment. Signal is
        # known at T close and evaluated on the tradable T+1 -> T+2 close return.
        for date in sample_dates:
            cross_section = []
            for item in usable:
                rows = item["rows"]
                index = item["indexes"].get(date)
                if index is None or index + 2 >= len(rows): continue
                value = _factor_value(rows, index, key)
                entry_close = rows[index + 1].get("close")
                next_return = rows[index + 2]["close"] / entry_close - 1 if entry_close else None
                if value is not None and next_return is not None and math.isfinite(value) and math.isfinite(next_return):
                    cross_section.append((value, next_return))
            if len(cross_section) < 20: continue
            factors, targets = zip(*cross_section)
            ic = _correlation(_rank(list(factors)), _rank(list(targets)))
            ordered = sorted(cross_section, key=lambda pair: pair[0])
            bucket = max(1, len(ordered) // 5)
            long_short.append(mean([x[1] for x in ordered[-bucket:]]) - mean([x[1] for x in ordered[:bucket]]))
            daily_ic.append(ic)
            factor_series.extend(factors)
        if len(daily_ic) < 10:
            results.append({"key": key, "name": name, "formula": formula, "status": "数据不足", "sample_days": len(daily_ic)})
            continue
        avg_ic = mean(daily_ic)
        ic_std = statistics.pstdev(daily_ic)
        results.append({"key": key, "name": name, "formula": formula, "status": "待验证", "sample_days": len(daily_ic),
                        "mean_rank_ic": round(avg_ic, 4), "icir": round(avg_ic / ic_std, 3) if ic_std else 0,
                        "ic_positive_rate": round(sum(value > 0 for value in daily_ic) / len(daily_ic), 3),
                        "mean_long_short": round(mean(long_short), 5)})
    ranked = sorted([item for item in results if item.get("mean_rank_ic") is not None], key=lambda item: abs(item["mean_rank_ic"]), reverse=True)
    for item in ranked:
        item["direction"] = "正向" if item["mean_rank_ic"] >= 0 else "反向"
        item["effective_rank_ic"] = round(abs(item["mean_rank_ic"]), 4)
        item["effective_long_short"] = round(item["mean_long_short"] if item["mean_rank_ic"] >= 0 else -item["mean_long_short"], 5)
        item["status"] = "候选" if abs(item["mean_rank_ic"]) >= 0.03 and item.get("sample_days", 0) >= 20 else "弱信号"
    return {"universe": len(stocks), "usable_stocks": len(usable), "history_bars": min((len(item["rows"]) for item in usable), default=0), "sample_days": len(sample_dates), "candidate_count": len(results), "results": results,
            "warning": "按真实交易日对齐；T 日收盘计算因子，评价 T+1 收盘至 T+2 收盘收益。当前 81 日窗口用于交互筛选，正式生产仍需滚动样本外验证。"}

def backtest_factor(stocks, factor_key, direction):
    """Single-factor backtest delegates to the canonical combo engine."""
    return backtest_factor_combo(stocks, [(factor_key, 1 if direction == "正向" else -1)])

def prepare_backtest_universe(stocks):
    """Load and index each stock's history once for a complete experiment run."""
    usable = []
    for stock in stocks:
        rows = completed_daily_rows(read_kline_cache(stock.get("code", "")) or stock.get("klines", []))
        if len(rows) >= 122:
            usable.append((stock.get("code", ""), rows, {row.get("date"): index for index, row in enumerate(rows)}))
    return usable

def prepare_backtest_dataset(stocks, factor_keys):
    """Precompute the shared date/stock factor matrix for every combination."""
    usable = prepare_backtest_universe(stocks)
    observations = {}
    latest_data_date = max((rows[-1].get("date") or "" for _, rows, _ in usable if rows), default="")
    latest = []
    for code, rows, _ in usable:
        for index in range(120, len(rows) - 2):
            values = {key: _factor_value(rows, index, key) for key in factor_keys}
            values["__liquidity"] = liquidity_value(rows, index)
            entry_close = rows[index + 1].get("close")
            target = rows[index + 2]["close"] / entry_close - 1 if entry_close else None
            if target is None or not math.isfinite(target):
                continue
            observations.setdefault(rows[index].get("date"), []).append((code, values, target))
        if rows and rows[-1].get("date") == latest_data_date:
            index = len(rows) - 1
            values = {key: _factor_value(rows, index, key) for key in factor_keys}
            values["__liquidity"] = liquidity_value(rows, index)
            latest.append((code, values))
    return {"usable": usable, "observations": observations, "latest": latest, "latest_data_date": latest_data_date}

def backtest_factor_combo(stocks, factors, prepared=None):
    """Rank at T close, enter at T+1 close, exit at T+2 close; Top 3 equal weight."""
    prepared = prepared if isinstance(prepared, dict) else prepare_backtest_dataset(stocks, [key for key, _ in factors])
    usable = prepared["usable"]
    daily = []; previous = set(); latest_selected = []; cost_rate = 0.0015
    for date in sorted(prepared["observations"]):
        date_rows = prepared["observations"][date]
        liquidities = sorted(values.get("__liquidity", 0) for _, values, _ in date_rows if values.get("__liquidity", 0) > 0)
        liquidity_cutoff = liquidities[max(0, int(len(liquidities) * 0.30) - 1)] if liquidities else 0
        cross = [(code, [values.get(key) for key, _ in factors], target) for code, values, target in date_rows
                 if values.get("__liquidity", 0) >= liquidity_cutoff and values.get("__liquidity", 0) > 0
                 if all(values.get(key) is not None and math.isfinite(values[key]) for key, _ in factors)]
        if len(cross) < 20: continue
        ranks = [_rank([row[1][col] for row in cross]) for col in range(len(factors))]
        scored = [(cross[pos][0], mean([ranks[col][pos] * direction for col, (_, direction) in enumerate(factors)]), cross[pos][2]) for pos in range(len(cross))]
        bucket = min(3, len(scored)); selected = sorted(scored, key=lambda row: row[1], reverse=True)[:bucket]
        selected_codes = {row[0] for row in selected}; turnover = 1.0 if not previous else 1 - len(previous & selected_codes) / bucket
        daily.append(mean([row[2] for row in selected]) - cost_rate * turnover); previous = selected_codes
        latest_selected = selected
    if len(daily) < 20: return {"status":"数据不足", "days":len(daily)}
    equity = 1.0; peak = 1.0; max_drawdown = 0.0
    for value in daily:
        equity *= 1 + value; peak = max(peak, equity); max_drawdown = max(max_drawdown, 1 - equity / peak)
    avg, sd = mean(daily), statistics.pstdev(daily)
    names = {stock.get("code", ""): stock.get("name", stock.get("code", "")) for stock in stocks}
    latest_liquidities = sorted(values.get("__liquidity", 0) for _, values in prepared["latest"] if values.get("__liquidity", 0) > 0)
    latest_cutoff = latest_liquidities[max(0, int(len(latest_liquidities) * 0.30) - 1)] if latest_liquidities else 0
    latest_cross = [(code, [values.get(key) for key, _ in factors]) for code, values in prepared["latest"]
                    if values.get("__liquidity", 0) >= latest_cutoff and values.get("__liquidity", 0) > 0
                    if all(values.get(key) is not None and math.isfinite(values[key]) for key, _ in factors)]
    if latest_cross:
        latest_ranks = [_rank([row[1][col] for row in latest_cross]) for col in range(len(factors))]
        latest_selected = sorted([(latest_cross[pos][0], mean([latest_ranks[col][pos] * direction for col, (_, direction) in enumerate(factors)])) for pos in range(len(latest_cross))], key=lambda row: row[1], reverse=True)[:3]
    latest_holdings = [{"code": row[0], "name": names.get(row[0], row[0])} for row in latest_selected]
    cumulative = equity - 1
    annual = equity ** (252 / len(daily)) - 1
    annualization_reliable = len(daily) >= 126
    return {"status":"完成" if annualization_reliable else "短样本，仅供参考", "portfolio":"Top 3 等权组合，每只 1/3，不是单只股票", "universe":len(usable), "holding_count":len(latest_holdings), "latest_holdings":latest_holdings, "days":len(daily), "cost_bps":15, "mean_daily":round(avg, 6), "cumulative_return":round(cumulative, 4), "annual_return":round(annual, 4), "sharpe":round(avg / sd * math.sqrt(252), 3) if sd else 0, "max_drawdown":round(max_drawdown, 4), "win_rate":round(sum(value > 0 for value in daily) / len(daily), 3), "equity":round(equity, 4), "audited":True, "annualization_reliable":annualization_reliable, "return_definition":"T 日收盘生成信号，T+1 收盘建仓，T+2 收盘退出", "trade_delay_days":1}

def run_backtest_experiment(stocks):
    latest = read_factor_agent_run()
    universe_signature = universe_signature_for_stocks(stocks)
    # Refresh factor validation when the fixed universe changes instead of
    # returning a dead-end 409 or silently reusing incompatible research.
    if not latest or latest.get("universe_signature") != universe_signature:
        latest = run_factor_agent_loop(stocks)
    dashboard_date = latest_completed_data_date(stocks)
    cached = read_backtest_run()
    if cached and cached.get("engine_version") == BACKTEST_ENGINE_VERSION and cached.get("universe_signature") == universe_signature and cached.get("data_date") == dashboard_date and cached.get("factor_run_generated_at") == latest.get("generated_at"):
        return {**cached, "cache_hit": True}
    pool = [item for item in latest.get("validation", {}).get("results", []) if item.get("gate") == "通过"]
    if len(pool) < 4:
        pool = [item for item in latest.get("validation", {}).get("results", []) if item.get("mean_rank_ic") is not None]
    def family(item):
        key = item.get("key", "")
        if key.startswith("ma"): return "均线结构"
        if key.startswith(("momentum", "reversal")): return "动量反转"
        if key.startswith(("volume", "price_volume")): return "成交量"
        if key.startswith(("range_position", "breakout", "drawdown", "close_location")): return "价格位置"
        if key.startswith(("volatility", "downside_vol", "atr", "range_expansion")): return "波动风险"
        return key.split("_")[0]
    candidates, seen_families = [], set()
    for item in pool:
        group = family(item)
        if group in seen_families:
            continue
        candidates.append(item); seen_families.add(group)
        if len(candidates) == 4:
            break
    if len(candidates) < 2:
        raise RuntimeError("去重后可用因子不足 2 个，请重新运行因子挖掘")
    factors = [(item["key"], 1 if item.get("direction") == "正向" else -1) for item in candidates]
    prepared = prepare_backtest_dataset(stocks, [key for key, _ in factors])
    runs = []
    for size in [1, 2, 3]:
        for combo in combinations(factors, size):
            result = backtest_factor_combo(stocks, combo, prepared=prepared)
            combo_items = [next(item for item in candidates if item["key"] == key) for key, _ in combo]
            runs.append({"kind":f"{size} 因子" if size == 1 else f"{size} 因子组合", "factor_count":size, "factors":[key for key, _ in combo], "names":[item["name"] for item in combo_items], "directions":[item.get("direction", "待定") for item in combo_items], "result":result})
    runs.sort(key=lambda item: (item["result"].get("audited", False), item["result"].get("sharpe", -999), item["result"].get("cumulative_return", -999)), reverse=True)
    for index, item in enumerate(runs, 1):
        item["rank"] = index
        item["rank_metric"] = "成本后 Sharpe"
    selected_names = [TECH_SECTOR_DEFINITIONS[key]["name"] for key in read_tech_config()]
    usable_count = len(prepared["usable"])
    payload = {"generated_at":time.strftime("%Y-%m-%d %H:%M:%S"), "engine_version":BACKTEST_ENGINE_VERSION, "factor_run_generated_at":latest.get("generated_at"), "candidate_count":len(candidates), "combination_count":len(runs), "screen_universe":len(stocks), "confirmation_universe":len(stocks), "usable_universe":usable_count, "universe_signature":universe_signature, "universe_name":"科技股池（不含科创板/ST）", "selected_sectors":selected_names, "cost_bps":15, "ranking_metric":"成本后 Sharpe（同 Sharpe 时按成本后累计收益）", "data_date":dashboard_date, "results":runs, "selection_rule":"T 日收盘计算因子，先剔除近 20 日成交额最低 30%，再做截面排名并取最高 3 只等权；T+1 收盘建仓，T+2 收盘退出，扣除 15bp × 换手率。", "warning":f"{len(runs)} 个策略均直接在完整真实科技股池回测；其中 {usable_count} 只有足够历史。已排除 688/689、ST/退市风险及每日流动性后 30%。结果仍属于样本内研究，不等同成交回报。"}
    CACHE_DIR.mkdir(parents=True, exist_ok=True); temporary = BACKTEST_RUN_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"); os.replace(temporary, BACKTEST_RUN_FILE)
    return payload

def run_factor_agent_loop(stocks):
    """Research hypotheses -> executable candidates -> local validation -> feedback."""
    universe_signature = universe_signature_for_stocks(stocks)
    dashboard_date = latest_completed_data_date(stocks)
    cached = read_factor_agent_run()
    if cached and cached.get("engine_version") == FACTOR_ENGINE_VERSION and cached.get("universe_signature") == universe_signature and cached.get("data_date") == dashboard_date:
        return {**cached, "cache_hit": True}
    proposal_map = {item["candidate"]: item for item in FACTOR_RESEARCH_PROPOSALS}
    search_space = build_factor_search_space()
    # Stage 1: deterministic stratified slice for broad search. Stage 2 below
    # confirms the strongest candidates on the complete real universe.
    ordered_stocks = sorted(stocks, key=lambda item: item.get("code", ""))
    search_stocks = ordered_stocks[::4]
    validation = mine_factor_candidates(search_stocks, search_space)
    broad_top = sorted([item for item in validation["results"] if item.get("mean_rank_ic") is not None], key=lambda item: abs(item["mean_rank_ic"]), reverse=True)[:12]
    broad_keys = {item["key"] for item in broad_top}
    confirmation_candidates = [item for item in search_space if item[0] in broad_keys]
    confirmation = mine_factor_candidates(stocks, confirmation_candidates)
    confirmation_by_key = {item["key"]: item for item in confirmation["results"]}
    results = []
    for result in validation["results"]:
        if result["key"] in confirmation_by_key:
            result = {**result, "broad_search": result.copy(), "confirmation": result.get("confirmation")}
            confirmed = confirmation_by_key[result["key"]]
            result.update({key: value for key, value in confirmed.items() if key not in {"key", "name", "formula"}})
        proposal = proposal_map.get(result["key"])
        if proposal:
            result = {**result, "proposal_id": proposal["id"], "hypothesis": proposal["hypothesis"]}
        if result.get("mean_rank_ic") is not None:
            ic = result["mean_rank_ic"]
            positive = result.get("ic_positive_rate", 0) if ic >= 0 else 1 - result.get("ic_positive_rate", 0)
            long_short = result.get("mean_long_short", 0) if ic >= 0 else -result.get("mean_long_short", 0)
            result.update({"direction": "正向" if ic >= 0 else "反向", "effective_rank_ic": round(abs(ic), 4),
                           "effective_ic_positive_rate": round(positive, 3), "effective_long_short": round(long_short, 5)})
            result["gate"] = "通过" if (result.get("sample_days", 0) >= 60 and abs(ic) >= .03 and abs(result.get("icir", 0)) >= .15 and positive >= .5 and long_short > .0005) else "淘汰/待验证"
        results.append(result)
    results.sort(key=lambda item: abs(item.get("mean_rank_ic", 0)), reverse=True)
    backtest_items = [item for item in results[:4] if item.get("mean_rank_ic") is not None]
    if backtest_items:
        prepared_backtests = prepare_backtest_dataset(stocks, [item["key"] for item in backtest_items])
        for item in backtest_items:
            direction = 1 if item.get("direction") == "正向" else -1
            item["backtest"] = backtest_factor_combo(stocks, [(item["key"], direction)], prepared=prepared_backtests)
    passed = [item for item in results if item.get("gate") == "通过"]
    feedback = (f"本轮 {len(results)} 个公式中 {len(passed)} 个通过验证门槛。" if passed else
                "本轮没有因子通过生产门槛；下一轮应扩展财务/行业数据或调整搜索空间，不能把弱信号直接用于交易。")
    payload = {"stages": [
        {"name":"研究 Agent", "status":"完成", "detail":f"提出 {len(FACTOR_RESEARCH_PROPOSALS)} 个带假设候选"},
        {"name":"开发 Agent", "status":"完成", "detail":f"映射为 {len(search_space)} 个白名单可执行公式"},
        {"name":"验证 Agent", "status":"完成", "detail":f"先筛 {len(search_stocks)} 只，再用全量 {len(stocks)} 只确认前 {len(confirmation_candidates)} 个"},
        {"name":"反馈迭代", "status":"完成", "detail":feedback},
    ], "engine_version": FACTOR_ENGINE_VERSION, "data_date": dashboard_date, "search_space_size": len(search_space), "search_universe": len(search_stocks), "confirmation_universe": len(stocks), "universe_signature": universe_signature, "universe_name": "科技股池（不含科创板/ST）", "selected_sectors": [TECH_SECTOR_DEFINITIONS[key]["name"] for key in read_tech_config()], "proposals": FACTOR_RESEARCH_PROPOSALS, "validation": {**validation, "universe": len(stocks), "usable_stocks": validation.get("usable_stocks", 0), "history_bars": confirmation.get("history_bars", 0), "results": results}, "feedback": feedback,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = FACTOR_AGENT_RUN_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, FACTOR_AGENT_RUN_FILE)
    return payload

def load_universe():
    try:
        payload = json.loads(GUPIAO_UNIVERSE_FILE.read_text(encoding="utf-8"))
        codes = payload.get("codes", []) if isinstance(payload, dict) else payload
        items = []
        for raw in codes:
            code = str(raw).zfill(6)
            if code.startswith(("688", "689", "302")):
                continue
            market = "1" if code.startswith("6") else "0"
            board = "创业板" if code.startswith(("300", "301")) else ("沪市主板" if market == "1" else "深市主板")
            items.append({"code": code, "name": code, "market": board, "secid": f"{market}.{code}", "initial": code[-1]})
        if items:
            return items
    except Exception:
        pass
    return []

UNIVERSE = load_universe()

def mean(values):
    return sum(values) / len(values) if values else 0.0

def universe_signature_for_stocks(stocks):
    sectors = ",".join(read_tech_config())
    codes = ",".join(sorted(stock.get("code", "") for stock in stocks))
    return f"{sectors}|{hashlib.sha256(codes.encode('utf-8')).hexdigest()[:16]}"

def eastmoney_json(url, jsonp=False):
    last_error = None
    for attempt in range(3):
        try:
            result = subprocess.run(["curl", "-L", "--max-time", "12", "-sS", "-H", "User-Agent: Mozilla/5.0", "-H", "Referer: https://quote.eastmoney.com/", url], capture_output=True, text=True, timeout=15, check=True)
            text = result.stdout.strip()
            if jsonp:
                match = re.search(r"\((\[.*\])\)\s*;?\s*$", text, re.S)
                if not match:
                    raise ValueError("invalid Sina JSONP response")
                return json.loads(match.group(1))
            return json.loads(text)
        except Exception as exc:
            last_error = exc
            time.sleep(0.4 * (attempt + 1))
    raise last_error

def http_text(url):
    last_error = None
    for attempt in range(3):
        try:
            result = subprocess.run(["curl", "-L", "--max-time", "12", "-sS", "-A", "Mozilla/5.0", url], capture_output=True, timeout=15, check=True)
            return result.stdout.decode("gbk", "replace")
        except Exception as exc:
            last_error = exc
            time.sleep(0.35 * (attempt + 1))
    raise last_error

def _atomic_json_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)

def _eastmoney_clist_page(fs, fields, page):
    url = ("https://push2.eastmoney.com/api/qt/clist/get"
           f"?pn={page}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f12&fs={fs}&fields={fields}")
    return (eastmoney_json(url).get("data") or {})

def fetch_eastmoney_clist(fs, fields):
    """Read a complete Eastmoney list despite its effective 100-row page cap."""
    first = _eastmoney_clist_page(fs, fields, 1)
    rows = list(first.get("diff") or [])
    total = int(first.get("total") or len(rows))
    page_count = max(1, math.ceil(total / 100))
    if page_count == 1:
        return rows
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="eastmoney-list") as pool:
        futures = {pool.submit(_eastmoney_clist_page, fs, fields, page): page for page in range(2, page_count + 1)}
        pages = {}
        for future in as_completed(futures):
            pages[futures[future]] = list(future.result().get("diff") or [])
    for page in range(2, page_count + 1):
        rows.extend(pages.get(page, []))
    return rows

def normalize_industry(name):
    return re.sub(r"(?:Ⅰ|Ⅱ|Ⅲ|IV|III|II)$", "", str(name or "").strip())

def build_tech_universe_snapshot():
    """Build a real A-share technology taxonomy from Eastmoney industries/concept boards."""
    market_rows = fetch_eastmoney_clist("m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23", "f12,f14,f100")
    market = {}
    for row in market_rows:
        code = str(row.get("f12") or "")
        if len(code) != 6 or not code.isdigit() or code.startswith(("688", "689")):
            continue
        market[code] = {"code": code, "name": row.get("f14") or code, "industry": row.get("f100") or "未分类"}

    concept_members = {key: set() for key in TECH_SECTOR_DEFINITIONS}
    matched_boards = {key: [] for key in TECH_SECTOR_DEFINITIONS}
    try:
        boards = fetch_eastmoney_clist("m:90+t:3", "f12,f14")
        jobs = []
        for board in boards:
            board_code, board_name = str(board.get("f12") or ""), str(board.get("f14") or "")
            for key, definition in TECH_SECTOR_DEFINITIONS.items():
                if board_name in definition["concepts"]:
                    matched_boards[key].append({"code": board_code, "name": board_name})
                    jobs.append((key, board_code, board_name))
        with ThreadPoolExecutor(max_workers=8, thread_name_prefix="concept-members") as pool:
            futures = {pool.submit(fetch_eastmoney_clist, f"b:{board_code}", "f12,f14"): (key, board_name) for key, board_code, board_name in jobs}
            for future in as_completed(futures):
                key, _ = futures[future]
                for row in future.result():
                    code = str(row.get("f12") or "")
                    if len(code) == 6 and code.isdigit() and not code.startswith(("688", "689")):
                        concept_members[key].add(code)
    except Exception:
        # Industry classification remains usable if concept-board service is temporarily unavailable.
        pass

    stocks = {}
    counts = {key: 0 for key in TECH_SECTOR_DEFINITIONS}
    for code, item in market.items():
        sectors = []
        normalized_industry = normalize_industry(item["industry"])
        for key, definition in TECH_SECTOR_DEFINITIONS.items():
            direct_industries = {normalize_industry(value) for value in definition["industries"]}
            concept_allowed = TECH_CONCEPT_ALLOWED_INDUSTRIES[key]
            if normalized_industry in direct_industries or (code in concept_members[key] and normalized_industry in concept_allowed):
                sectors.append(key)
                counts[key] += 1
        if sectors:
            stocks[code] = {**item, "sectors": sectors}
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "东方财富行业分类与概念板块成分",
        "rules": "A股科技行业/概念并集；硬排除 688/689 科创板；创业板 300/301 保留",
        "sector_definitions": [{"key": key, "name": value["name"], "count": counts[key], "boards": matched_boards[key]} for key, value in TECH_SECTOR_DEFINITIONS.items()],
        "stock_count": len(stocks),
        "stocks": stocks,
    }
    _atomic_json_write(TECH_UNIVERSE_FILE, payload)
    return payload

def sanitize_tech_universe_snapshot(payload):
    """Re-apply the strict industry gate to cached concept memberships."""
    cleaned = {}
    counts = {key: 0 for key in TECH_SECTOR_DEFINITIONS}
    for code, item in (payload.get("stocks") or {}).items():
        if str(code).startswith(("688", "689")):
            continue
        industry = normalize_industry(item.get("industry"))
        sectors = []
        for key in item.get("sectors", []):
            if key not in TECH_SECTOR_DEFINITIONS:
                continue
            direct = {normalize_industry(value) for value in TECH_SECTOR_DEFINITIONS[key]["industries"]}
            if industry in direct or industry in TECH_CONCEPT_ALLOWED_INDUSTRIES[key]:
                sectors.append(key)
                counts[key] += 1
        if sectors:
            cleaned[code] = {**item, "sectors": sectors}
    payload = {**payload, "rules": "A股科技行业/概念并集，概念需通过科技主营行业准入；硬排除 688/689 科创板；创业板 300/301 保留", "stock_count": len(cleaned), "stocks": cleaned}
    definitions = {item.get("key"): item for item in payload.get("sector_definitions", [])}
    payload["sector_definitions"] = [{**definitions.get(key, {}), "key": key, "name": value["name"], "count": counts[key]} for key, value in TECH_SECTOR_DEFINITIONS.items()]
    return payload

def read_tech_universe_snapshot(force=False):
    if not force and TECH_UNIVERSE_FILE.exists():
        try:
            payload = json.loads(TECH_UNIVERSE_FILE.read_text(encoding="utf-8"))
            cleaned = sanitize_tech_universe_snapshot(payload)
            if cleaned.get("stock_count") != payload.get("stock_count"):
                _atomic_json_write(TECH_UNIVERSE_FILE, cleaned)
            return cleaned
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    try:
        return sanitize_tech_universe_snapshot(build_tech_universe_snapshot())
    except Exception:
        if TECH_UNIVERSE_FILE.exists():
            cleaned = sanitize_tech_universe_snapshot(json.loads(TECH_UNIVERSE_FILE.read_text(encoding="utf-8")))
            _atomic_json_write(TECH_UNIVERSE_FILE, cleaned)
            return cleaned
        raise

def read_tech_config():
    try:
        payload = json.loads(TECH_CONFIG_FILE.read_text(encoding="utf-8"))
        selected = [key for key in payload.get("selected_sectors", []) if key in TECH_SECTOR_DEFINITIONS]
        if selected:
            return selected
    except (OSError, json.JSONDecodeError):
        pass
    return list(DEFAULT_TECH_SECTORS)

def write_tech_config(selected):
    selected = [key for key in selected if key in TECH_SECTOR_DEFINITIONS]
    if not selected:
        raise ValueError("至少选择一个科技细分板块")
    _atomic_json_write(TECH_CONFIG_FILE, {"selected_sectors": selected, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    return selected

def tech_universe_items(base_items, hot_codes=None):
    snapshot = read_tech_universe_snapshot()
    selected = read_tech_config()
    metadata = snapshot.get("stocks", {})
    result = []
    for code, meta in metadata.items():
        if code.startswith(("688", "689", "302")):
            continue
        name = meta.get("name") or code
        if "ST" in name.upper() or "退" in name:
            continue
        industry = normalize_industry(meta.get("industry"))
        metadata_sectors = set(meta.get("sectors", []))
        sectors = []
        for key in selected:
            direct = {normalize_industry(value) for value in TECH_SECTOR_DEFINITIONS[key]["industries"]}
            if industry in direct or (key in FIXED_CONCEPT_SECTORS and key in metadata_sectors):
                sectors.append(key)
        if "glass_substrate" in selected and code in GLASS_SUBSTRATE_MEMBERS:
            sectors.append("glass_substrate")
        if not sectors:
            continue
        market = "1" if code.startswith("6") else "0"
        board = "创业板" if code.startswith(("300", "301")) else ("沪市主板" if market == "1" else "深市主板")
        result.append({"code": code, "name": name, "market": board, "secid": f"{market}.{code}", "initial": name[:1],
                       "industry": meta.get("industry", "未分类"), "tech_sectors": list(dict.fromkeys(sectors)),
                       "tech_sector_names": [TECH_SECTOR_DEFINITIONS[key]["name"] for key in sectors]})
    result.sort(key=lambda item: item["code"])
    return result, snapshot, selected

def export_fixed_tech_universe(items, snapshot, selected):
    """Persist the exact versioned membership used by ranking and backtests."""
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "classification_snapshot_at": snapshot.get("generated_at"),
        "source": "东方财富行业/概念快照 + 同花顺玻璃基板核验名单",
        "rules": "固定科技全景股池；排除688/689/302及ST/退市风险；不并入人气榜",
        "selected_sectors": selected,
        "stock_count": len(items),
        "stocks": [{key: item.get(key) for key in ("code", "name", "market", "industry", "tech_sectors", "tech_sector_names")} for item in items],
    }
    _atomic_json_write(FIXED_TECH_UNIVERSE_FILE, payload)
    return payload

def fetch_hot_rank_codes():
    """Fetch the real Eastmoney current popularity Top100."""
    url = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
    body = json.dumps({"appId":"appId01", "globalId":"786e4c21-70dc-435a-93bb-38", "marketType":"", "pageNo":1, "pageSize":100})
    result = subprocess.run(["curl", "-L", "--max-time", "15", "-sS", "-A", "Mozilla/5.0", "-H", "Content-Type: application/json", "-X", "POST", "--data", body, url], capture_output=True, text=True, timeout=18, check=True)
    rows = json.loads(result.stdout).get("data") or []
    codes = []
    for row in rows:
        raw = str(row.get("sc", ""))
        code = raw[-6:] if len(raw) >= 6 else ""
        if len(code) == 6 and code.isdigit() and not code.startswith(("688", "689", "302")):
            codes.append(code)
    return list(dict.fromkeys(codes))[:100]

def update_hot_history(codes):
    today = time.strftime("%Y-%m-%d")
    try:
        history = json.loads(HOT_HISTORY_FILE.read_text(encoding="utf-8")) if HOT_HISTORY_FILE.exists() else []
        history = [row for row in history if row.get("date") != today]
        history.append({"date": today, "codes": codes})
        history = history[-15:]
        HOT_HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
        return history
    except Exception:
        return [{"date": today, "codes": codes}]

def fetch_stock_tencent(item):
    """Use Tencent's real-time quote plus qfq daily bars as the primary A-share source."""
    market, code = item["secid"].split(".")
    symbol = ("sh" if market == "1" else "sz") + code
    quote_text = http_text(f"https://qt.gtimg.cn/q={symbol}")
    match = re.search(r'="(.*)"', quote_text)
    if not match:
        raise ValueError("invalid Tencent quote response")
    parts = match.group(1).split("~")
    if len(parts) < 39 or not parts[3]:
        raise ValueError("Tencent quote is empty")
    current, prev_close = float(parts[3]), float(parts[4] or 0)
    quote_pct, quote_change = float(parts[32] or 0), float(parts[31] or 0)
    quote_time = parts[30]
    quote_name = parts[1] or item["name"]
    kline_text = http_text(f"http://ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,1000,qfq")
    kline_json = json.loads(kline_text)
    rows = (kline_json.get("data", {}).get(symbol, {}).get("qfqday") or
            kline_json.get("data", {}).get(symbol, {}).get("day") or [])
    klines = []
    for row in rows:
        if len(row) < 6:
            continue
        try:
            klines.append({"date": row[0], "open": float(row[1]), "close": float(row[2]), "high": float(row[3]), "low": float(row[4]), "volume": float(row[5]), "amount": 0, "pct": 0, "turnover": 0})
        except (TypeError, ValueError):
            continue
    if not klines:
        raise ValueError("Tencent K-line is empty")
    klines = klines[-1000:]
    klines = write_kline_cache(code, klines)
    closes = [x["close"] for x in klines]; volumes = [x["volume"] for x in klines]
    returns = [(closes[i] / closes[i-1] - 1) for i in range(1, len(closes)) if closes[i-1]]
    def avg(xs): return sum(xs) / len(xs) if xs else 0.0
    def change(window): return closes[-1] / closes[-window-1] - 1 if len(closes) > window and closes[-window-1] else 0.0
    momentum20, momentum60 = change(20), change(60); ma5, ma20 = avg(closes[-5:]), avg(closes[-20:]); trend = ma5 / ma20 - 1 if ma20 else 0.0
    volume_ratio = avg(volumes[-5:]) / avg(volumes[-20:]) if len(volumes) >= 20 and avg(volumes[-20:]) else 1.0
    vol20 = statistics.pstdev(returns[-20:]) * math.sqrt(252) if len(returns) >= 20 else 0.0
    return {**item, "name": quote_name, "price": current, "change": quote_change, "pct": quote_pct, "amount": float(parts[37] or 0), "volume": float(parts[6] or 0), "quote_time": quote_time, "last_date": klines[-1]["date"], "klines": klines[-60:], "mined_factors": latest_mined_factor_values(klines), **completed_factor_snapshot(klines), "raw": {}, "source": "tencent", "source_label": "腾讯实时 + 前复权日线", "factors": {"momentum20": momentum20, "momentum60": momentum60, "trend": trend, "volume_ratio": volume_ratio, "volatility20": vol20}}

def fetch_stock(item):
    fields = "f43,f44,f45,f46,f47,f48,f57,f58,f60,f169,f170,f162,f163"
    quote_url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={item['secid']}&fields={fields}"
    kline_url = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={item['secid']}"
                 "&klt=101&fqt=1&beg=20240101&end=20991231"
                 "&fields1=f1,f2,f3,f4&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61")
    quote = eastmoney_json(quote_url).get("data") or {}
    raw_klines = (eastmoney_json(kline_url).get("data") or {}).get("klines") or []
    klines = []
    for raw in raw_klines:
        p = raw.split(",")
        if len(p) < 11:
            continue
        try:
            klines.append({"date": p[0], "open": float(p[1]), "close": float(p[2]), "high": float(p[3]),
                           "low": float(p[4]), "volume": float(p[5]), "amount": float(p[6]),
                           "pct": float(p[8]), "turnover": float(p[10])})
        except ValueError:
            continue
    klines = klines[-1000:]
    klines = write_kline_cache(item["code"], klines)
    closes = [x["close"] for x in klines]
    volumes = [x["volume"] for x in klines]
    returns = [(closes[i] / closes[i-1] - 1) for i in range(1, len(closes)) if closes[i-1]]
    def mean(xs): return sum(xs) / len(xs) if xs else 0.0
    def pct_change(window): return closes[-1] / closes[-window-1] - 1 if len(closes) > window and closes[-window-1] else 0.0
    momentum20 = pct_change(20)
    momentum60 = pct_change(60)
    ma5, ma20 = mean(closes[-5:]), mean(closes[-20:])
    trend = ma5 / ma20 - 1 if ma20 else 0.0
    volume_ratio = mean(volumes[-5:]) / mean(volumes[-20:]) if len(volumes) >= 20 and mean(volumes[-20:]) else 1.0
    vol20 = statistics.pstdev(returns[-20:]) * math.sqrt(252) if len(returns) >= 20 else 0.0
    price = (quote.get("f43") or 0) / 100
    change = (quote.get("f169") or 0) / 100
    pct = (quote.get("f170") or 0) / 100
    return {**item, "name": quote.get("f58") or item["name"], "price": price, "change": change, "pct": pct, "amount": quote.get("f48") or 0, "source": "eastmoney", "source_label": "东方财富公开行情",
            "volume": quote.get("f47") or 0, "last_date": klines[-1]["date"] if klines else None,
            "klines": klines[-60:], "mined_factors": latest_mined_factor_values(klines), **completed_factor_snapshot(klines), "raw": {"pe": quote.get("f162"), "pb": quote.get("f163")},
            "factors": {"momentum20": momentum20, "momentum60": momentum60, "trend": trend,
                        "volume_ratio": volume_ratio, "volatility20": vol20}}

def fetch_stock_sina(item):
    """Real daily OHLCV fallback using Sina's public JSONP endpoint."""
    market, code = item["secid"].split(".")
    symbol = ("sh" if market == "1" else "sz") + code
    url = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20x/"
           f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen=240")
    raw = eastmoney_json(url, jsonp=True)
    klines = []
    for row in raw:
        try:
            klines.append({"date": row["day"], "open": float(row["open"]), "close": float(row["close"]),
                           "high": float(row["high"]), "low": float(row["low"]), "volume": float(row.get("volume", 0)),
                           "amount": 0, "pct": 0, "turnover": 0})
        except (KeyError, TypeError, ValueError):
            continue
    klines = klines[-1000:]
    klines = write_kline_cache(item["code"], klines)
    quote_name = item["name"]
    try:
        quote_text = http_text(f"https://qt.gtimg.cn/q={symbol}")
        quote_match = re.search(r'=\"(.*)\"', quote_text)
        if quote_match:
            quote_parts = quote_match.group(1).split("~")
            quote_name = quote_parts[1] or quote_name
    except Exception:
        pass
    closes = [x["close"] for x in klines]; volumes = [x["volume"] for x in klines]
    returns = [(closes[i] / closes[i-1] - 1) for i in range(1, len(closes)) if closes[i-1]]
    def mean(xs): return sum(xs) / len(xs) if xs else 0.0
    def pct_change(window): return closes[-1] / closes[-window-1] - 1 if len(closes) > window and closes[-window-1] else 0.0
    momentum20, momentum60 = pct_change(20), pct_change(60)
    ma5, ma20 = mean(closes[-5:]), mean(closes[-20:]); trend = ma5 / ma20 - 1 if ma20 else 0.0
    volume_ratio = mean(volumes[-5:]) / mean(volumes[-20:]) if len(volumes) >= 20 and mean(volumes[-20:]) else 1.0
    vol20 = statistics.pstdev(returns[-20:]) * math.sqrt(252) if len(returns) >= 20 else 0.0
    price = closes[-1] if closes else 0; change = price - closes[-2] if len(closes) > 1 else 0; pct = change / closes[-2] * 100 if len(closes) > 1 and closes[-2] else 0
    return {**item, "name": quote_name, "price": price, "change": change, "pct": pct, "amount": 0, "volume": volumes[-1] if volumes else 0,
            "last_date": klines[-1]["date"] if klines else None, "klines": klines[-60:], "mined_factors": latest_mined_factor_values(klines), **completed_factor_snapshot(klines), "raw": {},
            "source": "sina", "source_label": "新浪公开日线 K 线",
            "factors": {"momentum20": momentum20, "momentum60": momentum60, "trend": trend,
                        "volume_ratio": volume_ratio, "volatility20": vol20}}

def zscores(values):
    if not values: return []
    avg, sd = mean(values), statistics.pstdev(values)
    return [(v - avg) / sd if sd else 0.0 for v in values]

def build_dashboard_fresh():
    # Production backtests use one fixed technology universe. Popularity lists
    # are intentionally excluded because their daily membership changes would
    # alter the historical sample and invalidate comparable experiment runs.
    research_universe, tech_snapshot, selected_sectors = tech_universe_items(UNIVERSE)
    if not research_universe:
        raise RuntimeError("真实科技股池为空，请刷新东方财富行业分类")
    fixed_universe_manifest = export_fixed_tech_universe(research_universe, tech_snapshot, selected_sectors)
    stocks, errors = [], []
    def fetch_one(item):
        try:
            return fetch_stock_tencent(item), None
        except Exception as tencent_exc:
            try:
                return fetch_stock(item), None
            except Exception as east_exc:
                try:
                    return fetch_stock_sina(item), None
                except Exception as sina_exc:
                    return None, f"{item['code']}: Tencent={tencent_exc}; Eastmoney={east_exc}; Sina={sina_exc}"
    # Bounded concurrency keeps refreshes fast without opening an unbounded number of sockets.
    with ThreadPoolExecutor(max_workers=12, thread_name_prefix="quote-refresh") as pool:
        futures = [pool.submit(fetch_one, item) for item in research_universe]
        for future in as_completed(futures):
            stock, error = future.result()
            if stock:
                stocks.append(stock)
            if error:
                errors.append(error)
    if not stocks:
        raise RuntimeError("Eastmoney data unavailable")
    keys = ["momentum20", "trend", "volume_ratio", "volatility20"]
    zs = {key: zscores([s["factors"][key] for s in stocks]) for key in keys}
    for idx, stock in enumerate(stocks):
        # Real technical factors only. Quality/value remain explicitly unscored until a fundamentals feed is added.
        f = stock["factors"]
        f["momentum_score"] = 50 + 12 * zs["momentum20"][idx]
        f["trend_score"] = 50 + 12 * zs["trend"][idx]
        f["volume_score"] = 50 + 8 * zs["volume_ratio"][idx]
        f["risk_score"] = 50 - 10 * zs["volatility20"][idx]
        stock["score"] = max(0, min(100, 0.40*f["momentum_score"] + 0.30*f["trend_score"] + 0.15*f["volume_score"] + 0.15*f["risk_score"]))
        stock["status"] = "可建仓" if stock["score"] >= 70 else "等待触发" if stock["score"] >= 55 else "观察"
    stocks.sort(key=lambda x: x["score"], reverse=True)
    write_kline_manifest()
    history_manifest = export_tech_history(stocks)
    market = {"hs300": None, "cyb": None}
    for key, item in [("hs300", {"code":"000300","name":"沪深 300","market":"指数","secid":"1.000300","initial":"沪"}), ("cyb", {"code":"399006","name":"创业板指","market":"指数","secid":"0.399006","initial":"创"})]:
        try: market[key] = fetch_stock_tencent(item)
        except Exception:
            try: market[key] = fetch_stock(item)
            except Exception:
                try: market[key] = fetch_stock_sina(item)
                except Exception: pass
    up = sum(1 for s in stocks if s["pct"] > 0); down = len(stocks) - up
    latest = max((s["last_date"] for s in stocks if s["last_date"]), default=None)
    source_names = {"tencent": "腾讯实时 + 前复权日线", "eastmoney": "东方财富公开行情", "sina": "新浪公开行情"}
    sources = sorted(set(s.get("source", "unknown") for s in stocks + [v for v in market.values() if v]))
    quote_times = [s.get("quote_time") for s in stocks if s.get("quote_time")]
    factor_catalog = [
        {"key":"momentum20","name":"20 日动量","category":"价格动量","formula":"close / Ref(close, 20) - 1","description":"过去 20 个交易日的前复权收盘价变化，用于识别中短期趋势延续。","weight":0.40},
        {"key":"trend","name":"5/20 日趋势","category":"趋势结构","formula":"Mean(close, 5) / Mean(close, 20) - 1","description":"短均线相对长均线的偏离，衡量近期价格是否正在加速或转弱。","weight":0.30},
        {"key":"volume_ratio","name":"5/20 日量比","category":"成交确认","formula":"Mean(volume, 5) / Mean(volume, 20)","description":"近期成交量相对过去 20 日均值的放大程度，用于确认价格趋势是否有成交支持。","weight":0.15},
        {"key":"volatility20","name":"20 日年化波动","category":"风险约束","formula":"Std(Return(close), 20) × sqrt(252)","description":"过去 20 日收益波动率年化结果，用于给高波动标的施加风险扣分。","weight":0.15},
    ]
    sector_counts = {key: sum(key in stock.get("tech_sectors", []) for stock in stocks) for key in selected_sectors}
    return {"source": "+".join(sources), "source_label": " / ".join(source_names.get(s, s) for s in sources), "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "quote_time": max(quote_times) if quote_times else None,
            "latest_date": latest, "errors": errors, "universe_mode": "technology", "universe_signature": universe_signature_for_stocks(stocks),
            "universe_size": len(stocks), "base_universe_size": len(UNIVERSE), "hot_union_size": 0, "hot_history_days": 0, "stocks": stocks,
            "tech_universe": {"selected_sectors": selected_sectors, "excluded_boards": ["科创板 688/689"], "source": tech_snapshot.get("source"), "generated_at": tech_snapshot.get("generated_at"),
                              "fixed_file": str(FIXED_TECH_UNIVERSE_FILE.relative_to(Path(__file__).parent)), "fixed_count": fixed_universe_manifest["stock_count"],
                              "sectors": [{"key": key, "name": TECH_SECTOR_DEFINITIONS[key]["name"], "count": sector_counts.get(key, 0)} for key in TECH_SECTOR_DEFINITIONS]},
            "history_storage": history_manifest,
            "market": market, "breadth": {"up": up, "down": down}, "factor_catalog": factor_catalog,
            "factor_model": {"name": "Technical Cross-Section v0.1", "weights": {"momentum20": .40, "trend": .30, "volume_ratio": .15, "volatility20": .15},
                              "note": "仅在所选科技细分股池内做截面标准化；硬排除科创板 688/689"}}

def read_dashboard_cache():
    try:
        return json.loads(DASHBOARD_CACHE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

def read_factor_agent_run():
    try:
        return json.loads(FACTOR_AGENT_RUN_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

def read_backtest_run():
    try:
        return json.loads(BACKTEST_RUN_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

def attach_research_runs(payload):
    if not payload.get("history_storage") and TECH_HISTORY_MANIFEST_FILE.exists():
        try:
            payload["history_storage"] = json.loads(TECH_HISTORY_MANIFEST_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    agent = read_factor_agent_run()
    signature = payload.get("universe_signature")
    if agent:
        agent = dict(agent)
        agent["stale"] = agent.get("universe_signature") != signature
        agent["current_universe_signature"] = signature
        if agent["stale"]:
            agent["stale_reason"] = "科技股池成员已变化，需要重新验证因子"
    payload["agent_factor_run"] = agent
    if agent:
        top = [item for item in agent.get("validation", {}).get("results", []) if item.get("mean_rank_ic") is not None][:20]
        payload["factor_lab_agent_catalog"] = [{"key":item["key"], "name":item["name"], "category":"Agent 挖掘", "formula":item.get("formula", ""), "description":item.get("hypothesis", "真实数据验证候选"), "status":item.get("gate", item.get("status")), "direction":item.get("direction", "待定"), "rank_ic":item.get("mean_rank_ic"), "icir":item.get("icir")} for item in top]
        payload["factor_model"] = {"name":"Agent Factor Ensemble v1", "weights":{}, "note":f"最近一次搜索 {agent.get('search_space_size', 0)} 个公式；当前展示真实验证候选"}
    backtest_run = read_backtest_run()
    if backtest_run:
        backtest_run = dict(backtest_run)
        backtest_run["stale"] = backtest_run.get("universe_signature") != signature
        backtest_run["current_universe_signature"] = signature
        if backtest_run["stale"]:
            backtest_run["stale_reason"] = "科技股池成员已变化；运行组合回测会先同步因子，再重算全部组合"
    payload["backtest_factor_run"] = backtest_run
    council = read_multi_agent_run()
    if council:
        council = dict(council)
        council["stale"] = council.get("universe_signature") != signature
        if council["stale"]:
            council["stale_reason"] = "科技股池已变化，需要重新运行十二 Agent 会签"
    payload["multi_agent_run"] = council
    backtest = payload.get("backtest_factor_run")
    if agent and backtest and backtest.get("results") and payload.get("stocks"):
        combo = backtest["results"][0]
        validation_map = {item["key"]: item for item in agent.get("validation", {}).get("results", [])}
        factor_keys = combo.get("factors", [])
        factors = [(key, 1 if validation_map.get(key, {}).get("direction") == "正向" else -1) for key in factor_keys]
        rows_by_code = {}
        latest_data_date = max((stock.get("completed_factor_date") or stock.get("last_date") or "" for stock in payload["stocks"]), default="")
        for stock in payload["stocks"]:
            if (stock.get("completed_factor_date") or stock.get("last_date")) == latest_data_date and stock.get("completed_history_bars", 122) >= 122:
                rows_by_code[stock["code"]] = stock
        liquid_codes = liquid_codes_from_stocks(list(rows_by_code.values()))
        rows_by_code = {code: stock for code, stock in rows_by_code.items() if code in liquid_codes}
        rank_columns = []
        for key, direction in factors:
            values = [(code, (value * direction if value is not None else None)) for code, stock in rows_by_code.items() for value in [(stock.get("completed_mined_factors") or stock.get("mined_factors", {})).get(key)]]
            values = [(code, value) for code, value in values if value is not None and math.isfinite(value)]
            ranks = _rank([value for _, value in values]); rank_columns.append({"key":key, "direction":direction, "scores":{code: (rank - 1) / max(1, len(ranks) - 1) * 100 for (code, _), rank in zip(values, ranks)}})
        scores = {}
        for code in rows_by_code:
            available = [column["scores"][code] for column in rank_columns if code in column["scores"]]
            if available: scores[code] = round(mean(available), 2)
        top_codes = set(sorted(scores, key=lambda code: (scores[code], code), reverse=True)[:3])
        cutoff = min((scores[code] for code in top_codes), default=100)
        for stock in payload["stocks"]:
            score = scores.get(stock["code"])
            stock["agent_combo_score"] = score
            stock["agent_combo_selected"] = stock["code"] in top_codes
            stock["agent_combo_factors"] = factor_keys
            stock["agent_combo_breakdown"] = [{"key":column["key"], "name":validation_map.get(column["key"], {}).get("name", column["key"]), "direction":"正向" if column["direction"] == 1 else "反向", "score":round(column["scores"].get(stock["code"], 0), 1)} for column in rank_columns]
        payload["agent_selection"] = {"name":" / ".join(combo.get("names", [])), "factors":factor_keys, "direction_adjusted":True, "max_positions":3, "cutoff":cutoff, "selected_count":len(top_codes), "data_date":latest_data_date, "rule":"最新完整交易日先剔除近 20 日成交额最低 30%，再做方向调整后的因子截面排名并严格取 Top 3；科创板与 ST 已排除。"}
    return payload

def build_multi_agent_context(dashboard):
    factor_run = read_factor_agent_run() or {}
    previous_top = [{"key": item.get("key"), "name": item.get("name"), "rank_ic": item.get("mean_rank_ic"), "gate": item.get("gate")}
                    for item in factor_run.get("validation", {}).get("results", [])[:12]]
    history = {}
    try:
        history = json.loads(TECH_HISTORY_MANIFEST_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass
    candidates = [{"key": key, "name": name, "formula": formula} for key, name, formula in build_factor_search_space()]
    return {"data_date": latest_completed_data_date(dashboard.get("stocks", [])),
            "universe_signature": dashboard.get("universe_signature") or universe_signature_for_stocks(dashboard.get("stocks", [])),
            "universe_size": len(dashboard.get("stocks", [])), "breadth": dashboard.get("breadth", {}),
            "market": dashboard.get("market", {}), "stocks": dashboard.get("stocks", []),
            "sectors": dashboard.get("tech_universe", {}).get("sectors", []), "history": history,
            "factor_candidates": candidates, "previous_top": previous_top}

def run_multi_agent_background(dashboard):
    global MULTI_AGENT_THREAD
    try:
        stocks = dashboard.get("stocks", [])
        context = build_multi_agent_context(dashboard)
        run_research_council(context, lambda: run_factor_agent_loop(stocks), lambda: run_backtest_experiment(stocks))
    finally:
        with MULTI_AGENT_LOCK:
            MULTI_AGENT_THREAD = None

def select_by_factor_keys(stocks, factor_keys):
    agent = read_factor_agent_run()
    validation_map = {item["key"]: item for item in (agent or {}).get("validation", {}).get("results", [])}
    factors = [(key, 1 if validation_map.get(key, {}).get("direction") == "正向" else -1) for key in factor_keys if key in validation_map]
    rows_by_code = {}
    latest_data_date = max((stock.get("completed_factor_date") or stock.get("last_date") or "" for stock in stocks), default="")
    for stock in stocks:
        if (stock.get("completed_factor_date") or stock.get("last_date")) == latest_data_date and stock.get("completed_history_bars", 122) >= 122:
            rows_by_code[stock["code"]] = stock
    liquid_codes = liquid_codes_from_stocks(list(rows_by_code.values()))
    rows_by_code = {code: stock for code, stock in rows_by_code.items() if code in liquid_codes}
    columns = []
    for key, direction in factors:
        values = []
        for code, stock in rows_by_code.items():
            value = (stock.get("completed_mined_factors") or stock.get("mined_factors", {})).get(key)
            if value is not None and math.isfinite(value):
                values.append((code, value * direction))
        ranks = _rank([value for _, value in values]); columns.append({"key":key, "direction":direction, "scores":{code:(rank-1)/max(1,len(ranks)-1)*100 for (code,_),rank in zip(values,ranks)}})
    scores = {}
    for code in rows_by_code:
        available = [column["scores"][code] for column in columns if code in column["scores"]]
        if len(available) == len(columns) and available:
            scores[code] = round(mean(available), 2)
    top_codes = sorted(scores, key=lambda code: (scores[code], code), reverse=True)[:3]
    names = [validation_map.get(key, {}).get("name", key) for key in factor_keys]
    breakdowns = {code:[{"key":column["key"], "name":validation_map.get(column["key"], {}).get("name", column["key"]), "direction":"正向" if column["direction"] == 1 else "反向", "score":round(column["scores"].get(code, 0), 1)} for column in columns] for code in rows_by_code}
    return {"name":" / ".join(names), "factors":factor_keys, "max_positions":3, "selected_count":len(top_codes), "data_date":latest_data_date, "rule":"在最新共同交易日按所选因子的方向调整后截面排名等权合成，严格取最高 3 只。", "scores":scores, "breakdowns":breakdowns, "top_codes":top_codes}

def write_dashboard_cache(payload):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = DASHBOARD_CACHE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, DASHBOARD_CACHE_FILE)

def write_kline_cache(code, klines):
    KLINE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = KLINE_CACHE_DIR / f"{code}.json"
    existing = []
    try:
        existing = json.loads(target.read_text(encoding="utf-8")).get("klines") or []
    except (FileNotFoundError, OSError, json.JSONDecodeError, AttributeError):
        pass
    # Public fallbacks return different history lengths. Merge by trading date
    # so a short 240-bar response can update overlaps without erasing older bars.
    merged = {row.get("date"): row for row in existing if row.get("date")}
    merged.update({row.get("date"): row for row in klines if row.get("date")})
    klines = [merged[date] for date in sorted(merged)]
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({"code": code, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "klines": klines}, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, target)
    return klines

def read_kline_cache(code):
    try:
        payload = json.loads((KLINE_CACHE_DIR / f"{code}.json").read_text(encoding="utf-8"))
        return payload.get("klines") or []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []

def write_kline_manifest():
    files = list(KLINE_CACHE_DIR.glob("*.json")) if KLINE_CACHE_DIR.exists() else []
    records = []
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("klines") or []
            if rows:
                records.append({"code": payload.get("code") or path.stem, "bars": len(rows), "first_date": rows[0].get("date"), "last_date": rows[-1].get("date"), "updated_at": payload.get("updated_at")})
        except (OSError, json.JSONDecodeError, TypeError, AttributeError):
            continue
    manifest = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "stock_count": len(records), "min_bars": min((item["bars"] for item in records), default=0), "max_bars": max((item["bars"] for item in records), default=0), "latest_date": max((item["last_date"] or "" for item in records), default=None), "files": records}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = KLINE_MANIFEST_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, KLINE_MANIFEST_FILE)
    return manifest

def export_tech_history(stocks):
    """Persist the complete locally available daily history for the active tech universe."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    exported = {}
    total_bars = 0
    first_dates, last_dates = [], []
    for stock in stocks:
        code = stock.get("code", "")
        rows = completed_daily_rows(read_kline_cache(code))
        if not rows:
            continue
        total_bars += len(rows)
        first_dates.append(rows[0].get("date")); last_dates.append(rows[-1].get("date"))
        exported[code] = {"name": stock.get("name", code), "industry": stock.get("industry"), "tech_sectors": stock.get("tech_sectors", []), "bars": rows}
    generated_at = time.strftime("%Y-%m-%d %H:%M:%S")
    payload = {"generated_at": generated_at, "source": "腾讯前复权日线，东方财富/新浪备用", "adjustment": "前复权", "frequency": "日线", "excluded_boards": ["科创板 688/689"], "stock_count": len(exported), "total_bars": total_bars, "first_date": min((value for value in first_dates if value), default=None), "last_date": max((value for value in last_dates if value), default=None), "stocks": exported}
    temporary = TECH_HISTORY_FILE.with_suffix(TECH_HISTORY_FILE.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=6) as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    os.replace(temporary, TECH_HISTORY_FILE)
    manifest = {key: value for key, value in payload.items() if key != "stocks"}
    manifest.update({"file": str(TECH_HISTORY_FILE.relative_to(Path(__file__).parent)), "compressed_bytes": TECH_HISTORY_FILE.stat().st_size, "format": "gzip JSON；解压后 stocks.<code>.bars 为完整 OHLCV 日线数组"})
    _atomic_json_write(TECH_HISTORY_MANIFEST_FILE, manifest)
    return manifest

def refresh_dashboard():
    global REFRESH_THREAD
    if not CACHE_LOCK.acquire(blocking=False):
        return False
    try:
        payload = build_dashboard_fresh()
        write_dashboard_cache(payload)
        return True
    finally:
        CACHE_LOCK.release()
        REFRESH_THREAD = None

def build_dashboard():
    global REFRESH_THREAD
    cached = read_dashboard_cache()
    selected_sectors = read_tech_config()
    if cached and (cached.get("universe_mode") != "technology" or cached.get("tech_universe", {}).get("selected_sectors") != selected_sectors):
        cached = None
    now = time.time()
    if cached:
        generated = cached.get("generated_at", "")
        try:
            cache_age = max(0, now - time.mktime(time.strptime(generated, "%Y-%m-%d %H:%M:%S")))
        except ValueError:
            cache_age = REFRESH_TTL_SECONDS + 1
        if cache_age > REFRESH_TTL_SECONDS and (REFRESH_THREAD is None or not REFRESH_THREAD.is_alive()):
            REFRESH_THREAD = threading.Thread(target=refresh_dashboard, name="dashboard-refresh", daemon=True)
            REFRESH_THREAD.start()
        cached["cache_age_seconds"] = round(cache_age, 1)
        cached["refreshing"] = bool(REFRESH_THREAD and REFRESH_THREAD.is_alive())
        cached["cache_status"] = "fresh" if cache_age <= REFRESH_TTL_SECONDS else "stale_while_revalidate"
        return attach_research_runs(cached)
    # Cold start: never block the page on a full-universe scan.
    if REFRESH_THREAD and REFRESH_THREAD.is_alive():
        return {"source": "pending", "source_label": "真实数据缓存建立中", "generated_at": None,
                "latest_date": None, "errors": ["首次全量真实扫描正在后台执行"], "universe_size": 0,
                "base_universe_size": len(UNIVERSE), "hot_union_size": 0, "hot_history_days": 0,
                "stocks": [], "market": {"hs300": None, "cyb": None}, "breadth": {"up": 0, "down": 0},
                "universe_mode": "technology", "universe_signature": None,
                "tech_universe": {"selected_sectors": read_tech_config(), "excluded_boards": ["科创板 688/689"], "sectors": [{"key": key, "name": value["name"], "count": 0} for key, value in TECH_SECTOR_DEFINITIONS.items()]},
                "factor_catalog": [], "factor_model": {"name": "Technical Cross-Section v0.1", "weights": {}},
                "cache_age_seconds": None, "refreshing": True, "cache_status": "cold_start"}
    payload = build_dashboard_fresh()
    write_dashboard_cache(payload)
    payload["cache_age_seconds"] = 0
    payload["refreshing"] = False
    payload["cache_status"] = "fresh"
    return attach_research_runs(payload)

class Handler(SimpleHTTPRequestHandler):
    def do_POST(self):
        global MULTI_AGENT_THREAD
        parsed = urlparse(self.path)
        request_body = {}
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                request_body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            request_body = {}
        if parsed.path == "/api/agents/run":
            try:
                dashboard = read_dashboard_cache()
                if not dashboard or not dashboard.get("stocks"):
                    raise RuntimeError("真实行情缓存尚未完成，十二 Agent 不会使用模拟输入")
                with MULTI_AGENT_LOCK:
                    if MULTI_AGENT_THREAD and MULTI_AGENT_THREAD.is_alive():
                        payload = {"status": "运行中", "run": read_multi_agent_run()}
                    else:
                        MULTI_AGENT_THREAD = threading.Thread(target=run_multi_agent_background, args=(dashboard,), name="nstock-12-agent-council", daemon=True)
                        MULTI_AGENT_THREAD.start()
                        payload = {"status": "已启动", "agent_count": 12, "team_count": 4}
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(202); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            except Exception as exc:
                body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self.send_response(409); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        if parsed.path == "/api/universe":
            try:
                selected = write_tech_config([str(key) for key in request_body.get("sectors", [])])
                dashboard = build_dashboard_fresh()
                write_dashboard_cache(dashboard)
                payload = attach_research_runs(dashboard)
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            except Exception as exc:
                body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self.send_response(409); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        if parsed.path == "/api/mine":
            try:
                dashboard = read_dashboard_cache()
                if not dashboard or not dashboard.get("stocks"):
                    raise RuntimeError("真实行情缓存尚未完成，请稍后再运行挖掘")
                payload = mine_factor_candidates(dashboard["stocks"])
                payload["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                payload["data_date"] = latest_completed_data_date(dashboard["stocks"])
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            except Exception as exc:
                body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self.send_response(409); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        if parsed.path == "/api/mine/agent":
            try:
                dashboard = read_dashboard_cache()
                if not dashboard or not dashboard.get("stocks"):
                    raise RuntimeError("真实行情缓存尚未完成，请稍后再运行挖掘")
                payload = run_factor_agent_loop(dashboard["stocks"])
                payload["validation"]["data_date"] = payload.get("data_date")
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            except Exception as exc:
                body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self.send_response(409); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        if parsed.path == "/api/backtest":
            try:
                dashboard = read_dashboard_cache()
                if not dashboard or not dashboard.get("stocks"):
                    raise RuntimeError("真实行情缓存尚未完成")
                payload = run_backtest_experiment(dashboard["stocks"])
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            except Exception as exc:
                body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self.send_response(409); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        if parsed.path == "/api/backtest/select":
            try:
                dashboard = read_dashboard_cache()
                keys = [str(key) for key in request_body.get("factors", [])][:3]
                if not dashboard or not dashboard.get("stocks") or not keys:
                    raise RuntimeError("没有可用的回测策略或因子")
                selection = select_by_factor_keys(dashboard["stocks"], keys)
                selected = []
                for stock in dashboard["stocks"]:
                    code = stock["code"]
                    score = selection["scores"].get(code)
                    stock["agent_combo_score"] = score
                    stock["agent_combo_selected"] = code in selection["top_codes"]
                    stock["agent_combo_factors"] = keys
                    stock["agent_combo_breakdown"] = selection["breakdowns"].get(code, [])
                    if stock["agent_combo_selected"]: selected.append(stock)
                body = json.dumps({"selection": selection, "stocks": selected}, ensure_ascii=False).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            except Exception as exc:
                body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self.send_response(409); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        self.send_error(404)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/agents":
            running = bool(MULTI_AGENT_THREAD and MULTI_AGENT_THREAD.is_alive())
            payload = json.dumps({"running": running, "run": read_multi_agent_run(), "agent_count": 12, "team_count": 4}, ensure_ascii=False).encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
            return
        if parsed.path == "/api/dashboard":
            try:
                payload = json.dumps(build_dashboard(), ensure_ascii=False).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
            except Exception as exc:
                payload = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self.send_response(502); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
            return
        super().do_GET()

if __name__ == "__main__":
    print(f"Signal Foundry running at http://{HOST}:{PORT}")
    if not DASHBOARD_CACHE_FILE.exists():
        REFRESH_THREAD = threading.Thread(target=refresh_dashboard, name="dashboard-warmup", daemon=True)
        REFRESH_THREAD.start()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
