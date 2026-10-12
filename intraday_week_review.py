"""Read-only daily-history inputs; new, timestamped 14:50 replay artifacts."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
import json
import math
import csv
from pathlib import Path
from statistics import mean

import market_strategy

ROOT = Path(".cache/intraday_1450")


def parse_bars(rows):
    bars = []
    for row in rows:
        try:
            stamp = datetime.strptime(row[0], "%Y%m%d%H%M")
            values = [float(x) for x in row[1:6]]
            if not all(math.isfinite(x) for x in values) or min(values[:4]) <= 0 or values[4] < 0:
                continue
            bars.append(dict(date=stamp.strftime("%Y-%m-%d"), time=stamp.strftime("%H:%M"),
                             open=values[0], close=values[1], high=values[2], low=values[3], volume=values[4]))
        except (ValueError, TypeError, IndexError):
            continue
    return sorted(bars, key=lambda b: (b["date"], b["time"]))


def snapshot(rows, day):
    if isinstance(rows, dict):
        return rows["snapshots"].get(day)
    selected = [r for r in rows if r["date"] == day and r["time"] <= "14:50"]
    expected = {f"{hour:02d}:{minute:02d}" for hour, minutes in
                ((9, range(35, 60, 5)), (10, range(0, 60, 5)), (11, range(0, 35, 5)),
                 (13, range(5, 60, 5)), (14, range(0, 55, 5))) for minute in minutes}
    if {r["time"] for r in selected} != expected:
        return None
    return {"date": day, "open": selected[0]["open"], "close": selected[-1]["close"],
            "high": max(r["high"] for r in selected), "low": min(r["low"] for r in selected),
            "volume": sum(r["volume"] for r in selected)}


def causal_rows(history, minutes, day):
    partial = snapshot(minutes, day)
    previous = [r for r in history if r["date"] < day]
    if partial is None or len(previous) < 119:
        return None
    # Establish adjustment scale only from a prior day's raw minute close.
    references = ({d: p for d, p in minutes["closes"].items() if d < day} if isinstance(minutes, dict)
                  else {r["date"]: r["close"] for r in minutes if r["date"] < day and r["time"] == "15:00"})
    anchor = next((r for r in reversed(previous) if r["date"] in references), None)
    if anchor is None:
        return None
    scale = anchor["close"] / references[anchor["date"]]
    for bar in previous[-20:]:
        if bar["date"] in references and abs(bar["close"] / references[bar["date"]] / scale - 1) > .002:
            return None
    adjusted = {**partial, **{key: partial[key] * scale for key in ("open", "high", "low", "close")}}
    return previous + [adjusted]


def audit_fills(fills, quotes, curve, capital=20000):
    cash, positions, marks, events, trades = float(capital), {}, {}, [], []
    pending = {}
    for point in curve:
        day = point["date"]
        for fill in (f for f in fills if f["date"] == day):
            units, gross, fee = (fill[k] * capital for k in ("units", "gross", "fee"))
            item = {**fill, "units": units, "gross": gross, "fee": fee}
            if fill["side"] == "buy":
                cash -= gross + fee
                positions[fill["code"]] = units
                marks[fill["code"]] = fill["price"]
                pending[(fill["code"], day)] = item
            else:
                cash += gross - fee
                positions.pop(fill["code"])
                buy = pending.pop((fill["code"], fill["entry_date"]))
                pnl = gross - fee - buy["gross"] - buy["fee"]
                trades.append({"code": fill["code"], "buy_date": buy["date"], "sell_date": day,
                               "buy_price": buy["price"], "sell_price": fill["price"], "units": units,
                               "buy_cost": buy["gross"] + buy["fee"], "sell_net": gross - fee,
                               "fees": buy["fee"] + fee, "pnl": pnl,
                               "return": pnl / (buy["gross"] + buy["fee"]), "status": "closed"})
            item["cash_after"] = cash
            events.append(item)
        marks.update(quotes[day])
        equity = cash + sum(units * marks[code] for code, units in positions.items())
        if abs(equity - point["equity"] * capital) > 1e-7:
            raise AssertionError("Independent cash reconciliation failed")
    for (code, day), buy in pending.items():
        trades.append({"code": code, "buy_date": day, "sell_date": None, "buy_price": buy["price"],
                       "sell_price": None, "units": buy["units"], "buy_cost": buy["gross"] + buy["fee"],
                       "pnl": None, "return": None, "status": "open"})
    return events, trades


def latest():
    paths = sorted(ROOT.glob("*/report.json"))
    if not paths:
        return None
    return json.loads(paths[-1].read_text(encoding="utf-8"))


def build(api, stocks, reuse_run=None):
    import numpy as np
    import pandas as pd
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    folder = ROOT / stamp
    folder.mkdir(parents=True, exist_ok=False)
    histories, names = {}, {s["code"]: s.get("name", s["code"]) for s in stocks}
    for stock in stocks:
        rows, basis = api.read_kline_cache_full(stock["code"])
        if basis == api.ADJUSTMENT and len(rows) >= 120:
            histories[stock["code"]] = rows
    minutes, failures = {}, []

    def fetch(code):
        symbol = ("sh" if code.startswith("6") else "sz") + code
        url = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={symbol},m5,,320"
        saved = ROOT / reuse_run / f"{code}.json" if reuse_run else None
        raw = json.loads(saved.read_text(encoding="utf-8"))["raw"] if saved and saved.exists() else json.loads(api.http_text(url))
        rows = parse_bars(raw.get("data", {}).get(symbol, {}).get("m5", []))
        if not rows:
            raise ValueError("No valid minute bars")
        (folder / f"{code}.json").write_text(json.dumps({"url": url, "raw": raw}, ensure_ascii=False), encoding="utf-8")
        return code, rows

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(fetch, code): code for code in histories}
        for count, future in enumerate(as_completed(futures), 1):
            try:
                code, rows = future.result()
                minutes[code] = rows
            except Exception as exc:
                failures.append({"code": futures[future], "error": str(exc)})
            if count % 100 == 0:
                print(f"14:50 minute history: {count}/{len(histories)}", flush=True)
    today = datetime.now().strftime("%Y-%m-%d")
    cutoff_time = datetime.now().strftime("%H:%M")
    dates = sorted({r["date"] for rows in minutes.values() for r in rows
                    if r["time"] == "14:50" and (r["date"] < today or cutoff_time >= "14:50")})[-5:]
    if not dates:
        raise ValueError("No 14:50 snapshots available")
    observations, quotes, decisions = {}, {}, []
    for day in dates:
        prefix, values, raw_prices = {}, {}, {}
        for code, history in histories.items():
            rows = causal_rows(history, minutes.get(code, []), day)
            if rows is None:
                continue
            prefix[code] = rows
            closes, volumes = [r["close"] for r in rows], [r["volume"] for r in rows]
            if mean(volumes[-20:]) <= 0:
                continue
            values[code] = (closes[-1] / max(closes[-40:]) - 1,
                            closes[-1] / closes[-4] - 1,
                            mean(volumes[-3:]) / mean(volumes[-20:]),
                            mean([r["close"] * r["volume"] for r in rows[-20:]]))
            raw_prices[code] = snapshot(minutes[code], day)["close"]
        state = market_strategy.market_states(prefix, {day}).get(day, {})
        coverage = len(values) / len(histories)
        passes = market_strategy.gate_open(state) and coverage >= .95
        liquidities = sorted(v[3] for v in values.values())
        cutoff = float(np.quantile(liquidities, .3)) if liquidities else 0
        eligible = sorted(code for code, v in values.items() if v[3] >= cutoff)
        ranks = [pd.Series([values[code][j] for code in eligible], dtype=float).rank(method="average", pct=True).tolist() for j in range(3)]
        scored = [(mean((ranks[0][i], ranks[1][i], -ranks[2][i])), code) for i, code in enumerate(eligible)]
        top = [code for _, code in sorted(scored, key=lambda row: (-row[0], row[1]))[:3]]
        quotes[day] = raw_prices
        observations[day] = top if passes else []
        decisions.append({"date": day, "market": state, "coverage": coverage, "stocks": len(values),
                          "gate_open": passes, "top3": [{"code": c, "name": names[c], "price": raw_prices[c]} for c in top],
                          "reason": "行情通过" if passes else "分时覆盖不足" if coverage < .95 else "行情未通过，不开新仓"})
    prepared = {"quotes": quotes, "observations": {day: [(code, {"score": 3-i, "__liquidity": 1},
                 tuple(quotes[d].get(code) for d in dates[j:j+2])) for i, code in enumerate(observations[day])]
                 for j, day in enumerate(dates)}}
    week_start = (date.fromisoformat(dates[-1]) - timedelta(days=6)).isoformat()
    context_dates = dates
    dates = [day for day in dates if day >= week_start]
    decisions = [row for row in decisions if row["date"] in dates]
    price_checks = []
    for code in sorted({s["code"] for row in decisions for s in row["top3"]}):
        symbol = ("sh" if code.startswith("6") else "sz") + code
        try:
            url = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={symbol},m1,,320"
            raw = json.loads(api.http_text(url))
            (folder / f"{code}-m1.json").write_text(json.dumps({"url": url, "raw": raw}, ensure_ascii=False), encoding="utf-8")
            checks = {r["date"]: r["close"] for r in parse_bars(raw.get("data", {}).get(symbol, {}).get("m1", [])) if r["time"] == "14:50"}
            for day in dates:
                if code in quotes[day] and day in checks:
                    matched = abs(quotes[day][code] - checks[day]) < .005
                    price_checks.append({"code": code, "date": day, "m5_price": quotes[day][code], "m1_price": checks[day], "matched": matched})
                    if not matched:
                        raise ValueError("1-minute and 5-minute 14:50 prices disagree")
        except (KeyError, OSError, json.JSONDecodeError) as exc:
            price_checks.append({"code": code, "error": str(exc)})
    with (folder / "prices_1450.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "code", "name", "price_1450", "source", "eligible_for_factors"])
        for day in dates:
            for code, rows in sorted(minutes.items()):
                price = next((r["close"] for r in rows if r["date"] == day and r["time"] == "14:50"), None)
                if price is not None:
                    writer.writerow([day, code, names[code], price, "tencent_m5_close_1450", code in quotes[day]])
    curve, fills, rounds = [], [], []
    api.execution_daily_series(prepared, [("score", 1)], dates=dates, min_cross=3, holding_days=1,
                               entry_offset=0, capital_cohorts=1, account_curve=curve, fill_log=fills, trade_log=rounds)
    events, trades = audit_fills(fills, quotes, curve)
    for item in events + trades:
        item["name"] = names[item["code"]]
    known = curve[-1]["complete"]
    report = {"report_id": stamp, "strategy_id": "market-gated-1450-single-nextday-v1",
              "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "start_date": dates[0], "end_date": dates[-1],
              "calendar_week_start": week_start, "trading_days": len(dates), "initial_capital": 20000,
              "context_dates": context_dates,
              "holding_days": 1, "capital_cohorts": 1, "entry_time": "14:50", "exit_time": "14:50",
              "price_basis": "不复权五分钟线（截至14:50）；因子仅用14:50前的数据和以前日线",
              "decision_log": decisions, "fills": events, "trades": trades, "rounds": rounds,
              "curve": [{**p, "equity": p["equity"]*20000, "cash": p["cash"]*20000} for p in curve],
              "valuation_complete": known,
              "ending_equity": curve[-1]["equity"]*20000 if known else None,
              "pnl": (curve[-1]["equity"]-1)*20000 if known else None,
              "net_return": curve[-1]["equity"]-1 if known else None, "closed_rounds": sum(r["status"] == "closed" for r in rounds),
              "win_rate": mean([r["return"] > 0 for r in rounds if r["status"] == "closed"]) if any(r["status"] == "closed" for r in rounds) else None,
              "fetch_failures": failures, "cash_reconciled": True,
              "minute_price_checks": price_checks,
              "warnings": ["最近7自然日中的交易日；跨国庆休市，不把5个交易日冒称一周。",
                           "窗口起点从两万元现金开始，无窗口前持仓。",
                           "14:50五分钟线末价不是实际成交回报；不假设14:50以后价格可知。",
                           "标准化份额，未计整手、最低佣金、涨跌停可成交性；买卖各7.5bp。",
                           "无平仓交易时胜率不可计算，不能证明策略准确。"]}
    (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 14:50 最近一周逐笔核对（独立新报告）", "",
             f"最近7自然日 {week_start} 至 {dates[-1]}，{len(dates)} 个有数据交易日。",
             "规则：当日14:50买、下一交易日14:50卖；本金20000元，只持一组三只，卖完再买。", "",
             "| 日期 | 上涨比例 | 高于MA20 | 分时覆盖 | 决策 |", "|---|---:|---|---:|---|"]
    for row in decisions:
        lines.append(f"| {row['date']} | {row['market'].get('up_fraction', 0):.2%} | {row['market'].get('above_ma20')} | {row['coverage']:.2%} | {row['reason']} |")
    lines += ["", "| 股票 | 买入日14:50 | 买价 | 卖出日14:50 | 卖价 | 标准份额 | 总成本 | 净盈亏 |", "|---|---|---:|---|---:|---:|---:|---:|"]
    for t in trades:
        lines.append(f"| {t['name']} {t['code']} | {t['buy_date']} | {t['buy_price']} | {t['sell_date'] or '未到期/缺价'} | {t['sell_price']} | {t['units']:.4f} | {t.get('fees', 0):.2f} | {t['pnl']} |")
    if not trades:
        lines.append("| 无买入交易：行情门槛未通过，不能凭空生成交割单 | | | | | | | |")
    summary = f"期末模型资产 {report['ending_equity']:.2f} 元，盈亏 {report['pnl']:.2f} 元" if known else "期末持仓缺价，不报告净值和收益"
    lines += ["", summary + "；独立现金账核对通过。", ""] + report["warnings"]
    (folder / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("report_id", "start_date", "end_date", "pnl", "closed_rounds", "win_rate")}, ensure_ascii=False), flush=True)
    return report


if __name__ == "__main__":
    import argparse
    import server
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse-run")
    args = parser.parse_args()
    build(server, server.read_dashboard_cache()["stocks"], reuse_run=args.reuse_run)
