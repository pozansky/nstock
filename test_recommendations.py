"""推荐留痕与归因的单元测试。"""

import unittest
from unittest.mock import patch
from datetime import date, timedelta

from recommendations import (
    ENTRY_RULE,
    HORIZONS,
    attribute_entries,
    compute_benchmarks,
    forward_returns,
    make_snapshot,
    needs_attribution,
    ranking_entries,
    refresh,
    summarize,
    upsert,
)


def bars(count=40, start=10.0):
    return [{"date": "D%03d" % i, "open": start + i, "high": start + i, "low": start + i,
             "close": start + i, "volume": 1000} for i in range(count)]


class ForwardReturnsTests(unittest.TestCase):
    def test_entry_is_the_signal_day_close(self):
        """尾盘买入：进场就是 T 日自己的收盘价，不是次日收盘。"""
        result = forward_returns(bars(40), "D005")
        self.assertEqual(result["entry_date"], "D005")
        self.assertEqual(result["entry_close"], 15.0)
        self.assertAlmostEqual(result["returns"][1], 16 / 15 - 1, places=6)
        self.assertEqual(set(result["returns"]), {1})

    def test_exit_prices_are_recorded_for_audit(self):
        result = forward_returns(bars(40), "D005")
        self.assertEqual(result["exits"][1], {"date": "D006", "close": 16.0})
        self.assertEqual(set(result["exits"]), {1})
        self.assertIsNone(forward_returns(bars(6), "D005")["exits"][1])

    def test_detail_keeps_entry_and_exit_prices(self):
        series = {"A": bars(40)}
        result = attribute_entries([{"code": "A", "name": "甲"}], lambda code: series.get(code) or [], "D005")
        leg = result["detail"][0]
        self.assertEqual(leg["entry_close"], 15.0)
        self.assertEqual(leg["exits"]["1"]["close"], 16.0)

    def test_returns_are_none_when_data_not_arrived(self):
        result = forward_returns(bars(6), "D005")
        self.assertIsNone(result["returns"][1])

    def test_missing_data_date_returns_nothing(self):
        self.assertIsNone(forward_returns(bars(40), "D999"))

    def test_last_bar_can_be_entered_but_has_no_exit(self):
        """T 日就是最新一根时，已经买入、但卖出日还没到——不能当成"缺少行情"。"""
        result = forward_returns(bars(40), "D039")
        self.assertEqual(result["entry_date"], "D039")
        self.assertTrue(all(value is None for value in result["returns"].values()))


class AttributeEntriesTests(unittest.TestCase):
    def loader(self, series):
        return lambda code: series.get(code) or []

    def test_equal_weight_mean_minus_cost(self):
        series = {"A": bars(40), "B": bars(40), "C": bars(40)}
        entries = [{"code": "A"}, {"code": "B"}, {"code": "C"}]
        result = attribute_entries(entries, self.loader(series), "D005")
        gross = 16 / 15 - 1
        self.assertAlmostEqual(result["gross"]["1"], gross, places=5)
        self.assertAlmostEqual(result["net"]["1"], gross - 0.0015, places=5)
        self.assertEqual(result["status"], "已回填")
        self.assertEqual(result["entry_count"], 3)
        self.assertEqual(result["entry_rule"], ENTRY_RULE)

    def test_partial_fill_is_reported_honestly(self):
        series = {"A": bars(40), "B": bars(3)}     # B 根本没有 T 日这根 K 线
        entries = [{"code": "A"}, {"code": "B"}]
        result = attribute_entries(entries, self.loader(series), "D005")
        self.assertEqual(result["status"], "部分回填")
        self.assertEqual(result["entry_count"], 1)
        self.assertEqual(result["total_count"], 2)
        # B 完全无法回填时，即使 A 的数据足够长，也不能宣称持有期已覆盖——
        # 否则等 B 的行情到齐后，这个快照不会再被补算。
        self.assertIsNone(result["covered_horizon"])

    def test_entry_without_exit_yet_is_flagged(self):
        series = {"A": bars(6)}                    # T 日就是最后一根：已买入，还没到卖出日
        result = attribute_entries([{"code": "A"}], self.loader(series), "D005")
        self.assertEqual(result["status"], "已建仓待退出")
        self.assertEqual(result["entry_count"], 1)
        self.assertIsNone(result["net"]["1"])

    def test_no_data_returns_none(self):
        result = attribute_entries([{"code": "X"}], self.loader({}), "D005")
        self.assertIsNone(result)

    def test_covered_horizon_tracks_shortest_complete_window(self):
        series = {"A": bars(40), "B": bars(40)}
        result = attribute_entries([{"code": "A"}, {"code": "B"}], self.loader(series), "D005")
        self.assertEqual(result["covered_horizon"], max(HORIZONS))


