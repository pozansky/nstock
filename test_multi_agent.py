import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import multi_agent
from multi_agent import _decode_mcp_records


class ChiefOpinionsParsingTests(unittest.TestCase):
    def test_decodes_structured_content_and_nested_opinion(self):
        raw = json.dumps({"title": "观点标题", "content": "观点正文"}, ensure_ascii=False)
        result = {"structuredContent": {"result": [{
            "opi_pubtime": "2026-09-23T10:00:00",
            "publish_time_beijing": "2026-09-23 10:00:00",
            "opi_all": raw,
        }]}}

        records = _decode_mcp_records(result)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["title"], "观点标题")
        self.assertEqual(records[0]["content"], "观点正文")
        self.assertEqual(records[0]["opi_all"], raw)

    def test_falls_back_to_text_content_and_deduplicates(self):
        record = {"opi_pubtime": "2026-09-23T10:00:00", "opi_all": "plain text"}
        result = {"content": [
            {"type": "text", "text": json.dumps(record)},
            {"type": "text", "text": json.dumps(record)},
            {"type": "image", "data": "ignored"},
        ]}

        records = _decode_mcp_records(result)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["content"], "plain text")


class DeepSeekHypothesisTests(unittest.TestCase):
    def test_uses_structured_deepseek_output_and_deduplicates_candidates(self):
        body = {"choices": [{"message": {"content": json.dumps({
            "summary": "市场偏多，验证动量与波动假设。",
            "hypotheses": [
                {"candidate": "momentum20", "rationale": "上涨广度较高", "expectation": "Rank IC 为正"},
                {"candidate": "momentum20", "rationale": "重复项", "expectation": "应被去重"},
            ],
            "generated_factors": [
                {"name": "动量乘积", "left": "momentum20", "operator": "multiply", "right": "momentum20", "rationale": "检验非线性", "expectation": "方向由样本确定"},
                {"name": "动量差值", "left": "momentum20", "operator": "difference", "right": "momentum20", "rationale": "检验差值", "expectation": "应接近零"},
                {"name": "动量比率", "left": "momentum20", "operator": "ratio", "right": "momentum20", "rationale": "检验比率", "expectation": "方向由样本确定"},
            ],
            "risks": ["样本内偏差"],
        }, ensure_ascii=False)}}]}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(body, ensure_ascii=False).encode("utf-8")
        context = {"data_date": "2026-09-29", "universe_size": 896, "breadth": {"up": 599, "down": 297},
                   "factor_candidates": [{"key": "momentum20", "name": "20日动量", "formula": "return20"}]}
        env = {"NSTOCK_AGENT_LLM": "deepseek", "DEEPSEEK_API_KEY": "test-key",
               "DEEPSEEK_MODEL": "deepseek-flash", "DEEPSEEK_DISPLAY_NAME": "DeepSeek V4.1 Flash"}

        with patch.dict(os.environ, env, clear=True), patch.object(multi_agent, "urlopen", return_value=response):
            result, error, engine = multi_agent._codex_hypotheses(context)

        self.assertIsNone(error)
        self.assertEqual(engine, "DeepSeek V4.1 Flash")
        self.assertEqual([item["candidate"] for item in result["hypotheses"]], ["momentum20"])

    def test_retries_when_deepseek_returns_empty_content(self):
        empty_body = {"choices": [{"finish_reason": "length", "message": {"content": ""}}]}
        valid_body = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
            "summary": "放量突破值得验证。",
            "hypotheses": [{"candidate": "momentum20", "rationale": "量价确认", "expectation": "Rank IC 为正"}],
            "generated_factors": [
                {"name": "生成一", "left": "momentum20", "operator": "multiply", "right": "momentum20", "rationale": "非线性", "expectation": "待验证"},
                {"name": "生成二", "left": "momentum20", "operator": "difference", "right": "momentum20", "rationale": "差值", "expectation": "待验证"},
                {"name": "生成三", "left": "momentum20", "operator": "confirm", "right": "momentum20", "rationale": "条件确认", "expectation": "待验证"},
            ],
            "risks": ["假突破"],
        }, ensure_ascii=False)}}]}
        empty_response = MagicMock()
        empty_response.__enter__.return_value.read.return_value = json.dumps(empty_body).encode("utf-8")
        valid_response = MagicMock()
        valid_response.__enter__.return_value.read.return_value = json.dumps(valid_body, ensure_ascii=False).encode("utf-8")
        context = {"factor_candidates": [{"key": "momentum20", "name": "20日动量", "formula": "return20"}]}
        env = {"NSTOCK_AGENT_LLM": "deepseek", "DEEPSEEK_API_KEY": "test-key", "DEEPSEEK_MODEL": "deepseek-flash"}

        with patch.dict(os.environ, env, clear=True), patch.object(
            multi_agent, "urlopen", side_effect=[empty_response, valid_response]
        ) as mocked_urlopen:
            result, error, _ = multi_agent._codex_hypotheses(context)

        self.assertIsNone(error)
        self.assertEqual(result["hypotheses"][0]["candidate"], "momentum20")
        self.assertEqual(mocked_urlopen.call_count, 2)


