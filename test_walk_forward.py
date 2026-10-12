"""样本外与多重检验的单元测试（使用合成数据，不联网）。"""

import math
import random
import unittest

import server


def synthetic_prepared(days=320, stocks=40, keys=("f_a", "f_b", "f_c", "f_d", "f_e"), seed=11):
    """构造一个可控的因子矩阵：只有 f_a 带真实信号，其余是噪声。

    观测值第三项现在是「T / T+1 / T+2 / T+3 四根收盘价」，因为交易口径改成了
    「T 日尾盘买入、持有 1~2 个交易日」，进出场组合都要能从同一份矩阵算出来。
    """
    rng = random.Random(seed)
    dates = ["D%04d" % i for i in range(days)]
    observations = {}
    for day_index, date in enumerate(dates):
        rows = []
        for stock_index in range(stocks):
            signal = rng.gauss(0, 1)
            values = {
                "f_a": signal,
                "f_b": rng.gauss(0, 1),
                "f_c": signal + rng.gauss(0, 0.4),      # 与 f_a 高度相关
                "f_d": rng.gauss(0, 1),
                "f_e": -signal + rng.gauss(0, 0.4),     # f_a 的反向重复
            }
            values["__liquidity"] = 1e8 + rng.random() * 1e6
            target = 0.01 * signal + rng.gauss(0, 0.02)
            closes = (1.0, 1.0 + target, 1.0 + target * 1.5, 1.0 + target * 1.5)
            rows.append(("S%03d" % stock_index, values, closes))
        observations[date] = rows
    latest = [(code, {key: rng.gauss(0, 1) for key in list(keys) + ["__liquidity"]}) for code in
              ["S%03d" % i for i in range(stocks)]]
    return {"usable": [], "observations": observations, "latest": latest, "latest_data_date": dates[-1]}


class ComboSeriesTests(unittest.TestCase):
    def test_series_length_matches_requested_dates(self):
        prepared = synthetic_prepared(days=60)
        dates = sorted(prepared["observations"])[:30]
        daily, _ = server.combo_daily_series(prepared, [("f_a", 1)], dates)
        self.assertEqual(len(daily), 30)

    def test_entry_is_the_signal_day_close(self):
        """尾盘买入：进场价必须是 T 日收盘，而不是次日收盘。"""
        prepared = synthetic_prepared(days=30, stocks=40)
        closes = (1.0, 1.10, 1.20, 1.30)
        self.assertAlmostEqual(server.window_return(closes, 0, 1), 0.10, places=6)
        self.assertAlmostEqual(server.window_return(closes, 0, 2), 0.20, places=6)
        # 旧的 T+1 建仓口径仍然可复现，用于对照
        self.assertAlmostEqual(server.window_return(closes, 1, 1), 1.20 / 1.10 - 1, places=6)

    def test_window_return_reports_missing_data(self):
        self.assertIsNone(server.window_return((1.0, 1.1), 0, 2))
        self.assertIsNone(server.window_return((1.0, None), 0, 1))

    def test_holding_choices_change_the_result(self):
        prepared = synthetic_prepared(days=200)
        dates = sorted(prepared["observations"])[:120]
        one, _ = server.combo_daily_series(prepared, [("f_a", 1)], dates, holding_days=1)
        two, _ = server.combo_daily_series(prepared, [("f_a", 1)], dates, holding_days=2)
        self.assertNotEqual(one, two)

    def test_mark_to_market_is_not_bunched_at_exit(self):
        """持有 2 天时收益必须每天都能看到，而不是只在卖出那天跳一次。"""
        prepared = synthetic_prepared(days=140)
        dates = sorted(prepared["observations"])[:90]
        daily, _ = server.combo_daily_series(prepared, [("f_a", 1)], dates, holding_days=2, cost_rate=0)
        non_zero = sum(1 for value in daily if abs(value) > 1e-12)
        self.assertGreater(non_zero, len(daily) * 0.8)

    def test_two_day_holding_is_not_double_counted(self):
        """持有 2 天的日均收益应与持有 1 天同量级——仓位重叠会把收益放大一倍。"""
        prepared = synthetic_prepared(days=220, seed=5)
        dates = sorted(prepared["observations"])[:160]
        one, _ = server.combo_daily_series(prepared, [("f_a", 1)], dates, holding_days=1, cost_rate=0)
        two, _ = server.combo_daily_series(prepared, [("f_a", 1)], dates, holding_days=2, cost_rate=0)
        mean_one = sum(one) / len(one)
        mean_two = sum(two) / len(two)
        self.assertLess(abs(mean_two), abs(mean_one) * 1.6)
        self.assertGreater(abs(mean_two), abs(mean_one) * 0.3)

    def test_one_day_holding_returns_the_single_day_move(self):
        """持有 1 天、零成本时，日收益就是被选中标的当天的涨幅均值。"""
        prepared = synthetic_prepared(days=60, stocks=40, seed=3)
        dates = sorted(prepared["observations"])[:40]
        daily, selected = server.combo_daily_series(prepared, [("f_a", 1)], dates, holding_days=1, cost_rate=0)
        self.assertEqual(len(daily), len(dates))
        self.assertTrue(selected)
        # 手工复算最后一天：它来自「倒数第二个信号日」那笔仓位的盯市收益
        signal_date = dates[-3]
        top = server._select_top(prepared, [("f_a", 1)], signal_date, 3, 20, 1, 1)
        expected = sum(row[2][2] / row[2][1] - 1 for row in top) / len(top)
        self.assertAlmostEqual(daily[-1], expected, places=9)

    def test_stats_are_consistent_with_the_series(self):
        prepared = synthetic_prepared(days=80)
        daily, _ = server.combo_daily_series(prepared, [("f_a", 1)])
        stats = server.series_stats(daily)
        self.assertEqual(stats["days"], len(daily))
        self.assertAlmostEqual(stats["mean_daily"], round(sum(daily) / len(daily), 6), places=6)
        self.assertGreaterEqual(stats["max_drawdown"], 0.0)

    def test_short_series_returns_none(self):
        self.assertIsNone(server.series_stats([0.01] * 5))


