from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import server
from server import _factor_value, build_factor_search_space, build_generated_factor_candidates


def make_rows(count=21, *, latest=None):
    rows = []
    for index in range(count):
        close = 10 + index * 0.01
        rows.append({
            "open": close - 0.02,
            "high": close + 0.10,
            "low": close - 0.10,
            "close": close,
            "volume": 100,
        })
    if latest:
        rows[-1].update(latest)
    return rows


class CandleVolumeFactorTests(unittest.TestCase):
    def test_new_factors_are_in_whitelist(self):
        keys = {key for key, _, _ in build_factor_search_space()}
        self.assertTrue({
            "body_strength", "upper_shadow_ratio", "lower_shadow_ratio",
            "engulfing_volume", "breakout_volume_confirm",
            "close_volume_pressure", "contraction_breakout", "cup_handle_breakout",
        }.issubset(keys))

    def test_body_and_wicks_are_directional(self):
        rows = make_rows(latest={"open": 10, "high": 12, "low": 9, "close": 11, "volume": 100})
        index = len(rows) - 1
        self.assertAlmostEqual(_factor_value(rows, index, "body_strength"), 1 / 3)
        self.assertAlmostEqual(_factor_value(rows, index, "upper_shadow_ratio"), 1 / 3)
        self.assertAlmostEqual(_factor_value(rows, index, "lower_shadow_ratio"), 1 / 3)

    def test_flat_candle_does_not_divide_by_zero(self):
        rows = make_rows(latest={"open": 10, "high": 10, "low": 10, "close": 10})
        index = len(rows) - 1
        for key in ("body_strength", "upper_shadow_ratio", "lower_shadow_ratio", "close_volume_pressure"):
            self.assertEqual(_factor_value(rows, index, key), 0.0)

    def test_bullish_engulfing_is_scaled_by_abnormal_volume(self):
        rows = make_rows(22)
        rows[-2].update({"open": 11, "high": 11.1, "low": 9.9, "close": 10})
        rows[-1].update({"open": 9.8, "high": 11.3, "low": 9.7, "close": 11.2, "volume": 250})
        self.assertAlmostEqual(_factor_value(rows, len(rows) - 1, "engulfing_volume"), 2.5)

    def test_breakout_is_amplified_by_volume(self):
        rows = make_rows(21, latest={"open": 10.9, "high": 11.2, "low": 10.8, "close": 11, "volume": 200})
        prior_high = max(row["high"] for row in rows[:-1])
        expected = (11 / prior_high - 1) * 2
        self.assertAlmostEqual(_factor_value(rows, len(rows) - 1, "breakout_volume_confirm"), expected)

    def test_cup_handle_requires_shape_breakout_and_volume(self):
        rows = make_rows(80)
        closes = []
        closes.extend([90 + index * (10 / 29) for index in range(30)])
        closes.extend([100 - index * (25 / 14) for index in range(1, 15)])
        closes.extend([75 + index * (15 / 6) for index in range(1, 7)])
        closes.extend([90 + index * (9 / 19) for index in range(20)])
        closes.extend([98, 97, 96, 95, 94, 95, 96, 97, 98])
        closes.append(101)
        self.assertEqual(len(closes), 80)
        for row, close in zip(rows, closes):
            row.update({"open": close - 0.2, "high": close + 0.4, "low": close - 0.4, "close": close, "volume": 100})
        rows[-1]["volume"] = 160
        score = _factor_value(rows, len(rows) - 1, "cup_handle_breakout")
        self.assertGreater(score, 0)
        rows[-1]["volume"] = 110
        self.assertEqual(_factor_value(rows, len(rows) - 1, "cup_handle_breakout"), 0.0)

    def test_generated_formula_is_compiled_and_evaluated(self):
        base = build_factor_search_space()
        specs = [{
            "name": "动量放量确认", "left": "momentum20", "operator": "multiply",
            "right": "volume_surprise20", "rationale": "趋势需要成交确认", "expectation": "正向",
        }]
        candidates, proposals = build_generated_factor_candidates(specs, base)
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0][0].startswith("gen__multiply__"))
        rows = make_rows(25, latest={"close": 12, "volume": 200})
        generated = _factor_value(rows, len(rows) - 1, candidates[0][0])
        expected = _factor_value(rows, len(rows) - 1, "momentum20") * _factor_value(rows, len(rows) - 1, "volume_surprise20")
        self.assertAlmostEqual(generated, expected)
        self.assertTrue(proposals[0]["generated"])


