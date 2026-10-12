#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small Eastmoney-backed research API for the first frontend version."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import combinations
import json, math, statistics, time, subprocess, re, threading, os, gzip, hashlib, collections, shutil
import trading_ledger
import review_cache
import market_strategy
import intraday_week_review
import intraday_entry_review
import sys
from multi_agent import RUNS_DIR as MULTI_AGENT_RUNS_DIR
from multi_agent import load_chief_opinion_cache, load_latest as read_multi_agent_run, run_research_council
from wyckoff import ENGINE_VERSION as WYCKOFF_ENGINE_VERSION, backtest as backtest_wyckoff, screen_universe
from recommendations import (
    COST_BPS,
    REVIEW_START_DATE,
    load_log as load_recommendation_log,
    make_snapshot as make_recommendation_snapshot,
    ranking_entries as recommendation_ranking_entries,
    refresh as refresh_recommendations,
    save_log as save_recommendation_log,
    summarize as summarize_recommendations,
    upsert as upsert_recommendation,
)

HOST, PORT = "127.0.0.1", 4173
GUPIAO_UNIVERSE_FILE = Path("/Users/pozansky/Documents/gupiao/fixed_universe_codes.json")
HOT_HISTORY_FILE = Path(__file__).with_name("hot_rank_history.json")
CACHE_DIR = Path(__file__).with_name(".cache")
DASHBOARD_CACHE_FILE = CACHE_DIR / "dashboard.json"
KLINE_CACHE_DIR = CACHE_DIR / "klines"
KLINE_MANIFEST_FILE = CACHE_DIR / "historical_data_manifest.json"
FACTOR_AGENT_RUN_FILE = CACHE_DIR / "factor_agent_last_run.json"
BACKTEST_RUN_FILE = CACHE_DIR / "backtest_factor_last_run.json"
# 用户选定的策略，独立于行情缓存，刷新/会签都不会覆盖
SELECTED_STRATEGY_FILE = CACHE_DIR / "selected_strategy.json"
FACTOR_DAILY_METRICS_FILE = CACHE_DIR / "factor_daily_metrics.json"
WYCKOFF_CACHE_FILE = CACHE_DIR / "wyckoff.json"
TECH_UNIVERSE_FILE = Path(__file__).with_name("tech_universe.json")
TECH_CONFIG_FILE = CACHE_DIR / "tech_universe_config.json"
DATA_DIR = Path(__file__).with_name("data")
TECH_HISTORY_FILE = DATA_DIR / "tech_history.json.gz"
TECH_HISTORY_MANIFEST_FILE = DATA_DIR / "tech_history_manifest.json"
FIXED_TECH_UNIVERSE_FILE = DATA_DIR / "fixed_tech_universe.json"
CACHE_LOCK = threading.Lock()
REFRESH_THREAD = None
# 最近一次后台行情刷新的结果，供 /api/refresh-status 低成本查询（不必反序列化 15MB 缓存）
LAST_REFRESH = {"started_at": None, "finished_at": None, "ok": None, "error": None, "data_date": None}
MULTI_AGENT_THREAD = None
MULTI_AGENT_LOCK = threading.Lock()
MULTI_AGENT_PREP_STATUS = None
RECOMMENDATION_LOCK = threading.RLock()
# A complete 930-name refresh is expensive; stale-while-revalidate on a
# 15-minute cadence prevents permanent refresh loops during market hours.
REFRESH_TTL_SECONDS = 15 * 60
# 成交量统一口径：缓存里一律存"手"（1 手 = 100 股）。
# 腾讯/东方财富的日线本身就返回"手"，新浪返回"股"，必须在此换算，
# 否则同一只股票的历史与最新一根 K 线会差 100 倍，量比类信号全部失真。
VOLUME_UNIT = "lot"
SHARES_PER_LOT = 100
# 复权基准：腾讯/东财日线是前复权，新浪是不复权。同一文件必须只有一种基准，
# 否则除权日会在净值里留下假断层，直接污染回测与推荐归因。
ADJUSTMENT = "qfq"
ADJUSTMENT_TOLERANCE = 0.005
FACTOR_ENGINE_VERSION = "tech-factor-v8-delayed-return-target"
BACKTEST_ENGINE_VERSION = "tech-top3-v13-nextday-only"
# 两个因子的截面秩相关绝对值超过该阈值，就视为同一个因子（方向由 direction 单独处理）
FACTOR_CORRELATION_THRESHOLD = 0.75
# Signals use the completed close; execution is delayed to the next close.
ENTRY_OFFSET = 1
HOLDING_CHOICES = (1,)
DEFAULT_HOLDING_DAYS = 1
FORWARD_STEPS = 3
HOLDING_LABELS = {1: "下一交易日卖出"}
FACTOR_DAILY_METRICS_VERSION = "rank-ic-t1-t2-v4-nextday-only"

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
    ("body_strength", "K线实体强度", "(close - open) / (high - low)"),
    ("upper_shadow_ratio", "上影线压力", "(high - Max(open, close)) / (high - low)"),
    ("lower_shadow_ratio", "下影线支撑", "(Min(open, close) - low) / (high - low)"),
    ("engulfing_volume", "放量吞没", "Engulfing(open, high, low, close) * volume / Mean(Ref(volume, 1), 20)"),
    ("breakout_volume_confirm", "放量突破确认", "(close / Max(Ref(high, 1), 20) - 1) * volume / Mean(Ref(volume, 1), 20)"),
    ("close_volume_pressure", "收盘量能压力", "(2 * CloseLocation - 1) * volume / Mean(Ref(volume, 1), 20)"),
    ("contraction_breakout", "缩量整理后放量突破", "Breakout20 * VolumeSurprise20 / ATRContraction5_20"),
    ("cup_handle_breakout", "杯柄放量突破", "CupHandle(close, 80) * Breakout(pivot) * volume / Mean(Ref(volume, 1), 20)"),
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
    {"id":"R08", "name":"K线实体强度", "hypothesis":"实体方向和实体占日内振幅的比例可度量当日买卖力量。", "formula":"(close - open) / (high - low)", "candidate":"body_strength"},
    {"id":"R09", "name":"上影线压力", "hypothesis":"较长上影线可能代表高位抛压，后续收益方向需要样本外验证。", "formula":"(high - Max(open, close)) / (high - low)", "candidate":"upper_shadow_ratio"},
    {"id":"R10", "name":"下影线支撑", "hypothesis":"较长下影线可能代表低位承接，但也可能只是高波动噪声。", "formula":"(Min(open, close) - low) / (high - low)", "candidate":"lower_shadow_ratio"},
    {"id":"R11", "name":"放量吞没", "hypothesis":"吞没形态获得异常成交量确认时，反转信号可能更可靠。", "formula":"Engulfing(open, high, low, close) * volume / Mean(Ref(volume, 1), 20)", "candidate":"engulfing_volume"},
    {"id":"R12", "name":"放量突破确认", "hypothesis":"突破前高同时放量可能比无量突破具有更强的延续性。", "formula":"(close / Max(Ref(high, 1), 20) - 1) * volume / Mean(Ref(volume, 1), 20)", "candidate":"breakout_volume_confirm"},
    {"id":"R13", "name":"收盘量能压力", "hypothesis":"收盘在日内区间的位置与异常成交量结合，可区分真实买卖压力。", "formula":"(2 * CloseLocation - 1) * volume / Mean(Ref(volume, 1), 20)", "candidate":"close_volume_pressure"},
    {"id":"R14", "name":"缩量整理后放量突破", "hypothesis":"波幅收敛后的放量突破可能代表新趋势启动。", "formula":"Breakout20 * VolumeSurprise20 / ATRContraction5_20", "candidate":"contraction_breakout"},
    {"id":"R15", "name":"杯柄放量突破", "hypothesis":"杯底充分整理、右沿修复、浅柄回撤后放量越过枢轴位，可能对应欧奈尔式主升浪启动。", "formula":"CupHandle(close, 80) * Breakout(pivot) * volume / Mean(Ref(volume, 1), 20)", "candidate":"cup_handle_breakout"},
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
    # 去重必须**按公式**而不只是按 key：种子里的 momentum20 与生成器的 momentum_20
    # 是同一个公式，只比 key 会让同一个因子在搜索空间里出现两次。
    unique, seen_keys, seen_formulas = [], set(), set()
    for key, name, formula in candidates:
        fingerprint = re.sub(r"\s+", "", str(formula)).lower()
        if key in seen_keys or fingerprint in seen_formulas:
            continue
        unique.append((key, name, formula))
        seen_keys.add(key)
        seen_formulas.add(fingerprint)
    return unique[:100]

def build_generated_factor_candidates(specs, base_candidates):
    """Compile model proposals into a small, deterministic factor DSL."""
    allowed = {key for key, _, _ in base_candidates}
    operators = {
        "multiply": lambda left, right: left * right,
        "difference": lambda left, right: left - right,
        "ratio": lambda left, right: max(-100.0, min(100.0, left / (abs(right) + 1e-9))),
        "confirm": lambda left, right: left * max(right, 0.0),
    }
    candidates, proposals, seen = [], [], set()
    for index, item in enumerate((specs or [])[:8], 1):
        left, right = str(item.get("left", "")), str(item.get("right", ""))
        operator = str(item.get("operator", ""))
        if left not in allowed or right not in allowed or operator not in operators:
            continue
        identity = (left, operator, right)
        if identity in seen:
            continue
        seen.add(identity)
        key = f"gen__{operator}__{left}__{right}"
        name = str(item.get("name") or f"生成因子 {index}")[:24]
        formula = f"{operator}({left}, {right})"
        candidates.append((key, name, formula))
        proposals.append({
            "id": f"G{index:02d}", "name": name,
            "hypothesis": str(item.get("rationale") or "模型生成的可证伪组合假设"),
            "expectation": str(item.get("expectation") or "由真实样本确定方向"),
            "formula": formula, "candidate": key, "generated": True,
            "components": {"left": left, "operator": operator, "right": right},
        })
    return candidates, proposals

def _factor_value(rows, index, key):
    # No factor in the audited library needs more than 120 lookback bars.
    # Bounding the slice avoids repeatedly scanning a stock's full 1000-bar history.
    factor_rows = rows[max(0, index - 120):index + 1]
    closes = [float(row["close"]) for row in factor_rows]
    opens = [float(row.get("open", row["close"])) for row in factor_rows]
    highs = [float(row["high"]) for row in factor_rows]
    lows = [float(row["low"]) for row in factor_rows]
    volumes = [float(row.get("volume", 0) or 0) for row in factor_rows]
    def avg(values): return mean(values)
    def ret(window): return closes[-1] / closes[-window-1] - 1 if len(closes) > window and closes[-window-1] else None
    if key.startswith("gen__"):
        parts = key.split("__")
        if len(parts) != 4:
            return None
        _, operator, left_key, right_key = parts
        left = _factor_value(rows, index, left_key)
        right = _factor_value(rows, index, right_key)
        if left is None or right is None or not math.isfinite(left) or not math.isfinite(right):
            return None
        if operator == "multiply": return left * right
        if operator == "difference": return left - right
        if operator == "ratio": return max(-100.0, min(100.0, left / (abs(right) + 1e-9)))
        if operator == "confirm": return left * max(right, 0.0)
        return None
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
    if key in {"body_strength", "upper_shadow_ratio", "lower_shadow_ratio"}:
        span = highs[-1] - lows[-1]
        if not span:
            return 0.0
        if key == "body_strength":
            return (closes[-1] - opens[-1]) / span
        if key == "upper_shadow_ratio":
            return (highs[-1] - max(opens[-1], closes[-1])) / span
        return (min(opens[-1], closes[-1]) - lows[-1]) / span
    if key in {"engulfing_volume", "breakout_volume_confirm", "close_volume_pressure", "contraction_breakout"}:
        if len(closes) < 21:
            return None
        prior_volume = avg(volumes[-21:-1])
        if not prior_volume:
            return None
        volume_surprise = volumes[-1] / prior_volume
        if key == "engulfing_volume":
            bullish = closes[-2] < opens[-2] and closes[-1] > opens[-1] and opens[-1] <= closes[-2] and closes[-1] >= opens[-2]
            bearish = closes[-2] > opens[-2] and closes[-1] < opens[-1] and opens[-1] >= closes[-2] and closes[-1] <= opens[-2]
            return volume_surprise if bullish else (-volume_surprise if bearish else 0.0)
        if key == "close_volume_pressure":
            span = highs[-1] - lows[-1]
            return ((2 * (closes[-1] - lows[-1]) / span) - 1) * volume_surprise if span else 0.0
        prior_high = max(highs[-21:-1])
        breakout = closes[-1] / prior_high - 1 if prior_high else None
        if breakout is None:
            return None
        if key == "breakout_volume_confirm":
            return breakout * volume_surprise
        true_ranges = [max(highs[pos] - lows[pos], abs(highs[pos] - closes[pos - 1]), abs(lows[pos] - closes[pos - 1])) for pos in range(1, len(closes))]
        prior_atr = avg(true_ranges[-25:-5]) if len(true_ranges) >= 25 else None
        recent_atr = avg(true_ranges[-5:]) if len(true_ranges) >= 5 else None
        if not prior_atr or not recent_atr or recent_atr >= prior_atr or breakout <= 0:
            return 0.0
        return breakout * volume_surprise * (prior_atr / recent_atr)
    if key == "cup_handle_breakout":
        if len(closes) < 80:
            return None
        pattern = closes[-80:]
        left_zone = pattern[:30]
        left_rim = max(left_zone)
        left_index = left_zone.index(left_rim)
        bottom_zone = pattern[left_index + 1:55]
        if not bottom_zone or not left_rim:
            return 0.0
        cup_bottom = min(bottom_zone)
        bottom_index = left_index + 1 + bottom_zone.index(cup_bottom)
        right_zone = pattern[max(50, bottom_index + 1):70]
        handle = pattern[70:79]
        if not right_zone or not handle:
            return 0.0
        right_rim = max(right_zone)
        pivot = max(left_rim, right_rim)
        cup_depth = (left_rim - cup_bottom) / left_rim
        rim_gap = abs(right_rim / left_rim - 1)
        handle_drawdown = (right_rim - min(handle)) / right_rim if right_rim else 1
        cup_midpoint = cup_bottom + (left_rim - cup_bottom) * 0.5
        prior_volume = avg(volumes[-21:-1])
        if not prior_volume:
            return None
        volume_surprise = volumes[-1] / prior_volume
        valid_shape = (
            0.12 <= cup_depth <= 0.40
            and rim_gap <= 0.08
            and 0 <= handle_drawdown <= 0.12
            and min(handle) >= cup_midpoint
            and pattern[-1] > pivot
            and pattern[-1] / pivot - 1 <= 0.15
            and volume_surprise >= 1.20
        )
        if not valid_shape:
            return 0.0
        shape_quality = max(0.25, 1 - rim_gap / 0.08)
        return (pattern[-1] / pivot - 1) * volume_surprise * shape_quality
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

def volume_is_anomalous(rows, index, window=20):
    """True when bar[index]'s volume cannot belong to the same series as the bars before it.

    A public-feed unit mix (股 vs 手) shows up as an exact ~100x step, so the low
    bound is deliberately close to 1.0 while the high bound only catches an
    inverted unit rather than a genuine volume spike.
    """
    if index < 4:
        return False
    volume = float(rows[index].get("volume") or 0)
    if volume <= 0:
        return True
    reference = [float(row.get("volume") or 0) for row in rows[max(0, index - window):index]]
    reference = sorted(value for value in reference if value > 0)
    if len(reference) < 5:
        return False
    median = reference[len(reference) // 2]
    if median <= 0:
        return False
    ratio = volume / median
    return ratio < 0.1 or ratio > 25

def tradable_daily_rows(rows, now=None):
    """Completed daily bars with trailing unit-broken or stub bars removed.

    Analysis must not treat a bar as a real trading day when its volume is
    inconsistent with the surrounding series; dropping it is safer than letting
    it fabricate a "volume dry-up" or a 100x "breakout".
    """
    rows = list(completed_daily_rows(rows, now))
    while len(rows) > 4 and volume_is_anomalous(rows, len(rows) - 1):
        rows = rows[:-1]
    return rows

def latest_completed_data_date(stocks):
    return max((rows[-1].get("date") for stock in stocks for rows in [completed_daily_rows(stock.get("klines", []))] if rows), default=None)

def liquidity_value(rows, index):
    window = rows[max(0, index - 19):index + 1]
    values = [float(row.get("close") or 0) * float(row.get("volume") or 0) for row in window]
    return mean(values) if len(values) >= 10 else 0.0

def liquidity_cutoff(values):
    """流动性下限：剔除近 20 日成交额最低的 30%。

    回测、会签候选与驾驶舱候选池必须用同一个口径，否则"候选池第一名"和
    "会签输出的第一只"会是两只不同的股票，用户看到的就是两套互相矛盾的名单。
    """
    ordered = sorted(value for value in values if value > 0)
    return ordered[max(0, int(len(ordered) * 0.30) - 1)] if ordered else 0.0

# 数据源熔断状态。腾讯/东财挂掉时每个源都要等一次超时，全股池就是几百次
# 无谓等待；连续失败若干次后本轮直接跳过，下一轮刷新自动重新尝试。
PROVIDER_FAILURES = {}
PROVIDER_DOWN = set()
PROVIDER_ERRORS = {}
PROVIDER_FAILURE_LIMIT = 4

# 数据源优先级。新浪目前最稳（实测 126ms、0 失败），腾讯与东财分别返回
# HTTP 501 与连接重置。靠前的先试，失败才降级到下一个；连续失败会被熔断，
# 所以即使顺序里的源当前不可用，也只是一次性代价而不是每只股票都付。
PROVIDER_ORDER = ("tencent", "sina", "eastmoney")
# 取数函数在下面定义，用名字延迟解析，避免这里的常量依赖函数定义顺序
PROVIDERS = {}
PROVIDER_HITS = collections.Counter()
PROVIDER_LOCK = threading.Lock()

def reset_providers():
    PROVIDER_FAILURES.clear()
    PROVIDER_DOWN.clear()
    PROVIDER_ERRORS.clear()
    PROVIDER_HITS.clear()

def provider_available(name):
    return name not in PROVIDER_DOWN

def note_provider(name, ok, error=None):
    if ok:
        PROVIDER_FAILURES[name] = 0
        PROVIDER_DOWN.discard(name)
        return
    PROVIDER_FAILURES[name] = PROVIDER_FAILURES.get(name, 0) + 1
    if error is not None:
        PROVIDER_ERRORS.setdefault(name, str(error)[:200])
    if PROVIDER_FAILURES[name] >= PROVIDER_FAILURE_LIMIT:
        PROVIDER_DOWN.add(name)

def stock_liquidity(stock):
    rows = stock.get("klines") or []
    return liquidity_value(rows, len(rows) - 1) if rows else 0.0

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

def read_factor_daily_metrics():
    try:
        payload = json.loads(FACTOR_DAILY_METRICS_FILE.read_text(encoding="utf-8"))
        if payload.get("version") == FACTOR_DAILY_METRICS_VERSION:
            return payload
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass
    return {"version": FACTOR_DAILY_METRICS_VERSION, "entries": {}}

def write_factor_daily_metrics(payload):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = FACTOR_DAILY_METRICS_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, FACTOR_DAILY_METRICS_FILE)

