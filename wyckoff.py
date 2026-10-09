"""Independent Wyckoff price-volume screener and swing backtest."""

from __future__ import annotations

import math
import statistics
from collections import Counter


ENGINE_VERSION = "wyckoff-ohlcv-v1"
MIN_HISTORY = 80
PHASE_LABELS = {
    "spring": "Spring 弹簧",
    "sos": "SOS 强势突破",
    "lps": "LPS 最后支撑",
    "accumulation": "吸筹观察",
    "neutral": "结构未完成",
}


def _number(row, key):
    try:
        value = float(row.get(key, 0))
        return value if math.isfinite(value) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _mean(values):
    return sum(values) / len(values) if values else 0.0


def _clamp(value, low=0.0, high=100.0):
    return max(low, min(high, value))


def _prior_range(rows, index, window=60, offset=0):
    end = index - offset
    start = end - window
    if start < 0 or end <= start:
        return None
    sample = rows[start:end]
    low = min(_number(row, "low") for row in sample)
    high = max(_number(row, "high") for row in sample)
    return (low, high) if low > 0 and high > low else None


def _spring_strength(rows, index):
    best = 0.0
    for event_index in range(max(60, index - 4), index + 1):
        prior = _prior_range(rows, event_index, 55)
        if not prior:
            continue
        support, resistance = prior
        row = rows[event_index]
        low, close = _number(row, "low"), _number(row, "close")
        if low < support * 0.995 and close > support:
            recovery = (close - low) / max(resistance - support, close * 0.01)
            best = max(best, _clamp(58 + recovery * 170))
    return best


def _sos_event_strength(rows, event_index):
    prior = _prior_range(rows, event_index, 55)
    if not prior:
        return 0.0
    _, resistance = prior
    close = _number(rows[event_index], "close")
    volume = _number(rows[event_index], "volume")
    average_volume = _mean([_number(row, "volume") for row in rows[event_index - 20:event_index]])
    volume_ratio = volume / average_volume if average_volume else 0.0
    if close <= resistance * 1.003 or volume_ratio < 1.15:
        return 0.0
    breakout = close / resistance - 1
    return _clamp(55 + min(breakout, 0.12) * 180 + min(volume_ratio - 1, 1.5) * 24)


def _sos_strength(rows, index):
    scored = [(_sos_event_strength(rows, event_index), event_index)
              for event_index in range(max(60, index - 4), index + 1)]
    return max(scored, default=(0.0, None))


def _recent_sos(rows, index):
    for event_index in range(index - 5, max(59, index - 26), -1):
        if _sos_event_strength(rows, event_index) >= 60:
            return event_index
    return None


