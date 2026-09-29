#!/usr/bin/env python3
"""Bulk and incremental cache sync for the chief-opinions MCP server."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
import argparse
import json
import os
import time

from multi_agent import (
    CHIEF_OPINIONS_DIR,
    CHIEF_OPINIONS_STOCKS_DIR,
    DEFAULT_CHIEF_OPINIONS_MCP_URL,
    _decode_mcp_records,
    _mcp_request,
    _now,
    _write_json,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_UNIVERSE_FILE = ROOT / "data" / "fixed_tech_universe.json"
INDEX_FILE = CHIEF_OPINIONS_DIR / "index.json"


def load_universe(path=DEFAULT_UNIVERSE_FILE):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    stocks = payload.get("stocks", [])
    result, seen = [], set()
    for item in stocks:
        code = str(item.get("code", "")).strip().zfill(6)
        if not code or code in seen:
            continue
        seen.add(code)
        result.append({"code": code, "name": item.get("name")})
    return result


def _read_cache(code):
    try:
        return json.loads((CHIEF_OPINIONS_STOCKS_DIR / f"{code}.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _is_fresh(payload, max_age_hours):
    if not payload or max_age_hours <= 0:
        return False
    try:
        fetched = datetime.strptime(payload["fetched_at"], "%Y-%m-%d %H:%M:%S")
    except (KeyError, TypeError, ValueError):
        return False
    return datetime.now() - fetched < timedelta(hours=max_age_hours)


def _fetch_one(stock, token, endpoint, limit, retries):
    last_error = None
    for attempt in range(retries + 1):
        try:
            result = _mcp_request("tools/call", {"name": "get_stock_chief_opinions",
                                  "arguments": {"stock": stock["code"], "limit": limit}}, token, endpoint)
            opinions = _decode_mcp_records(result)
            payload = {"code": stock["code"], "name": stock.get("name"), "fetched_at": _now(),
                       "requested_limit": limit, "opinion_count": len(opinions), "opinions": opinions}
            _write_json(CHIEF_OPINIONS_STOCKS_DIR / f"{stock['code']}.json", payload)
            return payload, None
        except Exception as exc:
            last_error = str(exc)
            if attempt < retries:
                time.sleep(min(2 ** attempt, 4))
    return None, last_error


def sync_universe(stocks, token, endpoint, limit=1, workers=2, max_age_hours=20, retries=2):
    started_at = _now()
    due, index_rows = [], {}
    for stock in stocks:
        cached = _read_cache(stock["code"])
        if _is_fresh(cached, max_age_hours):
            index_rows[stock["code"]] = _index_row(cached, None, "cached")
        else:
            due.append(stock)
    completed = 0
    print(f"Chief opinions sync: total={len(stocks)} due={len(due)} cached={len(stocks) - len(due)}", flush=True)
    with ThreadPoolExecutor(max_workers=max(1, min(16, workers))) as pool:
        futures = {pool.submit(_fetch_one, stock, token, endpoint, limit, retries): stock for stock in due}
        for future in as_completed(futures):
            stock = futures[future]
            payload, error = future.result()
            if payload is None:
                payload = _read_cache(stock["code"]) or {"code": stock["code"], "name": stock.get("name"), "opinions": []}
            index_rows[stock["code"]] = _index_row(payload, error, "failed" if error else "updated")
            completed += 1
            if completed % 25 == 0 or completed == len(due):
                failures = sum(1 for item in index_rows.values() if item["status"] == "failed")
                print(f"Progress {completed}/{len(due)}; failures={failures}", flush=True)
    ordered = {code: index_rows[code] for code in sorted(index_rows)}
    failed = sum(1 for item in ordered.values() if item["status"] == "failed")
    payload = {"started_at": started_at, "completed_at": _now(), "source": "chief-opinions MCP",
               "server_url": endpoint, "tool": "get_stock_chief_opinions", "universe_size": len(stocks),
               "requested_limit": limit, "max_age_hours": max_age_hours,
               "updated": sum(1 for item in ordered.values() if item["status"] == "updated"),
               "cached": sum(1 for item in ordered.values() if item["status"] == "cached"),
               "failed": failed, "stocks": ordered}
    _write_json(INDEX_FILE, payload)
    return payload


def _index_row(payload, error, status):
    latest = (payload.get("opinions") or [{}])[0]
    return {"name": payload.get("name"), "status": status, "fetched_at": payload.get("fetched_at"),
            "opinion_count": payload.get("opinion_count", len(payload.get("opinions", []))),
            "latest_publish_time": latest.get("publish_time_beijing"), "latest_title": latest.get("title"),
            "error": error}


def main():
    parser = argparse.ArgumentParser(description="Sync chief opinions for the fixed technology universe")
    parser.add_argument("--universe", default=str(DEFAULT_UNIVERSE_FILE))
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--max-age-hours", type=float, default=20)
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()
    token = os.environ.get("CHIEF_OPINIONS_TOKEN", "").strip()
    if not token:
        raise SystemExit("CHIEF_OPINIONS_TOKEN is required")
    endpoint = os.environ.get("CHIEF_OPINIONS_MCP_URL", DEFAULT_CHIEF_OPINIONS_MCP_URL).strip()
    result = sync_universe(load_universe(args.universe), token, endpoint,
                           limit=max(1, min(10, args.limit)), workers=args.workers,
                           max_age_hours=max(0, args.max_age_hours), retries=max(0, min(5, args.retries)))
    print(json.dumps({key: result[key] for key in ("completed_at", "universe_size", "updated", "cached", "failed")},
                     ensure_ascii=False), flush=True)
    raise SystemExit(1 if result["failed"] else 0)


if __name__ == "__main__":
    main()
