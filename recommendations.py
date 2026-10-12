"""推荐留痕与事后归因（Recommendation logging & attribution）.

设计要点
--------
1. **留痕**：系统每输出一轮推荐，就按``kind + data_date``写一份快照（同一天重复运行
   只保留最后一次内容）。留痕只记录"当时到底推了什么"，不做事后修改。
2. **归因**：日线研究口径是"T 日收盘买入 → 下一交易日收盘卖出"。因此在
   ``data_date`` 之后必须重新读取同一批真实日线，回填各持有期的实际收益。
   未收盘/数据未到的快照保持 ``待回填``，绝不猜测。
3. **基准**：同一交易日、同一股池的等权收益，用于判断推荐是真有超额还是跟着市场漂。
4. 所有收益都扣除一次 ``COST_BPS`` 的成本假设，与回测口径保持一致。

对外只暴露纯函数 + 一个文件读写层，方便被 server.py 调用也方便单测。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "data" / "recommendations"
LOG_FILE = LOG_DIR / "log.json"

# 日线复盘只统计下一交易日退出；14:50 分时报告独立保存。
HORIZONS = (1,)
ENTRY_RULE = "T日尾盘收盘买入"
HORIZON_LABELS = {
    1: "下一交易日收盘卖出（日线近似）",
}
COST_BPS = 15
REVIEW_START_DATE = "2026-09-01"
# 成本敏感性档位：15bp 是回测口径，25/40bp 用于检验"成本假设偏乐观时还剩多少超额"
COST_LEVELS = (15, 25, 40)
TOP_N = 20
MAX_SNAPSHOTS = 400


# --------------------------------------------------------------------------- 文件层

def _atomic_write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, path)


def load_log(path=None):
    path = Path(path or LOG_FILE)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        payload = {}
    return {
        "version": 1,
        "snapshots": payload.get("snapshots") or {},
        "benchmarks": payload.get("benchmarks") or {},
    }


def save_log(log, path=None):
    snapshots = log.get("snapshots") or {}
    ordered = dict(sorted(snapshots.items(), reverse=True))
    _atomic_write(path or LOG_FILE, {
        "version": 1,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "benchmarks": log.get("benchmarks") or {},
        "snapshots": ordered,
    })


# --------------------------------------------------------------------------- 留痕

def snapshot_key(kind, data_date):
    return f"{data_date}|{kind}"


def make_snapshot(kind, data_date, entries, strategy=None, source=None, note=None, generated_at=None):
    return {
        "key": snapshot_key(kind, data_date),
        "kind": kind,
        "data_date": data_date,
        "generated_at": generated_at or time.strftime("%Y-%m-%d %H:%M:%S"),
        "strategy": strategy or {},
        "source": source,
        "note": note,
        "entries": entries,
        "attribution": None,
    }


def upsert(log, snapshot):
    """写入快照，内容未变化则返回 False（避免每次刷新都落盘）。

    标的名单一旦变化，旧的归因结果立即作废——否则会出现"用新名单配旧收益"的错配。
    """
    key = snapshot["key"]
    previous = (log.setdefault("snapshots", {})).get(key)
    if previous:
        existing = {k: v for k, v in previous.items() if k not in ("generated_at", "attribution")}
        incoming = {k: v for k, v in snapshot.items() if k not in ("generated_at", "attribution")}
        if existing == incoming:
            return False
        snapshot["attribution"] = previous.get("attribution")
        old_codes = [entry.get("code") for entry in previous.get("entries") or []]
        new_codes = [entry.get("code") for entry in snapshot.get("entries") or []]
        if old_codes != new_codes:
            snapshot["attribution"] = None
    log["snapshots"][key] = snapshot
    return True


def ranking_entries(stocks, use_combo, limit=TOP_N):
    """按驾驶舱实际展示的口径（同一股池、同一评分）取前 N 名。"""
    def score_of(stock):
        return stock.get("agent_combo_score") if use_combo else stock.get("score")

    dated = [(stock, score_of(stock)) for stock in stocks]
    latest = max((stock.get("completed_factor_date") or stock.get("last_date") or "" for stock in stocks), default="")
    usable = [(stock, score) for stock, score in dated
              if score is not None and (stock.get("completed_factor_date") or stock.get("last_date")) == latest]
    usable.sort(key=lambda item: item[1], reverse=True)
    entries = [{"code": stock.get("code"), "name": stock.get("name"), "rank": index + 1, "score": round(float(score), 2)}
               for index, (stock, score) in enumerate(usable[:limit])]
    return latest, entries


# --------------------------------------------------------------------------- 归因

def _index_rows(rows):
    return {row.get("date"): index for index, row in enumerate(rows or []) if row.get("date")}


def forward_returns(rows, data_date, horizons=HORIZONS):
    """T 日尾盘买入（以 T 日收盘价成交）→ 之后每个持有期的收益。数据不足返回 None。"""
    index = _index_rows(rows)
    start = index.get(data_date)
    if start is None:
        return None
    entry_index = start
    if entry_index >= len(rows):
        return None
    entry_close = float(rows[entry_index].get("close") or 0)
    if entry_close <= 0:
        return None
    returns, exits = {}, {}
    for horizon in horizons:
        exit_index = entry_index + horizon
        if exit_index < len(rows):
            exit_close = float(rows[exit_index].get("close") or 0)
            returns[horizon] = (exit_close / entry_close - 1) if exit_close > 0 else None
            exits[horizon] = {"date": rows[exit_index].get("date"), "close": round(exit_close, 4)} if exit_close > 0 else None
        else:
            returns[horizon] = None
            exits[horizon] = None
    latest_index = next((i for i in range(len(rows) - 1, start - 1, -1)
                         if float(rows[i].get("close") or 0) > 0), start)
    latest_close = float(rows[latest_index]["close"])
    return {"entry_date": rows[entry_index].get("date"), "entry_close": round(entry_close, 4),
            "latest_date": rows[latest_index].get("date"), "latest_close": round(latest_close, 4),
            "latest_return": round(latest_close / entry_close - 1, 5), "elapsed_days": latest_index - start,
            "returns": returns, "exits": exits}


def _clean_mean(values):
    usable = [value for value in values if value is not None]
    return round(sum(usable) / len(usable), 5) if usable else None


def attribute_entries(entries, history_loader, data_date, horizons=HORIZONS, cost_bps=COST_BPS):
    """把一批标的回填成组合级收益（等权，扣一次成本）。"""
    detail, per_horizon = [], {horizon: [] for horizon in horizons}
    for entry in entries or []:
        code = entry.get("code")
        result = forward_returns(history_loader(code) or [], data_date, horizons)
        if not result:
            detail.append({"code": code, "name": entry.get("name"), "status": "缺少行情"})
            continue
        for horizon in horizons:
            per_horizon[horizon].append(result["returns"].get(horizon))
        detail.append({
            "code": code,
            "name": entry.get("name"),
            "entry_date": result["entry_date"],
            "entry_close": result["entry_close"],
            "latest_date": result["latest_date"],
            "latest_close": result["latest_close"],
            "latest_return": result["latest_return"],
            "elapsed_days": result["elapsed_days"],
            "returns": {str(horizon): (round(value, 5) if value is not None else None)
                        for horizon, value in result["returns"].items()},
            # 存下每一档的退出价，事后任何一笔都能手算复核，而不是只给一个收益率
            "exits": {str(horizon): result["exits"].get(horizon) for horizon in horizons},
            "status": "已回填",
        })
    filled = sum(1 for item in detail if item["status"] == "已回填")
    if not filled:
        return None
    cost = cost_bps / 10000
    gross = {horizon: _clean_mean(values) if len(values) == len(entries or [])
             and all(value is not None for value in values) else None
             for horizon, values in per_horizon.items()}
    net = {horizon: (round(value - cost, 5) if value is not None else None) for horizon, value in gross.items()}
    # 只要有标的没回填上，就算持有期再长也不能宣称"已覆盖"——否则快照会被判定为
    # 已完成，等这只票的行情到齐后也不会再回来补算。
    complete = filled == len(entries or [])
    covered = max((horizon for horizon in horizons if all(value is not None for value in per_horizon[horizon])),
                  default=None) if complete else None
    if not complete:
        status = "部分回填"
    elif covered == max(horizons):
        status = "已回填"
    elif covered is None:
        status = "已建仓待退出"          # 建仓日已确定，卖出日还没到
    else:
        status = f"回填至 {covered} 日"
    return {
        "cost_bps": cost_bps,
        "entry_rule": ENTRY_RULE,
        "entry_count": filled,
        "total_count": len(entries or []),
        "gross": {str(horizon): gross[horizon] for horizon in horizons},
        "net": {str(horizon): net[horizon] for horizon in horizons},
        "covered_horizon": covered,
        "status": status,
        "detail": detail,
    }


def compute_benchmarks(data_dates, universe_codes, history_loader, horizons=HORIZONS):
    """同一交易日、同一股池的等权收益，作为"市场本身"的对照。"""
    wanted = sorted({date for date in data_dates if date})
    collected = {date: {horizon: [] for horizon in horizons} for date in wanted}
    for code in universe_codes:
        rows = history_loader(code) or []
        if not rows:
            continue
        for date in wanted:
            result = forward_returns(rows, date, horizons)
            if not result:
                continue
            for horizon in horizons:
                collected[date][horizon].append(result["returns"].get(horizon))
    return {date: {str(horizon): _clean_mean(values) for horizon, values in buckets.items()}
            for date, buckets in collected.items()}


def needs_attribution(snapshot):
    attribution = snapshot.get("attribution") or {}
    if not attribution:
        return True
    if attribution.get("entry_rule") != ENTRY_RULE:
        return True            # 交易口径变了，旧的收益口径必须作废重算
    return attribution.get("covered_horizon") != max(HORIZONS) or attribution.get("status") != "已回填"


def refresh(log, history_loader, universe_codes=(), horizons=HORIZONS, max_snapshots=None):
    """回填所有尚未完成的快照，并补齐缺失的基准。返回是否有改动。"""
    changed = False
    histories = {}
    def cached_loader(code):
        if code not in histories:
            histories[code] = history_loader(code) or []
        return histories[code]
    # Long-completed horizons still need their latest-price tracking updated.
    pending = [snap for _, snap in sorted(log.get("snapshots", {}).items(), reverse=True)][:max_snapshots]
    for snapshot in pending:
        attribution = attribute_entries((snapshot.get("entries") or [])[:3], cached_loader, snapshot.get("data_date"), horizons)
        if attribution and attribution != snapshot.get("attribution"):
            snapshot["attribution"] = attribution
            changed = True

    benchmarks = log.setdefault("benchmarks", {})
    missing = sorted({snap.get("data_date") for snap in log.get("snapshots", {}).values()
                      if snap.get("data_date") and (snap.get("data_date") not in benchmarks
                         or any(benchmarks[snap["data_date"]].get(str(h)) is None for h in horizons))})
    if missing and universe_codes:
        computed = compute_benchmarks(missing, universe_codes, cached_loader, horizons)
        for date, values in computed.items():
            if benchmarks.get(date) != values:
                benchmarks[date] = values
                changed = True
    return changed


# --------------------------------------------------------------------------- 汇总

def summarize(log, recent=None, horizons=HORIZONS, cost_levels=COST_LEVELS, start_date=REVIEW_START_DATE,
              strategy_id=None):
    all_snapshots = sorted((item for item in log.get("snapshots", {}).values()
                            if (item.get("data_date") or "") >= start_date),
                           key=lambda item: (item.get("data_date") or "", item.get("kind") or ""), reverse=True)
    snapshots = [item for item in all_snapshots if item.get("kind") != "replay"]
    if strategy_id is not None:
        all_snapshots = [item for item in all_snapshots if item.get("strategy_id") == strategy_id]
        snapshots = [item for item in all_snapshots if item.get("kind") != "replay"]
    benchmarks = log.get("benchmarks") or {}
    horizon_stats = {horizon: {"count": 0, "sum": 0.0, "wins": 0, "bench_sum": 0.0, "bench_count": 0}
                     for horizon in horizons}
    rounds, filled = [], 0
    # 成本敏感性：只对策略自身口径（持有 1 日）做，把每轮已扣成本的净收益还原成
    # 毛收益，再按不同成本档重算——用来回答"成本翻倍后还剩多少超额"。
    gross_1, bench_1 = [], []
    for snapshot in snapshots:
        attribution = snapshot.get("attribution") or {}
        net = attribution.get("net") or {}
        benchmark = benchmarks.get(snapshot.get("data_date")) or {}
        excess = {}
        for horizon in horizons:
            key = str(horizon)
            value, base = net.get(key), benchmark.get(key)
            excess[key] = round(value - base, 5) if (value is not None and base is not None) else None
        if net.get("1") is not None:
            filled += 1
            paid = attribution.get("cost_bps", COST_BPS) / 10000
            gross_1.append(net["1"] + paid)
            if benchmark.get("1") is not None:
                bench_1.append(benchmark["1"])
        for horizon in horizons:
            value = net.get(str(horizon))
            if value is None:
                continue
            bucket = horizon_stats[horizon]
            bucket["count"] += 1
            bucket["sum"] += value
            bucket["wins"] += 1 if value > 0 else 0
            base = benchmark.get(str(horizon))
            if base is not None:
                bucket["bench_sum"] += base
                bucket["bench_count"] += 1
        detail = attribution.get("detail") or []
        rounds.append({
            "key": snapshot.get("key"),
            "kind": snapshot.get("kind"),
            "data_date": snapshot.get("data_date"),
            "generated_at": snapshot.get("generated_at"),
            "strategy": (snapshot.get("strategy") or {}).get("name"),
            "note": snapshot.get("note"),
            "count": min(3, len(snapshot.get("entries") or [])),
            "strategy_id": snapshot.get("strategy_id"),
            "status": attribution.get("status") or "待回填",
            "covered_horizon": attribution.get("covered_horizon"),
            "entry_date": next((item.get("entry_date") for item in detail if item.get("entry_date")), None),
            "entry_close": next((item.get("entry_close") for item in detail if item.get("entry_close")), None),
            "legs": " · ".join(
                f"{item.get('name') or item.get('code')} {item.get('entry_date') or '—'} 买 {item.get('entry_close') or '—'}"
                f"{(item.get('exits') or {}).get('1') and ' → ' + str((item['exits']['1'] or {}).get('date')) + ' 卖 ' + str((item['exits']['1'] or {}).get('close')) or ''}"
                for item in detail),
            "net": net,
            "benchmark": benchmark,
            "excess": excess,
            "top_names": " / ".join((entry.get("name") or entry.get("code") or "") for entry in (snapshot.get("entries") or [])[:3]),
            "detail": _summary_detail(snapshot),
        })
    mean_benchmark = (sum(bench_1) / len(bench_1)) if bench_1 else None
    cost_sensitivity = []
    for bps in cost_levels:
        values = [gross - bps / 10000 for gross in gross_1]
        mean = (sum(values) / len(values)) if values else None
        cost_sensitivity.append({
            "bps": bps,
            "count": len(values),
            "mean": round(mean, 5) if mean is not None else None,
            "excess": (round(mean - mean_benchmark, 5) if (mean is not None and mean_benchmark is not None) else None),
        })
    horizons_out = []
    for horizon in horizons:
        bucket = horizon_stats[horizon]
        count = bucket["count"]
        horizons_out.append({
            "days": horizon,
            "label": HORIZON_LABELS.get(horizon, f"持有 {horizon} 日"),
            "count": count,
            "mean": round(bucket["sum"] / count, 5) if count else None,
            "win_rate": round(bucket["wins"] / count, 3) if count else None,
            "benchmark": round(bucket["bench_sum"] / bucket["bench_count"], 5) if bucket["bench_count"] else None,
            "excess": round((bucket["sum"] - bucket["bench_sum"]) / bucket["bench_count"], 5) if bucket["bench_count"] else None,
        })
    dated = [item.get("data_date") for item in snapshots if item.get("data_date")]
    replay_rounds = [{"key": snap.get("key"), "kind": "replay", "data_date": snap.get("data_date"),
                      "strategy": (snap.get("strategy") or {}).get("name"), "note": snap.get("note"),
                      "strategy_id": snap.get("strategy_id"),
                      "count": len(snap.get("entries") or []),
                      "gross": (snap.get("attribution") or {}).get("gross") or {},
                      "net": (snap.get("attribution") or {}).get("net") or {},
                      "status": (snap.get("attribution") or {}).get("status") or "待回填",
                      "detail": _summary_detail(snap)} for snap in all_snapshots if snap.get("kind") == "replay"]
    return {
        "start_date": start_date,
        "entry_rule": ENTRY_RULE,
        "replay_rounds": replay_rounds,
        "replay_count": len(replay_rounds),
        "replay_warning": "历史回放使用当前策略及当前股池，仅因子输入截断至当日；含策略选择与存活偏差，不是当时真实推荐，也不是样本外业绩。",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_rounds": len(snapshots),
        "filled_rounds": filled,
        "pending_rounds": len(snapshots) - filled,
        "first_date": min(dated) if dated else None,
        "latest_date": max(dated) if dated else None,
        "cost_bps": COST_BPS,
        "cost_sensitivity": cost_sensitivity,
        "horizons": horizons_out,
        "rounds": rounds[:recent],
    }


def _summary_detail(snapshot):
    attribution = snapshot.get("attribution") or {}
    indexed = {item.get("code"): item for item in attribution.get("detail") or []}
    cost = attribution.get("cost_bps", COST_BPS) / 10000
    result = []
    for entry in (snapshot.get("entries") or [])[:3]:
        item = {**entry, **indexed.get(entry.get("code"), {})}
        gross = item.get("returns") or {}
        item["net"] = {str(h): round(gross[str(h)] - cost, 5) if gross.get(str(h)) is not None else None
                       for h in HORIZONS}
        item.setdefault("status", "待回填")
        result.append(item)
    return result
