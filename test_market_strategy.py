import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import market_strategy
import server
from multi_agent import resolve_round_strategy


class RegisteredStrategyTests(unittest.TestCase):
    def test_only_next_day_holding_is_available(self):
        self.assertEqual(server.HOLDING_CHOICES, (1,))
        self.assertEqual(server.DEFAULT_HOLDING_DAYS, 1)
        self.assertEqual(market_strategy.specification()["holding_days"], 1)

    def test_two_day_selection_is_rejected_and_old_selection_migrates(self):
        with tempfile.TemporaryDirectory() as root, patch.object(server, "SELECTED_STRATEGY_FILE", Path(root) / "selection.json"):
            with self.assertRaises(ValueError):
                server.write_selected_strategy(["momentum_20"], 2)
            server.SELECTED_STRATEGY_FILE.write_text(json.dumps({"factors": ["momentum_20"], "holding_days": 2}))
            self.assertEqual(server.read_selected_strategy(), market_strategy.specification())

    def test_cached_two_day_results_are_not_exposed(self):
        with tempfile.TemporaryDirectory() as root, patch.object(server, "BACKTEST_RUN_FILE", Path(root) / "backtest.json"):
            server.BACKTEST_RUN_FILE.write_text(json.dumps({"results": [
                {"factors": ["a"], "holding_days": 2}, {"factors": ["b"], "holding_days": 1}]}))
            with patch.object(market_strategy, "read", return_value=None):
                result = server.read_backtest_run()
            self.assertEqual([row["holding_days"] for row in result["results"]], [1])
            self.assertTrue(result["stale_engine"])

    def test_old_two_day_replay_is_not_a_review_option(self):
        log = {"snapshots": {"old": {"kind": "replay", "strategy_id": "old-two-day",
            "strategy": {"factors": ["a"], "holding_days": 2}}}}
        with patch.object(server, "read_backtest_run", return_value={"results": []}), \
             patch.object(server, "read_selected_strategy", return_value=market_strategy.specification()):
            strategies, _ = server.recommendation_strategies({}, log)
        self.assertEqual(strategies, [])

    def test_recovered_quotes_preserve_endpoint_returns_not_path_metrics(self):
        curve = [{"equity": 1, "complete": True}, {"equity": 1, "complete": False},
                 {"equity": 1.1, "complete": True}]
        result = market_strategy.apply_return_reporting({"max_drawdown": .1, "sharpe": 2}, curve)
        self.assertAlmostEqual(result["cumulative_return"], .1)
        self.assertAlmostEqual(result["annual_return"], 1.1 ** (252 / 3) - 1)
        self.assertEqual(result["pnl_20000"], 2000)
        self.assertEqual(result["return_quality"], "recovered_quotes_delayed_exit")
        self.assertIsNone(result["max_drawdown"])
        self.assertIsNone(result["sharpe"])

    def test_unresolved_endpoint_quote_still_blocks_returns(self):
        for curve in ([], [{"equity": 1.1, "complete": False}]):
            result = market_strategy.apply_return_reporting({}, curve)
            self.assertIsNone(result["cumulative_return"])
            self.assertIsNone(result["annual_return"])
            self.assertIsNone(result["pnl_20000"])

    def test_annualization_includes_cash_days(self):
        curve = [{"equity": 1, "complete": True} for _ in range(251)] + [{"equity": 1.1, "complete": True}]
        result = market_strategy.apply_return_reporting({}, curve)
        self.assertAlmostEqual(result["annual_return"], .1)
        self.assertTrue(result["annualization_reliable"])

    def test_gate_is_strict_and_requires_trend_and_coverage(self):
        state = {"valid": True, "up_fraction": .46, "above_ma20": True}
        self.assertTrue(market_strategy.gate_open(state))
        for key, value in (("valid", False), ("up_fraction", .45), ("above_ma20", False)):
            self.assertFalse(market_strategy.gate_open({**state, key: value}))

    def test_no_selection_restores_registered_default(self):
        with tempfile.TemporaryDirectory() as root, patch.object(server, "SELECTED_STRATEGY_FILE", Path(root) / "selection.json"):
            self.assertEqual(server.read_selected_strategy()["strategy_id"], market_strategy.ID)
            server.write_selected_strategy(market_strategy.FACTORS, 1, market_strategy.ID)
            self.assertEqual(server.read_selected_strategy()["directions"], ["正向", "正向", "反向"])
            server.write_selected_strategy(["momentum_20"], 1)
            self.assertNotIn("strategy_id", server.read_selected_strategy())

    def test_council_resolves_by_strategy_identity_not_only_factors(self):
        ordinary = {"factors": list(market_strategy.FACTORS), "holding_days": 1}
        timed = {**ordinary, "strategy_id": market_strategy.ID}
        result, source = resolve_round_strategy({"results": [ordinary, timed]}, market_strategy.specification())
        self.assertIs(result, timed)
        self.assertEqual(source, "user")

    def test_read_backtest_includes_registered_once(self):
        with tempfile.TemporaryDirectory() as root, patch.object(server, "BACKTEST_RUN_FILE", Path(root) / "backtest.json"):
            row = {"strategy_id": market_strategy.ID, "factors": list(market_strategy.FACTORS)}
            server.BACKTEST_RUN_FILE.write_text(json.dumps({"engine_version": server.BACKTEST_ENGINE_VERSION,
                                                           "results": [row, row]}))
            with patch.object(market_strategy, "read", return_value={"row": row}):
                self.assertEqual(len(server.read_backtest_run()["results"]), 1)

    def test_closed_gate_does_not_publish_ranking_purchase(self):
        with patch.object(market_strategy, "read", return_value={"gate_open": False}):
            log = {"snapshots": {}}
            self.assertFalse(server.record_ranking_snapshot({"stocks": [{"code": "a"}],
                "agent_selection": market_strategy.specification()}, log))
            self.assertFalse(log["snapshots"])

    def test_custom_replay_uses_exact_gated_baskets(self):
        rows = {"2026-09-01": [], "2026-09-02": [{"code": code, "rank": i + 1}
                                                        for i, code in enumerate(("a", "b", "c"))]}
        with patch.object(market_strategy, "read", return_value={"baskets": rows}):
            log = {"snapshots": {}}
            self.assertTrue(server.seed_recommendation_replay({}, log, market_strategy.specification()))
            self.assertEqual(len(log["snapshots"]), 1)
            snapshot = next(iter(log["snapshots"].values()))
            self.assertEqual(snapshot["strategy_id"], market_strategy.ID)
            self.assertEqual(snapshot["strategy"]["entry_offset"], 0)
            self.assertFalse(server.seed_recommendation_replay({}, log, market_strategy.specification()))


if __name__ == "__main__":
    unittest.main()
