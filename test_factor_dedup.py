"""因子去共线（按公式去重 + 按截面相关性并簇）的单元测试。"""

import re
import unittest

import server
from server import build_factor_search_space, cluster_factors, factor_cluster_summary, factor_cross_section, rank_correlation


def series_from(values):
    """values: {key: [v1, v2, ...]} -> {key: {code: value}}"""
    return {key: {"S%03d" % i: value for i, value in enumerate(items)} for key, items in values.items()}


def fake_stocks(values):
    """把 {key: [值...]} 变成 dashboard 里 stocks 的最小替身。"""
    count = len(next(iter(values.values())))
    return [{"code": "S%03d" % i, "completed_factor_date": "2026-10-09",
             "completed_mined_factors": {key: items[i] for key, items in values.items()}}
            for i in range(count)]


class RankCorrelationTests(unittest.TestCase):
    def test_identical_series_correlate_at_one(self):
        base = list(range(40))
        self.assertAlmostEqual(rank_correlation(series_from({"a": base})["a"], series_from({"b": base})["b"]), 1.0, places=6)

    def test_negated_series_correlate_at_minus_one(self):
        base = list(range(40))
        flipped = [-value for value in base]
        self.assertAlmostEqual(
            rank_correlation(series_from({"a": base})["a"], series_from({"b": flipped})["b"]), -1.0, places=6)

    def test_unrelated_series_are_near_zero(self):
        import random

        staircase = list(range(40))
        shuffled = list(range(40))
        random.Random(7).shuffle(shuffled)          # 固定种子，结果可复现
        rho = rank_correlation(series_from({"a": staircase})["a"], series_from({"b": shuffled})["b"])
        self.assertLess(abs(rho), 0.5)
        # 关键性质：明显不同构的因子不应被判定为重复
        self.assertLess(abs(rho), server.FACTOR_CORRELATION_THRESHOLD)

    def test_too_little_overlap_returns_none(self):
        self.assertIsNone(rank_correlation({"A": 1, "B": 2}, {"A": 1, "B": 2}))


class ClusterTests(unittest.TestCase):
    def items(self):
        return [
            {"key": "strong", "name": "强因子", "mean_rank_ic": -0.09},
            {"key": "copy_of_strong", "name": "强因子副本", "mean_rank_ic": -0.06},
            {"key": "flipped", "name": "强因子取负", "mean_rank_ic": 0.05},
            {"key": "other", "name": "无关因子", "mean_rank_ic": -0.04},
        ]

    def test_same_and_flipped_factors_merge_into_the_representative(self):
        series = series_from({
            "strong": list(range(60)),
            "copy_of_strong": [value + 0.001 for value in range(60)],
            "flipped": [-value for value in range(60)],
            "other": [value % 7 for value in range(60)],
        })
        kept, merged = cluster_factors(self.items(), series)
        self.assertEqual([item["key"] for item in kept], ["strong", "other"])
        merged_keys = {item["key"]: item["duplicate_of"] for item in merged}
        self.assertEqual(merged_keys["copy_of_strong"], "strong")
        self.assertEqual(merged_keys["flipped"], "strong")   # 负相关同样算重复

    def test_representative_is_the_strongest_of_the_cluster(self):
        series = series_from({
            "strong": list(range(60)),
            "copy_of_strong": list(range(60)),
            "flipped": [-value for value in range(60)],
            "other": [value % 7 for value in range(60)],
        })
        kept, _ = cluster_factors(self.items(), series)
        self.assertEqual(kept[0]["key"], "strong")

    def test_threshold_is_configurable(self):
        series = series_from({"strong": list(range(60)), "copy_of_strong": [value + 0.001 for value in range(60)],
                              "flipped": [-value for value in range(60)], "other": [value % 7 for value in range(60)]})
        kept, merged = cluster_factors(self.items(), series, threshold=1.01)
        self.assertEqual(len(kept), 4)
        self.assertEqual(merged, [])

    def test_summary_annotates_duplicates_on_the_items(self):
        values = {"strong": list(range(60)), "copy_of_strong": list(range(60)),
                  "flipped": [-value for value in range(60)], "other": [value % 7 for value in range(60)]}
        items = self.items()
        summary = factor_cluster_summary(items, fake_stocks(values))
        self.assertEqual(summary["input_count"], 4)
        self.assertEqual(summary["independent_count"], 2)
        self.assertEqual({item["key"] for item in summary["merged"]}, {"copy_of_strong", "flipped"})
        by_key = {item["key"]: item for item in items}
        self.assertEqual(by_key["flipped"]["duplicate_of"], "strong")
        self.assertNotIn("duplicate_of", by_key["strong"])


class CrossSectionTests(unittest.TestCase):
    def test_uses_latest_completed_date_only(self):
        stocks = [
            {"code": "A", "completed_factor_date": "2026-10-09", "completed_mined_factors": {"f": 1.0}},
            {"code": "B", "completed_factor_date": "2026-10-09", "completed_mined_factors": {"f": 2.0}},
            {"code": "C", "completed_factor_date": "2026-10-08", "completed_mined_factors": {"f": 9.0}},
        ]
        date, series = factor_cross_section(stocks, ["f"])
        self.assertEqual(date, "2026-10-09")
        self.assertEqual(series["f"], {"A": 1.0, "B": 2.0})

    def test_skips_missing_and_non_finite_values(self):
        stocks = [
            {"code": "A", "last_date": "2026-10-09", "mined_factors": {"f": 1.0}},
            {"code": "B", "last_date": "2026-10-09", "mined_factors": {"f": None}},
            {"code": "C", "last_date": "2026-10-09", "mined_factors": {"f": float("nan")}},
        ]
        _, series = factor_cross_section(stocks, ["f"])
        self.assertEqual(series["f"], {"A": 1.0})


class SearchSpaceTests(unittest.TestCase):
    def test_no_two_entries_share_the_same_formula(self):
        """回归测试：种子键 momentum20 与生成键 momentum_20 曾经是同一个公式却同时存在。"""
        space = build_factor_search_space()
        fingerprints = [re.sub(r"\s+", "", formula).lower() for _, _, formula in space]
        self.assertEqual(len(fingerprints), len(set(fingerprints)))

    def test_keys_are_unique(self):
        space = build_factor_search_space()
        keys = [key for key, _, _ in space]
        self.assertEqual(len(keys), len(set(keys)))

    def test_seed_keys_win_over_generated_aliases(self):
        keys = {key for key, _, _ in build_factor_search_space()}
        self.assertIn("momentum20", keys)
        self.assertIn("ma5_20", keys)
        self.assertNotIn("momentum_20", keys)
        self.assertNotIn("ma_5_20", keys)


if __name__ == "__main__":
    unittest.main()
