import unittest
from unittest.mock import patch

import server
from audit_market_timing import reconcile


def prepared(prices, signals=None):
    observations = {}
    for i, price in enumerate(prices):
        values = {"signal": 1, "__liquidity": 1}
        rows = [(f"S{code}", dict(values), tuple(prices[i:i + 4])) for code in range(3)]
        observations[f"D{i:03d}"] = rows if signals is None or i in signals else [(c, {**v, "signal": None}, p) for c, v, p in rows]
    return {"observations": observations}


class ExecutionTests(unittest.TestCase):
    def test_independent_cash_book_matches_all_holding_modes(self):
        data = prepared([10, 11, 9, 12, 10, 13])
        for rows in data["observations"].values():
            for _, values, _ in rows:
                values["score"] = values["signal"]
        dates = sorted(data["observations"])
        data["quotes"] = {day: {f"S{code}": price for code in range(3)}
                          for day, price in zip(dates, [10, 11, 9, 12, 10, 13])}
        for hold, cohorts in ((1, 1), (2, 2), (2, 1)):
            for offset in (0, 1):
                curve = []
                server.execution_daily_series(data, [("signal", 1)], min_cross=3, holding_days=hold,
                                              capital_cohorts=cohorts, entry_offset=offset, account_curve=curve)
                independent, _ = reconcile(data, dates, hold, cohorts, offset)
                for expected, actual in zip(curve, independent):
                    self.assertAlmostEqual(expected["equity"], actual)

    def test_single_cohort_waits_until_two_day_position_is_closed(self):
        trades = []
        server.execution_daily_series(prepared([10] * 6), [("signal", 1)], min_cross=3,
                                      holding_days=2, entry_offset=0, capital_cohorts=1, trade_log=trades)
        self.assertEqual([trade["entry_date"] for trade in trades], ["D000", "D002", "D004"])

    def test_cohort_count_must_be_positive_integer(self):
        for count in (0, -1, 1.5):
            with self.assertRaises(ValueError):
                server.execution_daily_series(prepared([10] * 4), [("signal", 1)], capital_cohorts=count)

    def test_signal_day_and_entry_day_have_no_pre_entry_gain(self):
        data = prepared([10, 100, 110, 110], signals={0})
        daily, _ = server.combo_daily_series(data, [("signal", 1)], cost_rate=0, min_cross=3, holding_days=1)
        self.assertEqual(daily[:2], [0, 0])
        self.assertAlmostEqual(daily[2], .1)

    def test_future_missing_exit_does_not_change_ranking(self):
        data = prepared([10, None, 12, 13])
        selected = server._select_top(data, [("signal", 1)], "D000", 3, 3, 1, 2)
        self.assertEqual(len(selected), 3)
        daily, _ = server.combo_daily_series(data, [("signal", 1)], cost_rate=0, min_cross=3)
        self.assertEqual(daily[1], 0)

    def test_round_trip_cost_is_charged_on_both_fills(self):
        data = prepared([10] * 4, signals={0})
        daily, _ = server.combo_daily_series(data, [("signal", 1)], min_cross=3, holding_days=1)
        self.assertEqual(daily[0], 0)
        self.assertLess(daily[1], 0)
        self.assertLess(daily[2], 0)
        self.assertEqual(daily[3], 0)

    def test_fixed_units_do_not_rebalance_every_day(self):
        data = prepared([10] * 4, signals={0})
        rows = data["observations"]["D000"]
        rows[0] = (rows[0][0], rows[0][1], (10, 10, 20, 20))
        rows[1] = (rows[1][0], rows[1][1], (10, 10, 10, 20))
        daily, _ = server.combo_daily_series(data, [("signal", 1)], cost_rate=0, min_cross=3, holding_days=2)
        # Half the capital is invested; the other phase remains cash.
        self.assertAlmostEqual(daily[2], 1 / 6)
        self.assertAlmostEqual(daily[3], 1 / 7)

    def test_gate_ranks_returns_not_price_levels(self):
        rows = [(str(i), {"signal": i}, (100-i, 100-i, (100-i)*(1+i/1000), (100-i)*(1+2*i/1000))) for i in range(40)]
        data = {"observations": {f"D{i:03d}": rows for i in range(43)}}
        result = server.window_factor_gate(data, ["signal"], sorted(data["observations"]))
        self.assertAlmostEqual(result["signal"]["mean_rank_ic"], 1)
        self.assertEqual(result["signal"]["sample_days"], 41)

    def test_empty_folds_are_cash_days_not_deleted(self):
        data = prepared([10] * 160)
        with patch.object(server, "window_factor_gate", return_value={}):
            result = server.walk_forward_backtest(data, ["signal"], train_days=120, test_days=40)
        self.assertEqual(result["out_of_sample_days"], 40)
        self.assertEqual(result["out_of_sample"]["cumulative_return"], 0)


if __name__ == "__main__":
    unittest.main()
