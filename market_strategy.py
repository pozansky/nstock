"""Registered, fixed market-gated Top-3 strategy and its isolated backtest cache."""

import json
import math
import threading
from statistics import mean

ID = "market-gated-top3-nextday-v2"
NAME = "顺势缩量 Top3 · 行情择时"
FACTORS = ["drawdown_40", "momentum_3", "volume_ratio_3_20"]
NAMES = ["40日高位距离", "3日动量", "3/20日量比"]
DIRECTIONS = ["正向", "正向", "反向"]
VERSION = "market-gated-fixed-units-v4-nextday-only"
CACHE_NAME = "market_timing_nextday_backtest.json"
LOCK = threading.RLock()
MEMORY = {}


def specification():
    return {"strategy_id": ID, "factors": list(FACTORS), "holding_days": 1,
            "directions": list(DIRECTIONS), "name": NAME, "entry_offset": 0,
            "market_gate": {"breadth_above": .45, "index_above_ma": 20}}


def market_states(histories, dates):
    calendar = sorted({bar["date"] for rows in histories.values() for bar in rows})
    indexed = {code: {bar["date"]: bar["close"] for bar in rows} for code, rows in histories.items()}
    nav, curve, states = 1.0, [], {}
    for i, day in enumerate(calendar):
        values = [prices[day] / prices[calendar[i - 1]] - 1 for prices in indexed.values()
                  if i and prices.get(day, 0) > 0 and prices.get(calendar[i - 1], 0) > 0]
        if values:
            nav *= 1 + mean(values)
        curve.append(nav)
        if day in dates:
            valid = len(values) >= 20
            breadth = sum(value > 0 for value in values) / len(values) if values else None
            trend = len(curve) >= 20 and nav > mean(curve[-20:])
            states[day] = {"valid": valid, "stocks": len(values), "up_fraction": breadth,
                           "above_ma20": trend, "broad_market_return": mean(values) if values else None,
                           "breadth": valid and breadth > .5,
                           "breadth_trend": valid and breadth > .5 and trend}
    return states


def gate_open(state):
    return bool(state.get("valid") and state.get("up_fraction", 0) > .45 and state.get("above_ma20"))


def apply_return_reporting(result, curve, capital=20000):
    """Endpoint returns survive recovered quotes; path risk metrics do not."""
    missing = sum(not point["complete"] for point in curve)
    ending = curve[-1]["equity"] if curve else None
    known = bool(curve and curve[-1]["complete"] and math.isfinite(ending) and ending >= 0)
    result.update(ending_equity_complete=known, missing_mark_days=missing,
                  return_quality="recovered_quotes_delayed_exit" if known and missing else "complete" if known else "unavailable",
                  equity=ending if known else None,
                  cumulative_return=ending - 1 if known else None,
                  annual_return=ending ** (252 / len(curve)) - 1 if known else None,
                  annualization_reliable=known and len(curve) >= 252,
                  pnl_20000=round((ending - 1) * capital, 2) if known else None)
    if missing:
        for key in ("max_drawdown", "sharpe", "daily_win_rate"):
            result[key] = None
    return result


def read(api):
    path = api.CACHE_DIR / CACHE_NAME
    try:
        stamp = (str(path), path.stat().st_mtime_ns)
        with LOCK:
            if MEMORY.get("stamp") != stamp:
                MEMORY.update(stamp=stamp, payload=json.loads(path.read_text(encoding="utf-8")))
            result = MEMORY["payload"]
            return result if result.get("version") == VERSION else None
    except (OSError, ValueError):
        return None


def ensure(api, stocks, force=False):
    with LOCK:
        fingerprint = api.kline_data_fingerprint(stocks)
        existing = read(api)
        if not force and existing and existing.get("fingerprint") == fingerprint:
            return existing
        result = build(api, stocks)
        result["fingerprint"] = fingerprint
        path = api.CACHE_DIR / CACHE_NAME
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
        return result


