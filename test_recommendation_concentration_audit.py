import unittest

from audit_recommendation_concentration import return_based_gate


class ReturnBasedGateTests(unittest.TestCase):
    def prepared(self, scale=1):
        rows = [(str(i), {"signal": i},
                 (scale*(100-i), scale*(100-i), scale*(100-i)*(1+i/1000), scale*(100-i)*(1+2*i/1000)))
                for i in range(40)]
        return {"observations": {f"D{i:03d}": rows for i in range(43)}}

    def test_signal_direction_depends_on_returns_not_price_levels(self):
        prepared = self.prepared()
        result = return_based_gate(prepared, ["signal"], sorted(prepared["observations"]))
        self.assertAlmostEqual(result["signal"]["mean_rank_ic"], 1)
        self.assertEqual(result["signal"]["direction"], "\u6b63\u5411")

    def test_price_units_do_not_change_gate(self):
        first, second = self.prepared(), self.prepared(1000)
        a = return_based_gate(first, ["signal"], sorted(first["observations"]))
        b = return_based_gate(second, ["signal"], sorted(second["observations"]))
        self.assertAlmostEqual(a["signal"]["mean_rank_ic"], b["signal"]["mean_rank_ic"])

    def test_training_boundary_labels_are_purged(self):
        prepared = self.prepared()
        dates = sorted(prepared["observations"])
        for day in dates[-3:]:
            prepared["observations"][day] = [(str(i), {"signal": -i}, (1, 1 + i / 1000)) for i in range(40)]
        result = return_based_gate(prepared, ["signal"], dates)
        self.assertEqual(result["signal"]["sample_days"], 40)
        self.assertAlmostEqual(result["signal"]["mean_rank_ic"], 1)

    def test_missing_exits_do_not_become_target_prices(self):
        prepared = self.prepared()
        for day, rows in prepared["observations"].items():
            prepared["observations"][day] = [(code, values, (closes[0], None)) for code, values, closes in rows]
        self.assertEqual(return_based_gate(prepared, ["signal"], sorted(prepared["observations"])), {})


if __name__ == "__main__":
    unittest.main()
