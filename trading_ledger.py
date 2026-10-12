"""Local paper-trading ledger. No broker connectivity or order transmission."""

import copy
import json
import re
import threading
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path


LEDGER_FILE = Path(__file__).with_name("data") / "trading" / "ledger.json"
LOCK = threading.RLock()
CENT = Decimal("0.01")


def money(value, positive=False):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("金额必须是有效数字") from None
    if not number.is_finite() or number < 0 or number > Decimal("1000000000000") or (positive and number <= 0):
        raise ValueError("金额必须非负，价格和本金必须大于零")
    result = number.quantize(CENT, rounding=ROUND_HALF_UP)
    if positive and result <= 0:
        raise ValueError("金额精度为分，价格和本金不能舍入为零")
    return result


def trading_day(value):
    try:
        parsed = date.fromisoformat(str(value))
    except ValueError:
        raise ValueError("日期格式必须为 YYYY-MM-DD") from None
    if parsed > date.today():
        raise ValueError("不能记录未来成交")
    return parsed.isoformat()


def load(path=LEDGER_FILE):
    path = Path(path)
    if not path.exists():
        return {"version": 1, "accounts": []}
    # Corrupt storage must fail closed, never silently reset the user's account.
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("accounts"), list):
        raise ValueError("账本格式无效，已阻止写入")
    return value


