"""Read-only market-gate experiment: freeze recorded baskets, compare cash versus buying."""

import argparse
import json
import math
from pathlib import Path
from statistics import mean

import server


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


def prepare_recorded_baskets(rounds, calendar, histories, allowed):
    prices = {code: {bar["date"]: bar["close"] for bar in rows} for code, rows in histories.items()}
    indexes = {day: i for i, day in enumerate(calendar)}
    observations = {day: [] for day in calendar}
    for row in rounds:
        day = row["data_date"]
        if day not in indexes or day not in allowed:
            continue
        items = (row.get("detail") or [])[:3]
        if len(items) != 3:
            continue
        for rank, item in enumerate(items):
            closes = tuple(prices.get(item["code"], {}).get(target) for target in calendar[indexes[day]:])
            observations[day].append((item["code"], {"rank": 3 - rank, "__liquidity": 1}, closes))
    quotes = {day: {code: values[day] for code, values in prices.items() if day in values} for day in calendar}
    return {"observations": observations, "quotes": quotes}


def evaluate(rounds, calendar, histories, allowed, holding, offset, capital=20000):
    prepared = prepare_recorded_baskets(rounds, calendar, histories, allowed)
    trades, curve = [], []
    daily, _ = server.execution_daily_series(prepared, [("rank", 1)], cost_rate=.0015,
                                             min_cross=3, holding_days=holding, entry_offset=offset,
                                             account_curve=curve, trade_log=trades)
    stats = server.series_stats(daily) or {}
    closed = [trade for trade in trades if trade["status"] == "closed"]
    n = len(closed)
    wins = sum(trade["return"] > 0 for trade in closed)
    # Wilson interval is descriptive; correlated trades make it optimistic.
    probability = wins / n if n else None
    interval = None
    if n:
        z = 1.96
        center = (probability + z * z / (2 * n)) / (1 + z * z / n)
        radius = z * math.sqrt(probability * (1 - probability) / n + z * z / (4 * n * n)) / (1 + z * z / n)
        interval = [round(center - radius, 4), round(center + radius, 4)]
    complete = bool(curve) and all(point["complete"] for point in curve)
    ending = curve[-1]["equity"] if curve else 1
    losses = -sum(min(trade["return"], 0) for trade in closed)
    return {"decision_days": len(allowed), "decision_rate": len(allowed) / len(calendar),
            "above_one_third": len(allowed) / len(calendar) > 1 / 3,
            "filled_rounds": len(trades), "fill_rate": len(trades) / len(calendar),
            "closed_rounds": n, "wins": wins, "open_rounds": len(trades) - n,
            "win_rate": probability, "win_rate_interval": interval,
            "mean_trade_return": mean([trade["return"] for trade in closed]) if closed else None,
            "profit_factor": sum(max(trade["return"], 0) for trade in closed) / losses if losses else None,
            "complete": complete, "pnl": round(capital * (ending - 1), 2) if complete else None,
            "ending_equity": round(capital * ending, 2) if complete else None,
            "max_drawdown": stats.get("max_drawdown") if complete else None,
            "trades": trades, "daily": curve, "allowed_dates": sorted(allowed)}


def run():
    dashboard = server.read_dashboard_cache() or {}
    review = server.recommendation_review_summary(server.load_recommendation_log(), dashboard)
    rounds = review["replay_rounds"]
    strategy = review["selected_strategy"]
    if not rounds or not strategy:
        raise ValueError("Current strategy has no recorded replay")
    histories, excluded = {}, []
    codes = {stock["code"] for stock in dashboard["stocks"]} | {item["code"] for row in rounds for item in row["detail"][:3]}
    for code in sorted(codes):
        rows, basis = server.read_kline_cache_full(code)
        if basis != server.ADJUSTMENT:
            excluded.append(code)
            continue
        rows = server.completed_daily_rows(rows)
        if rows:
            histories[code] = rows
    start = min(row["data_date"] for row in rounds)
    calendar = sorted({bar["date"] for rows in histories.values() for bar in rows if bar["date"] >= start})
    dates = set(calendar)
    states = market_states(histories, dates)
    decisions = {"baseline": dates, "breadth": {day for day in dates if states[day]["breadth"]},
                 "breadth_trend": {day for day in dates if states[day]["breadth_trend"]}}
    results = {mode: {name: evaluate(rounds, calendar, histories, allowed, strategy["holding_days"], offset)
                      for name, allowed in decisions.items()}
               for mode, offset in (("same_close_approximation", 0), ("next_close_control", 1))}
    return {"strategy": strategy, "start_date": calendar[0], "end_date": calendar[-1],
            "trading_days": len(calendar), "initial_capital": 20000,
            "thresholds": {"breadth": "up_fraction > 0.5", "breadth_trend": "up_fraction > 0.5 and equal_weight_index > MA20"},
            "price_basis": "qfq standardized units; not real whole-share fills", "cost_bps": 15,
            "market_universe": len(codes), "usable_history_count": len(histories), "excluded_basis": excluded,
            "market_states": states, "results": results,
            "warnings": ["Same-close approximation uses final daily data: not an executable intraday result.",
                         "Historical baskets and current universe retain selection/survivorship biases.",
                         "Only fixed rules are tested; this is not walk-forward model selection or proof of improvement.",
                         "No whole lots, minimum commission, slippage or limit-order fill model.",
                         "Missing qualified entry quotes remain cash; no replacement stocks.",
                         "Win rate is for closed model baskets after costs; open rounds are excluded.",
                         "Wilson interval ignores temporal dependence; short samples cannot establish an edge."]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=server.CACHE_DIR / "market_gate_experiment_20261012.json")
    args = parser.parse_args()
    result = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"strategy": result["strategy"], "dates": [result["start_date"], result["end_date"]]}, ensure_ascii=False))
    for mode, comparisons in result["results"].items():
        for name, row in comparisons.items():
            print(mode, name, json.dumps({key: value for key, value in row.items()
                                         if key not in ("trades", "daily", "allowed_dates")}, ensure_ascii=False))
    print("Saved", args.output)


if __name__ == "__main__":
    main()
