"""Data-integrity tests: volume units, cache gating, sanity checks and the startup pull."""

import json
import shutil
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import server


def bars(count=90, price=10.0, volume=10000.0, start="2026-01-01"):
    return [{"date": "D%03d" % i, "open": price, "high": price * 1.02, "low": price * .98,
             "close": price, "volume": volume, "amount": 0} for i in range(count)]


class VolumeUnitTests(unittest.TestCase):
    def test_sina_volume_is_converted_from_shares_to_lots(self):
        payload = [
            {"day": "2026-10-08", "open": "8.90", "high": "8.99", "low": "8.61", "close": "8.68", "volume": "11947896"},
            {"day": "2026-10-09", "open": "8.68", "high": "9.06", "low": "8.48", "close": "9.03", "volume": "18035354"},
        ]
        captured = {}

        def capture(code, klines, adjustment=None):
            captured["code"] = code
            captured["klines"] = list(klines)
            captured["adjustment"] = adjustment
            return list(klines)

        with patch.object(server, "eastmoney_json", return_value=payload), \
             patch.object(server, "read_kline_cache", return_value=[]), \
             patch.object(server, "write_kline_cache", side_effect=capture):
            server.fetch_stock_sina({"code": "300047", "secid": "0.300047"})

        self.assertEqual(captured["klines"][0]["volume"], 119478.96)
        self.assertEqual(captured["klines"][1]["volume"], 180353.54)
        # 新浪是不复权，必须打上不同基准，否则会与前复权序列混在一起
        self.assertEqual(captured["adjustment"], "none")

    def test_legacy_cache_without_unit_stamp_is_ignored(self):
        with workspace_kline_dir():
            path = server.KLINE_CACHE_DIR / "300047.json"
            path.write_text(json.dumps({"code": "300047", "klines": bars(5)}), encoding="utf-8")
            self.assertEqual(server.read_kline_cache("300047"), [])

    def test_stamped_cache_is_read_back(self):
        with workspace_kline_dir():
            server.write_kline_cache("300047", bars(5))
            rows = server.read_kline_cache("300047")
            self.assertEqual(len(rows), 5)

    def test_mixed_unit_history_is_not_merged_into_new_series(self):
        with workspace_kline_dir():
            legacy = bars(6)
            for row in legacy:  # 历史以"股"为单位写入，最后一根却是"手"
                row["volume"] = 1_000_000
            legacy[-1]["volume"] = 10_000
            (server.KLINE_CACHE_DIR / "300047.json").write_text(
                json.dumps({"code": "300047", "klines": legacy}), encoding="utf-8")

            fresh = bars(6, volume=10_000)
            written = server.write_kline_cache("300047", fresh)

            self.assertEqual(len(written), 6)
            self.assertTrue(all(row["volume"] == 10_000 for row in written))
            self.assertEqual(server.read_kline_cache("300047"), written)