def save(ledger, path=LEDGER_FILE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def find_account(ledger, account_id):
    account = next((item for item in ledger["accounts"] if item["id"] == account_id), None)
    if account is None:
        raise ValueError("找不到模拟账户")
    return account


def account_state(account):
    cash = money(account["initial_cash"], positive=True)
    holdings, orders = {}, {}
    realized, fees = Decimal(0), Decimal(0)
    for event in account["events"]:
        kind = event["kind"]
        if kind == "order":
            orders[event["id"]] = {**event, "filled_quantity": 0, "status": "pending"}
        elif kind == "cancel":
            orders[event["order_id"]]["status"] = "cancelled"
        elif kind == "fill":
            order = orders[event["order_id"]]
            quantity, price, fee = event["quantity"], money(event["price"], True), money(event["fees"])
            gross = price * quantity
            code = order["code"]
            holding = holdings.setdefault(code, {"code": code, "name": order["name"], "quantity": 0,
                                                  "cost": Decimal(0), "lots": []})
            holding["last_fill_date"] = event["date"]
            if order["side"] == "buy":
                cash -= gross + fee
                holding["quantity"] += quantity
                holding["cost"] += gross + fee
                holding["lots"].append({"date": event["date"], "quantity": quantity})
            else:
                basis = holding["cost"] * quantity / holding["quantity"]
                cash += gross - fee
                realized += gross - fee - basis
                holding["cost"] -= basis
                holding["quantity"] -= quantity
                remaining = quantity
                for lot in holding["lots"]:
                    if lot["date"] >= event["date"]:
                        continue
                    used = min(lot["quantity"], remaining)
                    lot["quantity"] -= used
                    remaining -= used
            fees += fee
            order["filled_quantity"] += quantity
            order["status"] = "filled" if order["filled_quantity"] == order["quantity"] else "partial"
    return cash, holdings, orders, realized, fees


def apply_action(ledger, request):
    action = request.get("action")
    token = str(request.get("request_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", token):
        raise ValueError("缺少有效的幂等请求编号")
    if action == "create":
        existing = next((item for item in ledger["accounts"] if item.get("request_id") == token), None)
        if existing:
            return existing["id"]
        cash = money(request.get("initial_cash", 20000), True)
        account = {"id": uuid.uuid4().hex, "request_id": token,
                   "name": str(request.get("name") or "两万元模拟账户")[:60],
                   "mode": "paper", "initial_cash": str(cash), "created_at": datetime.now().isoformat(timespec="seconds"),
                   "events": []}
        ledger["accounts"].append(account)
        return account["id"]
    account = find_account(ledger, request.get("account_id"))
    if any(event.get("request_id") == token for event in account["events"]):
        return account["id"]
    cash, holdings, orders, _, _ = account_state(account)
    event = {"id": uuid.uuid4().hex, "request_id": token, "recorded_at": datetime.now().isoformat(timespec="seconds")}
    if action == "order":
        code, side = str(request.get("code") or ""), request.get("side")
        quantity = request.get("quantity")
        if not re.fullmatch(r"\d{6}", code) or side not in {"buy", "sell"}:
            raise ValueError("股票代码必须为六位，方向必须是 buy 或 sell")
        if code.startswith(("688", "689")):
            raise ValueError("当前账本仅支持普通 A 股整手规则，不支持科创板")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("股数必须为正整数")
        if side == "buy" and quantity % 100:
            raise ValueError("当前普通 A 股模拟买入必须按 100 股整手")
        if side == "sell" and quantity > holdings.get(code, {}).get("quantity", 0):
            raise ValueError("卖出股数超过持仓")
        signal_date = trading_day(request.get("signal_date"))
        event.update(kind="order", code=code, name=str(request.get("name") or code)[:60], side=side,
                     quantity=quantity, signal_date=signal_date, strategy_id=str(request.get("strategy_id") or "manual")[:100])
    elif action in {"fill", "cancel"}:
        order = orders.get(request.get("order_id"))
        if not order or order["status"] not in {"pending", "partial"}:
            raise ValueError("订单不存在或已结束")
        event.update(kind=action, order_id=order["id"])
        if action == "fill":
            day = trading_day(request.get("date"))
            if date.fromisoformat(day).weekday() >= 5:
                raise ValueError("不能在周末记录 A 股模拟成交")
            last_day = max((item["date"] for item in account["events"] if item["kind"] == "fill"), default="")
            if day < last_day:
                raise ValueError("成交必须按日期顺序录入，不能插入历史成交改变已记账结果")
            if day <= order["signal_date"]:
                raise ValueError("收盘后信号最早在后续交易日成交")
            quantity = request.get("quantity")
            remaining = order["quantity"] - order["filled_quantity"]
            if isinstance(quantity, bool) or not isinstance(quantity, int) or not 0 < quantity <= remaining:
                raise ValueError("成交股数超过订单剩余数量")
            if order["side"] == "buy" and quantity % 100:
                raise ValueError("模拟买入成交必须按 100 股整手")
            price, fee = money(request.get("price"), True), money(request.get("fees"))
            if order["side"] == "buy" and price * quantity + fee > cash:
                raise ValueError("现金不足（含费用），不会透支或融资")
            if order["side"] == "sell":
                holding = holdings.get(order["code"], {})
                sellable = sum(lot["quantity"] for lot in holding.get("lots", []) if lot["date"] < day)
                if quantity > sellable:
                    raise ValueError("可卖股数不足，不能卖空或卖出当天买入的股票")
                if fee > price * quantity:
                    raise ValueError("卖出费用超过成交金额")
            event.update(date=day, quantity=quantity, price=str(price), fees=str(fee), source="manual_paper_fill")
    else:
        raise ValueError("不支持的账本操作")
    account["events"].append(event)
    return account["id"]


def summarize(ledger, quotes=None):
    quotes = quotes or {}
    accounts = []
    for account in ledger["accounts"]:
        cash, holdings, orders, realized, fees = account_state(account)
        positions, market_value, complete = [], Decimal(0), True
        for code, holding in holdings.items():
            if not holding["quantity"]:
                continue
            quote = quotes.get(code) or {}
            valid_quote = str(quote.get("date") or "") >= holding.get("last_fill_date", "") and quote.get("basis", "spot") == "spot"
            price = money(quote["price"], True) if quote.get("price") and valid_quote else None
            value = price * holding["quantity"] if price is not None else None
            complete = complete and value is not None
            if value is not None:
                market_value += value
            positions.append({"code": code, "name": holding["name"], "quantity": holding["quantity"],
                              "cost": float(holding["cost"].quantize(CENT)),
                              "average_cost": float(holding["cost"] / holding["quantity"]),
                              "mark_price": float(price) if price else None, "mark_date": quote.get("date"),
                              "market_value": float(value) if value is not None else None,
                              "unrealized_pnl": float(value - holding["cost"]) if value is not None else None})
        equity = cash + market_value if complete else None
        initial = money(account["initial_cash"], True)
        accounts.append({"id": account["id"], "name": account["name"], "mode": "paper",
                         "initial_cash": float(initial), "cash": float(cash), "realized_pnl": float(realized),
                         "fees": float(fees), "positions": positions, "orders": list(orders.values()),
                         "events": copy.deepcopy(account["events"]), "valuation_complete": complete,
                         "equity": float(equity) if equity is not None else None,
                         "total_pnl": float(equity - initial) if equity is not None else None,
                         "return": float(equity / initial - 1) if equity is not None else None})
    return {"accounts": accounts, "default_initial_cash": 20000, "mode": "paper"}


def transact(request, quotes=None, path=LEDGER_FILE):
    with LOCK:
        ledger = load(path)
        apply_action(ledger, request)
        save(ledger, path)
        return summarize(ledger, quotes)
