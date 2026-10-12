"""Independently reconcile registered strategy cash/units and holding horizons."""

import json
import math
from pathlib import Path
from unittest.mock import patch

import market_strategy
import server


def reconcile(prepared, dates, holding, cohorts, offset, reinvest=True):
    initial = 1 / cohorts
    books = [{"cash": initial, "units": {}, "due": None} for _ in range(cohorts)]
    pending, curve, closed = {}, [], []
    for i, day in enumerate(dates):
        selected = server._select_top(prepared, [("score", 1)], day, 3, 3, offset, holding, True)
        if selected and i + offset < len(dates):
            pending[i + offset] = (i % cohorts, [row[0] for row in selected])
    for i, day in enumerate(dates):
        prices = prepared["quotes"].get(day, {})
        for book in books:
            for code, position in list(book["units"].items()):
                price = prices.get(code)
                if price is not None and math.isfinite(price) and price > 0:
                    position["mark"] = price
                    if i >= book["due"]:
                        book["cash"] += position["quantity"] * price * .99925
                        del book["units"][code]
            if "start_cash" in book and not book["units"]:
                closed.append({"entry": book.pop("entry"), "exit": day,
                               "return": book["cash"] / book.pop("start_cash") - 1,
                               "gain": book["cash"] - book.pop("before")})
        if i in pending:
            phase, codes = pending[i]
            book = books[phase]
            if not book["units"]:
                allocation = book["cash"] if reinvest else min(book["cash"], initial)
                budget = allocation / 3
                before = book["cash"]
                for code in codes:
                    price = prices.get(code)
                    if price is None or not math.isfinite(price) or price <= 0:
                        continue
                    book["units"][code] = {"quantity": budget / (price * 1.00075), "mark": price}
                    book["cash"] -= budget
                if book["units"]:
                    book.update(due=i + holding, start_cash=before, before=before, entry=day)
        curve.append(sum(book["cash"] + sum(p["quantity"] * p["mark"] for p in book["units"].values()) for book in books))
    return curve, closed


def main():
    captured = {}
    original = server.execution_daily_series

    def capture(prepared, factors, **kwargs):
        captured["prepared"] = prepared
        return original(prepared, factors, **kwargs)

    stocks = server.read_dashboard_cache()["stocks"]
    with patch.object(server, "execution_daily_series", side_effect=capture):
        payload = market_strategy.ensure(server, stocks, force=True)
    prepared = captured["prepared"]
    report = {"data_date": payload["data_date"], "checks": [], "warnings": [
        "Same-close selection uses finalized daily prices and cannot validate 14:40 live execution.",
        "Fixed fractional units: excludes whole-lot sizing, minimum commissions, price-limit fills and market impact.",
        "Universe and factor choice were selected retrospectively; full history is not an independent test."]}
    calendar = sorted(prepared["observations"])
    row = payload["row"]
    samples = [{"sample": "完整历史", **row["result"]}] + [
        {"sample": name, **result} for name, result in row["segments"].items() if result]
    for result in samples:
        dates = [d for d in calendar if result["start_date"] <= d <= result["end_date"]]
        args = (prepared, dates, result["holding_days"], result["capital_cohorts"], result["entry_offset"])
        curve, trades = reconcile(*args)
        expected = result["equity"]
        error = abs(curve[-1] - expected) if expected is not None else None
        if error is not None and error > 1e-8:
            raise AssertionError(f"Cash reconciliation failed: {error}")
        fixed_curve, _ = reconcile(*args, reinvest=False)
        report["checks"].append({**result, "independent_equity": curve[-1], "absolute_error": error,
            "fixed_initial_stake_return": fixed_curve[-1] - 1,
            "largest_round_returns": sorted(trades, key=lambda t: t["return"], reverse=True)[:5]})
    jumps = []
    by_code = {}
    for day in calendar:
        for code, price in prepared["quotes"][day].items():
            if code in by_code:
                previous_day, previous = by_code[code]
                change = price / previous - 1
                if abs(change) > .25:
                    jumps.append({"code": code, "from": previous_day, "to": day, "return": change})
            by_code[code] = (day, price)
    report["large_price_jumps_over_25pct"] = jumps
    target = Path(".cache/market_timing_audit.json")
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("sample offset hold cohorts win cumulative annual fixed-stake reconciliation-error")
    for r in report["checks"]:
        print(r["sample"], r["entry_offset"], r["holding_days"], r["capital_cohorts"],
              *(f"{r[k]:.4%}" for k in ("win_rate", "cumulative_return", "annual_return", "fixed_initial_stake_return")),
              r["absolute_error"])
    print("Price jumps >25%:", len(jumps), "Report:", target.resolve())


if __name__ == "__main__":
    main()
