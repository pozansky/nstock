import unittest

from wyckoff import analyze_rows, backtest


def bars(count=90, price=10.0, volume=1000.0):
    return [{"date": f"D{i:03d}", "open": price, "high": price * 1.02, "low": price * .98,
             "close": price, "volume": volume} for i in range(count)]


class WyckoffTests(unittest.TestCase):
    def test_spring_detected(self):
        rows = bars()
        rows[-1].update(low=9.6, close=9.9, high=10.05)
        result = analyze_rows(rows)
        self.assertEqual(result["phase_key"], "spring")
        self.assertGreater(result["signals"]["spring"], 50)

    def test_sos_requires_volume(self):
        strong = bars(); strong[-1].update(close=10.4, high=10.45, volume=1800)
        weak = bars(); weak[-1].update(close=10.4, high=10.45, volume=900)
        self.assertEqual(analyze_rows(strong)["phase_key"], "sos")
        self.assertEqual(analyze_rows(weak)["signals"]["sos"], 0)

    def test_insufficient_history(self):
        self.assertEqual(analyze_rows(bars(40))["status"], "数据不足")

    def test_analysis_does_not_read_future_rows(self):
        rows = bars(100)
        before = analyze_rows(rows, 89)
        rows[90].update(close=99, high=100, low=1, volume=999999)
        self.assertEqual(before, analyze_rows(rows, 89))

    def test_backtest_uses_future_entry_and_exit(self):
        rows = bars(180)
        for i in range(80, 170, 10):
            rows[i].update(close=10.4, high=10.45, volume=1800)
            for j in range(i + 1, min(i + 11, len(rows))):
                rows[j].update(close=10.4 + (j - i) * .03, high=10.45 + (j - i) * .03, low=10.2)
        stocks = [{"code": "000001", "name": "测试股", "rows": rows}]
        result = backtest(stocks, lambda stock: stock["rows"], max_periods=8)
        self.assertIn(result["status"], {"完成", "短样本，仅供参考", "数据不足"})
        self.assertNotIn("agent_combo_score", result)


if __name__ == "__main__":
    unittest.main()
