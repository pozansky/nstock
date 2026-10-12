"""Research-only causal factor search. Never writes production strategy caches."""

import argparse
import itertools
import json
import math
from collections import Counter

import numpy as np
import pandas as pd

import server
from experiment_market_gate import market_states

VERSION = "factor-search-v1"
OUTPUT = server.CACHE_DIR / "factor_search_20261012.json"


def stock_features(rows):
    frame = pd.DataFrame(rows)
    c = frame.close.astype(float)
    v = frame.volume.astype(float).fillna(0)
    h, low = frame.high.astype(float), frame.low.astype(float)
    r = c.pct_change(fill_method=None)
    features = {}
    for w in (3, 5, 10, 20, 40, 60, 120):
        features[f"momentum_{w}"] = c.pct_change(w, fill_method=None)
        features[f"volatility_{w}"] = r.rolling(w).std(ddof=0)
        features[f"drawdown_{w}"] = c / c.rolling(w).max() - 1
    for fast, slow in ((3, 20), (3, 120), (5, 20), (10, 60), (10, 120), (20, 60)):
        features[f"ma_{fast}_{slow}"] = c.rolling(fast).mean() / c.rolling(slow).mean() - 1
        features[f"volume_ratio_{fast}_{slow}"] = v.rolling(fast).mean() / v.rolling(slow).mean().replace(0, np.nan)
    spread = (h - low).replace(0, np.nan)
    features["close_location"] = (c - low) / spread
    features["intraday_return"] = c / frame.open.astype(float) - 1
    # New hypotheses use trailing bars only; both directions are training candidates.
    features["new_efficiency20"] = c.diff(20) / c.diff().abs().rolling(20).sum().replace(0, np.nan)
    features["new_repair5"] = features["momentum_5"] * (-features["drawdown_40"])
    features["new_risk_adjusted10"] = features["momentum_10"] / features["volatility_20"].replace(0, np.nan)
    features["new_acceleration"] = features["momentum_5"] - features["momentum_20"] / 4
    features["new_volume_divergence"] = features["momentum_5"] * (features["volume_ratio_5_20"] - 1)
    features["new_close_pressure5"] = (features["close_location"] * 2 - 1).rolling(5).mean()
    features["new_downside20"] = r.clip(upper=0).pow(2).rolling(20).mean().pow(.5)
    features["new_range_contraction"] = (spread / c).rolling(3).mean() / (spread / c).rolling(20).mean()
    features["new_pullback_trend"] = -features["momentum_3"] * features["ma_20_60"].clip(lower=0)
    features["new_price_volume_corr20"] = r.rolling(20).corr(v.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan))
    values = pd.DataFrame(features).replace([np.inf, -np.inf], np.nan)
    values["liquidity"] = (c * v).rolling(20).mean()
    values.loc[:119, :] = np.nan
    values.index = frame.date
    return values


def split_dates(dates):
    a, b = int(len(dates) * .6), int(len(dates) * .8)
    # Three-session purge prevents overlapping outcome labels across boundaries.
    return dates[:a - 3], dates[a:b - 3], dates[b:]


def passes(row, minimum=30):
    return (row["closed"] >= minimum and row["win_rate"] > .6
            and row["fill_rate"] > 1 / 3 and row["decision_rate"] > 1 / 3
            and row["pnl"] is not None and row["pnl"] > 0 and row["missing_mark_days"] == 0
            and row["partial_baskets"] == 0)


def priority(row):
    if row["fill_rate"] <= 1 / 3 or row["closed"] < 30:
        return (-1, -1, -1)
    return (row["win_rate"], row["profit_factor"] or 0, row["estimated_mark_pnl"])