class SnapshotTests(unittest.TestCase):
    def test_upsert_is_idempotent(self):
        log = {"snapshots": {}, "benchmarks": {}}
        snapshot = make_snapshot("ranking", "2026-10-09", [{"code": "A"}])
        self.assertTrue(upsert(log, snapshot))
        again = make_snapshot("ranking", "2026-10-09", [{"code": "A"}])
        self.assertFalse(upsert(log, again))

    def test_changing_constituents_invalidates_attribution(self):
        log = {"snapshots": {}, "benchmarks": {}}
        first = make_snapshot("ranking", "2026-10-09", [{"code": "A"}])
        first["attribution"] = {"status": "已回填"}
        upsert(log, first)
        second = make_snapshot("ranking", "2026-10-09", [{"code": "B"}])
        upsert(log, second)
        self.assertIsNone(log["snapshots"]["2026-10-09|ranking"]["attribution"])

    def test_same_key_per_day_per_kind(self):
        log = {"snapshots": {}, "benchmarks": {}}
        upsert(log, make_snapshot("ranking", "2026-10-09", [{"code": "A"}]))
        upsert(log, make_snapshot("council", "2026-10-09", [{"code": "B"}]))
        self.assertEqual(len(log["snapshots"]), 2)
        upsert(log, make_snapshot("ranking", "2026-10-09", [{"code": "C"}]))
        self.assertEqual(len(log["snapshots"]), 2)

    def test_needs_attribution_until_fully_covered(self):
        snapshot = make_snapshot("ranking", "2026-10-09", [{"code": "A"}])
        self.assertTrue(needs_attribution(snapshot))
        snapshot["attribution"] = {"status": "部分回填", "covered_horizon": 1, "entry_rule": ENTRY_RULE}
        self.assertTrue(needs_attribution(snapshot))
        snapshot["attribution"] = {"status": "已回填", "covered_horizon": max(HORIZONS), "entry_rule": ENTRY_RULE}
        self.assertFalse(needs_attribution(snapshot))

    def test_changed_entry_rule_invalidates_old_attribution(self):
        """交易口径一改，旧的收益口径必须作废重算。"""
        snapshot = make_snapshot("ranking", "2026-10-09", [{"code": "A"}])
        snapshot["attribution"] = {"status": "已回填", "covered_horizon": max(HORIZONS), "entry_rule": "T+1收盘买入"}
        self.assertTrue(needs_attribution(snapshot))