class AdjustmentBasisTests(unittest.TestCase):
    def test_cache_file_is_stamped_with_its_basis(self):
        with workspace_kline_dir():
            server.write_kline_cache("300047", bars(5), server.ADJUSTMENT)
            self.assertEqual(server.read_kline_basis("300047"), server.ADJUSTMENT)

    def test_unadjusted_rows_are_rescaled_onto_the_adjusted_series(self):
        with workspace_kline_dir():
            server.write_kline_cache("300047", bars(10, price=10.0), server.ADJUSTMENT)
            # 同一交易日、不复权价格整体高出 25%（模拟除权造成的基准差）
            unadjusted = [dict(row, open=row["open"] * 1.25, high=row["high"] * 1.25,
                               low=row["low"] * 1.25, close=row["close"] * 1.25)
                          for row in bars(10, price=10.0)]
            unadjusted.append({"date": "D010", "open": 12.5, "high": 12.5, "low": 12.5,
                               "close": 12.5, "volume": 1000, "amount": 0})
            merged = server.write_kline_cache("300047", unadjusted, "none")
            self.assertEqual(server.read_kline_basis("300047"), server.ADJUSTMENT)
            self.assertEqual(len(merged), 11)
            self.assertAlmostEqual(merged[-1]["close"], 10.0, places=3)   # 12.5 / 1.25

    def test_a_dividend_inside_the_overlap_refuses_the_merge(self):
        with workspace_kline_dir():
            server.write_kline_cache("300047", bars(10, price=10.0), server.ADJUSTMENT)
            before = server.read_kline_cache("300047")
            # 重叠区间内比值不恒定 → 说明期间发生除权，无法可靠换算
            drifting = []
            for i, row in enumerate(bars(10, price=10.0)):
                scale = 1.25 + i * 0.02
                drifting.append(dict(row, open=row["open"] * scale, high=row["high"] * scale,
                                     low=row["low"] * scale, close=row["close"] * scale))
            merged = server.write_kline_cache("300047", drifting, "none")
            self.assertEqual(merged, before)      # 原序列保持不动，宁可少更新也不混基准
            self.assertEqual(server.read_kline_cache("300047"), before)

    def test_standard_basis_is_adopted_when_it_arrives(self):
        with workspace_kline_dir():
            server.write_kline_cache("300047", bars(5), "none")
            self.assertEqual(server.read_kline_basis("300047"), "none")
            server.write_kline_cache("300047", bars(5), server.ADJUSTMENT)
            self.assertEqual(server.read_kline_basis("300047"), server.ADJUSTMENT)

    def test_rebase_needs_enough_overlap(self):
        self.assertIsNone(server.rebase_rows(bars(10)[:3], bars(10)[:3]))


class DataFingerprintTests(unittest.TestCase):
    def test_fingerprint_is_stable_for_unchanged_data(self):
        with workspace_kline_dir():
            server.write_kline_cache("300047", bars(8, price=10.0), server.ADJUSTMENT)
            stocks = [{"code": "300047"}]
            self.assertEqual(server.kline_data_fingerprint(stocks), server.kline_data_fingerprint(stocks))

    def test_fingerprint_changes_when_prices_are_revised(self):
        """同代码同日期、价格被改写（数据源切换/复权换算）也必须被发现。"""
        with workspace_kline_dir():
            server.write_kline_cache("300047", bars(8, price=10.0), server.ADJUSTMENT)
            stocks = [{"code": "300047"}]
            before = server.kline_data_fingerprint(stocks)
            revised = bars(8, price=10.5)
            server.write_kline_cache("300047", revised, server.ADJUSTMENT)
            self.assertNotEqual(before, server.kline_data_fingerprint(stocks))

    def test_fingerprint_changes_when_bars_are_added(self):
        with workspace_kline_dir():
            server.write_kline_cache("300047", bars(8, price=10.0), server.ADJUSTMENT)
            stocks = [{"code": "300047"}]
            before = server.kline_data_fingerprint(stocks)
            extra = bars(9, price=10.0)
            extra[-1]["date"] = "D008"
            server.write_kline_cache("300047", extra, server.ADJUSTMENT)
            self.assertNotEqual(before, server.kline_data_fingerprint(stocks))

    def test_missing_code_is_handled(self):
        with workspace_kline_dir():
            self.assertIsInstance(server.kline_data_fingerprint([{"code": "000000"}]), str)