def analyze_rows(rows, index=None):
    """Analyze bars through index only; no future row is read."""
    index = len(rows) - 1 if index is None else index
    if index < MIN_HISTORY - 1:
        return {"status": "数据不足", "phase_key": "neutral", "phase": PHASE_LABELS["neutral"], "wyckoff_score": None}

    current = rows[index]
    close = _number(current, "close")
    prior = _prior_range(rows, index, 60)
    if not prior or close <= 0:
        return {"status": "数据不足", "phase_key": "neutral", "phase": PHASE_LABELS["neutral"], "wyckoff_score": None}
    support, resistance = prior
    width = (resistance - support) / support
    range_position = (close - support) / (resistance - support)
    volumes = [_number(row, "volume") for row in rows[index - 20:index]]
    volume_average = _mean(volumes)
    volume_ratio = _number(current, "volume") / volume_average if volume_average else 0.0

    spring = _spring_strength(rows, index)
    sos, sos_event = _sos_strength(rows, index)
    recent_sos = _recent_sos(rows, index)
    lps = 0.0
    if recent_sos is not None:
        breakout_range = _prior_range(rows, recent_sos, 55)
        breakout_level = breakout_range[1] if breakout_range else resistance
        peak = max(_number(row, "high") for row in rows[recent_sos:index + 1])
        pullback = 1 - close / peak if peak else 0
        recent_volume = _mean([_number(row, "volume") for row in rows[max(recent_sos + 1, index - 4):index + 1]])
        breakout_volume = _number(rows[recent_sos], "volume")
        if close >= breakout_level * 0.98 and 0.015 <= pullback <= 0.13 and recent_volume < breakout_volume * 0.85:
            lps = _clamp(62 + (0.13 - pullback) * 120 + (1 - recent_volume / max(breakout_volume, 1)) * 25)

    recent_lows = [_number(row, "low") for row in rows[index - 59:index + 1]]
    support_tests = sum(1 for value in recent_lows if value <= support * 1.035)
    old_volume = _mean([_number(row, "volume") for row in rows[index - 40:index - 20]])
    new_volume = _mean([_number(row, "volume") for row in rows[index - 20:index]])
    dry_up = new_volume / old_volume if old_volume else 1.0
    accumulation = _clamp(
        32
        + max(0, 0.30 - width) * 105
        + min(support_tests, 5) * 5
        + max(0, 1 - dry_up) * 24
        + _clamp(range_position, 0, 1) * 12
    ) if width <= 0.38 and support_tests >= 2 else 0.0

    closes = [_number(row, "close") for row in rows[index - 20:index + 1]]
    returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1] > 0]
    volatility = statistics.pstdev(returns) * math.sqrt(252) if len(returns) > 1 else 0.0
    structure = _clamp(45 + (range_position - 0.5) * 55 - max(0, width - 0.30) * 60)
    volume_score = _clamp(50 + (volume_ratio - 1) * 35)
    risk_penalty = _clamp(max(0, volatility - 0.45) * 45 + max(0, close / resistance - 1.12) * 120, 0, 28)

    strengths = {"spring": spring, "sos": sos, "lps": lps, "accumulation": accumulation}
    # A confirmed event is more actionable than the broader accumulation
    # background, even when the latter has a slightly higher structure score.
    if lps >= 60:
        phase_key = "lps"
    elif sos >= 55:
        phase_key = "sos"
    elif spring >= 55:
        phase_key = "spring"
    else:
        phase_key = max(strengths, key=strengths.get)
    phase_strength = strengths[phase_key]
    if phase_strength < 48:
        phase_key = "neutral"
    score = _clamp(0.62 * phase_strength + 0.23 * structure + 0.15 * volume_score - risk_penalty)
    if phase_key == "neutral":
        score = min(score, 54.9)

    return {
        "status": "完成",
        "phase_key": phase_key,
        "phase": PHASE_LABELS[phase_key],
        "wyckoff_score": round(score, 1),
        "price": round(close, 3),
        "range_low": round(support, 3),
        "range_high": round(resistance, 3),
        "range_position": round(range_position, 3),
        "volume_ratio": round(volume_ratio, 2),
        "volatility20": round(volatility, 3),
        "signals": {
            "structure": round(structure, 1),
            "spring": round(spring, 1),
            "sos": round(sos, 1),
            "lps": round(lps, 1),
            "accumulation": round(accumulation, 1),
            "volume": round(volume_score, 1),
            "risk_penalty": round(risk_penalty, 1),
        },
        "evidence": _evidence(phase_key, range_position, volume_ratio, volatility),
        "event_date": rows[sos_event].get("date") if sos_event is not None else current.get("date"),
    }


def _evidence(phase_key, range_position, volume_ratio, volatility):
    phase_notes = {
        "spring": "下破区间支撑后收回，出现弹簧测试",
        "sos": "放量越过区间阻力，出现强势信号",
        "lps": "突破后缩量回踩，仍守在原阻力附近",
        "accumulation": "区间收敛并反复测试支撑，卖压趋缓",
        "neutral": "当前量价结构尚未形成完整威科夫触发",
    }
    return [
        phase_notes[phase_key],
        f"区间位置 {range_position * 100:.0f}%",
        f"当日量比 {volume_ratio:.2f}x",
        f"20日年化波动 {volatility * 100:.1f}%",
    ]


