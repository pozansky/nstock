#!/usr/bin/env python3
"""Auditable 12-agent research runtime for the local A-share cockpit."""
from pathlib import Path
from datetime import datetime
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
RUNS_DIR = ROOT / "data" / "agent_runs"
LATEST_FILE = RUNS_DIR / "latest.json"
SCHEMA_FILE = ROOT / "agent_output_schema.json"

AGENTS = [
    ("chief", "首席研究 Agent", "指挥层", "制定任务、检查依赖并签发最终研究结论"),
    ("factor_hypothesis", "因子假设 Agent", "研究研发组", "根据历史实验提出可证伪的因子假设"),
    ("factor_developer", "因子开发 Agent", "研究研发组", "把假设限制在白名单公式并执行真实验证"),
    ("model_research", "模型研究 Agent", "研究研发组", "设计因子组合与排名模型"),
    ("market_regime", "市场状态 Agent", "市场情报组", "识别指数、市场广度和波动状态"),
    ("industry_chain", "科技产业链 Agent", "市场情报组", "比较科技细分板块强弱和覆盖"),
    ("event_news", "事件新闻 Agent", "市场情报组", "检查事件数据可用性和异常价格线索"),
    ("experiment_design", "实验设计 Agent", "实验验证组", "固定时间切分、成本和样本外规则"),
    ("backtest", "回测 Agent", "实验验证组", "确定性执行单、双、三因子回测"),
    ("challenger", "Challenger Agent", "实验验证组", "寻找过拟合、回撤和可成交性问题"),
    ("portfolio_manager", "组合经理 Agent", "决策执行组", "在通过审查的策略中选择最多三只"),
    ("risk_execution", "风控执行 Agent", "决策执行组", "设置仓位、流动性约束和人工审批闸门"),
]

