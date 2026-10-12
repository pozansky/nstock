"""Read-only audit of cached Top-3 replays; no network or strategy mutation."""

import argparse
import itertools
import json
import math
import statistics
from collections import Counter, defaultdict
from statistics import mean
from unittest.mock import patch

import server
from recommendations import load_log


def return_based_gate(prepared, keys, dates, min_days=40):
    """Diagnostic replacement: purge boundary labels and rank forward returns."""
    values = defaultdict(list)
    purge = server.ENTRY_OFFSET + max(server.HOLDING_CHOICES)
    for day in dates[:-purge]:
        rows = prepared["observations"].get(day) or []
        for key in keys:
            pairs = [(row[1].get(key), server.window_return(row[2], server.ENTRY_OFFSET, server.DEFAULT_HOLDING_DAYS)) for row in rows]
            pairs = [(factor, target) for factor, target in pairs
                     if factor is not None and target is not None
                     and math.isfinite(factor) and math.isfinite(target)]
            if len(pairs) >= 20:
                values[key].append(server._correlation(server._rank([v for v, _ in pairs]),
                                                      server._rank([v for _, v in pairs])))
    result = {}
    for key, series in values.items():
        if len(series) < min_days:
            continue
        average, deviation = mean(series), statistics.pstdev(series)
        result[key] = {"mean_rank_ic": average, "icir": average / deviation if deviation else 0,
                       "sample_days": len(series), "direction": "\u6b63\u5411" if average >= 0 else "\u53cd\u5411"}
    return result


def conditional_walk_forward(rank=4):
    strategies, _ = server.recommendation_strategies({})
    selected = next(item for item in strategies if item["rank"] == rank)
    prepared = server.prepare_backtest_dataset(server.read_dashboard_cache()["stocks"], selected["factors"])
    with patch.object(server, "window_factor_gate", return_based_gate):
        result = server.walk_forward_backtest(prepared, selected["factors"])
    return {"result": result, "warning": "Conditional diagnostic only: the four candidate factors were selected globally; current universe, execution and empty-fold handling remain unchanged. Not a fully independent validation."}


