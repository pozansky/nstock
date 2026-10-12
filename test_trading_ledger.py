import copy
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import trading_ledger as ledger


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.book = {"version": 1, "accounts": []}
        self.account = ledger.apply_action(self.book, {"action": "create", "request_id": "create0001"})
        self.counter = 0

    def act(self, action, **values):
        self.counter += 1
        return ledger.apply_action(self.book, {"action": action, "account_id": self.account,
                                              "request_id": f"request{self.counter:04d}", **values})

    def order(self, side="buy", quantity=100):
        self.act("order", code="000001", name="Test", side=side, quantity=quantity, signal_date="2026-01-02")
        return self.book["accounts"][0]["events"][-1]["id"]

    def fill(self, order, quantity=100, price=10, fees=5, day="2026-01-05"):
        self.act("fill", order_id=order, quantity=quantity, price=price, fees=fees, date=day)

    def summary(self, quotes=None):
        return ledger.summarize(self.book, quotes)["accounts"][0]

    def test_initial_capital_is_twenty_thousand(self):
        self.assertEqual(self.summary()["cash"], 20000)

    def test_unfilled_order_never_changes_cash_or_holdings(self):
        self.order()
        self.assertEqual(self.summary()["cash"], 20000)
        self.assertEqual(self.summary()["positions"], [])

    def test_partial_fill_records_only_filled_shares(self):
        order = self.order(quantity=300)
        self.fill(order)
        value = self.summary({"000001": {"price": 11, "date": "2026-01-05"}})
        self.assertEqual(value["cash"], 18995)
        self.assertEqual(value["positions"][0]["quantity"], 100)
        self.assertEqual(value["orders"][0]["status"], "partial")
        self.assertEqual(value["total_pnl"], 95)

    def test_sale_uses_cost_basis_and_cash_including_fees(self):
        self.fill(self.order())
        self.fill(self.order("sell"), price=12, day="2026-01-06")
        value = self.summary()
        self.assertEqual(value["cash"], 20190)
        self.assertEqual(value["realized_pnl"], 190)
        self.assertEqual(value["fees"], 10)
        self.assertEqual(value["positions"], [])

    def test_cannot_overdraw_account(self):
        order = self.order(quantity=2000)
        with self.assertRaises(ValueError):
            self.fill(order, quantity=2000)
        self.assertEqual(self.summary()["cash"], 20000)

    def test_cannot_sell_new_lot_same_day(self):
        self.fill(self.order())
        with self.assertRaises(ValueError):
            self.fill(self.order("sell"))

    def test_cannot_sell_without_holdings(self):
        with self.assertRaises(ValueError):
            self.order("sell")

    def test_buy_must_be_whole_lots(self):
        with self.assertRaises(ValueError):
            self.order(quantity=101)

    def test_same_day_signal_cannot_fill(self):
        with self.assertRaises(ValueError):
            self.fill(self.order(), day="2026-01-02")

    def test_future_fill_rejected(self):
        with self.assertRaises(ValueError):
            self.fill(self.order(), day=(date.today() + timedelta(days=1)).isoformat())

    def test_cancel_preserves_completed_part(self):
        order = self.order(quantity=200)
        self.fill(order)
        self.act("cancel", order_id=order)
        value = self.summary()
        self.assertEqual(value["orders"][0]["filled_quantity"], 100)
        self.assertEqual(value["orders"][0]["status"], "cancelled")
        with self.assertRaises(ValueError):
            self.fill(order)

    def test_missing_or_stale_quote_never_reports_zero_loss(self):
        self.fill(self.order())
        for quote in [{}, {"000001": {"price": 10, "date": "2026-01-02"}},
                      {"000001": {"price": 10, "date": "2026-01-05", "basis": "qfq"}}]:
            self.assertIsNone(self.summary(quote)["equity"])

    def test_request_retry_does_not_duplicate_fill(self):
        order = self.order()
        request = {"action": "fill", "account_id": self.account, "request_id": "retry0001", "order_id": order,
                   "quantity": 100, "price": 10, "fees": 5, "date": "2026-01-05"}
        ledger.apply_action(self.book, request)
        saved = copy.deepcopy(self.book)
        ledger.apply_action(self.book, request)
        self.assertEqual(self.book, saved)

    def test_persistence_roundtrip_and_corruption_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            ledger.save(self.book, path)
            self.assertEqual(ledger.load(path), self.book)
            path.write_text("broken", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                ledger.load(path)

    def test_invalid_money_rejected(self):
        for number in ["NaN", "Infinity", -1, "nonsense", "0.001"]:
            with self.assertRaises(ValueError):
                ledger.money(number, positive=True)


if __name__ == "__main__":
    unittest.main()