class WindowGateTests(unittest.TestCase):
    def test_signal_factor_passes_and_noise_factors_do_not(self):
        prepared = synthetic_prepared(days=200)
        dates = sorted(prepared["observations"])[:150]
        stats = server.window_factor_gate(prepared, ["f_a", "f_b", "f_d"], dates)
        self.assertIn("f_a", stats)
        self.assertGreater(abs(stats["f_a"]["mean_rank_ic"]), abs(stats["f_b"]["mean_rank_ic"]))
        self.assertEqual(stats["f_a"]["direction"], "正向")

    def test_direction_follows_the_sign_of_ic(self):
        prepared = synthetic_prepared(days=200)
        dates = sorted(prepared["observations"])[:150]
        stats = server.window_factor_gate(prepared, ["f_e"], dates)
        self.assertEqual(stats["f_e"]["direction"], "反向")


class WalkForwardTests(unittest.TestCase):
    def test_walk_forward_produces_out_of_sample_days_only(self):
        prepared = synthetic_prepared(days=320)
        keys = ["f_a", "f_b", "f_d", "f_c"]
        result = server.walk_forward_backtest(prepared, keys, train_days=120, test_days=40)
        self.assertEqual(result["status"], "完成")
        self.assertGreaterEqual(result["fold_count"], 3)
        # 样本外天数必须等于各折测试期天数之和，绝不能包含训练期
        total_test = sum(fold["test_days"] for fold in result["folds"] if fold.get("status") == "完成")
        self.assertEqual(result["out_of_sample_days"], total_test)
        self.assertLessEqual(result["out_of_sample_days"], 320 - 120)

    def test_each_fold_selects_only_from_training_data(self):
        prepared = synthetic_prepared(days=320)
        result = server.walk_forward_backtest(prepared, ["f_a", "f_b", "f_d", "f_c"], train_days=120, test_days=40)
        for fold in result["folds"]:
            if fold.get("status") != "完成":
                continue
            self.assertLess(fold["train"][1], fold["test"][0])      # 训练期严格早于测试期
            self.assertTrue(set(fold["selected"]) <= {"f_a", "f_b", "f_d", "f_c"})

    def test_insufficient_history_is_reported(self):
        prepared = synthetic_prepared(days=100)
        result = server.walk_forward_backtest(prepared, ["f_a"], train_days=200, test_days=60)
        self.assertEqual(result["status"], "数据不足")


class DeflatedSharpeTests(unittest.TestCase):
    def selected(self, sharpe=2.0, days=500):
        return {"sharpe": sharpe, "daily_sharpe": sharpe / math.sqrt(252), "days": days,
                "skewness": 0.0, "kurtosis": 3.0}

    def test_probability_falls_as_more_trials_are_run(self):
        few = server.deflated_sharpe_ratio(self.selected(), [1.0, 1.2, 1.4])
        many = server.deflated_sharpe_ratio(self.selected(), [0.2 * i for i in range(1, 60)])
        self.assertEqual(few["status"], "完成")
        self.assertEqual(many["status"], "完成")
        self.assertGreater(few["probability"], many["probability"])
        self.assertGreater(many["expected_max_sharpe"], few["expected_max_sharpe"])

    def test_a_stronger_strategy_gets_a_higher_probability(self):
        trials = [0.5, 0.8, 1.0, 1.1, 0.6, 0.9]
        weak = server.deflated_sharpe_ratio(self.selected(sharpe=1.2), trials)
        strong = server.deflated_sharpe_ratio(self.selected(sharpe=3.0), trials)
        self.assertGreater(strong["probability"], weak["probability"])

    def test_identical_trials_have_no_selection_penalty(self):
        flat = server.deflated_sharpe_ratio(self.selected(sharpe=2.0), [1.0] * 10)
        self.assertEqual(flat["expected_max_sharpe"], 0.0)

    def test_needs_at_least_two_trials(self):
        self.assertEqual(server.deflated_sharpe_ratio(self.selected(), [1.0])["status"], "试算次数不足")


if __name__ == "__main__":
    unittest.main()