class CouncilContractTests(unittest.TestCase):
    def test_runs_all_agents_and_keeps_execution_disabled(self):
        context = {
            "data_date": "2026-09-29", "universe_signature": "test", "universe_size": 1,
            "breadth": {"up": 1, "down": 0}, "market": {"index": {"pct": 0.5}},
            "stocks": [{"code": "000001", "name": "测试股票", "pct": 1.2, "tech_sectors": ["ai"],
                        "factors": {"volatility20": 0.9}}],
            "sectors": [{"key": "ai", "name": "AI"}],
            "history": {"first_date": "2025-01-01", "last_date": "2026-09-29"},
            "factor_candidates": [{"key": "momentum20", "name": "20日动量", "formula": "return20"}],
        }
        factor_run = {"search_space_size": 1, "confirmation_universe": 1, "engine_version": "test",
                      "validation": {"history_bars": 300, "results": [{"key": "momentum20", "name": "20日动量",
                      "formula": "return20", "direction": "正向", "mean_rank_ic": 0.05, "icir": 0.3,
                      "gate": "通过"}]}}
        holding = {"code": "000001", "name": "测试股票"}
        backtest_run = {"combination_count": 1, "usable_universe": 1, "engine_version": "test",
                        "selection_rule": "test", "results": [{"names": ["20日动量"], "factors": ["momentum20"],
                        "result": {"sharpe": 1.1, "cumulative_return": 0.1, "annual_return": 0.1,
                        "max_drawdown": 0.1, "days": 300, "annualization_reliable": True,
                        "latest_holdings": [holding]}}]}
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"NSTOCK_AGENT_LLM": "off"}, clear=True):
            root = Path(temporary)
            with patch.object(multi_agent, "ROOT", root), \
                 patch.object(multi_agent, "RUNS_DIR", root / "runs"), \
                 patch.object(multi_agent, "LATEST_FILE", root / "runs" / "latest.json"), \
                 patch.object(multi_agent, "CHIEF_OPINIONS_DIR", root / "opinions"), \
                 patch.object(multi_agent, "CHIEF_OPINIONS_LATEST_FILE", root / "opinions" / "latest.json"), \
                 patch.object(multi_agent, "CHIEF_OPINIONS_STOCKS_DIR", root / "opinions" / "stocks"), \
                 patch.object(multi_agent, "_load_stock_opinion_cache", return_value={
                     "opinions": [{"title": "AI 产业订单提速", "content": "订单增长，同时关注估值风险。",
                                   "publish_time_beijing": "2026-09-29 09:30:00"}]
                 }):
                def factor_runner(report):
                    report("抽样初筛", "测试因子初筛")
                    return factor_run

                def backtest_runner(report):
                    report("组合回测", "测试组合回测")
                    return backtest_run

                run = multi_agent.run_research_council(context, factor_runner, backtest_runner)
                quick_context = {**context, "reuse_research": True, "execution_mode": "快速会签（复用因子）"}
                quick_run = multi_agent.run_research_council(quick_context, factor_runner, backtest_runner)

        self.assertEqual(len(run["agents"]), 12)
        self.assertEqual(run["status"], "等待人工批准")
        self.assertEqual(run["decision"]["orders_created"], 0)
        self.assertFalse(next(item for item in run["agents"] if item["id"] == "risk_execution")["output"]["live_trading_allowed"])
        self.assertEqual(run["teams"][0]["id"], "intelligence")
        market_agent = next(item for item in run["agents"] if item["id"] == "market_regime")
        self.assertEqual(market_agent["output"]["median_stock_pct"], 1.2)
        self.assertEqual(market_agent["output"]["high_volatility_count"], 1)
        sector_agent = next(item for item in run["agents"] if item["id"] == "industry_chain")
        leading_sector = sector_agent["output"]["sector_ranking"][0]
        self.assertEqual(leading_sector["up_ratio"], 1.0)
        self.assertEqual(leading_sector["leaders"][0]["code"], "000001")
        self.assertIn("strength_score", leading_sector)
        event_agent = next(item for item in run["agents"] if item["id"] == "event_news")
        self.assertEqual(event_agent["status"], "完成")
        self.assertEqual(event_agent["output"]["news_provider"], "chief-opinions local library")
        self.assertEqual(event_agent["output"]["event_signals"][0]["title"], "AI 产业订单提速")
        factor_agent = next(item for item in run["agents"] if item["id"] == "factor_developer")
        self.assertIn("抽样初筛", [step["stage"] for step in factor_agent["process"]])
        risk_agent = next(item for item in run["agents"] if item["id"] == "risk_execution")
        self.assertIn("chief_opinions_reviewed", risk_agent["output"])
        self.assertEqual(quick_run["execution_mode"], "快速会签（复用因子）")
        for agent_id in ("factor_hypothesis", "factor_developer", "model_research", "backtest"):
            self.assertEqual(next(item for item in quick_run["agents"] if item["id"] == agent_id)["status"], "复用")


if __name__ == "__main__":
    unittest.main()
