import unittest

import pandas as pd

from experiment_factor_search import passes, split_dates, stock_features, summarize


class FactorSearchTests(unittest.TestCase):
    def test_features_do_not_read_future(self):
        rows = [{"date": str(i), "open": 10 + i / 100, "close": 10 + i / 100 + (i % 7) / 100,
                 "high": 11 + i / 100, "low": 9 + i / 100, "volume": 100 + i % 13}
                for i in range(180)]
        original = stock_features(rows)
        changed = [dict(row) for row in rows]
        for row in changed[155:]:
            row.update(close=1000, volume=100000)
        pd.testing.assert_frame_equal(original.iloc[:155], stock_features(changed).iloc[:155])

    def test_warmup_is_not_tradable(self):
        rows = [{"date": str(i), "open": 10, "close": 10, "high": 11, "low": 9, "volume": 100}
                for i in range(140)]
        self.assertTrue(stock_features(rows).iloc[:120].isna().all().all())

    def test_split_purges_three_sessions(self):
        train, validation, test = split_dates(list(range(100)))
        self.assertEqual((train[-1], validation[0], validation[-1], test[0]), (56, 60, 76, 80))
        self.assertFalse(set(train) & set(test))

    def test_acceptance_is_not_just_high_win_rate(self):
        good = {"closed": 38, "win_rate": .6316, "fill_rate": .4, "decision_rate": .4,
                "pnl": 100, "missing_mark_days": 0, "partial_baskets": 0}
        self.assertTrue(passes(good))
        for key, value in (("closed", 29), ("win_rate", .6), ("fill_rate", 1 / 3),
                           ("decision_rate", 1 / 3), ("pnl", -1), ("missing_mark_days", 1),
                           ("partial_baskets", 1)):
            self.assertFalse(passes({**good, key: value}), key)

    def test_cash_days_remain_in_frequency_denominator(self):
        days = [str(i) for i in range(6)]
        observations = {day: [] for day in days}
        observations[days[0]] = [(code, {"score": 1, "__liquidity": 1}, (10, 11, 11, 11))
                                  for code in ("a", "b", "c")]
        prepared = {"observations": observations,
                    "quotes": {day: {code: 11 for code in ("a", "b", "c")} for day in days}}
        result = summarize(prepared, days, 1, 0)
        self.assertEqual(result["closed"], 1)
        self.assertEqual(result["wins"], 1)
        self.assertEqual(result["fill_rate"], 1 / 6)
        self.assertFalse(passes(result))


if __name__ == "__main__":
    unittest.main()