class RankingCrossSectionTests(unittest.TestCase):
    def test_ranking_takes_the_latest_intraday_bar(self):
        """盘中时 A 已有今天那根实时 bar，就必须用今天排名，而不是退回昨天。"""
        stocks = [
            {"code": "A", "last_date": "2026-10-12", "completed_factor_date": "2026-10-09", "completed_history_bars": 500},
            {"code": "B", "last_date": "2026-10-09", "completed_factor_date": "2026-10-09", "completed_history_bars": 500},
        ]
        rows, date = server.ranking_cross_section(stocks)
        self.assertEqual(date, "2026-10-12")
        self.assertEqual(list(rows), ["A"])

    def test_history_shortfall_is_excluded(self):
        stocks = [
            {"code": "A", "last_date": "2026-10-12", "completed_history_bars": 500},
            {"code": "B", "last_date": "2026-10-12", "completed_history_bars": 40},
        ]
        rows, _ = server.ranking_cross_section(stocks)
        self.assertEqual(list(rows), ["A"])

    def test_falls_back_to_completed_date_when_last_date_missing(self):
        stocks = [{"code": "A", "completed_factor_date": "2026-10-09", "completed_history_bars": 500}]
        rows, date = server.ranking_cross_section(stocks)
        self.assertEqual(date, "2026-10-09")
        self.assertEqual(list(rows), ["A"])

    def test_empty_universe(self):
        rows, date = server.ranking_cross_section([])
        self.assertEqual(rows, {})
        self.assertEqual(date, "")


class SeriesSanityTests(unittest.TestCase):
    def test_unit_break_on_last_bar_is_anomalous(self):
        rows = bars(90, volume=10_000)
        rows[-1]["volume"] = 100  # 1% -> 单位断层
        self.assertTrue(server.volume_is_anomalous(rows, len(rows) - 1))
        self.assertEqual(len(server.tradable_daily_rows(rows)), 89)

    def test_regular_volume_spike_is_not_anomalous(self):
        rows = bars(90, volume=10_000)
        rows[-1]["volume"] = 90_000  # 真实放量 9 倍
        self.assertFalse(server.volume_is_anomalous(rows, len(rows) - 1))
        self.assertEqual(len(server.tradable_daily_rows(rows)), 90)

    def test_zero_volume_bar_is_anomalous(self):
        rows = bars(90, volume=10_000)
        rows[-1]["volume"] = 0
        self.assertTrue(server.volume_is_anomalous(rows, len(rows) - 1))

    def test_trimming_stops_at_the_first_good_bar(self):
        rows = bars(90, volume=10_000)
        rows[-1]["volume"] = 50
        rows[-2]["volume"] = 50
        self.assertEqual(len(server.tradable_daily_rows(rows)), 88)