class RankingTests(unittest.TestCase):
    def test_uses_latest_completed_date_and_sorts(self):
        stocks = [
            {"code": "A", "name": "甲", "score": 90, "agent_combo_score": 10, "completed_factor_date": "2026-10-09"},
            {"code": "B", "name": "乙", "score": 80, "agent_combo_score": 99, "completed_factor_date": "2026-10-09"},
            {"code": "C", "name": "丙", "score": 99, "agent_combo_score": 50, "completed_factor_date": "2026-10-08"},
        ]
        date, entries = ranking_entries(stocks, use_combo=True, limit=5)
        self.assertEqual(date, "2026-10-09")
        self.assertEqual([item["code"] for item in entries], ["B", "A"])
        self.assertEqual(entries[0]["rank"], 1)

    def test_respects_limit_and_skips_missing_scores(self):
        stocks = [{"code": str(i), "name": str(i), "score": i, "completed_factor_date": "2026-10-09"} for i in range(10)]
        stocks.append({"code": "X", "name": "X", "score": None, "completed_factor_date": "2026-10-09"})
        _, entries = ranking_entries(stocks, use_combo=False, limit=3)
        self.assertEqual([item["code"] for item in entries], ["9", "8", "7"])


class RefreshAndSummaryTests(unittest.TestCase):
    def build_log(self):
        series = {"A": bars(40), "B": bars(40), "C": bars(40)}
        log = {"snapshots": {}, "benchmarks": {}}
        upsert(log, make_snapshot("ranking", "D005", [{"code": "A", "name": "甲"}, {"code": "B", "name": "乙"}]))
        upsert(log, make_snapshot("council", "D010", [{"code": "C", "name": "丙"}]))
        return log, series

    def test_refresh_fills_attribution_and_benchmarks(self):
        log, series = self.build_log()
        loader = lambda code: series.get(code) or []
        changed = refresh(log, loader, universe_codes=list(series))
        self.assertTrue(changed)
        self.assertEqual(log["snapshots"]["D005|ranking"]["attribution"]["status"], "已回填")
        self.assertIn("D005", log["benchmarks"])
        self.assertIn("D010", log["benchmarks"])

    def test_refresh_is_stable_second_time(self):
        log, series = self.build_log()
        loader = lambda code: series.get(code) or []
        refresh(log, loader, universe_codes=list(series))
        self.assertFalse(refresh(log, loader, universe_codes=list(series)))

    def test_summary_aggregates_net_and_excess(self):
        log, series = self.build_log()
        loader = lambda code: series.get(code) or []
        refresh(log, loader, universe_codes=list(series))
        summary = summarize(log)
        self.assertEqual(summary["total_rounds"], 2)
        self.assertEqual(summary["filled_rounds"], 2)
        first = next(item for item in summary["horizons"] if item["days"] == 1)
        self.assertEqual(first["count"], 2)
        self.assertIsNotNone(first["mean"])
        self.assertIsNotNone(first["excess"])
        self.assertEqual(summary["rounds"][0]["data_date"], "D010")

    def test_summary_on_empty_log(self):
        summary = summarize({"snapshots": {}, "benchmarks": {}})
        self.assertEqual(summary["total_rounds"], 0)
        self.assertIsNone(summary["first_date"])
        self.assertTrue(all(item["mean"] is None for item in summary["horizons"]))

    def test_cost_sensitivity_restates_the_same_gross(self):
        log, series = self.build_log()
        loader = lambda code: series.get(code) or []
        refresh(log, loader, universe_codes=list(series))
        summary = summarize(log)
        rows = {item["bps"]: item for item in summary["cost_sensitivity"]}
        self.assertEqual(sorted(rows), [15, 25, 40])
        # 同一批毛收益，成本每加 10bp，净收益就应恰好低 0.001
        self.assertAlmostEqual(rows[15]["mean"] - rows[25]["mean"], 0.001, places=6)
        self.assertAlmostEqual(rows[25]["mean"] - rows[40]["mean"], 0.0015, places=6)
        self.assertAlmostEqual(rows[15]["mean"], next(item for item in summary["horizons"] if item["days"] == 1)["mean"], places=6)

    def test_round_row_exposes_entry_price_and_legs(self):
        log, series = self.build_log()
        loader = lambda code: series.get(code) or []
        refresh(log, loader, universe_codes=list(series))
        summary = summarize(log)
        row = next(item for item in summary["rounds"] if item["data_date"] == "D005")
        self.assertEqual(row["entry_date"], "D005")
        self.assertEqual(row["entry_close"], 15.0)
        self.assertIn("买 15.0", row["legs"])