def mine_factor_candidates(stocks, candidates=None, metrics_cache=None, cache_namespace=""):
    usable = []
    for stock in stocks:
        code = stock.get("code", "")
        if (KLINE_CACHE_DIR / f"{code}.json").exists() and read_kline_basis(code) != ADJUSTMENT:
            continue
        history = completed_daily_rows(read_kline_cache(stock.get("code", "")) or stock.get("klines", []))
        if len(history) >= 30:
            usable.append({"stock": stock, "rows": history, "indexes": {row.get("date"): index for index, row in enumerate(history)}})
    results = []
    cache_entries = metrics_cache.setdefault("entries", {}) if metrics_cache is not None else {}
    stock_signature = hashlib.sha256(",".join(sorted(item["stock"].get("code", "") for item in usable)).encode("utf-8")).hexdigest()[:16]
    total_cache_hits, total_computed_points = 0, 0
    available_dates = sorted(set().union(*(set(item["indexes"]) for item in usable))) if usable else []
    exit_offset = ENTRY_OFFSET + DEFAULT_HOLDING_DAYS
    sample_dates = available_dates[-81 - exit_offset:-exit_offset]
    date_positions = {day: i for i, day in enumerate(available_dates)}
    # Fingerprint raw bars once per trading date. A changed bar can affect the
    # two preceding return labels and up to 120 subsequent rolling signals.
    raw_digests = {date: hashlib.sha256() for date in available_dates}
    for item in sorted(usable, key=lambda value: value["stock"].get("code", "")):
        code = item["stock"].get("code", "")
        for row in item["rows"]:
            date = row.get("date")
            if date not in raw_digests:
                continue
            raw_digests[date].update(code.encode("utf-8"))
            raw_digests[date].update("|".join(str(row.get(field, "")) for field in ("open", "high", "low", "close", "volume")).encode("utf-8"))
    current_sources = {date: digest.hexdigest()[:16] for date, digest in raw_digests.items()}
    source_key = f"{cache_namespace}|{stock_signature}"
    source_cache = metrics_cache.setdefault("sources", {}) if metrics_cache is not None else {}
    previous_sources = source_cache.get(source_key, {})
    changed_source_dates = {date for date in set(previous_sources) | set(current_sources) if previous_sources.get(date) != current_sources.get(date)}
    source_cache[source_key] = current_sources
    timeline = sorted(set(previous_sources) | set(current_sources))
    affected_dates = set()
    for changed_date in changed_source_dates:
        changed_index = timeline.index(changed_date)
        affected_dates.update(timeline[max(0, changed_index - exit_offset):changed_index + 121])
    refreshed_dates = set()
    for key, name, formula in candidates or MINING_CANDIDATES:
        daily_ic, long_short = [], []
        definition_hash = hashlib.sha256(formula.encode("utf-8")).hexdigest()[:12]
        cache_key = f"{cache_namespace}|{stock_signature}|{key}|{definition_hash}"
        cached_dates = cache_entries.setdefault(cache_key, {})
        # Use actual trading dates, never positional row alignment. Signal is
        # known at T close and evaluated on the tradable T+1 -> T+2 close return.
        for date in sample_dates:
            cached_metric = cached_dates.get(date)
            if cached_metric is not None and date not in affected_dates:
                total_cache_hits += 1
                cached_ic = cached_metric.get("ic") if isinstance(cached_metric, dict) else cached_metric[0]
                cached_spread = cached_metric.get("spread") if isinstance(cached_metric, dict) else cached_metric[1]
                if cached_ic is not None:
                    daily_ic.append(float(cached_ic)); long_short.append(float(cached_spread))
                continue
            if cached_metric is not None:
                refreshed_dates.add(date)
            cross_section = []
            for item in usable:
                rows = item["rows"]
                index = item["indexes"].get(date)
                position = date_positions[date]
                entry_index = item["indexes"].get(available_dates[position + ENTRY_OFFSET])
                exit_index = item["indexes"].get(available_dates[position + exit_offset])
                if index is None or entry_index is None or exit_index is None: continue
                value = _factor_value(rows, index, key)
                entry_close = rows[entry_index].get("close")
                next_return = rows[exit_index]["close"] / entry_close - 1 if entry_close else None
                if value is not None and next_return is not None and math.isfinite(value) and math.isfinite(next_return):
                    cross_section.append((value, next_return))
            total_computed_points += 1
            if len(cross_section) < 20:
                cached_dates[date] = {"ic": None, "spread": None}
                continue
            factors, targets = zip(*cross_section)
            ic = _correlation(_rank(list(factors)), _rank(list(targets)))
            ordered = sorted(cross_section, key=lambda pair: pair[0])
            bucket = max(1, len(ordered) // 5)
            spread = mean([x[1] for x in ordered[-bucket:]]) - mean([x[1] for x in ordered[:bucket]])
            long_short.append(spread)
            daily_ic.append(ic)
            cached_dates[date] = {"ic": round(ic, 8), "spread": round(spread, 8)}
        if len(cached_dates) > 120:
            keep_dates = set(sorted(cached_dates)[-120:])
            cache_entries[cache_key] = {date: value for date, value in cached_dates.items() if date in keep_dates}
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
    return {"universe": len(stocks), "usable_stocks": len(usable), "history_bars": min((len(item["rows"]) for item in usable), default=0), "sample_days": len(sample_dates), "candidate_count": len(results), "cache_hits": total_cache_hits, "computed_points": total_computed_points, "changed_source_dates": sorted(changed_source_dates), "refreshed_dates": sorted(refreshed_dates), "results": results,
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
    """Precompute the shared date/stock factor matrix for every combination.

    每行同时保存 T、T+1、T+2、T+3 四根收盘价，这样"尾盘买入 + 持有 N 天"的
    任意进出场口径都能从同一份矩阵算出来，不必为每种口径重建矩阵。
    """
    usable = prepare_backtest_universe(stocks)
    excluded_basis = [code for code, _, _ in usable if read_kline_basis(code) != ADJUSTMENT]
    excluded = set(excluded_basis)
    usable = [item for item in usable if item[0] not in excluded]
    observations = {}
    calendar = sorted({bar["date"] for _, rows, _ in usable for bar in rows})
    calendar_positions = {day: index for index, day in enumerate(calendar)}
    latest_data_date = max((rows[-1].get("date") or "" for _, rows, _ in usable if rows), default="")
    latest = []
    for code, rows, indexes in usable:
        for index in range(120, len(rows)):
            values = {key: _factor_value(rows, index, key) for key in factor_keys}
            values["__liquidity"] = liquidity_value(rows, index)
            position = calendar_positions[rows[index]["date"]]
            closes = tuple(rows[indexes[calendar[position + step]]].get("close")
                           if position + step < len(calendar) and calendar[position + step] in indexes else None
                           for step in range(FORWARD_STEPS + 1))
            try:
                first = float(closes[0])
            except (TypeError, ValueError):
                continue
            if not math.isfinite(first) or first <= 0:
                continue
            observations.setdefault(rows[index].get("date"), []).append((code, values, closes))
        if rows and rows[-1].get("date") == latest_data_date:
            index = len(rows) - 1
            values = {key: _factor_value(rows, index, key) for key in factor_keys}
            values["__liquidity"] = liquidity_value(rows, index)
            latest.append((code, values))
    return {"usable": usable, "observations": observations, "latest": latest, "latest_data_date": latest_data_date,
            "excluded_basis": excluded_basis, "price_basis": ADJUSTMENT}

def window_return(closes, entry_offset=ENTRY_OFFSET, holding_days=DEFAULT_HOLDING_DAYS):
    """按「T 日尾盘买入、持有 N 个交易日」算这一段收益；数据不足返回 None。"""
    exit_offset = entry_offset + holding_days
    if not closes or exit_offset >= len(closes):
        return None
    try:
        entry = float(closes[entry_offset])
        exit_price = float(closes[exit_offset])
    except (TypeError, ValueError):
        return None
    if entry <= 0 or exit_price <= 0 or not math.isfinite(entry) or not math.isfinite(exit_price):
        return None
    return exit_price / entry - 1

def combo_daily_series(prepared, factors, dates=None, cost_rate=0.0015, bucket_size=3, min_cross=20,
                       entry_offset=ENTRY_OFFSET, holding_days=DEFAULT_HOLDING_DAYS, allow_partial_horizon=False):
    """组合的逐日收益序列（逐日盯市，按持有期拆成互不重叠的资金队列）。

    持有 K 天意味着每笔仓位占用 K 个交易日。如果对每个信号日都同时满仓开仓，
    仓位会重叠、同一笔资金被重复计算。这里把资金分成 K 个队列，信号日按 K 取模
    分配到队列里，每个队列内部两笔交易首尾相接、完全不重叠，各占 1/K 资金。

    收益按天盯市：一笔 T 日买入的仓位，在第 T+1…T+K 天分别贡献
    close[j]/close[j-1]-1 的平均值，而不是等到卖出那天一次性记整段收益——
    否则日收益会变成尖刺，Sharpe 与回撤全部失真。
    """
    if not allow_partial_horizon:
        return execution_daily_series(prepared, factors, dates, cost_rate, bucket_size, min_cross,
                                      entry_offset, holding_days)
    available = sorted(prepared["observations"])
    wanted = available if dates is None else [date for date in available if date in set(dates)]
    if not wanted:
        return [], []
    positions = {date: index for index, date in enumerate(wanted)}
    cohort_count = max(1, holding_days)
    daily = [0.0] * len(wanted)
    last_selected = []
    for phase in range(cohort_count):
        previous = set()
        for date in wanted[phase::cohort_count]:
            selected = _select_top(prepared, factors, date, bucket_size, min_cross, entry_offset, holding_days,
                                   allow_partial_horizon=allow_partial_horizon)
            if not selected:
                continue
            codes = [row[0] for row in selected]
            turnover = 1.0 if not previous else 1 - len(previous & set(codes)) / max(len(codes), 1)
            entry_position = positions[date]
            daily[entry_position] -= cost_rate * turnover / cohort_count
            for step in range(1, cohort_count + 1):
                target_position = entry_position + step
                if target_position >= len(wanted):
                    break
                step_returns = []
                for _, _, closes in selected:
                    if step >= len(closes):
                        break
                    before, after = closes[step - 1], closes[step]
                    if not before or after is None:
                        continue
                    step_returns.append(after / before - 1)
                if step_returns and (not allow_partial_horizon or len(step_returns) == len(selected)):
                    daily[target_position] += mean(step_returns) / cohort_count
            previous = set(codes)
            last_selected = selected
    return daily, last_selected

def execution_daily_series(prepared, factors, dates=None, cost_rate=.0015, bucket_size=3, min_cross=20,
                           entry_offset=ENTRY_OFFSET, holding_days=DEFAULT_HOLDING_DAYS, account_curve=None,
                           trade_log=None, capital_cohorts=None, fill_log=None):
    """Fixed units and cash, delayed close fills; missing quotes never become fills."""
    available = sorted(prepared["observations"])
    allowed = set(dates) if dates is not None else set(available)
    wanted = [day for day in available if day in allowed]
    cohorts = max(1, holding_days) if capital_cohorts is None else capital_cohorts
    if not isinstance(cohorts, int) or cohorts < 1:
        raise ValueError("capital_cohorts must be a positive integer")
    books = [{"cash": 1 / cohorts, "positions": {}, "exit": None} for _ in range(cohorts)]
    entries, last_selected = collections.defaultdict(list), []
    for i, day in enumerate(wanted):
        target = i + entry_offset
        if target < len(wanted):
            selected = _select_top(prepared, factors, day, bucket_size, min_cross, entry_offset, holding_days,
                                   allow_partial_horizon=True)
            if selected:
                entries[target].append((i % cohorts, selected))
                last_selected = selected
    daily, previous_equity = [], 1.0
    for i, day in enumerate(wanted):
        complete = True
        quotes = {**prepared.get("quotes", {}).get(day, {}),
                  **{code: closes[0] for code, _, closes in prepared["observations"][day]
                     if closes and closes[0] is not None and math.isfinite(closes[0]) and closes[0] > 0}}
        for book in books:
            for code, position in list(book["positions"].items()):
                offset = entry_offset + i - position["entry_index"]
                closes = position["closes"]
                price = closes[offset] if offset < len(closes) else quotes.get(code)
                if price is not None and math.isfinite(price) and price > 0:
                    position["price"] = price
                    if book["exit"] is not None and i >= book["exit"]:
                        if fill_log is not None:
                            fill_log.append({"date": day, "side": "sell", "code": code,
                                             "entry_date": position["entry_date"], "price": price,
                                             "units": position["units"], "gross": position["units"] * price,
                                             "fee": position["units"] * price * cost_rate / 2})
                        book["cash"] += position["units"] * price * (1 - cost_rate / 2)
                        del book["positions"][code]
                else:
                    complete = False
            if trade_log is not None and book.get("trade") and not book["positions"]:
                trade = book.pop("trade")
                trade.update({"exit_date": day, "status": "closed",
                              "return": book["cash"] / trade["capital"] - 1})
        for phase, selected in entries[i]:
            book = books[phase]
            if book["positions"]:
                continue
            budget = book["cash"] / bucket_size
            starting_cash = book["cash"]
            for code, _, closes in selected:
                price = closes[entry_offset] if entry_offset < len(closes) else None
                if price is None or not math.isfinite(price) or price <= 0:
                    continue
                units = budget / (price * (1 + cost_rate / 2))
                book["cash"] -= budget
                book["positions"][code] = {"units": units, "price": price, "entry_index": i, "closes": closes,
                                           "entry_date": day}
                if fill_log is not None:
                    fill_log.append({"date": day, "side": "buy", "code": code, "entry_date": day,
                                     "price": price, "units": units, "gross": units * price,
                                     "fee": units * price * cost_rate / 2})
            book["exit"] = i + holding_days
            if trade_log is not None and book["positions"]:
                trade = {"signal_date": wanted[i - entry_offset], "entry_date": day,
                         "capital": starting_cash, "codes": list(book["positions"]), "status": "open"}
                book["trade"] = trade
                trade_log.append(trade)
        equity = sum(book["cash"] + sum(position["units"] * position["price"]
                                        for position in book["positions"].values()) for book in books)
        daily.append(equity / previous_equity - 1)
        if account_curve is not None:
            account_curve.append({"date": day, "equity": equity, "cash": sum(book["cash"] for book in books),
                                  "complete": complete})
        previous_equity = equity
    return daily, last_selected


def _select_top(prepared, factors, date, bucket_size, min_cross, entry_offset, holding_days, allow_partial_horizon=False):
    """在某个交易日按因子截面排名选出 Top N；流动性不足或数据缺失返回空列表。

    返回选中的原始观测元组 (code, values, closes)——逐日盯市还要用它的收盘价序列。
    """
    observations = prepared["observations"].get(date) or []
    if not observations:
        return []
    # Ranking must never depend on whether a future entry/exit quote exists.
    rows = observations
    if not rows:
        return []
    liquidities = sorted(values.get("__liquidity", 0) for _, values, _ in rows if values.get("__liquidity", 0) > 0)
    cutoff = liquidity_cutoff(liquidities)
    cross = [observation for observation in rows
             if observation[1].get("__liquidity", 0) >= cutoff and observation[1].get("__liquidity", 0) > 0
             if all(observation[1].get(key) is not None and math.isfinite(observation[1][key]) for key, _ in factors)]
    if len(cross) < min_cross:
        return []
    ranks = [_rank([observation[1].get(key) for observation in cross]) for key, _ in factors]
    scored = []
    for position, observation in enumerate(cross):
        score = mean([ranks[column][position] * direction for column, (_, direction) in enumerate(factors)])
        scored.append((score, observation))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [observation for _, observation in scored[:min(bucket_size, len(scored))]]

def series_stats(daily):
    """把逐日收益折算成年化口径的指标，并给出 DSR 需要的偏度/峰度。"""
    if len(daily) < 20:
        return None
    equity, peak, max_drawdown = 1.0, 1.0, 0.0
    for value in daily:
        equity *= 1 + value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, 1 - equity / peak)
    average, deviation = mean(daily), statistics.pstdev(daily)
    count = len(daily)
    skewness = kurtosis = 0.0
    if deviation > 0 and count > 3:
        centred = [(value - average) / deviation for value in daily]
        skewness = sum(value ** 3 for value in centred) / count
        kurtosis = sum(value ** 4 for value in centred) / count
    return {
        "days": count,
        "equity": round(equity, 4),
        "cumulative_return": round(equity - 1, 4),
        "annual_return": round(equity ** (252 / count) - 1, 4),
        "max_drawdown": round(max_drawdown, 4),
        "mean_daily": round(average, 6),
        "daily_sharpe": round(average / deviation, 4) if deviation else 0.0,
        "sharpe": round(average / deviation * math.sqrt(252), 3) if deviation else 0.0,
        "win_rate": round(sum(1 for value in daily if value > 0) / count, 3),
        "skewness": round(skewness, 4),
        "kurtosis": round(kurtosis, 4),
        "annualization_reliable": count >= 126,
    }

def backtest_factor_combo(stocks, factors, prepared=None, entry_offset=ENTRY_OFFSET, holding_days=DEFAULT_HOLDING_DAYS):
    """T 日尾盘买入、持有 N 个交易日后收盘卖出；Top 3 等权。"""
    prepared = prepared if isinstance(prepared, dict) else prepare_backtest_dataset(stocks, [key for key, _ in factors])
    usable = prepared["usable"]
    daily, latest_selected = combo_daily_series(prepared, factors, entry_offset=entry_offset, holding_days=holding_days)
    stats = series_stats(daily)
    if stats is None:
        return {"status": "数据不足", "days": len(daily)}
    latest_liquidities = sorted(values.get("__liquidity", 0) for _, values in prepared["latest"] if values.get("__liquidity", 0) > 0)
    latest_cutoff = latest_liquidities[max(0, int(len(latest_liquidities) * 0.30) - 1)] if latest_liquidities else 0
    latest_cross = [(code, [values.get(key) for key, _ in factors]) for code, values in prepared["latest"]
                    if values.get("__liquidity", 0) >= latest_cutoff and values.get("__liquidity", 0) > 0
                    if all(values.get(key) is not None and math.isfinite(values[key]) for key, _ in factors)]
    if latest_cross:
        latest_ranks = [_rank([row[1][col] for row in latest_cross]) for col in range(len(factors))]
        latest_selected = sorted([(latest_cross[pos][0], mean([latest_ranks[col][pos] * direction for col, (_, direction) in enumerate(factors)])) for pos in range(len(latest_cross))], key=lambda row: row[1], reverse=True)[:3]
    names = {stock.get("code", ""): stock.get("name", stock.get("code", "")) for stock in stocks}
    latest_holdings = [{"code": row[0], "name": names.get(row[0], row[0])} for row in latest_selected]
    return {"status": "完成" if stats["annualization_reliable"] else "短样本，仅供参考", "portfolio": "Top 3 初始等权；固定份额，持有期间权重漂移",
            "universe": len(usable), "holding_count": len(latest_holdings), "latest_holdings": latest_holdings,
            "cost_bps": 15, "audited": True,
            "entry_offset": entry_offset, "holding_days": holding_days,
            "return_definition": f"T 日收盘后信号，T+{entry_offset} 收盘模拟买入，成交后持有 {holding_days} 日；买卖各扣 7.5bp，固定份额",
            "price_basis": prepared.get("price_basis"), "excluded_unadjusted": len(prepared.get("excluded_basis", [])),
            "execution_warning": "前复权标准化份额模拟；不是两万元整手账户，未建模涨跌停、实际滑点与券商最低佣金",
            "trade_delay_days": entry_offset, **stats}

def window_factor_gate(prepared, keys, dates, min_days=40):
    """只用窗口内数据重算每个因子的 Rank IC / ICIR 与方向。

    这是样本外验证的关键：因子能不能用，只能由它当期的历史数据说了算。
    """
    ic_series = {key: [] for key in keys}
    # Purge labels whose delayed entry/exit crosses into the test window.
    purge = ENTRY_OFFSET + max(HOLDING_CHOICES)
    for date in dates[:-purge] if purge else dates:
        rows = prepared["observations"].get(date)
        if not rows or len(rows) < 20:
            continue
        for key in keys:
            pairs = [(row[1].get(key), window_return(row[2], ENTRY_OFFSET, DEFAULT_HOLDING_DAYS)) for row in rows
                     if row[1].get(key) is not None and math.isfinite(row[1][key])]
            pairs = [(value, target) for value, target in pairs if target is not None]
            if len(pairs) < 20:
                continue
            ic_series[key].append(_correlation(_rank([value for value, _ in pairs]), _rank([target for _, target in pairs])))
    stats = {}
    for key, values in ic_series.items():
        if len(values) < min_days:
            continue
        average = mean(values)
        deviation = statistics.pstdev(values)
        stats[key] = {
            "mean_rank_ic": round(average, 4),
            "icir": round(average / deviation, 3) if deviation else 0.0,
            "sample_days": len(values),
            "ic_positive_rate": round(sum(1 for value in values if value > 0) / len(values), 3),
            "direction": "正向" if average >= 0 else "反向",
        }
    return stats

def _window_cross_section(prepared, keys, date):
    """窗口内某一天的因子截面，用于在窗口内做相关性并簇。"""
    rows = prepared["observations"].get(date) or []
    series = {}
    for key in keys:
        values = {}
        for code, factor_values, _ in rows:
            value = factor_values.get(key)
            if value is not None and isinstance(value, (int, float)) and math.isfinite(value):
                values[code] = value
        series[key] = values
    return series

def walk_forward_backtest(prepared, keys, progress=None, train_days=200, test_days=60,
                          max_factors=4, min_ic=0.03, min_icir=0.15, max_combo_size=3, names=None):
    """滚动样本外：每个窗口只用训练期数据选因子与组合，再放到紧接的测试期上评估。

    这样得到的收益曲线每一步都只用了"当时已经发生"的信息，是判断策略是否
    真的可推广、而不是在整段历史上挑出来的唯一办法。
    """
    progress = progress or (lambda stage, detail: None)
    names = names or {}
    dates = sorted(prepared["observations"])
    if len(dates) < train_days + test_days:
        return {"status": "数据不足", "available_days": len(dates)}
    folds, out_of_sample = [], []
    start = 0
    while start + train_days + 20 <= len(dates):
        train = dates[start:start + train_days]
        test = dates[start + train_days:start + train_days + test_days]
        if len(test) < 20:
            break
        gated = [(key, stats) for key, stats in window_factor_gate(prepared, keys, train).items()
                 if abs(stats["mean_rank_ic"]) >= min_ic and abs(stats["icir"]) >= min_icir]
        if not gated:
            folds.append({"train": [train[0], train[-1]], "test": [test[0], test[-1]],
                          "status": "窗口内没有因子通过门槛", "selected": []})
            out_of_sample.extend([0.0] * len(test))
            start += test_days
            continue
        series = _window_cross_section(prepared, [key for key, _ in gated], train[-1])
        items = [{"key": key, "name": names.get(key, key), **stats} for key, stats in gated]
        representatives, merged = cluster_factors(items, series)
        chosen = representatives[:max_factors]
        combos = []
        for size in range(1, min(max_combo_size, len(chosen)) + 1):
            for group in combinations(chosen, size):
                combo = tuple((item["key"], 1 if item["direction"] == "正向" else -1) for item in group)
                for holding in HOLDING_CHOICES:
                    combos.append((combo, holding))
        best, best_stats = None, None
        for combo, holding in combos:
            daily, _ = combo_daily_series(prepared, combo, train, holding_days=holding)
            stats = series_stats(daily)
            if stats and (best_stats is None or stats["sharpe"] > best_stats["sharpe"]):
                best, best_stats = (combo, holding), stats
        if best is None:
            folds.append({"train": [train[0], train[-1]], "test": [test[0], test[-1]],
                          "status": "训练期无法形成有效组合", "selected": []})
            out_of_sample.extend([0.0] * len(test))
            start += test_days
            continue
        best_combo, best_holding = best
        test_daily, _ = combo_daily_series(prepared, best_combo, test, holding_days=best_holding)
        test_stats = series_stats(test_daily)
        out_of_sample.extend(test_daily)
        folds.append({
            "train": [train[0], train[-1]], "test": [test[0], test[-1]], "status": "完成",
            "gated_count": len(gated), "merged_count": len(merged), "combo_candidates": len(combos),
            "selected": [key for key, _ in best_combo],
            "selected_names": [names.get(key, key) for key, _ in best_combo],
            "holding_days": best_holding,
            "holding_label": HOLDING_LABELS.get(best_holding),
            "train_sharpe": round(best_stats["sharpe"], 3) if best_stats else None,
            "train_annual": round(best_stats["annual_return"], 4) if best_stats else None,
            "test_days": len(test_daily),
            "test_annual": round(test_stats["annual_return"], 4) if test_stats else None,
            "test_sharpe": round(test_stats["sharpe"], 3) if test_stats else None,
            "test_max_drawdown": round(test_stats["max_drawdown"], 4) if test_stats else None,
        })
        start += test_days
    overall = series_stats(out_of_sample)
    if overall is None:
        return {"status": "样本外数据不足", "folds": folds, "out_of_sample_days": len(out_of_sample)}
    return {
        "status": "完成",
        "method": f"滚动窗口：训练 {train_days} 日 → 测试 {test_days} 日，步长 {test_days} 日；每折只用训练期数据选因子、组合与持有期（{' / '.join(HOLDING_LABELS[h] for h in HOLDING_CHOICES)}）",
        "train_days": train_days, "test_days": test_days,
        "entry_rule": "T 日收盘后信号，下一交易日收盘模拟成交",
        "holding_choices": list(HOLDING_CHOICES),
        "fold_count": len([fold for fold in folds if fold.get("status") == "完成"]),
        "out_of_sample_days": overall["days"],
        "out_of_sample": overall,
        "folds": folds,
        "note": "条件样本外验证：候选库仍由全样本筛选，当前股池存在存活偏差；不是完全独立样本证明。训练边界收益标签已剔除，空仓测试日期保留。",
    }

def deflated_sharpe_ratio(selected, trial_sharpes, periods_per_year=252):
    """Bailey & López de Prado 的 Deflated Sharpe Ratio。

    在"试了 T 组、按最高 Sharpe 挑了一组"的前提下，给出真实 Sharpe 大于 0 的概率。
    直接用观测到的 Sharpe 会系统性高估——T 越大，最大值被运气抬得越高。
    """
    trials = len([value for value in trial_sharpes if value is not None])
    if trials < 2 or not selected or selected.get("days", 0) < 3:
        return {"status": "试算次数不足", "trials": trials}
    per_period = [value / math.sqrt(periods_per_year) for value in trial_sharpes if value is not None]
    dispersion = statistics.pstdev(per_period)
    sharpe = selected.get("daily_sharpe") or (selected.get("sharpe", 0) / math.sqrt(periods_per_year))
    if dispersion <= 0:
        expected_max = 0.0
    else:
        gamma = 0.5772156649015329
        normal = statistics.NormalDist()
        expected_max = dispersion * ((1 - gamma) * normal.inv_cdf(1 - 1 / trials)
                                     + gamma * normal.inv_cdf(1 - 1 / (trials * math.e)))
    skewness = selected.get("skewness", 0.0)
    kurtosis = selected.get("kurtosis", 3.0)
    denominator = 1 - skewness * sharpe + (kurtosis - 1) / 4 * sharpe ** 2
    if denominator <= 0:
        return {"status": "收益分布异常，无法校正", "trials": trials, "expected_max_sharpe": round(expected_max * math.sqrt(periods_per_year), 3)}
    z = (sharpe - expected_max) * math.sqrt(selected["days"] - 1) / math.sqrt(denominator)
    return {
        "status": "完成",
        "trials": trials,
        "observed_sharpe": round(selected.get("sharpe", 0), 3),
        "expected_max_sharpe": round(expected_max * math.sqrt(periods_per_year), 3),
        "trial_sharpe_dispersion": round(dispersion * math.sqrt(periods_per_year), 3),
        "deflated_sharpe": round(sharpe - expected_max, 4),
        "z_score": round(z, 3),
        "probability": round(statistics.NormalDist().cdf(z), 4),
        "note": "probability 表示「真实 Sharpe 大于 0」的概率；试算次数越多、试算结果越分散，门槛越高。",
    }

def run_backtest_experiment(stocks, progress=None, force=False):
    progress = progress or (lambda stage, detail: None)
    latest = read_factor_agent_run()
    universe_signature = universe_signature_for_stocks(stocks)
    # Refresh factor validation when the fixed universe changes instead of
    # returning a dead-end 409 or silently reusing incompatible research.
    if not latest or latest.get("universe_signature") != universe_signature or latest.get("engine_version") != FACTOR_ENGINE_VERSION:
        latest = run_factor_agent_loop(stocks)
    dashboard_date = latest_completed_data_date(stocks)
    data_fingerprint = kline_data_fingerprint(stocks)
    cached = read_backtest_run()
    if cached and cached.get("stale_engine") and BACKTEST_RUN_FILE.exists():
        archive = CACHE_DIR / ("backtest_legacy_" + hashlib.sha256(BACKTEST_RUN_FILE.read_bytes()).hexdigest()[:16] + ".json")
        if not archive.exists():
            shutil.copy2(BACKTEST_RUN_FILE, archive)
    if not force and cached and cached.get("engine_version") == BACKTEST_ENGINE_VERSION and cached.get("universe_signature") == universe_signature and cached.get("data_date") == dashboard_date and cached.get("data_fingerprint") == data_fingerprint and cached.get("factor_run_generated_at") == latest.get("generated_at"):
        progress("缓存命中", "复用同一股池和交易日的回测结果")
        return {**cached, "cache_hit": True}
    pool = [item for item in latest.get("validation", {}).get("results", []) if item.get("gate") == "通过"]
    if len(pool) < 4:
        pool = [item for item in latest.get("validation", {}).get("results", []) if item.get("mean_rank_ic") is not None]
    def family(item):
        key = item.get("key", "")
        if key.startswith("ma"): return "均线结构"
        if key.startswith(("momentum", "reversal")): return "动量反转"
        if key.startswith(("volume", "price_volume")): return "成交量"
        if key in {"body_strength", "upper_shadow_ratio", "lower_shadow_ratio", "engulfing_volume", "breakout_volume_confirm", "close_volume_pressure", "contraction_breakout", "cup_handle_breakout"}: return "K线量价"
        if key.startswith(("range_position", "breakout", "drawdown", "close_location")): return "价格位置"
        if key.startswith(("volatility", "downside_vol", "atr", "range_expansion")): return "波动风险"
        return key.split("_")[0]
    candidates, family_counts = [], {}
    # 先按真实相关性并簇（去掉"同一因子的不同写法"），再按行业族上限兜底，
    # 最后只保留最强且彼此独立的 7 个因子进入组合搜索。
    _, pool_series = factor_cross_section(stocks, [item["key"] for item in pool])
    independent, dropped = cluster_factors(pool, pool_series)
    for item in independent:
        group = family(item)
        if family_counts.get(group, 0) >= 2:
            continue
        candidates.append(item); family_counts[group] = family_counts.get(group, 0) + 1
        if len(candidates) == 7:
            break
    if dropped:
        progress("因子去重", f"{len(pool)} 个候选按相关性并簇后剩 {len(independent)} 个独立因子，进入组合搜索")
    if len(candidates) < 2:
        raise RuntimeError("去重后可用因子不足 2 个，请重新运行因子挖掘")
    factors = [(item["key"], 1 if item.get("direction") == "正向" else -1) for item in candidates]
    reusable_runs = {}
    compatible_previous = bool(
        cached
        and cached.get("engine_version") == BACKTEST_ENGINE_VERSION
        and cached.get("universe_signature") == universe_signature
        and cached.get("data_date") == dashboard_date
        and cached.get("data_fingerprint") == data_fingerprint
    )
    if compatible_previous:
        for previous_run in cached.get("results", []):
            keys = previous_run.get("factors", [])
            directions = previous_run.get("directions", [])
            if len(keys) != len(directions):
                continue
            identity = (tuple(zip(keys, (1 if value == "正向" else -1 for value in directions))),
                        previous_run.get("holding_days", DEFAULT_HOLDING_DAYS))
            reusable_runs[identity] = previous_run
    prepared = None
    reused_combination_count = 0
    computed_combination_count = 0
    runs, evaluated = [], set()
    factor_order = {key: index for index, (key, _) in enumerate(factors)}
    def evaluate(combo, holding_days=DEFAULT_HOLDING_DAYS):
        nonlocal prepared, reused_combination_count, computed_combination_count
        combo = tuple(sorted(combo, key=lambda item: factor_order[item[0]]))
        identity = (tuple(key for key, _ in combo), holding_days)
        if identity in evaluated:
            return None
        evaluated.add(identity)
        combo_items = [next(item for item in candidates if item["key"] == key) for key, _ in combo]
        previous_run = reusable_runs.get((combo, holding_days))
        if previous_run is not None:
            result = previous_run.get("result", {})
            reused_combination_count += 1
        else:
            if prepared is None:
                progress("准备数据", f"发现新组合，正在准备 {len(stocks)} 只股票、{len(factors)} 个候选因子的回测矩阵")
                prepared = prepare_backtest_dataset(stocks, [key for key, _ in factors])
            result = backtest_factor_combo(stocks, combo, prepared=prepared, holding_days=holding_days)
            computed_combination_count += 1
        run = {"kind":f"{len(combo)} 因子" if len(combo) == 1 else f"{len(combo)} 因子组合", "factor_count":len(combo), "holding_days":holding_days,
               "factors":[key for key, _ in combo], "names":[item["name"] for item in combo_items],
               "directions":[item.get("direction", "待定") for item in combo_items], "result":result}
        runs.append(run)
        return run
    def run_score(item):
        result = item.get("result", {})
        return (result.get("audited", False), result.get("sharpe", -999), result.get("cumulative_return", -999))
    def evaluate_all_holdings(combo):
        """同一个组合在「第二天卖」和「第三天卖」两种持有期下都跑一遍。

        多一个选择维度就要多付一份多重检验的代价——两组结果都会计入试算次数，
        DSR 的运气门槛会相应抬高，这一点不允许被悄悄忽略。
        """
        return [run for run in (evaluate(combo, holding) for holding in HOLDING_CHOICES) if run]
    frontier = [run for factor in factors for run in evaluate_all_holdings((factor,))]
    frontier = sorted(frontier, key=run_score, reverse=True)[:3]
    for size in range(2, min(5, len(factors)) + 1):
        proposals = []
        proposal_keys = set()
        for prior in frontier:
            prior_keys = set(prior["factors"])
            for factor in factors:
                if factor[0] in prior_keys:
                    continue
                combo = tuple(sorted([next(value for value in factors if value[0] == key) for key in prior["factors"]] + [factor], key=lambda item: factor_order[item[0]]))
                identity = tuple(key for key, _ in combo)
                if identity not in proposal_keys:
                    proposal_keys.add(identity); proposals.append(combo)
        progress("组合回测", f"正在用束搜索计算 {size} 因子组合（候选 {min(12, len(proposals))} 组 × {len(HOLDING_CHOICES)} 种持有期）")
        level = [run for combo in proposals[:12] for run in evaluate_all_holdings(combo)]
        frontier = sorted(level, key=run_score, reverse=True)[:3]
        if not frontier:
            break
    runs.sort(key=lambda item: (item["result"].get("audited", False), item["result"].get("sharpe", -999), item["result"].get("cumulative_return", -999)), reverse=True)
    for index, item in enumerate(runs, 1):
        item["rank"] = index
        item["rank_metric"] = "成本后 Sharpe"
    selected_names = [TECH_SECTOR_DEFINITIONS[key]["name"] for key in read_tech_config()]
    usable_count = len(prepared["usable"]) if prepared is not None else int(cached.get("usable_universe", 0) if compatible_previous else 0)
    progress("结果排名", f"已完成 {len(runs)} 组回测：复用 {reused_combination_count} 组，新算 {computed_combination_count} 组")

    # 多重检验校正：47 组里挑 Sharpe 最高的一组，最大值天然被运气抬高
    trial_sharpes = [(item.get("result") or {}).get("sharpe") for item in runs]
    winner = (runs[0].get("result") or {}) if runs else {}
    multiple_testing = deflated_sharpe_ratio(winner, trial_sharpes)
    if multiple_testing.get("status") == "完成":
        progress("多重检验校正", f"试算 {multiple_testing['trials']} 组：观测 Sharpe {multiple_testing['observed_sharpe']}，"
                                 f"纯运气期望上限 {multiple_testing['expected_max_sharpe']}，真实 Sharpe>0 的概率 {multiple_testing['probability']:.1%}")

    # 滚动样本外：每折只用训练期数据重选因子与组合，再放到测试期评估
    out_of_sample = None
    walk_keys = []
    clusters = latest.get("factor_clusters") or {}
    if clusters.get("independent"):
        walk_keys = [item["key"] for item in clusters["independent"]][:8]
    else:
        walk_keys = [item["key"] for item in pool][:8]
    reuse_oos = (compatible_previous and computed_combination_count == 0
                 and cached.get("factor_run_generated_at") == latest.get("generated_at")
                 and cached.get("out_of_sample") is not None)
    if reuse_oos:
        out_of_sample = cached["out_of_sample"]
    elif walk_keys:
        progress("样本外验证", f"滚动窗口重跑：候选 {len(walk_keys)} 个独立因子，每折只用训练期数据重新选因子与组合")
        try:
            # 独立因子与本次进入组合搜索的 7 个因子未必重合，必须单独准备矩阵
            walk_names = {item["key"]: item.get("name") for item in (clusters.get("independent") or [])}
            walk_prepared = (prepared if prepared is not None and set(walk_keys).issubset({key for key, _ in factors})
                             else prepare_backtest_dataset(stocks, walk_keys))
            out_of_sample = walk_forward_backtest(walk_prepared, walk_keys, progress=progress, names=walk_names)
        except Exception as exc:
            out_of_sample = {"status": "失败", "error": str(exc)}
        if out_of_sample.get("status") == "完成":
            oos = out_of_sample["out_of_sample"]
            progress("样本外结果", f"{out_of_sample['fold_count']} 折 / {oos['days']} 个样本外交易日：年化 {oos['annual_return']:.2%}、"
                                   f"Sharpe {oos['sharpe']}、最大回撤 {oos['max_drawdown']:.2%}（全样本 Sharpe {winner.get('sharpe')}）")

    warning = (f"{len(runs)} 个策略均直接在完整真实科技股池回测；其中 {usable_count} 只有足够历史。"
               "已排除 688/689、ST/退市风险及每日流动性后 30%。")
    if multiple_testing.get("status") == "完成":
        warning += f" 已做多重检验校正：{multiple_testing['trials']} 组试算下，纯运气也能得到约 {multiple_testing['expected_max_sharpe']} 的 Sharpe。"
    if out_of_sample and out_of_sample.get("status") == "完成":
        warning += " 样本外为滚动窗口逐步重选的结果，与全样本 Sharpe 的差距即为过拟合的直接度量。"
    payload = {"generated_at":time.strftime("%Y-%m-%d %H:%M:%S"), "engine_version":BACKTEST_ENGINE_VERSION, "data_fingerprint":data_fingerprint, "factor_run_generated_at":latest.get("generated_at"), "candidate_count":len(candidates), "combination_count":len(runs), "reused_combination_count":reused_combination_count, "computed_combination_count":computed_combination_count, "max_factors":5, "search_strategy":"beam search, width=3, max 12 expansions per level", "screen_universe":len(stocks), "confirmation_universe":len(stocks), "usable_universe":usable_count, "universe_signature":universe_signature, "universe_name":"科技股池（不含科创板/ST）", "selected_sectors":selected_names, "cost_bps":15, "ranking_metric":"成本后 Sharpe（同 Sharpe 时按成本后累计收益）", "data_date":dashboard_date, "results":runs, "multiple_testing":multiple_testing, "out_of_sample":out_of_sample,
            "entry_rule":"T 日收盘后信号，下一交易日收盘模拟成交", "holding_choices":list(HOLDING_CHOICES), "holding_labels":HOLDING_LABELS,
            "selected_holding_days":(runs[0].get("holding_days") if runs else None),
            "selection_rule":"日线近似：收盘后按当日因子排名选前三，下一交易日收盘模拟成交，成交后下一交易日卖出；固定份额，买卖各扣 7.5bp。", "warning":warning}
    try:
        registered = market_strategy.ensure(sys.modules[__name__], stocks)
        payload["results"].append(registered["row"])
        payload["registered_strategy_count"] = 1
    except ValueError:
        payload["registered_strategy_count"] = 0
    CACHE_DIR.mkdir(parents=True, exist_ok=True); temporary = BACKTEST_RUN_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"); os.replace(temporary, BACKTEST_RUN_FILE)
    return payload

def run_factor_agent_loop(stocks, progress=None, force=False, generated_specs=None):
    """Research hypotheses -> executable candidates -> local validation -> feedback."""
    progress = progress or (lambda stage, detail: None)
    universe_signature = universe_signature_for_stocks(stocks)
    dashboard_date = latest_completed_data_date(stocks)
    data_fingerprint = kline_data_fingerprint(stocks)
    cached = read_factor_agent_run()
    if not force and cached and cached.get("engine_version") == FACTOR_ENGINE_VERSION and cached.get("universe_signature") == universe_signature and cached.get("data_date") == dashboard_date and cached.get("data_fingerprint") == data_fingerprint:
        progress("缓存命中", "复用同一股池和交易日的因子验证结果")
        return {**cached, "cache_hit": True}
    base_search_space = build_factor_search_space()
    generated_candidates, generated_proposals = build_generated_factor_candidates(generated_specs, base_search_space)
    proposals = FACTOR_RESEARCH_PROPOSALS + generated_proposals
    proposal_map = {item["candidate"]: item for item in proposals}
    search_space = base_search_space + generated_candidates
    # Stage 1: deterministic stratified slice for broad search. Stage 2 below
    # confirms the strongest candidates on the complete real universe.
    ordered_stocks = sorted(stocks, key=lambda item: item.get("code", ""))
    search_stocks = ordered_stocks[::4]
    progress("抽样初筛", f"正在用 {len(search_stocks)} 只股票筛选 {len(base_search_space)} 个基线公式 + {len(generated_candidates)} 个本轮新公式")
    metrics_cache = read_factor_daily_metrics()
    validation = mine_factor_candidates(search_stocks, search_space, metrics_cache=metrics_cache, cache_namespace="screen")
    broad_top = sorted([item for item in validation["results"] if item.get("mean_rank_ic") is not None], key=lambda item: abs(item["mean_rank_ic"]), reverse=True)[:12]
    broad_keys = {item["key"] for item in broad_top}
    confirmation_candidates = [item for item in search_space if item[0] in broad_keys]
    progress("全量确认", f"正在用 {len(stocks)} 只股票确认前 {len(confirmation_candidates)} 个因子")
    confirmation = mine_factor_candidates(stocks, confirmation_candidates, metrics_cache=metrics_cache, cache_namespace="confirmation")
    write_factor_daily_metrics(metrics_cache)
    reused_metric_points = validation.get("cache_hits", 0) + confirmation.get("cache_hits", 0)
    computed_metric_points = validation.get("computed_points", 0) + confirmation.get("computed_points", 0)
    progress("增量统计", f"历史指标复用 {reused_metric_points} 条，本轮新增或刷新 {computed_metric_points} 条")
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
    passed = [item for item in results if item.get("gate") == "通过"]
    # 通过门槛的因子里有大量同源变体（ma_3/5/10_60、momentum/reversal 等），
    # 这里用真实截面相关性把它们并簇，只保留代表性因子。
    cluster_source = passed or [item for item in results if item.get("mean_rank_ic") is not None]
    clusters = factor_cluster_summary(cluster_source, stocks)
    if clusters["merged"]:
        progress("因子去共线", f"{clusters['input_count']} 个因子按相关性并簇后剩 {clusters['independent_count']} 个独立因子")
    backtest_items = [item for item in results[:4] if item.get("mean_rank_ic") is not None]
    if backtest_items:
        progress("单因子回测", f"正在复核排名最高的 {len(backtest_items)} 个因子")
        prepared_backtests = prepare_backtest_dataset(stocks, [item["key"] for item in backtest_items])
        for item in backtest_items:
            direction = 1 if item.get("direction") == "正向" else -1
            item["backtest"] = backtest_factor_combo(stocks, [(item["key"], direction)], prepared=prepared_backtests)
    passed = [item for item in results if item.get("gate") == "通过"]
    feedback = (f"本轮 {len(results)} 个公式中 {len(passed)} 个通过验证门槛。" if passed else
                "本轮没有因子通过生产门槛；下一轮应扩展财务/行业数据或调整搜索空间，不能把弱信号直接用于交易。")
    progress("汇总结果", feedback)
    payload = {"data_fingerprint": data_fingerprint, "stages": [
        {"name":"研究 Agent", "status":"完成", "detail":f"提出 {len(generated_candidates)} 个本轮新公式"},
        {"name":"开发 Agent", "status":"完成", "detail":f"执行 {len(base_search_space)} 个基线 + {len(generated_candidates)} 个生成公式"},
        {"name":"验证 Agent", "status":"完成", "detail":f"先筛 {len(search_stocks)} 只，再用全量 {len(stocks)} 只确认前 {len(confirmation_candidates)} 个"},
        {"name":"反馈迭代", "status":"完成", "detail":feedback},
    ], "engine_version": FACTOR_ENGINE_VERSION, "data_date": dashboard_date, "search_space_size": len(search_space), "baseline_formula_count": len(base_search_space), "generated_formula_count": len(generated_candidates), "generated_formulas": generated_proposals, "search_universe": len(search_stocks), "confirmation_universe": len(stocks), "universe_signature": universe_signature, "universe_name": "科技股池（不含科创板/ST）", "selected_sectors": [TECH_SECTOR_DEFINITIONS[key]["name"] for key in read_tech_config()], "proposals": proposals, "factor_clusters": clusters, "incremental_cache": {"reused_metric_points": reused_metric_points, "computed_metric_points": computed_metric_points, "refreshed_dates": sorted(set(validation.get("refreshed_dates", [])) | set(confirmation.get("refreshed_dates", [])))}, "validation": {**validation, "universe": len(stocks), "usable_stocks": validation.get("usable_stocks", 0), "history_bars": confirmation.get("history_bars", 0), "results": results}, "feedback": feedback,
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

def _http_get(url, headers=None, timeout=12):
    """Fetch raw bytes. urllib is primary so a missing/broken curl cannot blind the feed."""
    request_headers = {"User-Agent": "Mozilla/5.0"}
    if headers:
        request_headers.update(headers)
    try:
        with urlopen(Request(url, headers=request_headers), timeout=timeout) as response:
            return response.read()
    except HTTPError:
        # 服务器已经明确应答了状态码（例如 501），换成 curl 结果一样，
        # 再试一次只是白白多等一个超时——全股池刷新时这是分钟级的浪费。
        raise
    except Exception as exc:
        urllib_error = exc
    command = ["curl", "-L", "--max-time", str(timeout), "-sS"]
    for key, value in request_headers.items():
        command += ["-H", f"{key}: {value}"]
    command.append(url)
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout + 3, check=True)
    except Exception as curl_error:
        raise URLError(f"urllib={urllib_error!r}; curl={curl_error!r}")
    return result.stdout

def eastmoney_json(url, jsonp=False):
    last_error = None
    for attempt in range(3):
        try:
            body = _http_get(url, {"Referer": "https://quote.eastmoney.com/"})
            text = decode_http_body(body).strip()
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

def decode_http_body(body):
    """Decode Chinese market data without silently introducing mojibake."""
    for encoding in ("utf-8", "gb18030"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8/gb18030", body, 0, len(body), "unsupported response encoding")


def http_text(url):
    last_error = None
    for attempt in range(3):
        try:
            return decode_http_body(_http_get(url))
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
    existing_klines = read_kline_cache(code)
    fetch_bars = 30 if existing_klines else 1000
    kline_text = http_text(f"https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,{fetch_bars},qfq")
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
    klines = write_kline_cache(code, klines, ADJUSTMENT)
    closes = [x["close"] for x in klines]; volumes = [x["volume"] for x in klines]
    returns = [(closes[i] / closes[i-1] - 1) for i in range(1, len(closes)) if closes[i-1]]
    def avg(xs): return sum(xs) / len(xs) if xs else 0.0
    def change(window): return closes[-1] / closes[-window-1] - 1 if len(closes) > window and closes[-window-1] else 0.0
    momentum20, momentum60 = change(20), change(60); ma5, ma20 = avg(closes[-5:]), avg(closes[-20:]); trend = ma5 / ma20 - 1 if ma20 else 0.0
    volume_ratio = avg(volumes[-5:]) / avg(volumes[-20:]) if len(volumes) >= 20 and avg(volumes[-20:]) else 1.0
    vol20 = statistics.pstdev(returns[-20:]) * math.sqrt(252) if len(returns) >= 20 else 0.0
    return {**item, "price": current, "change": quote_change, "pct": quote_pct, "amount": float(parts[37] or 0), "volume": float(parts[6] or 0), "quote_time": quote_time, "last_date": klines[-1]["date"], "klines": klines[-60:], "mined_factors": latest_mined_factor_values(klines), **completed_factor_snapshot(klines), "raw": {}, "source": "tencent", "source_label": "腾讯实时 + 前复权日线", "factors": {"momentum20": momentum20, "momentum60": momentum60, "trend": trend, "volume_ratio": volume_ratio, "volatility20": vol20}}

def fetch_stock(item):
    fields = "f43,f44,f45,f46,f47,f48,f57,f58,f60,f169,f170,f162,f163"
    quote_url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={item['secid']}&fields={fields}"
    existing_klines = read_kline_cache(item["code"])
    begin_date = ((existing_klines[-10] if len(existing_klines) >= 10 else existing_klines[0]).get("date", "2024-01-01").replace("-", "")
                  if existing_klines else "20240101")
    kline_url = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={item['secid']}"
                 f"&klt=101&fqt=1&beg={begin_date}&end=20991231"
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
    klines = write_kline_cache(item["code"], klines, ADJUSTMENT)
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
    return {**item, "price": price, "change": change, "pct": pct, "amount": quote.get("f48") or 0, "source": "eastmoney", "source_label": "东方财富公开行情",
            "volume": quote.get("f47") or 0, "last_date": klines[-1]["date"] if klines else None,
            "klines": klines[-60:], "mined_factors": latest_mined_factor_values(klines), **completed_factor_snapshot(klines), "raw": {"pe": quote.get("f162"), "pb": quote.get("f163")},
            "factors": {"momentum20": momentum20, "momentum60": momentum60, "trend": trend,
                        "volume_ratio": volume_ratio, "volatility20": vol20}}

def build_stock_from_klines(item, klines, source, source_label):
    """由日线序列构造 stocks 条目。联网抓取与本地缓存两条路径共用，保证口径一致。"""
    closes = [x["close"] for x in klines]
    volumes = [x["volume"] for x in klines]
    returns = [(closes[i] / closes[i-1] - 1) for i in range(1, len(closes)) if closes[i-1]]
    def mean(xs): return sum(xs) / len(xs) if xs else 0.0
    def pct_change(window): return closes[-1] / closes[-window-1] - 1 if len(closes) > window and closes[-window-1] else 0.0
    momentum20, momentum60 = pct_change(20), pct_change(60)
    ma5, ma20 = mean(closes[-5:]), mean(closes[-20:]); trend = ma5 / ma20 - 1 if ma20 else 0.0
    volume_ratio = mean(volumes[-5:]) / mean(volumes[-20:]) if len(volumes) >= 20 and mean(volumes[-20:]) else 1.0
    vol20 = statistics.pstdev(returns[-20:]) * math.sqrt(252) if len(returns) >= 20 else 0.0
    price = closes[-1] if closes else 0
    change = price - closes[-2] if len(closes) > 1 else 0
    pct = change / closes[-2] * 100 if len(closes) > 1 and closes[-2] else 0
    return {**item, "price": price, "change": change, "pct": pct,
            # 成交额用收盘价 × 成交量（手）× 100 估算；新浪接口不直接给成交额
            "amount": price * (volumes[-1] if volumes else 0) * SHARES_PER_LOT,
            "volume": volumes[-1] if volumes else 0,
            "last_date": klines[-1]["date"] if klines else None, "klines": klines[-60:],
            "mined_factors": latest_mined_factor_values(klines), **completed_factor_snapshot(klines), "raw": {},
            "source": source, "source_label": source_label,
            "factors": {"momentum20": momentum20, "momentum60": momentum60, "trend": trend,
                        "volume_ratio": volume_ratio, "volatility20": vol20}}

def quote_session_requires_network(now=None):
    now = now or time.localtime()
    return now.tm_wday < 5 and 915 <= now.tm_hour * 100 + now.tm_min < 1510


def cached_klines_are_final(code, now=None):
    """Only reuse settled bars outside the live quote session."""
    rows = read_kline_cache(code)
    return not quote_session_requires_network(now) and bool(rows) and rows[-1].get("date") == expected_latest_trading_date(now)

def stock_from_kline_cache(item, now=None):
    """完全用本地日线构造条目；缓存不够新就返回 None 让调用方联网。"""
    code = item.get("code") or item["secid"].split(".")[1]
    rows, basis = read_kline_cache_full(code)
    if quote_session_requires_network(now) or not rows or rows[-1].get("date") != expected_latest_trading_date(now):
        return None
    label = "前复权日线" if (basis or ADJUSTMENT) == "qfq" else "不复权日线"
    return build_stock_from_klines(item, rows, "cache", "本地日线缓存（%s）" % label)

def fetch_stock_sina(item):
    """Real daily OHLCV fallback using Sina's public JSONP endpoint."""
    market, code = item["secid"].split(".")
    symbol = ("sh" if market == "1" else "sz") + code
    fetch_bars = 30 if read_kline_cache(code) else 240
    url = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20x/"
           f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={fetch_bars}")
    raw = eastmoney_json(url, jsonp=True)
    klines = []
    for row in raw:
        try:
            klines.append({"date": row["day"], "open": float(row["open"]), "close": float(row["close"]),
                           "high": float(row["high"]), "low": float(row["low"]),
                           "volume": float(row.get("volume", 0)) / SHARES_PER_LOT,
                           "amount": 0, "pct": 0, "turnover": 0})
        except (KeyError, TypeError, ValueError):
            continue
    klines = klines[-1000:]
    # 新浪返回的是不复权日线，必须打上不同的基准戳，绝不能与前复权序列直接合并
    klines = write_kline_cache(item["code"], klines, "none")
    return build_stock_from_klines(item, klines, "sina", "新浪公开日线 K 线")

def zscores(values):
    if not values: return []
    avg, sd = mean(values), statistics.pstdev(values)
    return [(v - avg) / sd if sd else 0.0 for v in values]

def build_dashboard_fresh(force_network=False):
    # Production backtests use one fixed technology universe. Popularity lists
    # are intentionally excluded because their daily membership changes would
    # alter the historical sample and invalidate comparable experiment runs.
    research_universe, tech_snapshot, selected_sectors = tech_universe_items(UNIVERSE)
    if not research_universe:
        raise RuntimeError("真实科技股池为空，请刷新东方财富行业分类")
    fixed_universe_manifest = export_fixed_tech_universe(research_universe, tech_snapshot, selected_sectors)
    stocks, errors = [], []
    PROVIDERS.update({"sina": fetch_stock_sina, "tencent": fetch_stock_tencent, "eastmoney": fetch_stock})
    reset_providers()
    def fetch_one(item):
        # 数据已定（缓存里已有最近一个已收盘交易日）就直接用本地日线：
        # 收盘后重复刷新不必把 896 只重新联网抓一遍、再全部重写回磁盘。
        cached = None if force_network else stock_from_kline_cache(item)
        if cached is not None:
            with PROVIDER_LOCK:
                PROVIDER_HITS["cache"] += 1
            return cached, None
        # 按优先级逐个尝试，靠前的源连续失败会被熔断跳过（见 note_provider），
        # 因此降级只付一次代价，不会每只股票都去等同一个死源超时。
        for name in PROVIDER_ORDER:
            if not provider_available(name):
                continue
            try:
                stock = PROVIDERS[name](item)
                note_provider(name, True)
                return stock, None
            except Exception as exc:
                note_provider(name, False, exc)
        rows = read_kline_cache(item['code'])
        if rows:
            stock = build_stock_from_klines(item, rows, "cache", "联网失败，保留历史缓存")
            stock["quote_stale"] = True
            return stock, f"{item['code']}: 联网失败，保留 {rows[-1].get('date')} 缓存"
        return None, f"{item['code']}: 三个数据源都失败"
    # Bounded concurrency keeps refreshes fast without opening an unbounded number of sockets.
    with ThreadPoolExecutor(max_workers=16, thread_name_prefix="quote-refresh") as pool:
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
            "latest_date": latest, "volume_unit": VOLUME_UNIT, "errors": errors, "universe_mode": "technology", "universe_signature": universe_signature_for_stocks(stocks),
            # 哪些数据源本轮被熔断跳过了——用户有权知道行情是从哪个源来的
            "provider_status": {"down": sorted(PROVIDER_DOWN), "errors": dict(PROVIDER_ERRORS),
                                "hits": dict(PROVIDER_HITS), "failure_limit": PROVIDER_FAILURE_LIMIT},
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
        result = json.loads(BACKTEST_RUN_FILE.read_text(encoding="utf-8"))
        result["stale_engine"] = result.get("engine_version") != BACKTEST_ENGINE_VERSION
        result["results"] = [item for item in result.get("results", [])
                             if item.get("holding_days") == 1]
        result["holding_choices"] = [1]
        result["holding_labels"] = HOLDING_LABELS
        result["combination_count"] = len([item for item in result["results"] if not item.get("research_only")])
        if result["stale_engine"]:
            result["warning"] = "旧成交与验证口径已失效，请重新运行回测；旧数据仍保留，不可作为交易依据。"
            result["out_of_sample"] = {"status": "待按次日退出重算", "folds": []}
            result["multiple_testing"] = {}
        registered = market_strategy.read(sys.modules[__name__])
        if registered:
            result["results"] = [item for item in result.get("results", [])
                                 if item.get("strategy_id") != market_strategy.ID] + [registered["row"]]
            result["registered_strategy_count"] = 1
        return result
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def ledger_quotes():
    # Dashboard spot prices are unadjusted; never value cash/share positions using qfq closes.
    return {stock["code"]: {"price": stock.get("price"), "date": stock.get("last_date"), "basis": "spot"}
            for stock in (read_dashboard_cache() or {}).get("stocks", []) if stock.get("price", 0) > 0}


def normalize_stock_names(payload, stocks):
    """Replace stale provider names in nested research output by stock code."""
    names_by_code = {
        str(stock.get("code", "")): stock.get("name")
        for stock in stocks
        if re.fullmatch(r"\d{6}", str(stock.get("code", ""))) and stock.get("name")
    }
    aliases = {}

    def collect(value):
        if isinstance(value, dict):
            code = str(value.get("code", ""))
            old_name = value.get("name")
            correct_name = names_by_code.get(code)
            if correct_name and isinstance(old_name, str) and old_name != correct_name:
                aliases[old_name] = correct_name
                value["name"] = correct_name
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    def replace_text(value):
        if isinstance(value, dict):
            for key, child in list(value.items()):
                value[key] = replace_text(child)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                value[index] = replace_text(child)
        elif isinstance(value, str):
            for old_name, correct_name in aliases.items():
                value = value.replace(old_name, correct_name)
        return value

    collect(payload)
    return replace_text(payload)


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
        agent["stale"] = agent.get("universe_signature") != signature or agent.get("engine_version") != FACTOR_ENGINE_VERSION
        agent["current_universe_signature"] = signature
        if agent["stale"]:
            agent["stale_reason"] = "因子版本或股池已变化，需要按下一交易日成交口径重新验证"
    payload["agent_factor_run"] = agent
    if agent:
        top = [item for item in agent.get("validation", {}).get("results", []) if item.get("mean_rank_ic") is not None][:20]
        payload["factor_lab_agent_catalog"] = [{"key":item["key"], "name":item["name"], "category":"Agent 挖掘", "formula":item.get("formula", ""), "description":item.get("hypothesis", "真实数据验证候选"), "status":item.get("gate", item.get("status")), "direction":item.get("direction", "待定"), "rank_ic":item.get("mean_rank_ic"), "icir":item.get("icir"), "duplicate_of":item.get("duplicate_of"), "duplicate_of_name":item.get("duplicate_of_name"), "duplicate_correlation":item.get("duplicate_correlation")} for item in top]
        payload["factor_clusters"] = agent.get("factor_clusters")
        payload["factor_model"] = {"name":"Agent Factor Ensemble v1", "weights":{}, "note":f"最近一次搜索 {agent.get('search_space_size', 0)} 个公式；当前展示真实验证候选"}
    if payload.get("stocks"):
        market_strategy.ensure(sys.modules[__name__], payload["stocks"])
    backtest_run = read_backtest_run()
    if backtest_run:
        backtest_run = dict(backtest_run)
        backtest_run["stale"] = backtest_run.get("universe_signature") != signature or backtest_run.get("stale_engine", False)
        backtest_run["current_universe_signature"] = signature
        if backtest_run["stale"]:
            backtest_run["stale_reason"] = "科技股池成员已变化；运行组合回测会先同步因子，再重算全部组合"
    payload["backtest_factor_run"] = backtest_run
    council = read_multi_agent_run()
    if council and (council.get("decision") or {}).get("holding_days", 1) != 1:
        council = None
    # 用户在 Agent 协作台选定的策略优先，否则退回排名第一的组合
    selection = read_selected_strategy()
    payload["selected_strategy"] = selection
    payload["strategy_unavailable"] = bool(selection and not find_strategy_result(
        backtest_run or {}, selection.get("factors"), selection.get("holding_days"), selection.get("strategy_id")))
    if council:
        council = dict(council)
        council["stale"] = council.get("universe_signature") != signature
        if council["stale"]:
            council["stale_reason"] = "科技股池已变化，需要重新运行十二 Agent 会签"
        # 换了策略却不重跑会签，候选名单就还是旧策略选出来的——必须明说，
        # 否则用户会看到"选择的策略"和"给出的候选"互相矛盾。
        decision = council.get("decision") or {}
        chosen = list((selection or {}).get("factors") or [])
        council_factors = list(decision.get("strategy_factors") or [])
        if chosen and council_factors and (chosen != council_factors
                                           or ((selection or {}).get("holding_days") is not None
                                               and decision.get("holding_days") != (selection or {}).get("holding_days"))):
            council["stale"] = True
            council["stale_reason"] = "你在 Agent 协作台改选了策略，当前候选仍是上一个策略的结论，请重新运行会签"
            council["strategy_changed"] = True
    payload["multi_agent_run"] = council
    backtest = payload.get("backtest_factor_run")
    if backtest and backtest.get("results") and payload.get("stocks"):
        combo = find_strategy_result(backtest, (selection or {}).get("factors"), (selection or {}).get("holding_days"), (selection or {}).get("strategy_id")) or backtest["results"][0]
        selected_factors = combo.get("factors") or []
        payload["selected_strategy_factors"] = selected_factors
        payload["selected_strategy_resolved"] = {"factors": selected_factors, "holding_days": combo.get("holding_days"),
                                                 "rank": combo.get("rank"), "names": combo.get("names"),
                                                 "strategy_id": combo.get("strategy_id"), "name": combo.get("name"),
                                                 "from_user": bool(selection and list((selection or {}).get("factors") or []) == list(selected_factors))}
        validation_map = {item["key"]: item for item in (agent or {}).get("validation", {}).get("results", [])}
        factor_keys = combo.get("factors", [])
        directions = dict(zip(factor_keys, combo.get("directions") or []))
        factors = [(key, 1 if directions.get(key, validation_map.get(key, {}).get("direction")) == "正向" else -1) for key in factor_keys]
        # 排名一律用"最新一根 K 线"，盘中就是当天那根实时 bar（价格实时、
        # 成交量是当日累计）。这样盘中刷新看到的就是当下的排名，不必等 15:10。
        # 同时必须套用与会签完全相同的流动性过滤，否则两处会选出不同的票。
        rows_by_code, latest_data_date = ranking_cross_section(payload["stocks"])
        cutoff = liquidity_cutoff(stock_liquidity(stock) for stock in rows_by_code.values())
        if cutoff > 0:
            rows_by_code = {code: stock for code, stock in rows_by_code.items() if stock_liquidity(stock) >= cutoff}
        rank_columns = []
        for key, direction in factors:
            values = [(code, (value * direction if value is not None else None)) for code, stock in rows_by_code.items() for value in [(stock.get("mined_factors") or stock.get("completed_mined_factors") or {}).get(key)]]
            values = [(code, value) for code, value in values if value is not None and math.isfinite(value)]
            ranks = _rank([value for _, value in values]); rank_columns.append({"key":key, "direction":direction, "scores":{code: (rank - 1) / max(1, len(ranks) - 1) * 100 for (code, _), rank in zip(values, ranks)}})
        scores = {}
        for code in rows_by_code:
            available = [column["scores"][code] for column in rank_columns if code in column["scores"]]
            if len(available) == len(rank_columns) and available: scores[code] = round(mean(available), 2)
        for stock in payload["stocks"]:
            score = scores.get(stock["code"])
            stock["agent_combo_score"] = score
            stock["agent_combo_factors"] = factor_keys
            stock["agent_combo_breakdown"] = ([{"key":column["key"], "name":validation_map.get(column["key"], {}).get("name", column["key"]), "direction":"正向" if column["direction"] == 1 else "反向", "score":round(column["scores"][stock["code"]], 1)} for column in rank_columns]
                                                if score is not None else [])
        payload["agent_selection"] = {"name":" / ".join(combo.get("names", [])), "factors":factor_keys, "direction_adjusted":True, "scored_count":len(scores), "data_date":latest_data_date, "rule":"所选因子按历史验证方向调整后，对全部数据完整股票做截面排名并等权合成；科技总榜和分类榜统一使用该策略评分。"}
        if combo.get("strategy_id") == market_strategy.ID:
            registered = market_strategy.read(sys.modules[__name__])
            current = registered["scores"]
            top_codes = {item["code"] for item in registered["latest_candidates"]}
            for stock in payload["stocks"]:
                item = current.get(stock["code"], {})
                stock["agent_combo_score"] = item.get("score")
                stock["agent_combo_breakdown"] = item.get("breakdown", [])
                stock["strategy_trade_state"] = ("不开新仓" if not registered["gate_open"] else
                                                  "可建仓" if stock["code"] in top_codes else "观察")
            payload["agent_selection"] = {**market_strategy.specification(), "scored_count": len(current),
                                          "data_date": registered["data_date"], "gate_open": registered["gate_open"],
                                          "market_state": registered["states"][registered["data_date"]],
                                          "top3": registered["latest_candidates"] if registered["gate_open"] else [],
                                          "rule": "上涨比例 >45% 且等权指数 >MA20 时，仅买前三只；下一交易日卖出；收盘价研究近似，非14:50成交"}
    # 只做汇总，不做回填：回填需要读取全部日线，放在行情刷新与会签完成时执行。
    payload["recommendation_review"] = recommendation_review_summary(load_recommendation_log(), payload)
    return normalize_stock_names(payload, payload.get("stocks", []))

def recommendation_history_loader(code):
    """归因只认真实缓存日线；没有单位戳的旧文件返回空，绝不用于回填。"""
    return completed_daily_rows(read_kline_cache(code))

def record_ranking_snapshot(dashboard, log):
    """留痕：驾驶舱当日榜单前 N 名（与页面展示口径一致）。"""
    stocks = dashboard.get("stocks") or []
    if not stocks:
        return False
    use_combo = bool(dashboard.get("agent_selection"))
    if (dashboard.get("agent_selection") or {}).get("strategy_id") == market_strategy.ID:
        registered = market_strategy.read(sys.modules[__name__])
        if not registered or not registered["gate_open"]:
            return False
        snapshot = make_recommendation_snapshot("ranking", registered["data_date"],
            registered["latest_candidates"], strategy=market_strategy.specification(),
            source=dashboard.get("source_label"), note="行情择时策略前三只；同收盘价研究近似")
        snapshot["strategy_id"] = market_strategy.ID
        return upsert_recommendation(log, snapshot)
    data_date, entries = recommendation_ranking_entries(stocks, use_combo)
    if not data_date or not entries:
        return False
    strategy = dashboard.get("agent_selection") or {}
    return upsert_recommendation(log, make_recommendation_snapshot(
        "ranking", data_date, entries,
        strategy={"name": strategy.get("name"), "factors": strategy.get("factors"),
                  "holding_days": (read_selected_strategy() or {}).get("holding_days") or 1},
        source=dashboard.get("source_label"),
        note=f"驾驶舱榜单前 {len(entries)} 名（{('策略评分' if use_combo else '技术综合分')}降序）",
    ))

def canonical_stock_names():
    """以固定科技股池为准的 代码→名称 索引。

    历史运行归档里出现过被写坏的名称（UTF-8 字节按 GBK 解码那一类乱码），
    但代码始终是干净的；留痕只做"按代码回填名称"的修复，不动推荐名单本身。
    """
    try:
        payload = json.loads(FIXED_TECH_UNIVERSE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(item.get("code", "")).zfill(6): item.get("name") for item in payload.get("stocks") or []}

def _council_snapshot(run):
    """把一次会签运行里"真实输出过的候选"整理成留痕记录；没输出标的就返回 None。"""
    decision = run.get("decision") or {}
    holdings = decision.get("holdings") or []
    data_date = run.get("data_date")
    if not holdings or not data_date:
        return None
    names = canonical_stock_names()
    entries = [{"code": item.get("code"),
                "name": names.get(str(item.get("code", "")).zfill(6)) or item.get("name"),
                "rank": index + 1}
               for index, item in enumerate(holdings)]
    return make_recommendation_snapshot(
        "council", data_date, entries,
        strategy={"name": " / ".join(decision.get("strategy") or []),
                  "factors": decision.get("strategy_factors") or [], "holding_days": decision.get("holding_days") or 1},
        source=f"十二 Agent 会签 · {run.get('run_id')}",
        note=f"决策状态 {decision.get('status')}；Challenger {decision.get('challenger_status')}",
    )

def record_council_snapshot(log):
    """留痕：十二 Agent 会签正式输出的研究候选。

    被风控阻断（未输出标的）时不写快照——没有标的就没有可归因的对象，
    阻断次数由 data/agent_runs 下的运行记录负责审计。
    """
    snapshot = _council_snapshot(read_multi_agent_run() or {})
    return upsert_recommendation(log, snapshot) if snapshot else False

def seed_council_history(log):
    """把历史会签归档中真实输出过的候选补进留痕。

    只搬运当时落盘的结果，不重新计算、不改写名单——避免用今天的策略去"事后选股"。
    """
    changed = False
    for path in sorted(MULTI_AGENT_RUNS_DIR.glob("*/summary.json")):
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        snapshot = _council_snapshot(run)
        if snapshot and upsert_recommendation(log, snapshot):
            changed = True
    return changed

def recommendation_strategy_id(factors, holding_days=1):
    return hashlib.sha256(json.dumps([sorted(factors or []), holding_days or 1]).encode()).hexdigest()[:16]


def recommendation_strategies(dashboard, log=None):
    results = (read_backtest_run() or {}).get("results") or []
    strategies = [{"id": item.get("strategy_id") or recommendation_strategy_id(item.get("factors"), item.get("holding_days")),
                   "factors": item.get("factors") or [], "holding_days": item.get("holding_days") or 1,
                   "name": item.get("name") or " / ".join(item.get("names") or item.get("factors") or []), "rank": item.get("rank"),
                   **({"strategy_id": market_strategy.ID, "entry_offset": 0, "directions": item.get("directions"),
                       "market_gate": item.get("market_gate")} if item.get("strategy_id") == market_strategy.ID else {})}
                  for item in results if item.get("factors") and item.get("holding_days") == 1]
    current_ids = {item["id"] for item in strategies}
    for snapshot in (log or {}).get("snapshots", {}).values():
        spec = snapshot.get("strategy") or {}
        identity = snapshot.get("strategy_id")
        if snapshot.get("kind") != "replay" or not identity or identity in current_ids or not spec.get("factors") or spec.get("holding_days", 1) != 1:
            continue
        strategies.append({"id": identity, "factors": spec["factors"], "holding_days": spec.get("holding_days") or 1,
                           "name": "旧回放 · " + (spec.get("name") or " / ".join(spec["factors"])), "rank": None, "legacy": True})
        current_ids.add(identity)
    selected = read_selected_strategy() or {}
    keys = selected.get("factors") or (dashboard.get("agent_selection") or {}).get("factors") or []
    identity = selected.get("strategy_id") or recommendation_strategy_id(keys, selected.get("holding_days"))
    chosen = next((item for item in strategies if item["id"] == identity), None)
    return strategies, chosen or next((item for item in strategies if item["factors"] == keys), None) or (strategies[0] if strategies else None)


def recommendation_review_revision():
    files = [DATA_DIR / "recommendations" / "log.json", DASHBOARD_CACHE_FILE, BACKTEST_RUN_FILE,
             FACTOR_AGENT_RUN_FILE, SELECTED_STRATEGY_FILE, KLINE_MANIFEST_FILE, CACHE_DIR / market_strategy.CACHE_NAME]
    now = time.localtime()
    settlement = time.strftime("%Y-%m-%d", now) + str(now.tm_hour * 100 + now.tm_min >= 1510)
    return review_cache.file_revision(files) + settlement + BACKTEST_ENGINE_VERSION


def cached_recommendation_review(strategy_id=None, force=False):
    directory = CACHE_DIR / "recommendation_views"
    revision = recommendation_review_revision()
    if not force:
        cached = review_cache.read(directory, strategy_id, revision, KLINE_CACHE_DIR)
        if cached is not None:
            return cached
    dashboard = read_dashboard_cache() or {}
    if force:
        result = update_recommendation_review(dashboard, strategy_id=strategy_id)
        revision = recommendation_review_revision()
    else:
        result = recommendation_review_summary(load_recommendation_log(), dashboard, strategy_id)
        selected = result.get("selected_strategy") or {}
        if strategy_id and not result.get("replay_rounds") and not selected.get("legacy"):
            # Only a never-computed strategy needs historical generation on first access.
            result = update_recommendation_review(dashboard, strategy_id=strategy_id)
            revision = recommendation_review_revision()
    if revision == recommendation_review_revision():
        review_cache.write(directory, strategy_id, revision, KLINE_CACHE_DIR, result)
    return result


def recommendation_review_summary(log, dashboard=None, strategy_id=None):
    strategies, default = recommendation_strategies(dashboard or read_dashboard_cache() or {}, log)
    selected = next((item for item in strategies if item["id"] == strategy_id), None) if strategy_id else default
    if strategy_id and not selected:
        raise ValueError("复盘策略不存在，请重新选择")
    result = summarize_recommendations(log, strategy_id=selected["id"] if selected else None)
    result.update({"strategies": strategies, "selected_strategy": selected})
    histories = {}
    def loader(code):
        if code not in histories:
            histories[code] = recommendation_history_loader(code) or []
        return histories[code]
    result["portfolio_equity"] = {kind: recommendation_equity(
        result["replay_rounds"] if kind == "replay" else [row for row in result["rounds"] if row["kind"] == kind],
        (selected or {}).get("holding_days") or 1, loader)
        for kind in ("replay", "council", "ranking")}
    result["capital_equity"] = {kind: recommendation_capital_equity(
        result["replay_rounds"] if kind == "replay" else [row for row in result["rounds"] if row["kind"] == kind],
        (selected or {}).get("holding_days") or 1, loader, read_kline_basis,
        entry_offset=(selected or {}).get("entry_offset", 1))
        for kind in ("replay", "council", "ranking")}
    return result


def recommendation_capital_equity(rounds, holding_days, history_loader, basis_loader, capital=20000, entry_offset=1):
    """Monetary equivalent of delayed, fixed-unit Top-3 replay, not a share ledger."""
    from datetime import date, timedelta
    histories = {item["code"]: history_loader(item["code"]) or []
                 for row in rounds for item in (row.get("detail") or [])[:3]}
    first_signal = min((row.get("data_date") or REVIEW_START_DATE for row in rounds), default=REVIEW_START_DATE)
    calendar = sorted({bar["date"] for rows in histories.values() for bar in rows
                       if bar.get("date", "") >= max(REVIEW_START_DATE, first_signal)})
    base = {"initial_capital": capital, "daily": [], "periods": {}, "as_of": None,
            "basis": "标准化份额估算，非整手实盘收益", "holding_days": holding_days,
            "cost_bps": COST_BPS, "excluded_basis": [], "missing_entries": []}
    base["entry_offset"] = entry_offset
    if not rounds or not calendar:
        return base
    excluded = {code for code in histories if basis_loader(code) != ADJUSTMENT}
    base["excluded_basis"] = sorted(excluded)
    indexed = {code: {bar["date"]: bar.get("close") for bar in rows} if code not in excluded else {}
               for code, rows in histories.items()}
    observations = {day: [] for day in calendar}
    positions = {day: i for i, day in enumerate(calendar)}
    for row in rounds:
        day = row.get("data_date")
        items = (row.get("detail") or [])[:3]
        if day not in positions or len(items) != 3:
            continue
        i = positions[day]
        for rank, item in enumerate(items):
            closes = tuple(indexed[item["code"]].get(calendar[j])
                           for j in range(i, min(len(calendar), i + holding_days + 2)))
            observations[day].append((item["code"], {"review_rank": 3 - rank, "__liquidity": 1}, closes))
            if i + entry_offset < len(calendar) and (len(closes) <= entry_offset or not closes[entry_offset]):
                base["missing_entries"].append({"signal_date": day, "code": item["code"]})
    curve = []
    quotes = {day: {code: prices[day] for code, prices in indexed.items()
                    if prices.get(day) is not None} for day in calendar}
    execution_daily_series({"observations": observations, "quotes": quotes}, [("review_rank", 1)],
                           cost_rate=COST_BPS / 10000, min_cross=3, entry_offset=entry_offset,
                           holding_days=holding_days, account_curve=curve)
    # Period P&L is an actual change in this running model, not a fresh 20k reset.
    starts = {"1d": max(0, len(curve) - 1), "3d": max(0, len(curve) - 3),
              "7d": max(0, len(curve) - 7), "all": 0}
    cutoff = (date.fromisoformat(calendar[-1]) - timedelta(days=30)).isoformat()
    starts["1m"] = next((i for i, point in enumerate(curve) if point["date"] > cutoff), len(curve))
    for key, start in starts.items():
        points = curve[start:]
        before = curve[start - 1] if start else {"equity": 1, "complete": True}
        complete = bool(points) and before["complete"] and all(point["complete"] for point in points)
        ending = points[-1]["equity"] if points else before["equity"]
        base["periods"][key] = {
            "start_date": points[0]["date"] if points else None, "end_date": calendar[-1],
            "trading_days": len(points), "complete": complete,
            "starting_equity": round(capital * before["equity"], 2) if complete else None,
            "ending_equity": round(capital * ending, 2) if complete else None,
            "pnl": round(capital * (ending - before["equity"]), 2) if complete else None,
            "net_return": ending / before["equity"] - 1 if complete else None,
            "cash": round(capital * points[-1]["cash"], 2) if complete else None,
        }
    base.update({"daily": [{**point, "equity": round(capital * point["equity"], 2),
                            "cash": round(capital * point["cash"], 2)} for point in curve],
                 "as_of": calendar[-1]})
    return base


def recommendation_equity(rounds, holding_days, history_loader):
    """Value recorded Top-3 baskets using the existing phased-capital engine."""
    from datetime import date, timedelta
    histories = {item["code"]: history_loader(item["code"]) or []
                 for row in rounds for item in (row.get("detail") or [])[:3]}
    first_signal = min((row.get("data_date") or REVIEW_START_DATE for row in rounds), default=REVIEW_START_DATE)
    calendar = sorted({bar["date"] for rows in histories.values() for bar in rows
                       if bar.get("date", "") >= max(REVIEW_START_DATE, first_signal)})
    if not calendar or not rounds:
        return {"daily": [], "periods": {}, "as_of": None}
    indexed = {code: {bar["date"]: bar.get("close") for bar in rows} for code, rows in histories.items()}
    observations = {day: [] for day in calendar}
    entry_days = set()
    missing_entries = set()
    for row in rounds:
        day = row.get("data_date")
        if day not in observations:
            continue
        if len(row.get("detail") or []) != 3:
            missing_entries.add(day)
            continue
        position = calendar.index(day)
        for i, item in enumerate(row["detail"]):
            closes = tuple(indexed[item["code"]].get(calendar[j]) for j in range(position, min(len(calendar), position + holding_days + 1)))
            observations[day].append((item["code"], {"review_rank": 3 - i, "__liquidity": 1}, closes))
        if all(window_return(item[2], 0, 0) is not None for item in observations[day]):
            entry_days.add(day)
        else:
            missing_entries.add(day)
    daily, _ = combo_daily_series({"observations": observations}, [("review_rank", 1)],
                                 cost_rate=0, bucket_size=3, min_cross=3, holding_days=holding_days,
                                 entry_offset=0, allow_partial_horizon=True)
    incomplete_days = {calendar[j] for day in missing_entries for j in
                       range(calendar.index(day), min(len(calendar), calendar.index(day) + holding_days + 1))}
    for i, day in enumerate(calendar):
        if day not in entry_days:
            continue
        for step in range(1, min(holding_days + 1, len(calendar) - i)):
            if any(step >= len(item[2]) or not item[2][step - 1] or not item[2][step]
                   for item in observations[day]):
                incomplete_days.add(calendar[i + step])
    # Match the review's fixed per-round cost, rather than estimating turnover discounts.
    for i, day in enumerate(calendar):
        if day in entry_days:
            daily[i] -= COST_BPS / 10000 / holding_days
    equity, curve = 1.0, []
    for day, value in zip(calendar, daily):
        equity *= 1 + value
        curve.append({"date": day, "return": round(value, 8), "equity": round(equity, 8),
                      "incomplete": day in incomplete_days})
    windows = {"1d": curve[-1:], "3d": curve[-3:], "7d": curve[-7:], "all": curve}
    cutoff = (date.fromisoformat(calendar[-1]) - timedelta(days=30)).isoformat()
    windows["1m"] = [point for point in curve if point["date"] > cutoff]
    periods = {}
    for key, points in windows.items():
        nav, peak, drawdown = 1.0, 1.0, 0.0
        for point in points:
            nav *= 1 + point["return"]
            peak = max(peak, nav)
            drawdown = max(drawdown, 1 - nav / peak)
        complete = not any(point["incomplete"] for point in points)
        periods[key] = {"net_return": round(nav - 1, 8) if complete else None,
                        "max_drawdown": round(drawdown, 8) if complete else None, "complete": complete,
                        "start_date": points[0]["date"] if points else None,
                        "end_date": points[-1]["date"] if points else None, "trading_days": len(points)}
    return {"daily": curve, "periods": periods, "as_of": calendar[-1],
            "holding_days": holding_days, "basis": "三只等权；多日持有期分批资金逐日盯市；含未退出持仓的已收盘浮动收益"}


def seed_recommendation_replay(dashboard, log, strategy=None, context=None):
    """Replay the current strategy with strictly trailing per-date factor inputs."""
    selection = strategy or dashboard.get("agent_selection") or {}
    if selection.get("strategy_id") == market_strategy.ID:
        registered = market_strategy.read(sys.modules[__name__])
        changed = False
        for day, entries in (registered or {}).get("baskets", {}).items():
            key = f"{day}|replay|{market_strategy.ID}"
            if day < REVIEW_START_DATE or not entries or log.get("snapshots", {}).get(key):
                continue
            snapshot = make_recommendation_snapshot("replay", day, entries,
                strategy=market_strategy.specification(), source="historical_replay",
                note="固定三因子与行情门槛历史回放；同收盘价近似；非当时真实推荐")
            snapshot.update(key=key, strategy_id=market_strategy.ID)
            changed = upsert_recommendation(log, snapshot) or changed
        return changed
    if selection.get("legacy"):
        return False
    keys = selection.get("factors") or (read_selected_strategy() or {}).get("factors") or []
    if not keys:
        return False
    validation = {item["key"]: item for item in (read_factor_agent_run() or {}).get("validation", {}).get("results", [])}
    factors = [(key, 1 if validation.get(key, {}).get("direction") == "正向" else -1) for key in keys]
    stocks = dashboard.get("stocks") or []
    names = {item["code"]: item.get("name") for item in stocks}
    signature = hashlib.sha256(json.dumps([factors, sorted(names)], ensure_ascii=True).encode()).hexdigest()
    strategy_id = recommendation_strategy_id(keys, selection.get("holding_days"))
    context = context if context is not None else {}
    if "usable" not in context:
        context["usable"] = prepare_backtest_universe(stocks)
    usable = context["usable"]
    dates = sorted({row["date"] for _, rows, _ in usable for row in rows
                    if row.get("date", "") >= REVIEW_START_DATE})
    changed = False
    for date in dates:
        key = f"{date}|replay|{strategy_id}"
        # Preserve published lists even when later validation changes direction.
        if log.get("snapshots", {}).get(key):
            continue
        sections = context.setdefault("sections", {})
        if date not in sections:
            section = [(code, rows, indexes[date]) for code, rows, indexes in usable
                       if date in indexes and indexes[date] >= 120]
            liquidity = {code: liquidity_value(rows, index) for code, rows, index in section}
            cutoff = liquidity_cutoff(liquidity.values())
            sections[date] = [(code, rows, index) for code, rows, index in section if liquidity[code] >= cutoff]
        section = sections[date]
        if len(section) < 20:
            continue
        columns = []
        for factor, direction in factors:
            cached_columns = context.setdefault("columns", {})
            column_key = (date, factor, direction)
            if column_key not in cached_columns:
                column = [(code, value * direction) for code, rows, index in section
                          for value in [_factor_value(rows, index, factor)]
                          if value is not None and math.isfinite(value)]
                ranks = _rank([value for _, value in column])
                cached_columns[column_key] = {code: (rank - 1) / max(1, len(ranks) - 1) * 100
                                               for (code, _), rank in zip(column, ranks)}
            columns.append(cached_columns[column_key])
        scored = [(code, mean([column[code] for column in columns if code in column]))
                  for code, _, _ in section if any(code in column for column in columns)]
        scored.sort(key=lambda item: (-item[1], item[0]))
        entries = [{"code": code, "name": names.get(code), "rank": i + 1, "score": round(score, 2)}
                   for i, (code, score) in enumerate(scored[:3])]
        if not entries:
            continue
        snapshot = make_recommendation_snapshot("replay", date, entries,
            strategy={"name": selection.get("name") or " / ".join(keys), "factors": keys,
                      "holding_days": selection.get("holding_days") or 1},
            source="historical_replay", note="当前策略历史回放前 3 名；非当时真实推荐；不纳入真实留痕统计")
        snapshot["replay_signature"] = signature
        snapshot["key"] = key
        snapshot["strategy_id"] = strategy_id
        changed = upsert_recommendation(log, snapshot) or changed
    return changed


def update_recommendation_review(dashboard, force_refresh=True, strategy_id=None):
    with RECOMMENDATION_LOCK:
        return _update_recommendation_review(dashboard, force_refresh, strategy_id)


def _update_recommendation_review(dashboard, force_refresh=True, strategy_id=None):
    """写入当日快照并把已到期的推荐回填成真实收益，返回汇总。"""
    log = load_recommendation_log()
    changed = record_ranking_snapshot(dashboard or {}, log)
    changed = record_council_snapshot(log) or changed
    changed = seed_council_history(log) or changed
    strategies, default = recommendation_strategies(dashboard or {}, log)
    selected = next((item for item in strategies if item["id"] == strategy_id), None) if strategy_id else default
    if strategy_id and not selected:
        raise ValueError("复盘策略不存在，请重新选择")
    wanted = [selected] if strategy_id else strategies
    replay_context = {}
    for strategy in wanted:
        changed = seed_recommendation_replay(dashboard or {}, log, strategy, replay_context) or changed
    for snapshot in log["snapshots"].values():
        if snapshot.get("kind") == "replay":
            continue
        spec = snapshot.get("strategy") or {}
        matched = next((item for item in strategies if (spec.get("factors") and item["factors"] == spec["factors"]
                        or item["name"] == spec.get("name")) and item["holding_days"] == (spec.get("holding_days") or 1)), None)
        if matched and snapshot.get("strategy_id") != matched["id"]:
            snapshot["strategy_id"] = matched["id"]
            changed = True
    if force_refresh:
        universe = [stock.get("code") for stock in (dashboard or {}).get("stocks") or [] if stock.get("code")]
        changed = refresh_recommendations(log, recommendation_history_loader, universe_codes=universe) or changed
    if changed:
        save_recommendation_log(log)
    return recommendation_review_summary(log, dashboard, selected["id"] if selected else None)


def read_selected_strategy():
    """用户在 Agent 协作台选定的策略。单独存文件，避免被行情刷新重建缓存冲掉。"""
    try:
        payload = json.loads(SELECTED_STRATEGY_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return market_strategy.specification()
    keys = [str(key) for key in payload.get("factors") or []][:5]
    if not keys:
        return None
    if payload.get("holding_days", 1) != 1 or payload.get("strategy_id") == "market-gated-top3-v1":
        return market_strategy.specification()
    return {"factors": keys, "holding_days": payload.get("holding_days"), "selected_at": payload.get("selected_at"),
            **({**market_strategy.specification()} if payload.get("strategy_id") == market_strategy.ID else {})}

def write_selected_strategy(factors, holding_days, strategy_id=None):
    if holding_days != 1:
        raise ValueError("仅支持当日买入、下一交易日卖出，不支持多日持有")
    payload = {"factors": [str(key) for key in factors][:5], "holding_days": holding_days,
               "selected_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    if strategy_id == market_strategy.ID:
        payload.update(market_strategy.specification())
    SELECTED_STRATEGY_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload

def find_strategy_result(backtest_run, factors, holding_days=None, strategy_id=None):
    """在回测结果里定位某个组合；holding_days 为 None 时接受任意持有期。"""
    keys = [str(key) for key in (factors or [])]
    if not keys:
        return None
    for item in (backtest_run or {}).get("results") or []:
        if strategy_id and item.get("strategy_id") != strategy_id:
            continue
        if list(item.get("factors") or []) != keys:
            continue
        if holding_days is None or item.get("holding_days") == holding_days:
            return item
    return None

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
            "factor_candidates": candidates, "previous_top": previous_top,
            "selected_strategy": read_selected_strategy()}

def run_multi_agent_background(dashboard, mode="full"):
    global MULTI_AGENT_THREAD, MULTI_AGENT_PREP_STATUS
    try:
        previous_date = latest_completed_data_date(dashboard.get("stocks", []))
        MULTI_AGENT_PREP_STATUS = {"stage": "同步K线", "detail": "正在增量拉取全部科技股的最新日线", "error": None}
        try:
            with CACHE_LOCK:
                dashboard = build_dashboard_fresh()
                write_dashboard_cache(dashboard)
        except Exception as exc:
            MULTI_AGENT_PREP_STATUS = {"stage": "K线同步失败", "detail": str(exc), "error": str(exc)}
            return
        current_date = latest_completed_data_date(dashboard.get("stocks", []))
        date_detail = f"已从 {previous_date or '无历史'} 更新至 {current_date or '无已收盘数据'}"
        if previous_date == current_date:
            date_detail = f"已检查最新数据，已收盘交易日仍为 {current_date or '未知'}"
        MULTI_AGENT_PREP_STATUS = {"stage": "同步完成", "detail": date_detail, "error": None}
        stocks = dashboard.get("stocks", [])
        context = build_multi_agent_context(dashboard)
        force = mode == "full"
        context["execution_mode"] = "全量重算（含研究研发组）" if force else "快速会签（复用因子）"
        context["reuse_research"] = not force
        if force:
            factor_runner = lambda report, generated=None: run_factor_agent_loop(stocks, report, force=True, generated_specs=generated)
            backtest_runner = lambda report: run_backtest_experiment(stocks, report, force=True)
        else:
            def factor_runner(report, generated=None):
                cached = read_factor_agent_run()
                if not cached:
                    raise RuntimeError("没有可复用的因子研发结果，请先运行一次全量重算")
                report("直接复用", f"读取最近一次 {cached.get('search_space_size', 0)} 个公式的验证结果，不重新计算")
                return {**cached, "cache_hit": True, "strict_reuse": True}
            def backtest_runner(report):
                cached = read_backtest_run()
                if not cached:
                    raise RuntimeError("没有可复用的回测结果，请先运行一次全量重算")
                report("直接复用", f"读取最近一次 {cached.get('combination_count', 0)} 组回测，不重建矩阵")
                return {**cached, "cache_hit": True, "strict_reuse": True}
        MULTI_AGENT_PREP_STATUS = None
        run_research_council(context, factor_runner, backtest_runner)
        try:
            # 会签结束后立刻留痕并回填，保证"推荐→兑现"的闭环不断档
            update_recommendation_review(read_dashboard_cache() or dashboard)
        except Exception:
            pass
    finally:
        with MULTI_AGENT_LOCK:
            MULTI_AGENT_THREAD = None

def ranking_cross_section(stocks, min_bars=122):
    """挑出参与截面排名的股票：只取"最新一根 K 线"所在交易日的样本。

    盘中时最新一根就是当天那根实时 bar（价格实时、成交量为当日累计），
    所以盘中刷新看到的就是当下的排名，不必等到 15:10 收盘结算。
    """
    latest = max((stock.get("last_date") or stock.get("completed_factor_date") or "" for stock in stocks), default="")
    rows = {stock["code"]: stock for stock in stocks
            if (stock.get("last_date") or stock.get("completed_factor_date")) == latest
            and stock.get("completed_history_bars", min_bars) >= min_bars}
    return rows, latest

def select_by_factor_keys(stocks, factor_keys):
    agent = read_factor_agent_run()
    validation_map = {item["key"]: item for item in (agent or {}).get("validation", {}).get("results", [])}
    factors = [(key, 1 if validation_map.get(key, {}).get("direction") == "正向" else -1) for key in factor_keys if key in validation_map]
    rows_by_code, latest_data_date = ranking_cross_section(stocks)
    cutoff = liquidity_cutoff(stock_liquidity(stock) for stock in rows_by_code.values())
    if cutoff > 0:
        rows_by_code = {code: stock for code, stock in rows_by_code.items() if stock_liquidity(stock) >= cutoff}
    columns = []
    for key, direction in factors:
        values = []
        for code, stock in rows_by_code.items():
            value = (stock.get("mined_factors") or stock.get("completed_mined_factors") or {}).get(key)
            if value is not None and math.isfinite(value):
                values.append((code, value * direction))
        ranks = _rank([value for _, value in values]); columns.append({"key":key, "direction":direction, "scores":{code:(rank-1)/max(1,len(ranks)-1)*100 for (code,_),rank in zip(values,ranks)}})
    scores = {}
    for code in rows_by_code:
        available = [column["scores"][code] for column in columns if code in column["scores"]]
        if len(available) == len(columns) and available:
            scores[code] = round(mean(available), 2)
    names = [validation_map.get(key, {}).get("name", key) for key in factor_keys]
    breakdowns = {code:[{"key":column["key"], "name":validation_map.get(column["key"], {}).get("name", column["key"]), "direction":"正向" if column["direction"] == 1 else "反向", "score":round(column["scores"][code], 1)} for column in columns] for code in scores}
    return {"name":" / ".join(names), "factors":factor_keys, "scored_count":len(scores), "data_date":latest_data_date, "rule":"所选因子按历史验证方向调整后，对全部数据完整股票做截面排名并等权合成；科技总榜和分类榜统一使用该策略评分。", "scores":scores, "breakdowns":breakdowns}

def write_dashboard_cache(payload):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = DASHBOARD_CACHE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, DASHBOARD_CACHE_FILE)

def rebase_rows(base_rows, rows, tolerance=ADJUSTMENT_TOLERANCE):
    """把 rows 的价格换算到 base_rows 的复权基准上。

    腾讯/东财给的日线是前复权，新浪给的是不复权，两者相差一个乘数因子。
    只要重叠区间内没有发生除权，这个因子就是常数——用重叠日的收盘价比值中位数
    可以直接换算。一旦比值不恒定，说明重叠区间内发生了除权，无法可靠换算，
    此时返回 None，宁可不合并也不用错误的基准去算收益。
    """
    by_date = {row.get("date"): row for row in base_rows if row.get("date")}
    ratios = []
    for row in rows:
        base = by_date.get(row.get("date"))
        if not base:
            continue
        try:
            old, new = float(base.get("close") or 0), float(row.get("close") or 0)
        except (TypeError, ValueError):
            continue
        if old > 0 and new > 0:
            ratios.append(old / new)
    if len(ratios) < 5:
        return None
    ordered = sorted(ratios)
    if ordered[-1] / ordered[0] - 1 > tolerance:
        return None
    scale = ordered[len(ordered) // 2]
    if abs(scale - 1) < 1e-9:
        return list(rows)
    rebased = []
    for row in rows:
        item = dict(row)
        for field in ("open", "high", "low", "close"):
            try:
                item[field] = round(float(row.get(field) or 0) * scale, 4)
            except (TypeError, ValueError):
                pass
        rebased.append(item)
    return rebased

def write_kline_cache(code, klines, adjustment=ADJUSTMENT):
    """写入日线缓存，并保证同一文件内只有一种复权基准。"""
    KLINE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = KLINE_CACHE_DIR / f"{code}.json"
    existing, basis = [], None
    try:
        stored = json.loads(target.read_text(encoding="utf-8"))
        # Legacy files carry no unit stamp and may mix "股"/"手" from the Tencent,
        # Eastmoney and Sina fallbacks. Reusing them would preserve a 100x step in
        # the volume series, so they are dropped and refetched instead of merged.
        if stored.get("volume_unit") == VOLUME_UNIT:
            existing = stored.get("klines") or []
            basis = stored.get("adjustment")
    except (FileNotFoundError, OSError, json.JSONDecodeError, AttributeError):
        pass

    if existing and basis != adjustment:
        if adjustment == ADJUSTMENT:
            # 新数据是标准基准：把旧数据换算过来，升级整条序列
            converted = rebase_rows(klines, existing)
            if converted is None:
                return existing
            existing, basis = converted, ADJUSTMENT
        else:
            # 新数据不是标准基准：换算到已有基准，否则拒绝合并（保持原序列不动）
            converted = rebase_rows(existing, klines)
            if converted is None:
                return existing
            klines = converted

    # Public fallbacks return different history lengths. Merge by trading date
    # so a short 240-bar response can update overlaps without erasing older bars.
    merged = {row.get("date"): row for row in existing if row.get("date")}
    merged.update({row.get("date"): row for row in klines if row.get("date")})
    klines = [merged[date] for date in sorted(merged)]
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({"code": code, "volume_unit": VOLUME_UNIT,
                                     "adjustment": basis or adjustment,
                                     "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                     "klines": klines}, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, target)
    return klines

def read_kline_cache_full(code):
    """一次读盘同时拿回日线与复权基准，避免同一份 60KB 文件被反复解析。"""
    try:
        payload = json.loads((KLINE_CACHE_DIR / f"{code}.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return [], None
    if payload.get("volume_unit") != VOLUME_UNIT:
        return [], None
    return payload.get("klines") or [], payload.get("adjustment")

def read_kline_cache(code):
    """Read a cache file, but ignore rows whose volume unit is not the canonical one."""
    return read_kline_cache_full(code)[0]

def read_kline_basis(code):
    """返回该股票日线的复权基准（qfq / none），用于体检数据一致性。"""
    try:
        payload = json.loads((KLINE_CACHE_DIR / f"{code}.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return payload.get("adjustment")


def build_wyckoff(force=False):
    """Build the independent Wyckoff screen without touching factor rankings."""
    dashboard = read_dashboard_cache() or {}
    stocks = dashboard.get("stocks") or []
    if not stocks:
        raise RuntimeError("真实行情缓存尚未完成")
    signature = dashboard.get("universe_signature") or universe_signature_for_stocks(stocks)
    data_date = max((stock.get("completed_factor_date") or stock.get("last_date") or "" for stock in stocks), default="")
    if not force:
        try:
            cached = json.loads(WYCKOFF_CACHE_FILE.read_text(encoding="utf-8"))
            if (cached.get("engine_version") == WYCKOFF_ENGINE_VERSION
                    and cached.get("universe_signature") == signature
                    and cached.get("data_date") == data_date
                    and cached.get("volume_unit") == VOLUME_UNIT):
                return {**cached, "cache_hit": True}
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            pass

    histories = {}
    quality = {"unit_missing": 0, "trimmed_tail": 0, "short_history": 0}
    # Freeze the "completed bar" cutoff to the moment the universe was refreshed.
    # Re-deriving it from the wall clock would let this screen use a bar the factor
    # pipeline never saw, which is how the displayed data date drifted a day behind.
    try:
        cut_off = time.strptime(dashboard.get("generated_at") or "", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        cut_off = None

    def history_loader(stock):
        code = stock.get("code", "")
        if code not in histories:
            cached = read_kline_cache(code)
            raw = cached if cached else (stock.get("klines") or [])
            if not cached and raw:
                # No unit-stamped cache file: the embedded series came from the last
                # dashboard refresh and its volume unit cannot be verified.
                quality["unit_missing"] += 1
            rows = tradable_daily_rows(raw, now=cut_off)
            if len(rows) < len(completed_daily_rows(raw, now=cut_off)):
                quality["trimmed_tail"] += 1
            if len(rows) < 80:
                quality["short_history"] += 1
            histories[code] = rows
        return histories[code]

    screened, insufficient, phase_counts = screen_universe(stocks, history_loader)
    # A suspended or feed-less name keeps its old bars, so its "breakout" is a
    # stale quote. Rank only names that actually traded on the universe bar date.
    universe_bar_date = max((rows[-1].get("date") for rows in histories.values() if rows), default=None)
    fresh_screened = [item for item in screened if item.get("date") == universe_bar_date]
    stale_candidates = [item for item in screened
                        if item.get("phase_key") != "neutral" and item.get("date") != universe_bar_date]
    candidates = [item for item in fresh_screened if item.get("phase_key") != "neutral"]
    phase_counts = {}
    for item in fresh_screened:
        phase_counts[item["phase_key"]] = phase_counts.get(item["phase_key"], 0) + 1
    result = backtest_wyckoff(stocks, history_loader)
    quality["stale_last_bar"] = len(stale_candidates)
    stale_candidates.sort(key=lambda item: item["wyckoff_score"], reverse=True)
    payload = {
        "engine_version": WYCKOFF_ENGINE_VERSION,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "data_date": data_date,
        "bar_date": universe_bar_date,
        "volume_unit": VOLUME_UNIT,
        "data_stale": dashboard_data_is_stale(dashboard),
        "data_quality": quality,
        "stale_count": len(stale_candidates),
        "stale_candidates": [{"code": item["code"], "name": item["name"], "score": item["wyckoff_score"],
                              "bar_date": item.get("date")} for item in stale_candidates[:20]],
        "universe_signature": signature,
        "universe_name": "科技股池（独立威科夫模型）",
        "analyzed_count": len(fresh_screened),
        "screened_count": len(screened),
        "insufficient_count": insufficient,
        "candidate_count": len(candidates),
        "phase_counts": phase_counts,
        "candidates": candidates,
        "backtest": result,
        "model": {
            "name": "Wyckoff Price-Volume v1",
            "inputs": "仅使用前复权日线 OHLCV",
            "phases": ["Spring 弹簧", "SOS 强势突破", "LPS 最后支撑", "吸筹观察"],
            "independent": True,
            "rule": "识别 60 日交易区间、支撑测试、突破量能与突破后缩量回踩；不读取当前多因子策略评分。",
        },
    }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = WYCKOFF_CACHE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, WYCKOFF_CACHE_FILE)
    return payload

def write_kline_manifest():
    files = list(KLINE_CACHE_DIR.glob("*.json")) if KLINE_CACHE_DIR.exists() else []
    records = []
    basis_counts = {}
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("klines") or []
            basis = payload.get("adjustment") or "未标注"
            basis_counts[basis] = basis_counts.get(basis, 0) + 1
            if rows:
                records.append({"code": payload.get("code") or path.stem, "bars": len(rows), "first_date": rows[0].get("date"), "last_date": rows[-1].get("date"), "updated_at": payload.get("updated_at"), "adjustment": basis})
        except (OSError, json.JSONDecodeError, TypeError, AttributeError):
            continue
    manifest = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "volume_unit": VOLUME_UNIT,
                "adjustment": ADJUSTMENT, "adjustment_counts": basis_counts,
                "stock_count": len(records), "min_bars": min((item["bars"] for item in records), default=0), "max_bars": max((item["bars"] for item in records), default=0), "latest_date": max((item["last_date"] or "" for item in records), default=None), "files": records}
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
    # 复权基准分布直接决定收益算得对不对，必须跟着缓存一起暴露给前端。
    # 复用刚刚写好的 K 线清单，避免在循环里再读 900 个文件。
    try:
        adjustment_counts = (json.loads(KLINE_MANIFEST_FILE.read_text(encoding="utf-8")).get("adjustment_counts") or {})
    except (OSError, json.JSONDecodeError):
        adjustment_counts = {}
    payload = {"generated_at": generated_at, "source": "腾讯前复权日线，东方财富/新浪备用", "adjustment": ADJUSTMENT, "adjustment_counts": adjustment_counts, "frequency": "日线", "volume_unit": VOLUME_UNIT, "excluded_boards": ["科创板 688/689"], "stock_count": len(exported), "total_bars": total_bars, "first_date": min((value for value in first_dates if value), default=None), "last_date": max((value for value in last_dates if value), default=None), "stocks": exported}
    temporary = TECH_HISTORY_FILE.with_suffix(TECH_HISTORY_FILE.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=6) as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    os.replace(temporary, TECH_HISTORY_FILE)
    manifest = {key: value for key, value in payload.items() if key != "stocks"}
    manifest.update({"file": str(TECH_HISTORY_FILE.relative_to(Path(__file__).parent)), "compressed_bytes": TECH_HISTORY_FILE.stat().st_size, "format": "gzip JSON；解压后 stocks.<code>.bars 为完整 OHLCV 日线数组"})
    _atomic_json_write(TECH_HISTORY_MANIFEST_FILE, manifest)
    return manifest

def factor_cross_section(stocks, keys):
    """取最新已完成交易日的因子截面，用来判断两个因子在"排序"上是否重复。"""
    latest = max((stock.get("completed_factor_date") or stock.get("last_date") or "" for stock in stocks), default="")
    series = {key: {} for key in keys}
    for stock in stocks:
        if (stock.get("completed_factor_date") or stock.get("last_date")) != latest:
            continue
        values = stock.get("completed_mined_factors") or stock.get("mined_factors") or {}
        for key in keys:
            value = values.get(key)
            if value is None:
                continue
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                series[key][stock.get("code")] = value
    return latest, series

def rank_correlation(left, right, min_overlap=30):
    """两个因子在同一批股票上的秩相关；样本不足返回 None。"""
    codes = [code for code in left if code in right]
    if len(codes) < min_overlap:
        return None
    left_ranks = _rank([left[code] for code in codes])
    right_ranks = _rank([right[code] for code in codes])
    left_mean, right_mean = mean(left_ranks), mean(right_ranks)
    covariance = sum((a - left_mean) * (b - right_mean) for a, b in zip(left_ranks, right_ranks))
    left_var = sum((a - left_mean) ** 2 for a in left_ranks)
    right_var = sum((b - right_mean) ** 2 for b in right_ranks)
    if left_var <= 0 or right_var <= 0:
        return None
    return covariance / math.sqrt(left_var * right_var)

def cluster_factors(items, series, threshold=FACTOR_CORRELATION_THRESHOLD):
    """按 |IC| 降序贪心聚类，每个簇只保留一个代表因子。

    用**相关系数的绝对值**：reversal_40 与 momentum_40 只差一个负号，是同一个
    因子；方向由 direction 字段单独处理，所以负相关同样算重复。
    """
    representatives, merged = [], []
    for item in sorted(items, key=lambda entry: abs(entry.get("mean_rank_ic") or 0), reverse=True):
        key = item.get("key")
        duplicate = None
        for representative in representatives:
            correlation = rank_correlation(series.get(key) or {}, series.get(representative["key"]) or {})
            if correlation is not None and abs(correlation) >= threshold:
                duplicate = (representative, correlation)
                break
        if duplicate:
            representative, correlation = duplicate
            merged.append({**item, "duplicate_of": representative["key"],
                           "duplicate_of_name": representative.get("name"),
                           "correlation": round(correlation, 3)})
        else:
            representatives.append(item)
    return representatives, merged

def factor_cluster_summary(items, stocks, threshold=FACTOR_CORRELATION_THRESHOLD):
    """给因子验证结果打上"是否只是别人的重复"的标记，并汇总独立因子。"""
    if not items:
        return {"date": None, "threshold": threshold, "input_count": 0, "independent_count": 0,
                "independent": [], "merged": []}
    date, series = factor_cross_section(stocks, [item["key"] for item in items])
    representatives, merged = cluster_factors(items, series, threshold)
    merged_map = {item["key"]: item for item in merged}
    for item in items:
        duplicate = merged_map.get(item["key"])
        if duplicate:
            item["duplicate_of"] = duplicate["duplicate_of"]
            item["duplicate_of_name"] = duplicate["duplicate_of_name"]
            item["duplicate_correlation"] = duplicate["correlation"]
    return {
        "date": date,
        "threshold": threshold,
        "input_count": len(items),
        "independent_count": len(representatives),
        "independent": [{"key": item["key"], "name": item.get("name"), "rank_ic": item.get("mean_rank_ic"),
                         "icir": item.get("icir")} for item in representatives],
        "merged": [{"key": item["key"], "name": item.get("name"), "rank_ic": item.get("mean_rank_ic"),
                    "icir": item.get("icir"), "duplicate_of": item["duplicate_of"],
                    "duplicate_of_name": item["duplicate_of_name"], "correlation": item["correlation"]}
                   for item in merged],
    }

def kline_data_fingerprint(stocks):
    """对本次用到的全部日线做指纹。

    引擎版本指纹只能保证"代码没变"，但数据本身会被后台刷新改写（腾讯/新浪切换、
    复权换算），同样日期同样代码的收盘价可能不同。没有数据指纹，缓存会把旧结果
    当成新数据的结果返回，实验也就无法复现。
    """
    digest = hashlib.sha256()
    for code in sorted(stock.get("code", "") for stock in stocks):
        rows = read_kline_cache(code) or []
        digest.update(code.encode("utf-8"))
        if not rows:
            digest.update(b":empty")
            continue
        digest.update(("%d|%s|%s|%.4f" % (len(rows), rows[0].get("date"), rows[-1].get("date"),
                                          sum(float(row.get("close") or 0) for row in rows))).encode("utf-8"))
    return digest.hexdigest()[:16]

def expected_latest_trading_date(now=None):
    """Latest A-share daily bar that should already exist (bars settle at 15:10).

    Weekends are skipped; exchange holidays are not modelled because this is only
    used to decide whether a refresh is worth starting, never to drop data.
    """
    now = now or time.localtime()
    stamp = time.mktime(now)
    if now.tm_hour * 100 + now.tm_min < 1510:
        stamp -= 24 * 3600
    for _ in range(10):
        probe = time.localtime(stamp)
        if probe.tm_wday < 5:
            return time.strftime("%Y-%m-%d", probe)
        stamp -= 24 * 3600
    return time.strftime("%Y-%m-%d", now)

def dashboard_data_is_stale(payload, now=None):
    """True when the cached universe has no bar for the latest settled trading day."""
    if not payload or not payload.get("stocks"):
        return True
    if payload.get("volume_unit") != VOLUME_UNIT:
        # Legacy cache: its成交量 may mix "股" and "手", so it must be rebuilt
        # before any volume-based factor is trusted.
        return True
    latest = payload.get("latest_date")
    return not latest or latest < expected_latest_trading_date(now)

def refresh_dashboard(force_network=False):
    global REFRESH_THREAD, LAST_REFRESH
    if not CACHE_LOCK.acquire(blocking=False):
        return False
    LAST_REFRESH = {"started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "finished_at": None, "ok": None, "error": None}
    try:
        payload = build_dashboard_fresh(force_network=force_network)
        payload = attach_research_runs(payload)
        try:
            payload["recommendation_review"] = update_recommendation_review(payload)
        except Exception as exc:
            # 留痕失败绝不能连累行情刷新本身
            review = summarize_recommendations(load_recommendation_log())
            review["error"] = str(exc)
            payload["recommendation_review"] = review
        write_dashboard_cache(payload)
        LAST_REFRESH.update({"finished_at": time.strftime("%Y-%m-%d %H:%M:%S"), "ok": True,
                             "data_date": payload.get("latest_date"), "stocks": len(payload.get("stocks") or []),
                             "stale_stocks": sum(bool(s.get("quote_stale")) for s in payload.get("stocks") or [])})
        return True
    except Exception as exc:
        LAST_REFRESH.update({"finished_at": time.strftime("%Y-%m-%d %H:%M:%S"), "ok": False, "error": str(exc)})
        raise
    finally:
        CACHE_LOCK.release()
        REFRESH_THREAD = None

def build_dashboard(force_refresh=False):
    """返回驾驶舱数据。

    force_refresh=True 时无条件在后台重新拉取全量行情（不阻塞本次请求），
    用于「刷新数据」按钮：缓存 15 分钟 TTL 内的普通刷新是读缓存、不会联网的。
    """
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
        thread_idle = REFRESH_THREAD is None or not REFRESH_THREAD.is_alive()
        if thread_idle and (force_refresh or cache_age > REFRESH_TTL_SECONDS):
            REFRESH_THREAD = threading.Thread(target=refresh_dashboard, kwargs={"force_network": force_refresh}, name="dashboard-refresh", daemon=True)
            REFRESH_THREAD.start()
        cached["cache_age_seconds"] = round(cache_age, 1)
        cached["refreshing"] = bool(REFRESH_THREAD and REFRESH_THREAD.is_alive())
        cached["cache_status"] = "refreshing" if (force_refresh and cached["refreshing"]) else ("fresh" if cache_age <= REFRESH_TTL_SECONDS else "stale_while_revalidate")
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
    def end_headers(self):
        if not urlparse(self.path).path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        super().end_headers()

    def do_POST(self):
        global MULTI_AGENT_THREAD, MULTI_AGENT_PREP_STATUS
        parsed = urlparse(self.path)
        request_body = {}
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                request_body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            request_body = {}
        if parsed.path == "/api/trading-ledger":
            try:
                payload = trading_ledger.transact(request_body, ledger_quotes())
                self.send_json(payload)
            except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
                self.send_json({"error": str(exc)}, 409)
            return
        if parsed.path == "/api/agents/run":
            try:
                dashboard = read_dashboard_cache()
                if not dashboard or not dashboard.get("stocks"):
                    raise RuntimeError("真实行情缓存尚未完成，十二 Agent 不会使用模拟输入")
                with MULTI_AGENT_LOCK:
                    if MULTI_AGENT_THREAD and MULTI_AGENT_THREAD.is_alive():
                        payload = {"status": "运行中", "run": read_multi_agent_run()}
                    else:
                        mode = request_body.get("mode", "full")
                        if mode not in {"full", "quick"}:
                            raise RuntimeError("运行模式必须是 full 或 quick")
                        MULTI_AGENT_THREAD = threading.Thread(target=run_multi_agent_background, args=(dashboard, mode), name="nstock-12-agent-council", daemon=True)
                        MULTI_AGENT_PREP_STATUS = {"stage": "准备同步", "detail": "即将增量检查全部股票的最新 K 线", "error": None}
                        MULTI_AGENT_THREAD.start()
                        payload = {"status": "已启动", "agent_count": 12, "team_count": 4,
                                   "execution_mode": "全量重算（含研究研发组）" if mode == "full" else "快速会签（复用因子）"}
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
        if parsed.path == "/api/strategy/select":
            try:
                dashboard = read_dashboard_cache() or {}
                keys = [str(key) for key in request_body.get("factors", [])][:5]
                holding = request_body.get("holding_days")
                if not keys:
                    if SELECTED_STRATEGY_FILE.exists():
                        SELECTED_STRATEGY_FILE.unlink()
                    dashboard.pop("selected_strategy_factors", None)
                    if dashboard:
                        write_dashboard_cache(dashboard)
                    body = json.dumps({"cleared": True}, ensure_ascii=False).encode("utf-8")
                    self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                    return
                backtest = read_backtest_run() or {}
                if backtest.get("stale_engine"):
                    raise RuntimeError("旧回测口径已失效，请先重算，再应用策略")
                strategy_id = request_body.get("strategy_id")
                item = (next((row for row in backtest.get("results", []) if row.get("strategy_id") == strategy_id), None)
                        if strategy_id else find_strategy_result(backtest, keys, holding))
                if item is None:
                    raise RuntimeError("回测结果里找不到这个策略，请先运行组合回测")
                if item.get("factors") != keys or (holding is not None and item.get("holding_days") != holding):
                    raise RuntimeError("策略标识与因子或持有期不匹配，请刷新后重新选择")
                stored = write_selected_strategy(keys, item.get("holding_days"), item.get("strategy_id"))
                selection = select_by_factor_keys(dashboard.get("stocks") or [], keys) if item.get("strategy_id") != market_strategy.ID else {
                    "scores": {code: data["score"] for code, data in (market_strategy.read(sys.modules[__name__]) or {}).get("scores", {}).items()},
                    "breakdowns": {code: data["breakdown"] for code, data in (market_strategy.read(sys.modules[__name__]) or {}).get("scores", {}).items()}}
                for stock in dashboard.get("stocks") or []:
                    stock["agent_combo_score"] = selection["scores"].get(stock["code"])
                    stock["agent_combo_factors"] = keys
                    stock["agent_combo_breakdown"] = selection["breakdowns"].get(stock["code"], [])
                dashboard["selected_strategy_factors"] = keys
                if dashboard.get("stocks"):
                    write_dashboard_cache(dashboard)
                result = item.get("result") or {}
                body = json.dumps({"selected_strategy": stored, "rank": item.get("rank"), "names": item.get("names"),
                                   "holding_days": item.get("holding_days"), "annual_return": result.get("annual_return"),
                                   "max_drawdown": result.get("max_drawdown"), "sharpe": result.get("sharpe"),
                                   "passes_gate": item.get("research_only", False) or ((result.get("days", 0) or 0) >= 20 and (result.get("max_drawdown", 1) or 1) <= .35),
                                   }, ensure_ascii=False).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            except Exception as exc:
                body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self.send_response(409); self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        if parsed.path == "/api/backtest/select":
            try:
                if (read_backtest_run() or {}).get("stale_engine"):
                    raise RuntimeError("旧回测口径已失效，请先重算")
                dashboard = read_dashboard_cache()
                keys = [str(key) for key in request_body.get("factors", [])][:5]
                if not dashboard or not dashboard.get("stocks") or not keys:
                    raise RuntimeError("没有可用的回测策略或因子")
                selection = select_by_factor_keys(dashboard["stocks"], keys)
                for stock in dashboard["stocks"]:
                    code = stock["code"]
                    score = selection["scores"].get(code)
                    stock["agent_combo_score"] = score
                    stock["agent_combo_factors"] = keys
                    stock["agent_combo_breakdown"] = selection["breakdowns"].get(code, [])
                dashboard["selected_strategy_factors"] = keys
                write_dashboard_cache(dashboard)
                body = json.dumps({"selection": selection}, ensure_ascii=False).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            except Exception as exc:
                body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self.send_response(409); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        self.send_error(404)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/timing-week-review":
            try:
                self.send_json(intraday_entry_review.latest() or intraday_week_review.latest() or {"status": "pending"})
            except (OSError, ValueError) as exc:
                self.send_json({"error": str(exc)}, 502)
            return
        if parsed.path == "/api/trading-ledger":
            try:
                with trading_ledger.LOCK:
                    payload = trading_ledger.summarize(trading_ledger.load(), ledger_quotes())
                self.send_json(payload)
            except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
                self.send_json({"error": str(exc)}, 409)
            return
        if parsed.path == "/api/wyckoff":
            try:
                force = (parse_qs(parsed.query).get("refresh") or ["0"])[0] == "1"
                payload = json.dumps(build_wyckoff(force=force), ensure_ascii=False).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
            except Exception as exc:
                payload = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self.send_response(502); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
            return
        if parsed.path == "/api/recommendations":
            try:
                force = (parse_qs(parsed.query).get("refresh") or ["0"])[0] == "1"
                strategy_id = (parse_qs(parsed.query).get("strategy") or [None])[0]
                review = cached_recommendation_review(strategy_id, force=force)
                payload = json.dumps(review, ensure_ascii=False).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
            except Exception as exc:
                payload = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self.send_response(502); self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
            return
        if parsed.path == "/api/chief-opinions":
            code = (parse_qs(parsed.query).get("stock") or [""])[0].strip()
            if not re.fullmatch(r"\d{6}", code):
                payload = json.dumps({"error": "stock 必须是 6 位股票代码"}, ensure_ascii=False).encode("utf-8")
                self.send_response(400)
            else:
                cached = load_chief_opinion_cache(code)
                payload = json.dumps(cached or {"error": "该股票的首席观点尚未缓存", "code": code}, ensure_ascii=False).encode("utf-8")
                self.send_response(200 if cached else 404)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(payload)))
            self.end_headers(); self.wfile.write(payload)
            return
        if parsed.path == "/api/agents":
            running = bool(MULTI_AGENT_THREAD and MULTI_AGENT_THREAD.is_alive())
            dashboard = read_dashboard_cache() or {}
            response = {"running": running, "preparing": MULTI_AGENT_PREP_STATUS, "run": read_multi_agent_run(), "agent_count": 12, "team_count": 4}
            payload = json.dumps(normalize_stock_names(response, dashboard.get("stocks", [])), ensure_ascii=False).encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
            return
        if parsed.path == "/api/refresh-status":
            payload = json.dumps({"refreshing": bool(REFRESH_THREAD and REFRESH_THREAD.is_alive()), **LAST_REFRESH},
                                 ensure_ascii=False).encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
            return
        if parsed.path == "/api/dashboard":
            try:
                force = (parse_qs(parsed.query).get("refresh") or ["0"])[0] == "1"
                payload = json.dumps(build_dashboard(force_refresh=force), ensure_ascii=False).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
            except Exception as exc:
                payload = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self.send_response(502); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
            return
        super().do_GET()

    def send_json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

if __name__ == "__main__":
    print(f"Signal Foundry running at http://{HOST}:{PORT}")
    startup_cache = read_dashboard_cache()
    if dashboard_data_is_stale(startup_cache):
        reason = "缓存缺失" if not startup_cache else f"缓存最新交易日为 {startup_cache.get('latest_date')}，早于 {expected_latest_trading_date()}"
        print(f"行情数据不是最新（{reason}），启动后台全量拉取…")
        REFRESH_THREAD = threading.Thread(target=refresh_dashboard, name="dashboard-warmup", daemon=True)
        REFRESH_THREAD.start()
    else:
        print(f"行情缓存已是最新交易日 {startup_cache.get('latest_date')}，跳过启动拉取。")
    def automatic_review_updates():
        while True:
            try:
                now = time.localtime()
                # Refresh the market cache while the exchange is active, including close settlement.
                if now.tm_wday < 5 and 900 <= now.tm_hour * 100 + now.tm_min <= 1630:
                    build_dashboard()
                update_recommendation_review(read_dashboard_cache() or {})
            except Exception as exc:
                print(f"推荐复盘自动更新失败：{exc}")
            time.sleep(REFRESH_TTL_SECONDS)
    threading.Thread(target=automatic_review_updates, name="recommendation-auto-update", daemon=True).start()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