def screen_universe(stocks, history_loader):
    candidates, insufficient = [], 0
    for stock in stocks:
        rows = history_loader(stock)
        result = analyze_rows(rows)
        if result.get("wyckoff_score") is None:
            insufficient += 1
            continue
        result.update({
            "code": stock.get("code", ""),
            "name": stock.get("name") or stock.get("code", ""),
            "industry": stock.get("industry") or "未分类",
            "date": rows[-1].get("date") if rows else None,
        })
        candidates.append(result)
    candidates.sort(key=lambda item: item["wyckoff_score"], reverse=True)
    phase_counts = Counter(item["phase_key"] for item in candidates)
    return candidates, insufficient, dict(phase_counts)


def backtest(stocks, history_loader, holding_days=10, top_n=5, cost_bps=15, max_periods=26):
    """Non-overlapping swing test: signal T, enter T+1 close, exit T+10 close."""
    prepared = []
    all_dates = set()
    for stock in stocks:
        rows = history_loader(stock)
        if len(rows) < MIN_HISTORY + holding_days + 1:
            continue
        indexes = {row.get("date"): index for index, row in enumerate(rows)}
        prepared.append((stock, rows, indexes))
        all_dates.update(indexes)
    dates = sorted(all_dates)
    signal_dates = dates[-(max_periods * holding_days + 1):-holding_days:holding_days]
    period_returns, previous, observations = [], set(), []
    for date in signal_dates:
        cross = []
        for stock, rows, indexes in prepared:
            index = indexes.get(date)
            if index is None or index < MIN_HISTORY - 1 or index + holding_days >= len(rows):
                continue
            signal = analyze_rows(rows, index)
            if signal.get("phase_key") == "neutral" or (signal.get("wyckoff_score") or 0) < 55:
                continue
            entry = _number(rows[index + 1], "close")
            exit_price = _number(rows[index + holding_days], "close")
            if entry > 0 and exit_price > 0:
                cross.append((signal["wyckoff_score"], stock.get("code", ""), exit_price / entry - 1))
        if not cross:
            continue
        selected = sorted(cross, reverse=True)[:top_n]
        codes = {item[1] for item in selected}
        turnover = 1.0 if not previous else 1 - len(previous & codes) / max(len(codes), 1)
        net_return = _mean([item[2] for item in selected]) - cost_bps / 10000 * turnover
        period_returns.append(net_return)
        observations.append({"signal_date": date, "holding_count": len(selected), "net_return": round(net_return, 5)})
        previous = codes
    if len(period_returns) < 4:
        return {"status": "数据不足", "periods": len(period_returns)}
    equity, peak, max_drawdown = 1.0, 1.0, 0.0
    for value in period_returns:
        equity *= 1 + value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, 1 - equity / peak)
    average = _mean(period_returns)
    deviation = statistics.pstdev(period_returns)
    periods_per_year = 252 / holding_days
    return {
        "status": "完成" if len(period_returns) >= 13 else "短样本，仅供参考",
        "periods": len(period_returns),
        "cumulative_return": round(equity - 1, 4),
        "annual_return": round(equity ** (periods_per_year / len(period_returns)) - 1, 4),
        "max_drawdown": round(max_drawdown, 4),
        "sharpe": round(average / deviation * math.sqrt(periods_per_year), 3) if deviation else 0.0,
        "win_rate": round(sum(value > 0 for value in period_returns) / len(period_returns), 3),
        "holding_days": holding_days,
        "holding_count": top_n,
        "cost_bps": cost_bps,
        "return_definition": "T 日收盘确认信号，T+1 收盘买入，T+10 收盘卖出；Top 5 等权，组合不重叠",
        "observations": observations,
    }
