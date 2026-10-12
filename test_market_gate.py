import unittest
from datetime import date, timedelta

import experiment_market_gate as experiment
import server


class MarketGateTests(unittest.TestCase):
    def histories(self, count=20, days=22):
        return {str(code).zfill(6): [{"date": (date(2026, 9, 1) + timedelta(days=i)).isoformat(),
                                    "close": 10 + i} for i in range(days)] for code in range(count)}

    def test_breadth_and_trend_use_past_prices(self):
        histories = self.histories()
        day = "2026-09-21"
        states = experiment.market_states(histories, {day})
        self.assertEqual(states[day]["up_fraction"], 1)
        self.assertTrue(states[day]["breadth_trend"])
        for rows in histories.values():
            rows[-1]["close"] = 1
        self.assertEqual(experiment.market_states(histories, {day}), states)

    def test_exactly_half_rising_does_not_pass(self):
        histories = self.histories(days=2)
        for code, rows in histories.items():
            if int(code) < 10:
                rows[1]["close"] = 9
        states = experiment.market_states(histories, {"2026-09-02"})
        self.assertEqual(states["2026-09-02"]["up_fraction"], .5)
        self.assertFalse(states["2026-09-02"]["breadth"])

    def test_insufficient_market_coverage_blocks_filter(self):
        states = experiment.market_states(self.histories(count=3, days=2), {"2026-09-02"})
        self.assertFalse(states["2026-09-02"]["breadth"])

    def test_filter_keeps_calendar_and_exact_baskets(self):
        histories = self.histories(count=3, days=4)
        calendar = [row["date"] for row in next(iter(histories.values()))]
        rounds = [{"data_date": day, "detail": [{"code": code} for code in histories]} for day in calendar]
        allowed = {calendar[0]}
        prepared = experiment.prepare_recorded_baskets(rounds, calendar, histories, allowed)
        self.assertEqual(sorted(prepared["observations"]), calendar)
        self.assertEqual([row[0] for row in prepared["observations"][calendar[0]]], list(histories))
        self.assertEqual(prepared["observations"][calendar[1]], [])
        result = experiment.evaluate(rounds, calendar, histories, allowed, holding=2, offset=0)
        self.assertEqual(result["trades"][0]["exit_date"], calendar[2])
        self.assertEqual(result["decision_rate"], .25)
        self.assertFalse(result["above_one_third"])

    def test_round_win_rate_excludes_pending_basket(self):
        histories = self.histories(count=3, days=3)
        calendar = [row["date"] for row in next(iter(histories.values()))]
        rounds = [{"data_date": day, "detail": [{"code": code} for code in histories]} for day in calendar]
        result = experiment.evaluate(rounds, calendar, histories, set(calendar), holding=1, offset=1)
        self.assertEqual(result["filled_rounds"], 2)
        self.assertEqual(result["closed_rounds"], 1)
        self.assertEqual(result["open_rounds"], 1)
        self.assertEqual(result["wins"], 1)

    def test_instrumentation_does_not_change_cash_curve(self):
        histories = self.histories(count=3, days=5)
        calendar = [row["date"] for row in next(iter(histories.values()))]
        rounds = [{"data_date": day, "detail": [{"code": code} for code in histories]} for day in calendar]
        prepared = experiment.prepare_recorded_baskets(rounds, calendar, histories, set(calendar))
        before, _ = server.execution_daily_series(prepared, [("rank", 1)], min_cross=3)
        trades = []
        after, _ = server.execution_daily_series(prepared, [("rank", 1)], min_cross=3, trade_log=trades)
        self.assertEqual(before, after)
        self.assertTrue(trades)


if __name__ == "__main__":
    unittest.main()