class FreshnessTests(unittest.TestCase):
    def test_manual_refresh_propagates_network_override(self):
        cached = {"universe_mode": "technology", "tech_universe": {"selected_sectors": []},
                  "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "stocks": []}
        with patch.object(server, "REFRESH_THREAD", None), patch.object(server, "read_dashboard_cache", return_value=cached), patch.object(server, "read_tech_config", return_value=[]), patch.object(server, "attach_research_runs", side_effect=lambda data: data), patch.object(server.threading, "Thread") as thread:
            server.build_dashboard(force_refresh=True)
            self.assertEqual(thread.call_args.kwargs["kwargs"], {"force_network": True})
            thread.return_value.start.assert_called_once()

    def test_previous_close_cannot_shortcut_live_refresh(self):
        now = time.struct_time((2026, 10, 12, 14, 40, 0, 0, 285, -1))
        rows = [{"date": "2026-10-09"}]
        with patch.object(server, "read_kline_cache", return_value=rows), patch.object(server, "read_kline_cache_full", return_value=(rows, "qfq")):
            self.assertFalse(server.cached_klines_are_final("002491", now))
            self.assertIsNone(server.stock_from_kline_cache({"code": "002491", "secid": "0.002491"}, now))

    def test_settled_cache_remains_reusable_outside_session(self):
        now = time.struct_time((2026, 10, 9, 16, 0, 0, 4, 282, -1))
        with patch.object(server, "read_kline_cache", return_value=[{"date": "2026-10-09"}]):
            self.assertTrue(server.cached_klines_are_final("002491", now))

    def test_expected_date_is_yesterday_before_settlement(self):
        friday_before_close = time.struct_time((2026, 10, 9, 14, 59, 0, 4, 282, -1))
        self.assertEqual(server.expected_latest_trading_date(friday_before_close), "2026-10-08")

    def test_expected_date_is_today_after_settlement(self):
        friday_after_close = time.struct_time((2026, 10, 9, 15, 10, 0, 4, 282, -1))
        self.assertEqual(server.expected_latest_trading_date(friday_after_close), "2026-10-09")

    def test_expected_date_skips_the_weekend(self):
        saturday = time.struct_time((2026, 10, 10, 10, 0, 0, 5, 283, -1))
        monday_before_close = time.struct_time((2026, 10, 12, 9, 30, 0, 0, 285, -1))
        self.assertEqual(server.expected_latest_trading_date(saturday), "2026-10-09")
        self.assertEqual(server.expected_latest_trading_date(monday_before_close), "2026-10-09")

    def test_dashboard_without_todays_bar_is_stale(self):
        friday_after_close = time.struct_time((2026, 10, 9, 16, 0, 0, 4, 282, -1))
        stale = {"stocks": [{"code": "000001"}], "latest_date": "2026-10-08", "volume_unit": server.VOLUME_UNIT}
        current = {"stocks": [{"code": "000001"}], "latest_date": "2026-10-09", "volume_unit": server.VOLUME_UNIT}
        self.assertTrue(server.dashboard_data_is_stale(stale, friday_after_close))
        self.assertFalse(server.dashboard_data_is_stale(current, friday_after_close))

    def test_dashboard_without_volume_unit_is_stale(self):
        friday_after_close = time.struct_time((2026, 10, 9, 16, 0, 0, 4, 282, -1))
        legacy = {"stocks": [{"code": "000001"}], "latest_date": "2026-10-09"}
        self.assertTrue(server.dashboard_data_is_stale(legacy, friday_after_close))

    def test_missing_dashboard_is_stale(self):
        self.assertTrue(server.dashboard_data_is_stale(None))
        self.assertTrue(server.dashboard_data_is_stale({"stocks": []}))


class TransportTests(unittest.TestCase):
    class _Response:
        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def test_urllib_is_used_first(self):
        with patch.object(server, "urlopen", return_value=self._Response(b'{"ok":1}')) as fetch, \
             patch.object(server, "subprocess") as subprocess_run:
            body = server._http_get("https://example.invalid/x")
        self.assertEqual(body, b'{"ok":1}')
        fetch.assert_called_once()
        subprocess_run.run.assert_not_called()

    def test_curl_is_used_when_urllib_fails(self):
        fake = type("Result", (), {"stdout": b'{"ok":2}'})()
        with patch.object(server, "urlopen", side_effect=URLError("no tls")), \
             patch.object(server.subprocess, "run", return_value=fake) as subprocess_run:
            body = server._http_get("https://example.invalid/x")
        self.assertEqual(body, b'{"ok":2}')
        self.assertIn("curl", subprocess_run.call_args[0][0][0])

    def test_failure_of_both_transports_is_reported(self):
        with patch.object(server, "urlopen", side_effect=URLError("no tls")), \
             patch.object(server.subprocess, "run", side_effect=OSError("no curl")):
            with self.assertRaises(URLError):
                server._http_get("https://example.invalid/x")


class _WorkspaceKlineDir:
    """Point server.KLINE_CACHE_DIR at a scratch dir inside the workspace."""

    def __enter__(self):
        self.path = Path(__file__).parent / ".cache" / "_test_klines"
        shutil.rmtree(self.path, ignore_errors=True)
        self.path.mkdir(parents=True, exist_ok=True)
        self.original = server.KLINE_CACHE_DIR
        server.KLINE_CACHE_DIR = self.path
        return self.path

    def __exit__(self, *args):
        server.KLINE_CACHE_DIR = self.original
        shutil.rmtree(self.path, ignore_errors=True)
        return False


def workspace_kline_dir():
    return _WorkspaceKlineDir()


if __name__ == "__main__":
    unittest.main()
