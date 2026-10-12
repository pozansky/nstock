"""Money-denominated strategy replay remains separate from the paper ledger."""

import unittest

import server


class CapitalReviewTests(unittest.TestCase):
    days = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"]
    codes = ["000001", "000002", "000003"]

    def simulate(self, prices, rounds=None, basis=None, holding=1):
        histories = {code: [{"date": day, "close": price} for day, price in zip(self.days, values)
                            if price is not None] for code, values in prices.items()}
        if rounds is None:
            rounds = [{"data_date": self.days[0], "detail": [{"code": code} for code in self.codes]}]
        return server.recommendation_capital_equity(rounds, holding, lambda code: histories.get(code, []),
                                                    lambda code: (basis or {}).get(code, "qfq"))

    def test_delayed_entry_and_money_not_signal_day_return(self):
        result = self.simulate({code: [10, 20, 22, 22] for code in self.codes})
        equity = 20000 * 1.1 * (1 - .00075) / (1 + .00075)
        self.assertAlmostEqual(result["periods"]["all"]["ending_equity"], round(equity, 2))
        self.assertAlmostEqual(result["periods"]["all"]["pnl"], round(equity - 20000, 2))
        self.assertEqual(result["daily"][0]["cash"], 20000)
        self.assertEqual(result["periods"]["1d"]["pnl"], 0)

    def test_short_period_uses_existing_account_balance(self):
        rounds = [{"data_date": day, "detail": [{"code": code} for code in self.codes]}
                  for day in self.days]
        result = self.simulate({code: [10, 10, 9, 10] for code in self.codes}, rounds)
        period = result["periods"]["1d"]
        self.assertEqual(period["starting_equity"], result["daily"][-2]["equity"])
        self.assertLess(period["starting_equity"], 20000)
        self.assertAlmostEqual(period["pnl"], period["ending_equity"] - period["starting_equity"], places=1)

    def test_unadjusted_candidate_stays_cash_not_replaced(self):
        result = self.simulate({code: [10, 10, 11, 11] for code in self.codes},
                               basis={"000001": "none"})
        self.assertEqual(result["excluded_basis"], ["000001"])
        self.assertEqual(result["missing_entries"], [{"signal_date": self.days[0], "code": "000001"}])
        self.assertAlmostEqual(result["daily"][1]["cash"], round(20000 / 3, 2))

    def test_missing_position_quote_does_not_publish_zero_pnl(self):
        result = self.simulate({"000001": [10, 10, None, None],
                               "000002": [10, 10, 11, 11], "000003": [10, 10, 11, 11]})
        self.assertFalse(result["periods"]["all"]["complete"])
        self.assertIsNone(result["periods"]["all"]["pnl"])

    def test_recovered_quote_can_exit_on_day_without_signal(self):
        result = self.simulate({"000001": [10, 10, None, 11],
                               "000002": [10, 10, 11, 11], "000003": [10, 10, 11, 11]})
        self.assertFalse(result["daily"][2]["complete"])
        self.assertTrue(result["daily"][3]["complete"])
        self.assertEqual(result["daily"][3]["cash"], result["daily"][3]["equity"])

    def test_empty_source_is_unavailable_not_fictitious_zero(self):
        self.assertEqual(self.simulate({}, rounds=[])["periods"], {})

    def test_two_day_holding_does_not_reuse_overlapping_capital(self):
        result = self.simulate({code: [10, 10, 10, 11] for code in self.codes}, holding=2)
        self.assertEqual(result["daily"][1]["cash"], 10000)
        expected = 10000 + 10000 * 1.1 * (1 - .00075) / (1 + .00075)
        self.assertAlmostEqual(result["periods"]["all"]["ending_equity"], round(expected, 2))


if __name__ == "__main__":
    unittest.main()