def audit(rank=4):
    dashboard = server.read_dashboard_cache() or {}
    strategies, _ = server.recommendation_strategies(dashboard)
    strategy = next(item for item in strategies if item["rank"] == rank)
    log = load_log()
    summary = server.recommendation_review_summary(log, dashboard, strategy["id"])
    rounds = sorted(summary["replay_rounds"], key=lambda row: row["data_date"])
    if not rounds:
        return {"strategy": strategy, "status": "No saved replay rounds for this strategy yet"}
    baskets = {row["data_date"]: {item["code"] for item in row["detail"]} for row in rounds}
    names = {item["code"]: item.get("name") for row in rounds for item in row["detail"]}
    frequency = Counter(code for basket in baskets.values() for code in basket)
    streak, longest = Counter(), Counter()
    for basket in baskets.values():
        for code in frequency:
            streak[code] = streak[code] + 1 if code in basket else 0
            longest[code] = max(longest[code], streak[code])
    overlaps = [len(a & b) for a, b in zip(list(baskets.values()), list(baskets.values())[1:])]
    comparisons = []
    for other in strategies:
        other_baskets = {row["data_date"]: {item["code"] for item in row.get("entries", [])[:3]}
                         for row in log["snapshots"].values()
                         if row.get("kind") == "replay" and row.get("strategy_id") == other["id"]}
        common = sorted(set(baskets) & set(other_baskets))
        if common:
            comparisons.append({"rank": other["rank"], "factors": other["factors"],
                                "holding_days": other["holding_days"], "days": len(common),
                                "same_basket_days": sum(baskets[d] == other_baskets[d] for d in common),
                                "mean_overlap": mean(len(baskets[d] & other_baskets[d]) / 3 for d in common)})

    # Attribute the daily NAV used by the page, not overlapping full-capital trades.
    curve = summary["portfolio_equity"]["replay"]["daily"]
    calendar = [point["date"] for point in curve]
    positions = {day: i for i, day in enumerate(calendar)}
    prices = {code: {bar["date"]: bar["close"] for bar in server.recommendation_history_loader(code)}
              for code in frequency}
    contribution_by_day = defaultdict(Counter)
    holding = strategy["holding_days"]
    for row in rounds:
        i = positions[row["data_date"]]
        contribution_by_day[calendar[i]]["cost"] -= .0015 / holding
        for step in range(1, holding + 1):
            if i + step >= len(calendar):
                break
            before, after = calendar[i + step - 1], calendar[i + step]
            moves = {item["code"]: prices[item["code"]].get(after) / prices[item["code"]][before] - 1
                     for item in row["detail"]
                     if prices[item["code"]].get(before) and prices[item["code"]].get(after)}
            if len(moves) != 3:
                raise ValueError("Incomplete basket: attribution would be misleading")
            for code, move in moves.items():
                contribution_by_day[after][code] += move / (3 * holding)
    contributions, previous_nav = Counter(), 1.0
    for point in curve:
        pieces = contribution_by_day[point["date"]]
        if abs(sum(pieces.values()) - point["return"]) > 1e-7:
            raise AssertionError("Daily attribution does not reconcile with page")
        for code, value in pieces.items():
            contributions[code] += previous_nav * value
        previous_nav *= 1 + point["return"]
    assert abs(sum(contributions.values()) - (previous_nav - 1)) < 1e-6

    usable = server.prepare_backtest_universe(dashboard.get("stocks") or [])
    validation = {item["key"]: item for item in (server.read_factor_agent_run() or {}).get("validation", {}).get("results", [])}
    keys = strategy["factors"]
    correlations, persistence = defaultdict(list), defaultdict(list)
    previous_columns, evidence, mismatches = None, [], []
    for day in baskets:
        section = [(code, rows, index[day]) for code, rows, index in usable if day in index and index[day] >= 120]
        liquidities = {code: server.liquidity_value(rows, i) for code, rows, i in section}
        cutoff = server.liquidity_cutoff(liquidities.values())
        section = [row for row in section if liquidities[row[0]] >= cutoff]
        columns = {}
        for key in keys:
            direction = 1 if validation.get(key, {}).get("direction") == "\u6b63\u5411" else -1
            values = [(code, server._factor_value(rows, i, key)) for code, rows, i in section]
            values = [(code, value * direction) for code, value in values if value is not None and math.isfinite(value)]
            ranks = server._rank([value for _, value in values])
            columns[key] = {code: (r - 1) / max(1, len(ranks) - 1) * 100 for (code, _), r in zip(values, ranks)}
        for a, b in itertools.combinations(keys, 2):
            common = sorted(set(columns[a]) & set(columns[b]))
            correlations[a + " vs " + b].append(server._correlation([columns[a][c] for c in common], [columns[b][c] for c in common]))
        if previous_columns:
            for key in keys:
                common = sorted(set(columns[key]) & set(previous_columns[key]))
                persistence[key].append(server._correlation([columns[key][c] for c in common], [previous_columns[key][c] for c in common]))
        previous_columns = columns
        scores = {code: mean(col[code] for col in columns.values() if code in col) for code, _, _ in section
                  if any(code in col for col in columns.values())}
        ranked = sorted(scores, key=lambda code: (-scores[code], code))
        if set(ranked[:3]) != baskets[day]:
            mismatches.append(day)
        evidence.append({"date": day, "universe": len(section), "top": [
            {"code": code, "name": names.get(code), "score": scores[code],
             "factor_percentiles": {key: col.get(code) for key, col in columns.items()}}
            for code in ranked[:4]]})

    # Vary price levels while keeping a perfectly ordered return signal.
    gate_rows = [(str(i), {"signal": i}, (100-i, 100-i, (100-i)*(1+i/1000), (100-i)*(1+2*i/1000))) for i in range(40)]
    gate_data = {"observations": {f"D{i:03d}": gate_rows for i in range(43)}}
    gate = server.window_factor_gate(gate_data, ["signal"], sorted(gate_data["observations"]))
    correct_ic = server._correlation(server._rank([row[1]["signal"] for row in gate_rows]),
                                    server._rank([server.window_return(row[2], server.ENTRY_OFFSET, server.DEFAULT_HOLDING_DAYS) for row in gate_rows]))
    backtest = server.read_backtest_run() or {}
    oos = backtest.get("out_of_sample") or {}
    settled = [row for row in rounds if row["net"].get(str(holding)) is not None]
    return {
        "strategy": strategy, "directions": {key: validation.get(key, {}).get("direction") for key in keys},
        "start": calendar[0], "end": calendar[-1], "rounds": len(rounds),
        "slots": sum(frequency.values()), "unique_stocks": len(frequency),
        "adjacent_mean_retained": mean(overlaps), "identical_adjacent_baskets": sum(n == 3 for n in overlaps),
        "adjacent_pairs": len(overlaps), "top5_slot_share": sum(n for _, n in frequency.most_common(5)) / sum(frequency.values()),
        "stocks": [{"code": c, "name": names[c], "appearances": frequency[c], "longest_streak": longest[c],
                    "nav_contribution": contributions[c]} for c, _ in frequency.most_common()],
        "cost_nav_contribution": contributions["cost"], "net_return": previous_nav - 1,
        "settled": len(settled), "wins": sum(row["net"][str(holding)] > 0 for row in settled),
        "comparisons": comparisons, "factor_correlations": {key: mean(v) for key, v in correlations.items()},
        "rank_persistence": {key: mean(v) for key, v in persistence.items()},
        "recomputed_basket_mismatches": mismatches, "daily_factor_evidence": evidence,
        "gate_reproduction": {"engine_signal_ic": gate["signal"]["mean_rank_ic"], "correct_return_ic": correct_ic},
        "cached_oos": {"generated_at": backtest.get("generated_at"), "metrics": oos.get("out_of_sample"),
                       "fold_count": oos.get("fold_count"), "note": oos.get("note")},
        "warnings": ["Current universe and globally selected factors/directions are not an independent sample.",
                     "Pre-v12 OOS used an incorrect target; only results matching the current engine may be considered.",
                     "NAV contributions reconcile with the existing daily engine; they do not validate its execution assumptions."]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rank", type=int, default=4)
    parser.add_argument("--walk-forward", action="store_true")
    args = parser.parse_args()
    result = conditional_walk_forward(args.rank) if args.walk_forward else audit(args.rank)
    print(json.dumps(result, ensure_ascii=True, indent=2))
