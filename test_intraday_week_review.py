from datetime import date, timedelta
import unittest

import intraday_week_review as review
import intraday_entry_review as entries
import server


def minutes(day):
    rows = []
    for hour, end in ((9, 60), (10, 60), (11, 35), (13, 60), (14, 55)):
        start = 35 if hour == 9 else 5 if hour == 13 else 0
        for minute in range(start, end, 5):
            rows.append(dict(date=day, time=f"{hour:02d}:{minute:02d}",
                             open=10, close=10, high=11, low=9, volume=100))
    return rows


class IntradayReviewTests(unittest.TestCase):
    def test_last_seven_counts_filled_baskets_not_calendar_days(self):
        dates = [f"D{i:03d}" for i in range(30)]
        signal_indexes = {0, 2, 5, 9, 12, 16, 21, 25}
        prepared = {"quotes": {d: {str(c): 10 for c in range(3)} for d in dates},
                    "observations": {d: [(str(c), {"score": c, "__liquidity": 1}, (10, 10)) for c in range(3)]
                                     if i in signal_indexes else [] for i, d in enumerate(dates)}}
        self.assertEqual(entries.last_entry_dates(server, prepared, dates), [dates[i] for i in sorted(signal_indexes)[-7:]])

    def test_sina_volume_is_normalized_from_shares_to_lots(self):
        raw = [{"day": f"{r['date']} {r['time']}:00", "open": "10", "high": "10", "low": "10", "close": "10", "volume": "10000"} for r in minutes("2026-10-09")]
        raw.append({"day": "2026-10-09 15:00:00", "open": "10", "high": "10", "low": "10", "close": "10", "volume": "10000"})
        result = entries.parse_sina(raw)
        self.assertEqual(result["closes"], {"2026-10-09": 10})
        self.assertEqual(result["snapshots"]["2026-10-09"]["volume"], 4600)

    def test_snapshot_excludes_everything_after_1450(self):
        rows = minutes("2026-10-09")
        rows += [dict(date="2026-10-09", time="15:00", open=10, close=999, high=999, low=1, volume=99999)]
        result = review.snapshot(rows, "2026-10-09")
        self.assertEqual(result["close"], 10)
        self.assertEqual(result["high"], 11)
        self.assertEqual(result["volume"], 4600)

    def test_missing_minute_bar_does_not_become_close_fallback(self):
        self.assertIsNone(review.snapshot(minutes("2026-10-09")[:-1], "2026-10-09"))

    def test_final_daily_bar_cannot_leak_into_1450_factors(self):
        history = [dict(date=(date(2026, 1, 1)+timedelta(days=i)).isoformat(),
                        close=10, open=10, high=10, low=10, volume=100) for i in range(120)]
        anchor = history[-1]["date"]
        bars = minutes("2026-10-09") + [dict(date=anchor, time="15:00", close=10)]
        a = review.causal_rows(history, bars, "2026-10-09")
        future = history + [dict(date="2026-10-09", close=99999), dict(date="2026-10-12", close=99999)]
        self.assertEqual(a, review.causal_rows(future, bars, "2026-10-09"))

    def test_next_trading_day_sell_and_independent_cash_reconciliation(self):
        dates = ["2026-10-09", "2026-10-12"]
        quotes = {dates[0]: {str(i): 10 for i in range(3)}, dates[1]: {str(i): 11 for i in range(3)}}
        prepared = {"quotes": quotes, "observations": {dates[0]: [(str(i), {"score": i, "__liquidity": 1}, (10, 11)) for i in range(3)], dates[1]: []}}
        fills, curve = [], []
        server.execution_daily_series(prepared, [("score", 1)], min_cross=3, entry_offset=0,
                                      holding_days=1, capital_cohorts=1, fill_log=fills, account_curve=curve)
        events, trades = review.audit_fills(fills, quotes, curve)
        self.assertEqual(len(trades), 3)
        self.assertTrue(all(t["buy_date"] == dates[0] and t["sell_date"] == dates[1] for t in trades))
        self.assertAlmostEqual(sum(t["pnl"] for t in trades), (curve[-1]["equity"]-1)*20000)
        self.assertAlmostEqual(events[-1]["cash_after"], curve[-1]["equity"]*20000)


if __name__ == "__main__":
    unittest.main()