def build(api, stocks):
    import numpy as np
    import pandas as pd

    histories = {}
    for stock in sorted(stocks, key=lambda item: item["code"]):
        rows, basis = api.read_kline_cache_full(stock["code"])
        rows = api.completed_daily_rows(rows)
        if basis == api.ADJUSTMENT and len(rows) >= 122:
            histories[stock["code"]] = rows
    dates = sorted({r["date"] for rows in histories.values() for r in rows[120:]})
    if not dates:
        raise ValueError("No qualified history for market-gated strategy")
    codes = sorted(histories)
    raw = np.full((3, len(dates), len(codes)), np.nan)
    liquidity = np.full((len(dates), len(codes)), np.nan)
    prices = np.full_like(liquidity, np.nan)
    for j, code in enumerate(codes):
        frame = pd.DataFrame(histories[code])
        c, v = frame.close.astype(float), frame.volume.astype(float).fillna(0)
        values = pd.DataFrame({FACTORS[0]: c / c.rolling(40).max() - 1,
                               FACTORS[1]: c.pct_change(3, fill_method=None),
                               FACTORS[2]: v.rolling(3).mean() / v.rolling(20).mean().replace(0, np.nan),
                               "liquidity": (c * v).rolling(20).mean()})
        values.loc[:119, :] = np.nan
        values.index = frame.date
        values = values.reindex(dates)
        raw[:, :, j] = values[FACTORS].to_numpy().T
        liquidity[:, j] = values.liquidity
        prices[:, j] = pd.Series({r["date"]: r["close"] for r in histories[code]}).reindex(dates)
    cutoff = np.nanquantile(np.where(liquidity > 0, liquidity, np.nan), .3, axis=1)
    eligible = (liquidity > 0) & (liquidity >= cutoff[:, None])
    ranks = [pd.DataFrame(np.where(eligible, values, np.nan)).rank(axis=1, pct=True).to_numpy() for values in raw]
    score = ranks[0] + ranks[1] - ranks[2]
    states = market_states(histories, set(dates))
    observations, baskets, latest_scores = {}, {}, {}
    names = {stock["code"]: stock.get("name", stock["code"]) for stock in stocks}
    for i, day in enumerate(dates):
        valid = np.flatnonzero(np.isfinite(score[i]))
        selected = valid[np.argsort(-score[i, valid], kind="stable")[:3]] if len(valid) >= 20 else []
        entries = [{"code": codes[j], "name": names[codes[j]], "rank": rank + 1,
                    "score": round(float((ranks[0][i, j] + ranks[1][i, j] + 1 - ranks[2][i, j]) / 3 * 100), 2)}
                   for rank, j in enumerate(selected)]
        baskets[day] = entries if gate_open(states[day]) else []
        observations[day] = [(codes[j], {"score": float(score[i, j]), "__liquidity": 1},
                              tuple(float(x) if np.isfinite(x) else None for x in prices[i:i + 4, j]))
                             for j in selected] if gate_open(states[day]) else []
        if i == len(dates) - 1:
            latest_scores = {codes[j]: {"score": round(float((ranks[0][i, j] + ranks[1][i, j] + 1 - ranks[2][i, j]) / 3 * 100), 2),
                                       "breakdown": [{"key": key, "name": name, "direction": direction,
                                                      "score": round(float((ranks[k][i, j] if k < 2 else 1 - ranks[k][i, j]) * 100), 1)}
                                                     for k, (key, name, direction) in enumerate(zip(FACTORS, NAMES, DIRECTIONS))]}
                             for j in valid}
    quotes = {d: {codes[j]: float(prices[i, j]) for j in range(len(codes)) if np.isfinite(prices[i, j])}
              for i, d in enumerate(dates)}
    prepared = {"observations": observations, "quotes": quotes}

    def evaluate(wanted, offset, holding=1, cohorts=1):
        trades, curve = [], []
        daily, _ = api.execution_daily_series(prepared, [("score", 1)], dates=wanted, min_cross=3,
                                              entry_offset=offset, holding_days=holding, capital_cohorts=cohorts,
                                              trade_log=trades, account_curve=curve)
        result = api.series_stats(daily) or {}
        closed = [trade for trade in trades if trade["status"] == "closed"]
        result.update(win_rate=sum(t["return"] > 0 for t in closed) / len(closed) if closed else None,
                      win_rate_basis="成本后已平仓三只组合", closed_rounds=len(closed),
                      winning_rounds=sum(t["return"] > 0 for t in closed),
                      decision_rate=sum(bool(observations[day]) for day in wanted) / len(wanted),
                      cost_bps=15, entry_offset=offset, holding_days=holding,
                      capital_cohorts=holding if cohorts is None else cohorts,
                      start_date=wanted[0], end_date=wanted[-1], universe=len(codes),
                      latest_holdings=baskets[dates[-1]])
        indexes = {day: i for i, day in enumerate(wanted)}
        result["delayed_exit_rounds"] = sum(indexes[t["exit_date"]] - indexes[t["entry_date"]] > holding for t in closed)
        return apply_return_reporting(result, curve)

    # Freeze the original experiment boundaries even as new history arrives.
    splits = {"train": [d for d in dates if d <= "2025-11-10"],
              "validation": [d for d in dates if "2025-11-14" <= d <= "2026-04-22"],
              "test": [d for d in dates if "2026-04-28" <= d <= "2026-10-09"],
              "forward": [d for d in dates if d > "2026-10-09"]}
    segments = {name: evaluate(wanted, 0) for name, wanted in splits.items() if len(wanted) >= 20}
    segments["next_close_control"] = evaluate(splits["test"], 1) if len(splits["test"]) >= 20 else {}
    row = {**specification(), "kind": "行情择时策略", "names": list(NAMES), "rank": None,
           "rank_metric": "固定研究策略，不参与 Sharpe 排名", "research_only": True,
           "result": evaluate(dates, 0), "segments": segments,
           "warning": "同收盘价近似；最终日线在尾盘下单时不可得；标准化份额，非实盘收益。"}
    return {"version": VERSION, "row": row, "baskets": baskets, "states": states,
            "data_date": dates[-1], "scores": latest_scores, "latest_candidates": entries,
            "gate_open": gate_open(states[dates[-1]])}