TEAMS = [
    {"id": "research", "name": "研究研发组", "agents": ["factor_hypothesis", "factor_developer", "model_research"]},
    {"id": "intelligence", "name": "市场情报组", "agents": ["market_regime", "industry_chain", "event_news"]},
    {"id": "validation", "name": "实验验证组", "agents": ["experiment_design", "backtest", "challenger"]},
    {"id": "decision", "name": "决策执行组", "agents": ["portfolio_manager", "risk_execution"]},
]


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def load_latest():
    try:
        return json.loads(LATEST_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _base_agents():
    return [{"id": key, "name": name, "team": team, "role": role, "status": "等待", "started_at": None,
             "finished_at": None, "summary": "等待上游任务", "evidence": [], "artifact": None}
            for key, name, team, role in AGENTS]


def _agent(run, agent_id):
    return next(item for item in run["agents"] if item["id"] == agent_id)


def _persist(run, run_dir, progress=None):
    run["updated_at"] = _now()
    _write_json(run_dir / "summary.json", run)
    _write_json(LATEST_FILE, run)
    if progress:
        progress(run)


def _execute(run, run_dir, agent_id, worker, progress=None):
    item = _agent(run, agent_id)
    item.update(status="运行中", started_at=_now(), summary="正在处理真实输入")
    _persist(run, run_dir, progress)
    try:
        output = worker() or {}
        status = output.pop("status", "完成")
        item.update(status=status, finished_at=_now(), **output)
    except Exception as exc:
        item.update(status="失败", finished_at=_now(), summary=str(exc), evidence=["该 Agent 未生成结论，下游必须识别此状态"])
    artifact = run_dir / "agents" / f"{agent_id}.json"
    item["artifact"] = str(artifact.relative_to(ROOT))
    _write_json(artifact, item)
    _persist(run, run_dir, progress)
    return item


def _codex_hypotheses(context):
    mode = os.environ.get("NSTOCK_AGENT_LLM", "auto").lower()
    if mode == "off":
        return None, "NSTOCK_AGENT_LLM=off"
    candidates = context.get("factor_candidates", [])[:30]
    prompt = """你是A股科技板块量化研究团队的因子假设Agent。不要调用工具，不要修改文件。
只能从给定候选白名单中选择最多5个candidate，不能创造白名单外公式。根据已有实验和当前市场，给出可证伪的研究理由。
数字必须来自输入，不得编造。输出必须符合JSON Schema。

研究上下文：\n""" + json.dumps({
        "data_date": context.get("data_date"), "universe_size": context.get("universe_size"),
        "breadth": context.get("breadth"), "previous_top": context.get("previous_top", []),
        "candidate_whitelist": candidates,
    }, ensure_ascii=False)
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key and mode in {"auto", "api"}:
        try:
            schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
            payload = {"model": os.environ.get("NSTOCK_AGENT_MODEL", "gpt-5.6-luna"), "input": prompt,
                       "text": {"format": {"type": "json_schema", "name": "factor_hypotheses", "strict": True, "schema": schema}}}
            request = Request("https://api.openai.com/v1/responses", data=json.dumps(payload).encode("utf-8"),
                              headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
            with urlopen(request, timeout=90) as response:
                body = json.loads(response.read().decode("utf-8"))
            texts = [content.get("text", "") for item in body.get("output", []) if item.get("type") == "message"
                     for content in item.get("content", []) if content.get("type") == "output_text"]
            return json.loads("".join(texts)), None
        except Exception as exc:
            if mode == "api":
                return None, f"OpenAI Responses API 失败：{exc}"
    if mode != "codex":
        return None, "未配置 OPENAI_API_KEY；Codex CLI 仅在 NSTOCK_AGENT_LLM=codex 时启用"
    binary = shutil.which("codex")
    if not binary or not SCHEMA_FILE.exists():
        return None, "本机 Codex CLI 或输出 Schema 不可用"
    output = Path(tempfile.mkstemp(prefix="nstock-agent-", suffix=".json")[1])
    try:
        command = [binary, "exec", "--ephemeral", "--ignore-rules", "--ignore-user-config", "-s", "read-only",
                   "-m", "gpt-5.6-luna", "-c", 'model_reasoning_effort="low"', "-C", str(ROOT),
                   "--output-schema", str(SCHEMA_FILE), "-o", str(output), prompt]
        completed = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=90, env=os.environ.copy())
        if completed.returncode != 0:
            return None, (completed.stderr or completed.stdout or "Codex 调用失败")[-800:]
        return json.loads(output.read_text(encoding="utf-8")), None
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        return None, str(exc)
    finally:
        output.unlink(missing_ok=True)


def _pct(value):
    return f"{float(value or 0) * 100:+.2f}%"


def run_research_council(context, factor_runner, backtest_runner, progress=None):
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    run_dir = RUNS_DIR / run_id
    run = {"run_id": run_id, "version": "nstock-multi-agent-v2", "status": "运行中", "started_at": _now(),
           "updated_at": _now(), "completed_at": None, "data_date": context.get("data_date"),
           "universe_signature": context.get("universe_signature"), "universe_size": context.get("universe_size", 0),
           "mission": "在固定科技股池中研究可解释因子，并输出最多3只候选；不自动下单",
           "teams": TEAMS, "agents": _base_agents(), "decision": None, "warnings": [],
           "artifact_dir": str(run_dir.relative_to(ROOT))}
    _persist(run, run_dir, progress)

    _execute(run, run_dir, "chief", lambda: {
        "summary": "已拆分为研究研发、市场情报、实验验证、决策执行四条工作流",
        "evidence": [f"研究日 {context.get('data_date')}", f"固定科技股池 {context.get('universe_size', 0)} 只", "最终持仓上限 3 只"],
        "output": {"objective": run["mission"], "gates": ["真实数据", "可执行公式", "成本后回测", "Challenger审查", "人工批准"]}
    }, progress)

    def hypothesis_worker():
        result, error = _codex_hypotheses(context)
        if result:
            return {"summary": result["summary"], "evidence": ["推理引擎：Codex CLI（只读、结构化输出）"], "output": result, "llm": "codex"}
        fallback = context.get("factor_candidates", [])[:5]
        return {"status": "降级", "summary": "Codex 不可用，保留白名单候选但不冒充 LLM 假设",
                "evidence": [error or "未知错误"], "output": {"hypotheses": [{"candidate": item["key"], "rationale": "等待 LLM 研究解释", "expectation": "仅进入代码验证"} for item in fallback]}, "llm": "unavailable"}
    hypothesis = _execute(run, run_dir, "factor_hypothesis", hypothesis_worker, progress)

    factor_run = None
    def factor_worker():
        nonlocal factor_run
        factor_run = factor_runner()
        validation = factor_run.get("validation", {})
        passed = [item for item in validation.get("results", []) if item.get("gate") == "通过"]
        return {"summary": f"{factor_run.get('search_space_size', 0)} 个白名单公式完成验证，{len(passed)} 个通过门槛",
                "evidence": [f"全量确认股池 {factor_run.get('confirmation_universe', 0)} 只", f"历史日线 {validation.get('history_bars', 0)} 根", f"引擎 {factor_run.get('engine_version')}"] ,
                "output": {"passed_count": len(passed), "top_factors": [{k: item.get(k) for k in ("key", "name", "formula", "direction", "mean_rank_ic", "icir", "gate")} for item in passed[:12]], "hypothesis_input": hypothesis.get("output", {})}}
    factor_agent = _execute(run, run_dir, "factor_developer", factor_worker, progress)

    _execute(run, run_dir, "model_research", lambda: {
        "summary": "采用方向调整后的截面百分位等权模型，并限制组合最多3个异质因子",
        "evidence": ["单因子、双因子、三因子分别比较", "按成本后 Sharpe 排名", "因子家族去重，降低共线性"],
        "output": {"model": "direction-adjusted cross-sectional rank ensemble", "max_factors": 3, "max_positions": 3}
    } if factor_agent["status"] != "失败" else {"status": "阻断", "summary": "因子开发失败，不能设计模型", "evidence": []}, progress)

    market = context.get("market", {})
    breadth = context.get("breadth", {})
    index_changes = [item.get("pct") for item in market.values() if item and isinstance(item.get("pct"), (int, float))]
    avg_index = sum(index_changes) / len(index_changes) if index_changes else 0
    up, down = breadth.get("up", 0), breadth.get("down", 0)
    regime = "偏多" if avg_index > .3 and up > down else "偏弱" if avg_index < -.3 or down > up * 1.2 else "震荡"
    _execute(run, run_dir, "market_regime", lambda: {"summary": f"当前市场状态：{regime}",
        "evidence": [f"指数平均涨跌 {avg_index:+.2f}%", f"科技股上涨/下跌 {up}/{down}", f"数据截止 {context.get('data_date')}"],
        "output": {"regime": regime, "average_index_pct": round(avg_index, 3), "breadth": breadth}}, progress)

    sector_rows = []
    for sector in context.get("sectors", []):
        changes = [stock.get("pct", 0) for stock in context.get("stocks", []) if sector["key"] in stock.get("tech_sectors", [])]
        sector_rows.append({"key": sector["key"], "name": sector["name"], "count": len(changes), "average_pct": round(sum(changes) / len(changes), 3) if changes else None})
    sector_rows.sort(key=lambda item: item["average_pct"] if item["average_pct"] is not None else -999, reverse=True)
    _execute(run, run_dir, "industry_chain", lambda: {"summary": f"已比较 {len(sector_rows)} 个科技细分，当前相对强势：{sector_rows[0]['name'] if sector_rows else '无数据'}",
        "evidence": [f"{item['name']} {item['count']}只 / 均值 {item['average_pct']:+.2f}%" for item in sector_rows[:4] if item["average_pct"] is not None], "output": {"sector_ranking": sector_rows}}, progress)

    movers = sorted(context.get("stocks", []), key=lambda stock: abs(stock.get("pct", 0)), reverse=True)[:10]
    _execute(run, run_dir, "event_news", lambda: {"status": "数据受限", "summary": "公告与新闻正文源尚未接入；仅输出真实行情异常观察名单",
        "evidence": [f"{item.get('name')}({item.get('code')}) {item.get('pct', 0):+.2f}%" for item in movers[:5]],
        "output": {"news_provider": None, "price_anomaly_watchlist": [{"code": item.get("code"), "name": item.get("name"), "pct": item.get("pct")} for item in movers], "trade_veto": False}}, progress)

    manifest = context.get("history", {})
    first_date, last_date = manifest.get("first_date"), manifest.get("last_date")
    _execute(run, run_dir, "experiment_design", lambda: {"summary": "采用时间顺序切分和滚动样本外复核；禁止随机打散时间序列",
        "evidence": [f"历史区间 {first_date or '—'} 至 {last_date or '—'}", "15bp × 换手率", "信号T日，T+1建仓，T+2退出"],
        "output": {"split": {"train": "前60%", "validation": "中20%", "test": "后20%"}, "walk_forward": True, "cost_bps": 15, "leakage_policy": "测试集不得用于选择因子"}}, progress)

    backtest_run = None
    def backtest_worker():
        nonlocal backtest_run
        backtest_run = backtest_runner()
        top = (backtest_run.get("results") or [{}])[0]
        result = top.get("result", {})
        return {"summary": f"完成 {backtest_run.get('combination_count', 0)} 组确定性回测；最佳成本后 Sharpe {result.get('sharpe', 0):.2f}",
                "evidence": [f"可回测 {backtest_run.get('usable_universe', 0)} 只", f"累计 {_pct(result.get('cumulative_return'))}", f"最大回撤 {_pct(-result.get('max_drawdown', 0))}"],
                "output": {"top_strategy": top, "engine_version": backtest_run.get("engine_version"), "selection_rule": backtest_run.get("selection_rule")}}
    backtest_agent = _execute(run, run_dir, "backtest", backtest_worker, progress)

    def challenger_worker():
        if not backtest_run or not backtest_run.get("results"):
            return {"status": "阻断", "summary": "没有回测结果可审查", "evidence": []}
        result = backtest_run["results"][0].get("result", {})
        findings = ["当前策略排名仍来自同一研究样本，必须继续做滚动样本外观察"]
        severity = "警告"
        if result.get("max_drawdown", 0) > .20: findings.append(f"最大回撤 {_pct(-result['max_drawdown'])} 超过20%")
        if result.get("annual_return", 0) > .50: findings.append(f"年化 {_pct(result['annual_return'])} 偏高，需排查过拟合与可成交性")
        if result.get("days", 0) < 252: findings.append("有效样本不足一年")
        return {"status": severity, "summary": f"发现 {len(findings)} 项需要人工关注的问题", "evidence": findings,
                "output": {"approved_for_research": True, "approved_for_live_trading": False, "findings": findings}}
    challenger = _execute(run, run_dir, "challenger", challenger_worker, progress)

    def portfolio_worker():
        if backtest_agent["status"] == "失败" or not backtest_run or not backtest_run.get("results"):
            return {"status": "阻断", "summary": "回测未通过，组合经理不输出股票", "evidence": [], "output": {"holdings": []}}
        top = backtest_run["results"][0]
        holdings = top.get("result", {}).get("latest_holdings", [])[:3]
        return {"summary": f"选择 #1 策略并输出 {len(holdings)} 只等权研究候选",
                "evidence": [f"{item.get('name')}({item.get('code')})" for item in holdings] + ["Challenger仅批准研究用途"],
                "output": {"strategy": {"factors": top.get("factors"), "names": top.get("names")}, "holdings": holdings, "weights": [round(1 / len(holdings), 4)] * len(holdings) if holdings else []}}
    portfolio = _execute(run, run_dir, "portfolio_manager", portfolio_worker, progress)

    risk = _execute(run, run_dir, "risk_execution", lambda: {"summary": "研究组合已加人工审批闸门，未产生任何订单",
        "evidence": ["最多3只、等权", "剔除近20日成交额最低30%", "科创板/ST排除", "15bp成本", "禁止自动下单"],
        "output": {"max_positions": 3, "position_weight": "1/3", "human_approval_required": True, "orders_created": 0, "live_trading_allowed": False}}, progress)

    holdings = portfolio.get("output", {}).get("holdings", [])
    top_result = ((backtest_run or {}).get("results") or [{}])[0]
    run["decision"] = {"status": "等待人工批准" if holdings else "阻断", "strategy": top_result.get("names", []),
                       "holdings": holdings, "max_positions": 3, "orders_created": 0,
                       "challenger_status": challenger.get("status"), "risk_status": risk.get("status")}
    run["warnings"] = challenger.get("output", {}).get("findings", []) + ["事件新闻 Agent 尚未接入公告与新闻正文源"]
    run["status"] = "等待人工批准" if holdings else "阻断"
    run["completed_at"] = _now()
    chief = _agent(run, "chief")
    chief["summary"] = f"四个团队已完成会签；输出 {len(holdings)} 只研究候选，订单数 0"
    chief["finished_at"] = run["completed_at"]
    _write_json(run_dir / "agents" / "chief.json", chief)
    _persist(run, run_dir, progress)
    return run