class IncrementalFactorCacheTests(unittest.TestCase):
    @staticmethod
    def stocks(count=25, days=40):
        return [{
            "code": f"{index:06d}",
            "name": f"测试{index}",
            "klines": [{
                "date": f"2026-01-{day + 1:02d}" if day < 31 else f"2026-02-{day - 30:02d}",
                "open": 10 + index * 0.01 + day * 0.02,
                "high": 10.2 + index * 0.01 + day * 0.02,
                "low": 9.8 + index * 0.01 + day * 0.02,
                "close": 10 + index * 0.01 + day * 0.02,
                "volume": 1000 + index * 10 + day,
            } for day in range(days)],
        } for index in range(count)]

    def test_reuses_all_dates_when_recent_inputs_are_unchanged(self):
        cache = {"version": server.FACTOR_DAILY_METRICS_VERSION, "entries": {}}
        candidates = [("momentum_3", "3日动量", "close / Ref(close, 3) - 1")]
        with patch.object(server, "read_kline_cache", return_value=[]):
            first = server.mine_factor_candidates(self.stocks(), candidates, cache, "screen")
            second = server.mine_factor_candidates(self.stocks(), candidates, cache, "screen")

        self.assertEqual(first["cache_hits"], 0)
        self.assertEqual(second["computed_points"], 0)
        self.assertEqual(second["cache_hits"], first["computed_points"])
        self.assertEqual(second["refreshed_dates"], [])

    def test_recomputes_only_recent_dates_whose_inputs_changed(self):
        cache = {"version": server.FACTOR_DAILY_METRICS_VERSION, "entries": {}}
        candidates = [("momentum_3", "3日动量", "close / Ref(close, 3) - 1")]
        stocks = self.stocks()
        with patch.object(server, "read_kline_cache", return_value=[]):
            first = server.mine_factor_candidates(stocks, candidates, cache, "screen")
            stocks[0]["klines"][-1]["close"] += 1
            second = server.mine_factor_candidates(stocks, candidates, cache, "screen")

        self.assertEqual(second["computed_points"], 1)
        self.assertEqual(second["cache_hits"], first["computed_points"] - 1)
        self.assertEqual(len(second["refreshed_dates"]), 1)

    def test_formula_change_invalidates_only_changed_factor(self):
        cache = {"version": server.FACTOR_DAILY_METRICS_VERSION, "entries": {}}
        stocks = self.stocks()
        original = [("momentum_3", "3日动量", "formula-v1")]
        changed = [("momentum_3", "3日动量", "formula-v2")]
        with patch.object(server, "read_kline_cache", return_value=[]):
            first = server.mine_factor_candidates(stocks, original, cache, "screen")
            second = server.mine_factor_candidates(stocks, changed, cache, "screen")

        self.assertEqual(second["cache_hits"], 0)
        self.assertEqual(second["computed_points"], first["computed_points"])
        self.assertEqual(len(cache["entries"]), 2)