def summarize(prepared, dates, holding, offset, cost=.0015):
    trades, curve = [], []
    daily, _ = server.execution_daily_series(
        prepared, [("score", 1)], dates=dates, cost_rate=cost, min_cross=3,
        holding_days=holding, entry_offset=offset, trade_log=trades, account_curve=curve)
    closed = [trade for trade in trades if trade["status"] == "closed"]
    wins = sum(trade["return"] > 0 for trade in closed)
    n = len(closed)
    losses = -sum(min(t["return"], 0) for t in closed)
    gains = sum(max(t["return"], 0) for t in closed)
    p = wins / n if n else 0
    z = 1.96
    denominator = 1 + z * z / n if n else 1
    center = (p + z * z / (2 * n)) / denominator if n else 0
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator if n else 0
    days = len(dates)
    missing = sum(not point["complete"] for point in curve)
    estimated_pnl = round(20000 * (curve[-1]["equity"] - 1), 2)
    counts = Counter(code for trade in closed for code in trade["codes"])
    losses_sorted = sorted((-t["return"] for t in closed if t["return"] < 0), reverse=True)
    return {"days": days, "closed": n, "wins": wins, "win_rate": p,
            "wilson_interval": [center - radius, center + radius] if n else None,
            "decision_rate": sum(bool(prepared["observations"][d]) for d in dates) / days,
            "fill_rate": len(trades) / days, "open": len(trades) - n,
            "partial_baskets": sum(len(t["codes"]) != 3 for t in trades),
            "pnl": estimated_pnl if not missing else None,
            "estimated_mark_pnl": estimated_pnl,
            "profit_factor": gains / losses if losses else None,
            "average_trade_return": sum(t["return"] for t in closed) / n if n else None,
            "max_drawdown": (server.series_stats(daily) or {}).get("max_drawdown") if not missing else None,
            "missing_mark_days": missing,
            "unique_stocks": len(counts), "most_selected": counts.most_common(10),
            "top_stock_basket_fraction": max(counts.values(), default=0) / n if n else None,
            "worst_five_loss_fraction": sum(losses_sorted[:5]) / losses if losses else None,
            "closed_trades": closed}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reclassify", action="store_true", help="Change acceptance labels without retuning on the test")
    args = parser.parse_args()
    if args.reclassify:
        payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
        classify(payload)
        OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": payload["status"], "criteria": payload["criteria"]}, ensure_ascii=False))
        return
    dashboard = server.read_dashboard_cache() or {}
    histories, excluded = {}, []
    for stock in sorted(dashboard["stocks"], key=lambda s: s["code"]):
        rows, basis = server.read_kline_cache_full(stock["code"])
        rows = server.completed_daily_rows(rows)
        if basis == server.ADJUSTMENT and len(rows) >= 122:
            histories[stock["code"]] = rows
        else:
            excluded.append(stock["code"])
    dates = sorted({r["date"] for rows in histories.values() for r in rows[120:]})
    codes = sorted(histories)
    train, validation, test = split_dates(dates)
    states = market_states(histories, set(dates))
    fingerprint = server.kline_data_fingerprint(dashboard["stocks"])
    matrix_path = OUTPUT.with_suffix(".npz")
    metadata_path = OUTPUT.with_suffix(".matrix.json")
    names = list(stock_features(next(iter(histories.values()))).columns[:-1])
    metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
    if (matrix_path.exists() and metadata.get("fingerprint") == fingerprint
            and metadata.get("version") == VERSION and metadata.get("factors") == names
            and metadata.get("dates") == dates and metadata.get("codes") == codes):
        with np.load(matrix_path) as cache:
            ranks, quotes_array = cache["ranks"], cache["quotes"]
        print("Reused causal matrix", ranks.shape, flush=True)
    else:
        raw = np.full((len(names), len(dates), len(codes)), np.nan)
        liquidity = np.full((len(dates), len(codes)), np.nan)
        quotes_array = np.full_like(liquidity, np.nan)
        for j, code in enumerate(codes):
            frame = stock_features(histories[code]).reindex(dates)
            raw[:, :, j] = frame[names].to_numpy().T
            liquidity[:, j] = frame.liquidity.to_numpy()
            quotes_array[:, j] = pd.Series({r["date"]: r["close"] for r in histories[code]}).reindex(dates)
            if j % 150 == 0:
                print("Features", j, "/", len(codes), flush=True)
        thresholds = np.nanquantile(np.where(liquidity > 0, liquidity, np.nan), .3, axis=1)
        eligible = (liquidity > 0) & (liquidity >= thresholds[:, None])
        ranks = np.stack([pd.DataFrame(np.where(eligible, values, np.nan)).rank(axis=1, pct=True).to_numpy()
                          for values in raw])
        np.savez_compressed(matrix_path, ranks=ranks, quotes=quotes_array)
        metadata_path.write_text(json.dumps({"fingerprint": fingerprint, "version": VERSION,
                                             "factors": names, "dates": dates, "codes": codes}))
    quotes = {d: {code: float(quotes_array[i, j]) for j, code in enumerate(codes)
                  if np.isfinite(quotes_array[i, j])} for i, d in enumerate(dates)}
    gates = [(0, False)] + [(threshold, trend) for threshold in (.35, .45, .5, .55) for trend in (False, True)]
    baskets = {}

    def prepare(combo, gate):
        identity = tuple(combo)
        if identity not in baskets:
            score = sum(ranks[names.index(key)] * direction for key, direction in combo)
            observations = {}
            for i, d in enumerate(dates):
                finite = np.flatnonzero(np.isfinite(score[i]))
                selected = finite[np.argsort(-score[i, finite], kind="stable")[:3]] if len(finite) >= 20 else []
                observations[d] = [(codes[j], {"score": float(score[i, j]), "__liquidity": 1},
                                    tuple(float(x) if np.isfinite(x) else None for x in quotes_array[i:i + 4, j]))
                                   for j in selected]
            baskets[identity] = observations
        threshold, trend = gate
        observations = {d: items if states[d]["valid"] and (not threshold or states[d]["up_fraction"] > threshold)
                        and (not trend or states[d]["above_ma20"]) else []
                        for d, items in baskets[identity].items()}
        return {"observations": observations, "quotes": quotes}

    trials, seen = [], set()

    def search(combos):
        for combo in combos:
            combo = tuple(sorted(combo))
            if combo in seen:
                continue
            seen.add(combo)
            for gate in gates:
                prepared = prepare(combo, gate)
                for holding in (1,):
                    result = summarize(prepared, train, holding, 0)
                    result.pop("closed_trades")
                    trials.append({"combo": combo, "gate": gate, "holding": holding, "train": result})
            if len(seen) % 20 == 0:
                print("Training candidates", len(seen), "parameter trials", len(trials), flush=True)

    search([((key, sign),) for key in names for sign in (1, -1)])
    seeds = []
    for row in sorted(trials, key=lambda row: priority(row["train"]), reverse=True):
        if row["combo"] not in seeds:
            seeds.append(row["combo"])
        if len(seeds) == 12:
            break
    search([left + right for left, right in itertools.combinations(seeds, 2) if left[0][0] != right[0][0]])
    pairs = []
    for row in sorted(trials, key=lambda row: priority(row["train"]), reverse=True):
        if len(row["combo"]) == 2 and row["combo"] not in pairs:
            pairs.append(row["combo"])
        if len(pairs) == 8:
            break
    search([pair + seed for pair in pairs for seed in seeds if seed[0][0] not in {key for key, _ in pair}])
    shortlist = sorted(trials, key=lambda row: priority(row["train"]), reverse=True)[:40]
    for row in shortlist:
        row["validation"] = summarize(prepare(row["combo"], row["gate"]), validation, row["holding"], 0)
        row["validation"].pop("closed_trades")
    finalists = sorted(shortlist, key=lambda row: priority(row["validation"]), reverse=True)[:10]
    # Choose once on validation, before inspecting the final segment.
    selected = finalists[0]
    for row in finalists:
        prepared = prepare(row["combo"], row["gate"])
        row["test_same_close"] = summarize(prepared, test, row["holding"], 0)
        row["test_next_close"] = summarize(prepared, test, row["holding"], 1)
        row["test_stress_30bp"] = summarize(prepared, test, row["holding"], 1, .003)
        row["full_history_same_close"] = summarize(prepared, dates, row["holding"], 0)
        row["passes_same_close"] = passes(row["test_same_close"])
        row["passes_next_close"] = passes(row["test_next_close"])
    payload = {"version": VERSION, "fingerprint": fingerprint, "history_days": len(dates),
               "universe": len(codes), "excluded": excluded, "factors": names,
               "splits": {name: [values[0], values[-1], len(values)] for name, values in
                          (("train", train), ("validation", validation), ("test", test))},
               "combo_count": len(seen), "parameter_trials": len(trials), "train_trials": trials,
               "selected_before_test": selected, "finalists": finalists,
               "warnings": ["Final-close features plus same-close entry are a lookahead approximation.",
                            "Current stock universe has survivorship and selection bias.",
                            "Many hypotheses tested; selecting by the test destroys independence.",
                            "Fractional qfq units; no whole lots, minimum commission or limit fills.",
                            "Wilson intervals assume independence, unlike correlated stock baskets.",
                            "Full-history result is diagnostic, not independent verification."]}
    classify(payload)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Saved", OUTPUT, flush=True)
    for row in finalists:
        print(json.dumps({"combo": row["combo"], "gate": row["gate"], "holding": row["holding"],
                          "test": {k: v for k, v in row["test_same_close"].items() if k != "closed_trades"},
                          "passes": row["passes_same_close"]}, ensure_ascii=False), flush=True)


def classify(payload):
    payload["criteria"] = {"basket_win_rate_strictly_above": .6, "min_closed": 30,
                           "fill_rate_strictly_above": 1 / 3, "positive_pnl": True,
                           "missing_mark_days": 0, "partial_baskets": 0}
    for row in payload["finalists"] + [payload["selected_before_test"]]:
        row["passes_same_close"] = passes(row["test_same_close"])
        row["passes_next_close"] = passes(row["test_next_close"])
    selected = payload["selected_before_test"]
    payload["status"] = ("delayed_close_research_pass" if selected["passes_next_close"]
                         else "same_close_approximation_only" if selected["passes_same_close"] else "not_passed")


if __name__ == "__main__":
    main()