class DetailedReviewTests(unittest.TestCase):
    def test_all_return_windows_are_precomputed(self):
        import server
        series=[{"date":(date(2026,9,1)+timedelta(days=i)).isoformat(),"close":10+i} for i in range(40)]
        rounds=[{"data_date":bar["date"],"detail":[{"code":code} for code in ("A","B","C")]} for bar in series]
        result=server.recommendation_equity(rounds,1,lambda code:series)
        self.assertEqual(set(result["periods"]),{"1d","3d","7d","1m","all"})
        self.assertEqual(result["periods"]["3d"]["trading_days"],3)
        self.assertEqual(result["periods"]["7d"]["trading_days"],7)
        self.assertEqual(result["periods"]["1m"]["trading_days"],30)
        self.assertAlmostEqual(result["periods"]["1d"]["net_return"],49/48-1-.0015,places=7)

    def test_multiday_returns_use_phased_capital_and_live_valuation(self):
        import server
        series=[{"date":f"2026-09-0{i+1}","close":10 if i==0 else 20} for i in range(4)]
        rounds=[{"data_date":bar["date"],"detail":[{"code":code} for code in ("A","B","C")]} for bar in series]
        result=server.recommendation_equity(rounds,2,lambda code:series)
        self.assertAlmostEqual(result["daily"][1]["return"],.5-.00075,places=7)
        self.assertIsNotNone(result["periods"]["all"]["net_return"])
        self.assertAlmostEqual(result["periods"]["1d"]["net_return"],-.00075,places=7)

    def test_missing_one_of_three_prices_does_not_use_two_stock_return(self):
        import server
        series=[{"date":"2026-09-01","close":10},{"date":"2026-09-02","close":20}]
        rounds=[{"data_date":"2026-09-01","detail":[{"code":code} for code in ("A","B","C")]}]
        result=server.recommendation_equity(rounds,1,lambda code:series if code!="C" else series[:1])
        self.assertEqual(result["daily"][1]["return"],0)
        self.assertFalse(result["periods"]["1d"]["complete"])
        self.assertIsNone(result["periods"]["1d"]["net_return"])

    def test_three_stock_portfolio_is_not_twenty_stock_average(self):
        log = {"snapshots": {}, "benchmarks": {}}
        upsert(log, make_snapshot("ranking", "D005", [{"code": str(i)} for i in range(20)]))
        refresh(log, lambda code: bars(40, start=10 if int(code)<3 else 1000))
        result = summarize(log)
        self.assertEqual(len(result["rounds"][0]["detail"]), 3)
        self.assertEqual(result["rounds"][0]["count"], 3)
        self.assertAlmostEqual(result["rounds"][0]["net"]["1"], 1/15 - .0015, places=5)

    def test_incomplete_three_stock_portfolio_does_not_settle(self):
        result=attribute_entries([{"code":"A"},{"code":"B"},{"code":"C"}], lambda code:bars(40) if code!="C" else [], "D005")
        self.assertIsNone(result["net"]["1"])

    def test_strategy_filter_does_not_mix_other_strategy_returns(self):
        log = {"snapshots": {}, "benchmarks": {}}
        for strategy in ("A", "B"):
            snapshot=make_snapshot("replay", "2026-09-01", [{"code":strategy}])
            snapshot["key"] += "|"+strategy
            snapshot["strategy_id"]=strategy
            upsert(log,snapshot)
        result=summarize(log,strategy_id="A")
        self.assertEqual(result["replay_count"],1)
        self.assertEqual(result["replay_rounds"][0]["detail"][0]["code"],"A")

    def test_completed_round_updates_latest_tracking(self):
        log = {"snapshots": {}, "benchmarks": {}}
        upsert(log, make_snapshot("ranking", "D005", [{"code": "A"}]))
        series = {"A": bars(40)}
        loader = lambda code: series[code]
        refresh(log, loader, universe_codes=["A"])
        old = summarize(log)["rounds"][0]["detail"][0]
        series["A"] = bars(45)
        refresh(log, loader, universe_codes=["A"])
        new = summarize(log)["rounds"][0]["detail"][0]
        self.assertEqual(new["latest_date"], "D044")
        self.assertGreater(new["latest_return"], old["latest_return"])
        self.assertEqual(new["elapsed_days"], 39)

    def test_partial_benchmark_is_filled_when_history_arrives(self):
        log = {"snapshots": {}, "benchmarks": {}}
        upsert(log, make_snapshot("ranking", "D005", [{"code": "A"}]))
        series = {"A": bars(6)}
        loader = lambda code: series[code]
        refresh(log, loader, universe_codes=["A"])
        self.assertIsNone(log["benchmarks"]["D005"]["1"])
        series["A"] = bars(40)
        refresh(log, loader, universe_codes=["A"])
        self.assertIsNotNone(log["benchmarks"]["D005"]["1"])

    def test_replay_is_not_counted_as_real_recommendation(self):
        log = {"snapshots": {}, "benchmarks": {}}
        for kind in ("ranking", "replay"):
            upsert(log, make_snapshot(kind, "2026-09-01", [{"code": "A", "rank": 1}]))
        upsert(log, make_snapshot("council", "2026-08-31", [{"code": "B"}]))
        summary = summarize(log)
        self.assertEqual(summary["total_rounds"], 1)
        self.assertEqual(summary["replay_count"], 1)
        self.assertEqual(summary["replay_rounds"][0]["detail"][0]["status"], "待回填")
        self.assertEqual(summary["rounds"][0]["detail"][0]["rank"], 1)

    def test_summary_does_not_truncate_history_to_twelve_rounds(self):
        log = {"snapshots": {}, "benchmarks": {}}
        for i in range(30):
            upsert(log, make_snapshot("ranking", (date(2026, 9, 1) + timedelta(days=i)).isoformat(), [{"code": "A"}]))
        self.assertEqual(len(summarize(log)["rounds"]), 30)

    def test_replay_ranking_does_not_use_future_prices(self):
        import server
        days = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(250)]
        stocks = [{"code": str(i), "name": str(i)} for i in range(40)]
        usable = []
        for i, stock in enumerate(stocks):
            rows = [{"date": day, "close": 10 + j * (i + 1) / 100,
                     "high": 10 + j * (i + 1) / 100, "low": 10 + j * (i + 1) / 100,
                     "volume": 10000} for j, day in enumerate(days)]
            usable.append((stock["code"], rows, {day: j for j, day in enumerate(days)}))
        dashboard = {"stocks": stocks, "agent_selection": {"name": "test", "factors": ["momentum_1"]}}
        validation = {"validation": {"results": [{"key": "momentum_1", "direction": "正向"}]}}
        with patch.object(server, "prepare_backtest_universe", return_value=usable), patch.object(server, "read_factor_agent_run", return_value=validation):
            first = {"snapshots": {}, "benchmarks": {}}
            self.assertTrue(server.seed_recommendation_replay(dashboard, first))
            self.assertFalse(server.seed_recommendation_replay(dashboard, first))
            replay_key = "2026-09-01|replay|" + server.recommendation_strategy_id(["momentum_1"])
            expected = first["snapshots"][replay_key]["entries"]
            for _, rows, _ in usable:
                rows[-1]["close"] *= 1000
            second = {"snapshots": {}, "benchmarks": {}}
            server.seed_recommendation_replay(dashboard, second)
            self.assertEqual(second["snapshots"][replay_key]["entries"], expected)
            self.assertEqual(len(expected), 3)


if __name__ == "__main__":
    unittest.main()