class IncrementalBacktestCacheTests(unittest.TestCase):
    def test_exact_combinations_are_reused_without_preparing_matrix(self):
        factors = [
            {"key": "alpha", "name": "Alpha", "direction": "正向", "mean_rank_ic": .05, "gate": "通过"},
            {"key": "beta", "name": "Beta", "direction": "反向", "mean_rank_ic": -.04, "gate": "通过"},
        ]
        result = {"audited": True, "sharpe": 1.0, "cumulative_return": .1}
        previous = {
            "engine_version": server.BACKTEST_ENGINE_VERSION,
            "universe_signature": "same-universe",
            "data_date": "2026-09-29",
            "usable_universe": 25,
            "results": [
                {"factors": ["alpha"], "directions": ["正向"], "result": result},
                {"factors": ["beta"], "directions": ["反向"], "result": result},
                {"factors": ["alpha", "beta"], "directions": ["正向", "反向"], "result": result},
            ],
        }
        latest = {"generated_at": "now", "universe_signature": "same-universe", "validation": {"results": factors}}
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(server, "BACKTEST_RUN_FILE", Path(temporary) / "backtest.json"), \
             patch.object(server, "universe_signature_for_stocks", return_value="same-universe"), \
             patch.object(server, "latest_completed_data_date", return_value="2026-09-29"), \
             patch.object(server, "read_factor_agent_run", return_value=latest), \
             patch.object(server, "read_backtest_run", return_value=previous), \
             patch.object(server, "read_tech_config", return_value=[]), \
             patch.object(server, "prepare_backtest_dataset") as prepare:
            payload = server.run_backtest_experiment([{"code": "000001"}], force=True)

        prepare.assert_not_called()
        self.assertEqual(payload["reused_combination_count"], 3)
        self.assertEqual(payload["computed_combination_count"], 0)
        self.assertEqual(payload["usable_universe"], 25)

    def test_only_new_combination_builds_matrix_and_runs(self):
        factors = [
            {"key": "alpha", "name": "Alpha", "direction": "正向", "mean_rank_ic": .05, "gate": "通过"},
            {"key": "beta", "name": "Beta", "direction": "反向", "mean_rank_ic": -.04, "gate": "通过"},
        ]
        result = {"audited": True, "sharpe": 1.0, "cumulative_return": .1}
        previous = {
            "engine_version": server.BACKTEST_ENGINE_VERSION,
            "universe_signature": "same-universe",
            "data_date": "2026-09-29",
            "usable_universe": 25,
            "results": [
                {"factors": ["alpha"], "directions": ["正向"], "result": result},
                {"factors": ["beta"], "directions": ["反向"], "result": result},
            ],
        }
        latest = {"generated_at": "now", "universe_signature": "same-universe", "validation": {"results": factors}}
        prepared = {"usable": [("000001", [], {})], "observations": {}, "latest": [], "latest_data_date": "2026-09-29"}
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(server, "BACKTEST_RUN_FILE", Path(temporary) / "backtest.json"), \
             patch.object(server, "universe_signature_for_stocks", return_value="same-universe"), \
             patch.object(server, "latest_completed_data_date", return_value="2026-09-29"), \
             patch.object(server, "read_factor_agent_run", return_value=latest), \
             patch.object(server, "read_backtest_run", return_value=previous), \
             patch.object(server, "read_tech_config", return_value=[]), \
             patch.object(server, "prepare_backtest_dataset", return_value=prepared) as prepare, \
             patch.object(server, "backtest_factor_combo", return_value=result) as calculate:
            payload = server.run_backtest_experiment([{"code": "000001"}], force=True)

        prepare.assert_called_once()
        calculate.assert_called_once()
        self.assertEqual(payload["reused_combination_count"], 2)
        self.assertEqual(payload["computed_combination_count"], 1)


class FreshDataPipelineTests(unittest.TestCase):
    def test_intraday_bar_is_excluded_before_settlement(self):
        rows = [{"date": "2026-09-28", "close": 10}, {"date": "2026-09-29", "close": 11}]
        before_close = time.struct_time((2026, 9, 29, 14, 59, 0, 1, 272, -1))
        after_settlement = time.struct_time((2026, 9, 29, 15, 10, 0, 1, 272, -1))

        self.assertEqual(server.completed_daily_rows(rows, before_close), rows[:-1])
        self.assertEqual(server.completed_daily_rows(rows, after_settlement), rows)

    def test_agent_run_refreshes_dashboard_before_building_context(self):
        old = {"stocks": [{"code": "000001", "klines": [{"date": "2026-09-27", "close": 10}]}]}
        fresh = {"stocks": [{"code": "000001", "klines": [
            {"date": "2026-09-27", "close": 10}, {"date": "2026-09-28", "close": 11},
        ]}], "breadth": {}, "market": {}, "tech_universe": {"sectors": []}}
        captured = {}

        def council(context, factor_runner, backtest_runner):
            captured.update(context)

        with patch.object(server, "build_dashboard_fresh", return_value=fresh) as refresh, \
             patch.object(server, "write_dashboard_cache") as write, \
             patch.object(server, "run_research_council", side_effect=council):
            server.run_multi_agent_background(old, mode="quick")

        refresh.assert_called_once()
        write.assert_called_once_with(fresh)
        self.assertEqual(captured["data_date"], "2026-09-28")


if __name__ == "__main__":
    unittest.main()
