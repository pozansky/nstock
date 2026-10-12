"""Last seven actual single-basket entries, isolated from prior reports."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from statistics import mean
import csv
import json
import math

import intraday_week_review as helpers
import market_strategy

ROOT = Path(".cache/intraday_1450_entries")


def latest():
    files = sorted(ROOT.glob("*/report.json"))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def parse_sina(raw):
    rows = []
    for r in raw:
        try:
            stamp = datetime.strptime(r["day"], "%Y-%m-%d %H:%M:%S")
            values = {key: float(r[key]) for key in ("open", "high", "low", "close", "volume")}
            if not all(math.isfinite(v) for v in values.values()) or min(values[k] for k in ("open", "high", "low", "close")) <= 0:
                continue
            values["volume"] /= 100
            rows.append({"date": stamp.strftime("%Y-%m-%d"), "time": stamp.strftime("%H:%M"), **values})
        except (KeyError, ValueError, TypeError):
            continue
    rows.sort(key=lambda r: (r["date"], r["time"]))
    days = sorted({r["date"] for r in rows})
    return {"snapshots": {day: s for day in days if (s := helpers.snapshot(rows, day)) is not None},
            "closes": {r["date"]: r["close"] for r in rows if r["time"] == "15:00"}}


def last_entry_dates(api, prepared, dates, count=7):
    trades = []
    api.execution_daily_series(prepared, [("score", 1)], dates=dates, min_cross=3, holding_days=1,
                               entry_offset=0, capital_cohorts=1, trade_log=trades)
    return [t["entry_date"] for t in trades[-count:]]


def build(api, stocks, reuse_run=None):
    import numpy as np
    import pandas as pd
    folder = ROOT / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    folder.mkdir(parents=True, exist_ok=False)
    histories, names = {}, {s["code"]: s.get("name", s["code"]) for s in stocks}
    for stock in stocks:
        rows, basis = api.read_kline_cache_full(stock["code"])
        if basis == api.ADJUSTMENT and len(rows) >= 120:
            histories[stock["code"]] = rows
    minutes, failures = {}, []

    def fetch(code):
        symbol = ("sh" if code.startswith("6") else "sz") + code
        url = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20x/"
               f"CN_MarketData.getKLineData?symbol={symbol}&scale=5&ma=no&datalen=1970")
        saved = ROOT / reuse_run / f"{code}.json" if reuse_run else None
        raw = json.loads(saved.read_text(encoding="utf-8"))["raw"] if saved and saved.exists() else api.eastmoney_json(url, jsonp=True)
        result = parse_sina(raw)
        if not result["snapshots"]:
            raise ValueError("No complete 14:50 bars")
        (folder / f"{code}.json").write_text(json.dumps({"url": url, "raw": raw}, ensure_ascii=False), encoding="utf-8")
        return code, result

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(fetch, code): code for code in histories}
        for count, future in enumerate(as_completed(futures), 1):
            try:
                code, result = future.result()
                minutes[code] = result
            except Exception as exc:
                failures.append({"code": futures[future], "error": str(exc)})
            if count % 100 == 0:
                print(f"Long minute history {count}/{len(histories)}", flush=True)
    dates = sorted({d for result in minutes.values() for d in result["snapshots"]})
    quotes, observations, decisions = {}, {}, []
    for index, day in enumerate(dates):
        prefix, values, prices = {}, {}, {}
        for code, history in histories.items():
            rows = helpers.causal_rows(history, minutes.get(code, {"snapshots": {}, "closes": {}}), day)
            if rows is None:
                continue
            prefix[code] = rows
            closes, volumes = [r["close"] for r in rows], [r["volume"] for r in rows]
            if mean(volumes[-20:]) <= 0:
                continue
            values[code] = (closes[-1]/max(closes[-40:])-1, closes[-1]/closes[-4]-1,
                            mean(volumes[-3:])/mean(volumes[-20:]), mean([r["close"]*r["volume"] for r in rows[-20:]]))
            prices[code] = helpers.snapshot(minutes[code], day)["close"]
        state = market_strategy.market_states(prefix, {day}).get(day, {})
        coverage = len(values)/len(histories)
        passes = market_strategy.gate_open(state) and coverage >= .95
        cutoff = float(np.quantile([v[3] for v in values.values()], .3)) if values else 0
        codes = sorted(c for c, v in values.items() if v[3] >= cutoff)
        ranks = [pd.Series([values[c][i] for c in codes], dtype=float).rank(method="average", pct=True).tolist() for i in range(3)]
        scores = [(ranks[0][i]+ranks[1][i]-ranks[2][i], c) for i, c in enumerate(codes)]
        top = [c for _, c in sorted(scores, key=lambda r: (-r[0], r[1]))[:3]]
        quotes[day] = prices
        observations[day] = top if passes else []
        decisions.append({"date": day, "market": state, "coverage": coverage, "stocks": len(values), "gate_open": passes,
                          "top3": [{"code": c, "name": names[c], "price": prices[c]} for c in top],
                          "reason": "行情通过" if passes else "分时覆盖不足" if coverage < .95 else "行情未通过，不开新仓"})
        if index % 5 == 0:
            print(f"Reconstructing 14:50 {day} ({index+1}/{len(dates)})", flush=True)
    prepared = {"quotes": quotes, "observations": {day: [(code, {"score": 3-i, "__liquidity": 1},
                 tuple(quotes[d].get(code) for d in dates[j:j+2])) for i, code in enumerate(observations[day])]
                 for j, day in enumerate(dates)}}
    entry_dates = last_entry_dates(api, prepared, dates)
    if not entry_dates:
        raise ValueError("No entries in available minute history; cannot fabricate seven trades")
    context_dates = dates
    dates = [d for d in dates if d >= entry_dates[0]]
    curve, fills, rounds = [], [], []
    api.execution_daily_series(prepared, [("score", 1)], dates=dates, min_cross=3, holding_days=1,
                               entry_offset=0, capital_cohorts=1, account_curve=curve, fill_log=fills, trade_log=rounds)
    if [t["entry_date"] for t in rounds] != entry_dates:
        raise AssertionError("Last-entry replay changed the selected entry schedule")
    events, trades = helpers.audit_fills(fills, quotes, curve)
    for item in events + trades:
        item["name"] = names[item["code"]]
    fields = ["code", "name", "buy_date", "buy_price", "sell_date", "sell_price", "units",
              "buy_cost", "sell_net", "fees", "pnl", "return", "status"]
    with (folder / "trades.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(trades)
    chosen = [r for r in decisions if r["date"] in entry_dates]
    groups = []
    for trade in rounds:
        constituents = [t for t in trades if t["buy_date"] == trade["entry_date"]]
        groups.append({"buy_date": trade["entry_date"], "sell_date": trade.get("exit_date"),
                       "codes": trade["codes"], "status": trade["status"], "return": trade.get("return"),
                       "pnl": sum(t["pnl"] for t in constituents) if all(t["pnl"] is not None for t in constituents) else None,
                       "stocks": len(constituents)})
    with (folder / "prices_1450.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "code", "name", "price_1450", "source"])
        for d in dates:
            for code, price in sorted(quotes[d].items()):
                writer.writerow([d, code, names[code], price, "sina_m5_close_1450"])
    known = curve[-1]["complete"]
    closed = [r for r in rounds if r["status"] == "closed"]
    report = {"mode": "last_entries", "requested_entries": 7, "actual_entries": len(rounds),
              "report_id": folder.name, "artifact_root": str(ROOT).replace('\\', '/'),
              "strategy_id": "market-gated-1450-single-nextday-v1", "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "start_date": dates[0], "end_date": dates[-1], "calendar_week_start": dates[0], "trading_days": len(dates),
              "initial_capital": 20000, "holding_days": 1, "capital_cohorts": 1, "entry_time": "14:50", "exit_time": "14:50",
              "price_basis": "新浪不复权五分钟线14:50末价；仅用14:50前数据选股，成交价为模型假设",
              "decision_log": chosen, "latest_decision": decisions[-1], "all_decisions": decisions,
              "entry_log": groups, "fills": events, "trades": trades, "rounds": rounds,
              "curve": [{**r, "equity": r["equity"]*20000, "cash": r["cash"]*20000} for r in curve],
              "ending_equity": curve[-1]["equity"]*20000 if known else None,
              "pnl": (curve[-1]["equity"]-1)*20000 if known else None,
              "net_return": curve[-1]["equity"]-1 if known else None, "closed_rounds": len(closed),
              "win_rate": mean([r["return"] > 0 for r in closed]) if closed else None,
              "cash_reconciled": True, "fetch_failures": failures, "context_dates": context_dates,
              "warnings": ["按实际买入组倒数7次，不按7天计数；从第一组开始以两万元现金重新模拟。",
                           "标准化份额，未计整手、最低佣金、涨跌停成交；买卖各7.5bp。",
                           "五分钟线14:50末价非券商成交回报；不是完整实盘验证。",
                           "不同公开源可能有分钟边界或价格差异，本报告统一使用新浪，不拼接腾讯价格。",
                           "历史股池、因子已被筛选过；7组是小样本，不能据此承诺胜率。"]}
    if len(rounds) < 7:
        report["warnings"].insert(0, f"可用分时历史仅有{len(rounds)}次出手，不足7次，没有补造。")
    lines = ["# 最近7次实际出手 · 14:50独立新回放", "", "当日14:50买，下一交易日14:50卖；两万元只持一组三只。", "",
             "| 买入日 | 卖出日 | 股票数 | 整组净盈亏 | 整组收益 |", "|---|---|---:|---:|---:|"]
    for g in groups:
        lines.append(f"| {g['buy_date']} | {g['sell_date'] or '未平仓'} | {g['stocks']} | {g['pnl']} | {g['return']} |")
    lines += ["", "| 股票 | 买入日14:50 | 买价 | 卖出日14:50 | 卖价 | 标准份额 | 总成本 | 净盈亏 |", "|---|---|---:|---|---:|---:|---:|---:|"]
    for t in trades:
        lines.append(f"| {t['name']} {t['code']} | {t['buy_date']} | {t['buy_price']} | {t['sell_date'] or '未平仓'} | {t['sell_price']} | {t['units']:.4f} | {t.get('fees', 0):.2f} | {t['pnl']} |")
    lines += ["", f"净盈亏：{report['pnl']} 元；独立现金账核对通过。", ""] + report["warnings"]
    (folder / "report.md").write_text("\n".join(lines), encoding="utf-8")
    (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("report_id", "actual_entries", "closed_rounds", "pnl", "win_rate")}, ensure_ascii=False), flush=True)
    print(json.dumps(groups, ensure_ascii=False), flush=True)
    return report


if __name__ == "__main__":
    import argparse
    import server
    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse-run")
    args = parser.parse_args()
    build(server, server.read_dashboard_cache()["stocks"], args.reuse_run)
