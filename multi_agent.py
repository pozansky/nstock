#!/usr/bin/env python3
"""Auditable 12-agent research runtime for the local A-share cockpit."""
from pathlib import Path
from datetime import datetime
import json
import inspect
import math
import os
import statistics
import shutil
import subprocess
import tempfile
import time
import uuid
from jsonschema import ValidationError, validate
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
RUNS_DIR = ROOT / "data" / "agent_runs"
LATEST_FILE = RUNS_DIR / "latest.json"
SCHEMA_FILE = ROOT / "agent_output_schema.json"
CHIEF_OPINIONS_DIR = ROOT / "data" / "chief_opinions"
CHIEF_OPINIONS_LATEST_FILE = CHIEF_OPINIONS_DIR / "latest.json"
CHIEF_OPINIONS_STOCKS_DIR = CHIEF_OPINIONS_DIR / "stocks"
DEFAULT_CHIEF_OPINIONS_MCP_URL = "https://aizg.abctougu.com:18443/chief-opinions/"


def _load_local_env():
    try:
        lines = (ROOT / ".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_local_env()

AGENTS = [
    ("chief", "首席研究 Agent", "指挥层", "制定任务、检查依赖并签发最终研究结论"),
    ("factor_hypothesis", "因子假设 Agent", "研究研发组", "根据历史实验提出可证伪的因子假设"),
    ("factor_developer", "因子开发 Agent", "研究研发组", "把新假设编译为受控公式并执行真实验证"),
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

AGENT_DEPENDENCIES = {
    "chief": [],
    "factor_hypothesis": ["market_regime", "industry_chain", "event_news"],
    "factor_developer": ["factor_hypothesis"],
    "model_research": ["factor_developer"],
    "market_regime": [],
    "industry_chain": [],
    "event_news": [],
    "experiment_design": [],
    "backtest": ["factor_developer", "model_research", "experiment_design"],
    "challenger": ["backtest"],
    "portfolio_manager": ["backtest", "challenger", "market_regime", "industry_chain"],
    "risk_execution": ["portfolio_manager", "challenger"],
}

BLOCKING_STATUSES = {"失败", "阻断"}

TEAMS = [
    {"id": "intelligence", "name": "市场情报组", "agents": ["market_regime", "industry_chain", "event_news"]},
    {"id": "research", "name": "研究研发组", "agents": ["factor_hypothesis", "factor_developer", "model_research"]},
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
             "finished_at": None, "duration_ms": None, "confidence": None, "data_quality": "待评估",
             "depends_on": AGENT_DEPENDENCIES.get(key, []), "summary": "等待上游任务", "evidence": [],
             "artifact": None, "process": []}
            for key, name, team, role in AGENTS]


def _agent(run, agent_id):
    return next(item for item in run["agents"] if item["id"] == agent_id)


def _persist(run, run_dir, progress=None):
    run["updated_at"] = _now()
    _write_json(run_dir / "summary.json", run)
    _write_json(LATEST_FILE, run)
    if progress:
        progress(run)


def _execute(run, run_dir, agent_id, worker, progress=None, enforce_dependencies=True):
    item = _agent(run, agent_id)
    blockers = [dependency for dependency in item.get("depends_on", [])
                if _agent(run, dependency).get("status") in BLOCKING_STATUSES]
    if enforce_dependencies and blockers:
        item.update(status="阻断", started_at=_now(), finished_at=_now(), duration_ms=0, confidence=0,
                    data_quality="不可用", summary=f"上游依赖失败：{', '.join(blockers)}",
                    evidence=[f"{dependency}: {_agent(run, dependency).get('summary', '无结论')}" for dependency in blockers],
                    output={"blocked_by": blockers})
        artifact = run_dir / "agents" / f"{agent_id}.json"
        item["artifact"] = str(artifact.relative_to(ROOT))
        _write_json(artifact, item)
        _persist(run, run_dir, progress)
        return item
    started = time.monotonic()
    item.update(status="运行中", started_at=_now(), summary="正在处理真实输入",
                process=[{"time": _now(), "stage": "开始", "detail": "读取真实输入"}])
    _persist(run, run_dir, progress)
    def report(stage, detail):
        item["summary"] = detail
        item.setdefault("process", []).append({"time": _now(), "stage": stage, "detail": detail})
        _persist(run, run_dir, progress)
    try:
        parameters = inspect.signature(worker).parameters.values()
        accepts_progress = any(parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD)
                               for parameter in parameters) or bool(parameters)
        output = (worker(report) if accepts_progress else worker()) or {}
        status = output.pop("status", "完成")
        item.update(status=status, finished_at=_now(), **output)
        item.setdefault("process", []).append({"time": _now(), "stage": "完成", "detail": item.get("summary", "已完成")})
    except Exception as exc:
        item.update(status="失败", finished_at=_now(), confidence=0, data_quality="不可用",
                    summary=str(exc), evidence=["该 Agent 未生成结论，下游必须识别此状态"])
        item.setdefault("process", []).append({"time": _now(), "stage": "失败", "detail": str(exc)})
    item["duration_ms"] = round((time.monotonic() - started) * 1000)
    item.setdefault("confidence", None)
    item.setdefault("data_quality", "待评估")
    artifact = run_dir / "agents" / f"{agent_id}.json"
    item["artifact"] = str(artifact.relative_to(ROOT))
    _write_json(artifact, item)
    _persist(run, run_dir, progress)
    return item


def _load_cached_chief_opinions():
    try:
        return json.loads(CHIEF_OPINIONS_LATEST_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _load_stock_opinion_cache(stock):
    code = str(stock.get("code", "")).strip()
    if not code:
        return None
    try:
        payload = json.loads((CHIEF_OPINIONS_STOCKS_DIR / f"{code}.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if str(payload.get("code", "")) != code or not isinstance(payload.get("opinions"), list):
        return None
    return payload


def load_chief_opinion_cache(code):
    """Return one stock's local opinion cache without making a network request."""
    normalized = str(code).strip()
    if len(normalized) != 6 or not normalized.isdigit():
        return None
    return _load_stock_opinion_cache({"code": normalized})


def _mcp_request(method, params, token, endpoint):
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("CHIEF_OPINIONS_MCP_URL 必须是有效的 HTTPS 地址")
    body = json.dumps({"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params},
                      ensure_ascii=False).encode("utf-8")
    request = Request(endpoint, data=body, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "nstock-chief-opinions/1.0",
    }, method="POST")
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"首席观点 MCP HTTP {exc.code}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"首席观点 MCP 请求失败：{exc}") from exc
    if payload.get("error"):
        error = payload["error"]
        raise RuntimeError(f"首席观点 MCP 错误：{error.get('message', error)}")
    return payload.get("result", {})


def _decode_mcp_records(result):
    structured = result.get("structuredContent", {})
    records = structured.get("result") if isinstance(structured, dict) else None
    if not isinstance(records, list):
        records = []
        for item in result.get("content", []):
            if item.get("type") != "text":
                continue
            try:
                decoded = json.loads(item.get("text", ""))
            except (TypeError, json.JSONDecodeError):
                continue
            records.extend(decoded if isinstance(decoded, list) else [decoded])
    normalized = []
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        raw_opinion = record.get("opi_all")
        opinion = {}
        if isinstance(raw_opinion, str):
            try:
                decoded = json.loads(raw_opinion)
                if isinstance(decoded, dict):
                    opinion = decoded
            except json.JSONDecodeError:
                opinion = {"content": raw_opinion}
        identity = (record.get("opi_pubtime"), raw_opinion)
        if identity in seen:
            continue
        seen.add(identity)
        normalized.append({
            "opi_pubtime": record.get("opi_pubtime"),
            "publish_time_beijing": record.get("publish_time_beijing"),
            "title": opinion.get("title"),
            "content": opinion.get("content"),
            "opi_all": raw_opinion,
        })
    return normalized


def fetch_chief_opinions(stocks, limit=None):
    """Fetch and persist recent read-only chief opinions for selected stocks."""
    selected = [stock for stock in stocks[:3] if stock.get("code") or stock.get("name")]
    if not selected:
        return None, "没有研究候选，不查询首席观点"
    cached_stocks = [_load_stock_opinion_cache(stock) for stock in selected]
    if all(item is not None for item in cached_stocks):
        payload = {
            "source": "chief-opinions MCP local cache",
            "server_url": os.environ.get("CHIEF_OPINIONS_MCP_URL", DEFAULT_CHIEF_OPINIONS_MCP_URL).strip(),
            "tool": "get_stock_chief_opinions",
            "fetched_at": max((item.get("fetched_at", "") for item in cached_stocks), default=None),
            "authorization_stored": False,
            "requested_limit": max((item.get("requested_limit", 0) for item in cached_stocks), default=0),
            "stocks": cached_stocks,
            "errors": [],
            "cache_hit": True,
        }
        _write_json(CHIEF_OPINIONS_LATEST_FILE, payload)
        return payload, None
    endpoint = os.environ.get("CHIEF_OPINIONS_MCP_URL", DEFAULT_CHIEF_OPINIONS_MCP_URL).strip()
    token = os.environ.get("CHIEF_OPINIONS_TOKEN", "").strip()
    if not token:
        cached = _load_cached_chief_opinions()
        wanted = {str(stock.get("code", "")) for stock in selected}
        cached_stocks = [stock for stock in (cached or {}).get("stocks", []) if str(stock.get("code", "")) in wanted]
        if cached and cached_stocks:
            return {**cached, "stocks": cached_stocks}, "未配置 CHIEF_OPINIONS_TOKEN；使用匹配本轮候选的缓存"
        return None, "未配置 CHIEF_OPINIONS_TOKEN"
    requested_limit = limit if limit is not None else os.environ.get("CHIEF_OPINIONS_LIMIT", "3")
    try:
        requested_limit = max(1, min(10, int(requested_limit)))
    except (TypeError, ValueError):
        requested_limit = 3
    results, errors = [], []
    for stock in selected:
        code, name = str(stock.get("code", "")), stock.get("name")
        if not code and not name:
            continue
        try:
            result = _mcp_request("tools/call", {"name": "get_stock_chief_opinions",
                                  "arguments": {"stock": code or name, "limit": requested_limit}}, token, endpoint)
            opinions = _decode_mcp_records(result)
            stock_payload = {"code": code, "name": name, "fetched_at": _now(),
                             "requested_limit": requested_limit, "opinion_count": len(opinions), "opinions": opinions}
            results.append(stock_payload)
            if code:
                _write_json(CHIEF_OPINIONS_STOCKS_DIR / f"{code}.json", stock_payload)
        except Exception as exc:
            errors.append({"code": code, "name": name, "error": str(exc)})
    payload = {
        "source": "chief-opinions MCP",
        "server_url": endpoint,
        "tool": "get_stock_chief_opinions",
        "fetched_at": _now(),
        "authorization_stored": False,
        "requested_limit": requested_limit,
        "stocks": results,
        "errors": errors,
    }
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    _write_json(CHIEF_OPINIONS_DIR / f"{stamp}.json", payload)
    _write_json(CHIEF_OPINIONS_LATEST_FILE, payload)
    error = f"{len(errors)} 只股票查询失败" if errors else None
    return payload, error


def _codex_hypotheses(context):
    mode = os.environ.get("NSTOCK_AGENT_LLM", "auto").lower()
    if mode == "off":
        return None, "NSTOCK_AGENT_LLM=off", None
    candidates = context.get("factor_candidates", [])[:40]
    prompt = """你是A股科技板块量化研究团队的因子假设Agent。不要调用工具，不要修改文件。
从给定候选白名单中选择最多5个已有candidate，同时必须产生3至8个新的结构化组合公式。新公式的left和right只能引用白名单key，operator只能是multiply、difference、ratio、confirm；不得输出自由文本代码。
multiply为两因子相乘，difference为left-right，ratio为left/(abs(right)+epsilon)，confirm为left*max(right,0)。根据已有实验和当前市场，给出可证伪的研究理由。
数字必须来自输入，不得编造。输出必须符合JSON Schema。

研究上下文：\n""" + json.dumps({
        "data_date": context.get("data_date"), "universe_size": context.get("universe_size"),
        "breadth": context.get("breadth"), "previous_top": context.get("previous_top", []),
        "candidate_whitelist": candidates,
    }, ensure_ascii=False)
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if deepseek_key and mode in {"auto", "deepseek"}:
        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash").strip()
        endpoint = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/chat/completions"
        try:
            result = None
            for attempt, max_tokens in enumerate((4096, 8192), 1):
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "只输出符合给定 JSON Schema 的 JSON 对象，不要输出 Markdown。"},
                        {"role": "user", "content": prompt + "\n\nJSON Schema：\n" + json.dumps(schema, ensure_ascii=False)},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.1,
                    "max_tokens": max_tokens,
                }
                request = Request(endpoint, data=json.dumps(payload).encode("utf-8"),
                                  headers={"Authorization": f"Bearer {deepseek_key}", "Content-Type": "application/json"}, method="POST")
                with urlopen(request, timeout=90) as response:
                    body = json.loads(response.read().decode("utf-8"))
                choice = body["choices"][0]
                content = (choice.get("message", {}).get("content") or "").strip()
                if not content:
                    if attempt < 2:
                        continue
                    raise ValueError(f"模型返回空 content（finish_reason={choice.get('finish_reason', 'unknown')}）")
                if content.startswith("```"):
                    content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                try:
                    result = json.loads(content)
                    break
                except json.JSONDecodeError:
                    if attempt == 2:
                        raise
            if result is None:
                raise ValueError("模型未返回可解析的结构化结果")
            unique, seen_candidates = [], set()
            for item in result.get("hypotheses", []):
                candidate = item.get("candidate")
                if candidate not in seen_candidates:
                    seen_candidates.add(candidate)
                    unique.append(item)
            result["hypotheses"] = unique
            validate(instance=result, schema=schema)
            allowed = {item.get("key") for item in candidates}
            invalid = [item.get("candidate") for item in result.get("hypotheses", []) if item.get("candidate") not in allowed]
            if invalid:
                raise ValueError(f"模型返回白名单外候选：{', '.join(str(item) for item in invalid)}")
            generated, seen_formulas = [], set()
            for item in result.get("generated_factors", []):
                left, right, operator = item.get("left"), item.get("right"), item.get("operator")
                if left not in allowed or right not in allowed:
                    raise ValueError(f"新公式引用白名单外原语：{left}, {right}")
                identity = (left, operator, right)
                if identity in seen_formulas:
                    continue
                seen_formulas.add(identity)
                generated.append(item)
            if len(generated) < 3:
                raise ValueError("去重后新公式不足 3 个")
            result["generated_factors"] = generated
            engine = os.environ.get("DEEPSEEK_DISPLAY_NAME", f"DeepSeek {model}").strip()
            return result, None, engine
        except (HTTPError, URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError, ValidationError, ValueError) as exc:
            if mode == "deepseek":
                engine = os.environ.get("DEEPSEEK_DISPLAY_NAME", f"DeepSeek {model}").strip()
                return None, f"DeepSeek API 失败：{exc}", engine
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key and mode in {"auto", "api"}:
        try:
            payload = {"model": os.environ.get("NSTOCK_AGENT_MODEL", "gpt-5.6-luna"), "input": prompt,
                       "text": {"format": {"type": "json_schema", "name": "factor_hypotheses", "strict": True, "schema": schema}}}
            request = Request("https://api.openai.com/v1/responses", data=json.dumps(payload).encode("utf-8"),
                              headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
            with urlopen(request, timeout=90) as response:
                body = json.loads(response.read().decode("utf-8"))
            texts = [content.get("text", "") for item in body.get("output", []) if item.get("type") == "message"
                     for content in item.get("content", []) if content.get("type") == "output_text"]
            return json.loads("".join(texts)), None, "OpenAI Responses API"
        except Exception as exc:
            if mode == "api":
                return None, f"OpenAI Responses API 失败：{exc}", "OpenAI Responses API"
    if mode != "codex":
        return None, "未配置可用的模型 API；Codex CLI 仅在 NSTOCK_AGENT_LLM=codex 时启用", None
    binary = shutil.which("codex")
    if not binary or not SCHEMA_FILE.exists():
        return None, "本机 Codex CLI 或输出 Schema 不可用", "Codex CLI"
    output = Path(tempfile.mkstemp(prefix="nstock-agent-", suffix=".json")[1])
    try:
        command = [binary, "exec", "--ephemeral", "--ignore-rules", "--ignore-user-config", "-s", "read-only",
                   "-m", "gpt-5.6-luna", "-c", 'model_reasoning_effort="low"', "-C", str(ROOT),
                   "--output-schema", str(SCHEMA_FILE), "-o", str(output), prompt]
        completed = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=90, env=os.environ.copy())
        if completed.returncode != 0:
            return None, (completed.stderr or completed.stdout or "Codex 调用失败")[-800:], "Codex CLI"
        return json.loads(output.read_text(encoding="utf-8")), None, "Codex CLI"
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        return None, str(exc), "Codex CLI"
    finally:
        output.unlink(missing_ok=True)


def _pct(value):
    return f"{float(value or 0) * 100:+.2f}%"


def run_research_council(context, factor_runner, backtest_runner, progress=None):
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    run_dir = RUNS_DIR / run_id
    run = {"run_id": run_id, "version": "nstock-multi-agent-v3", "status": "运行中", "started_at": _now(),
           "updated_at": _now(), "completed_at": None, "data_date": context.get("data_date"),
           "execution_mode": context.get("execution_mode", "允许复用"),
           "universe_signature": context.get("universe_signature"), "universe_size": context.get("universe_size", 0),
           "mission": "在固定科技股池中研究可解释因子，并输出最多3只候选；不自动下单",
           "teams": TEAMS, "agents": _base_agents(), "decision": None, "warnings": [],
           "external_sources": {"chief_opinions": {"provider": "chief-opinions MCP",
                                "configured": bool(os.environ.get("CHIEF_OPINIONS_TOKEN")),
                                "credential_in_artifact": False}},
           "artifact_dir": str(run_dir.relative_to(ROOT))}
    _persist(run, run_dir, progress)

    _execute(run, run_dir, "chief", lambda: {
        "summary": "先做市场情报，再进入因子研发、实验验证与决策执行",
        "evidence": [f"研究日 {context.get('data_date')}", f"固定科技股池 {context.get('universe_size', 0)} 只",
                     f"运行模式 {context.get('execution_mode', '允许复用')}", "最终持仓上限 3 只"],
        "confidence": 0.9, "data_quality": "待各组复核",
        "output": {"objective": run["mission"], "gates": ["真实数据", "可执行公式", "成本后回测", "Challenger审查", "人工批准"]}
    }, progress, enforce_dependencies=False)

    stocks = context.get("stocks", [])
    reuse_research = bool(context.get("reuse_research"))
    market = context.get("market", {})
    breadth = context.get("breadth", {})
    index_changes = [item.get("pct") for item in market.values() if item and isinstance(item.get("pct"), (int, float))]
    avg_index = sum(index_changes) / len(index_changes) if index_changes else 0
    up, down = breadth.get("up", 0), breadth.get("down", 0)
    stock_changes = [float(stock.get("pct")) for stock in stocks if isinstance(stock.get("pct"), (int, float))]
    median_change = statistics.median(stock_changes) if stock_changes else 0
    up_ratio = up / max(1, up + down)
    high_volatility = sum(float((stock.get("factors") or {}).get("volatility20") or 0) > .8 for stock in stocks)
    limit_up_like = sum(change >= 9.5 for change in stock_changes)
    limit_down_like = sum(change <= -9.5 for change in stock_changes)
    regime_score = (1 if avg_index > .3 else -1 if avg_index < -.3 else 0) + (1 if up_ratio >= .58 else -1 if up_ratio <= .42 else 0) + (1 if median_change >= .5 else -1 if median_change <= -.5 else 0)
    regime = "偏多" if regime_score >= 2 else "偏弱" if regime_score <= -2 else "震荡"
    _execute(run, run_dir, "market_regime", lambda: {"summary": f"市场{regime}：上涨占比 {up_ratio:.0%}，个股涨跌中位数 {median_change:+.2f}%",
        "evidence": [f"指数平均涨跌 {avg_index:+.2f}%", f"科技股上涨/下跌 {up}/{down}",
                     f"高波动股票 {high_volatility} 只", f"类涨停/类跌停 {limit_up_like}/{limit_down_like}",
                     f"数据截止 {context.get('data_date')}"],
        "confidence": 0.8 if index_changes and len(stock_changes) >= 100 else 0.45,
        "data_quality": "行情与市场广度可用" if index_changes and (up + down) else "市场输入不完整",
        "output": {"regime": regime, "regime_score": regime_score, "average_index_pct": round(avg_index, 3),
                   "breadth": {**breadth, "up_ratio": round(up_ratio, 3)}, "median_stock_pct": round(median_change, 3),
                   "high_volatility_count": high_volatility, "limit_up_like": limit_up_like,
                   "limit_down_like": limit_down_like}}, progress)

    sector_rows = []
    for sector in context.get("sectors", []):
        members = [stock for stock in stocks if sector["key"] in stock.get("tech_sectors", [])]
        changes = [float(stock.get("pct", 0)) for stock in members]
        leaders = sorted(members, key=lambda stock: stock.get("pct", 0), reverse=True)[:3]
        sector_up_ratio = sum(change > 0 for change in changes) / max(1, len(changes))
        average_pct = sum(changes) / len(changes) if changes else None
        sector_rows.append({"key": sector["key"], "name": sector["name"], "count": len(changes),
                            "average_pct": round(average_pct, 3) if average_pct is not None else None,
                            "median_pct": round(statistics.median(changes), 3) if changes else None,
                            "up_ratio": round(sector_up_ratio, 3),
                            "strength_score": round((average_pct or 0) + (sector_up_ratio - .5) * 2, 3),
                            "leaders": [{"code": item.get("code"), "name": item.get("name"),
                                         "pct": round(float(item.get("pct", 0)), 2)} for item in leaders]})
    sector_rows.sort(key=lambda item: item["strength_score"], reverse=True)
    _execute(run, run_dir, "industry_chain", lambda: {"summary": f"{sector_rows[0]['name'] if sector_rows else '无数据'}领先；强度同时考虑平均涨幅与上涨广度",
        "evidence": [f"{item['name']}：均值 {item['average_pct']:+.2f}% / 上涨 {item['up_ratio']:.0%} / 领涨 {item['leaders'][0]['name'] if item['leaders'] else '—'}" for item in sector_rows[:4] if item["average_pct"] is not None],
        "confidence": 0.7 if sector_rows else 0, "data_quality": "板块覆盖可用" if sector_rows else "不可用",
        "output": {"sector_ranking": sector_rows}}, progress)

    movers = sorted(stocks, key=lambda stock: abs(stock.get("pct", 0)), reverse=True)[:10]
    event_map = {}
    cached_stock_count = 0
    for stock in stocks:
        cached = _load_stock_opinion_cache(stock)
        if cached is not None:
            cached_stock_count += 1
        latest = ((cached or {}).get("opinions") or [None])[0]
        if not latest:
            continue
        title = latest.get("title") or "无标题观点"
        key = (title, latest.get("publish_time_beijing") or latest.get("opi_pubtime"))
        event = event_map.setdefault(key, {"title": title, "publish_time": key[1], "stocks": [], "risk": False})
        if len(event["stocks"]) < 8:
            event["stocks"].append({"code": stock.get("code"), "name": stock.get("name")})
        text = f"{title} {latest.get('content') or latest.get('opi_all') or ''}"
        event["risk"] = event["risk"] or any(word in text for word in ("风险", "承压", "下调", "减持", "亏损", "退市"))
    event_signals = sorted(event_map.values(), key=lambda item: item.get("publish_time") or "", reverse=True)[:12]
    event_status = "完成" if event_signals else "数据受限"
    event_summary = (f"从本地观点库 {cached_stock_count} 只股票中提炼 {len(event_signals)} 个近期事件主题" if event_signals else
                     "首席观点缓存未覆盖当前股池；仅输出真实行情异常观察名单")
    event_news = _execute(run, run_dir, "event_news", lambda: {"status": event_status, "summary": event_summary,
        "evidence": ([f"{'风险' if item['risk'] else '观察'}：{item['title']}（关联 {len(item['stocks'])} 只）" for item in event_signals[:5]] or
                     [f"{item.get('name')}({item.get('code')}) {item.get('pct', 0):+.2f}%" for item in movers[:5]]),
        "confidence": 0.7 if event_signals else 0.3,
        "data_quality": "本地首席观点库 + 实时价格异动" if event_signals else "仅价格异常，无事件正文",
        "output": {"news_provider": "chief-opinions local library" if event_signals else None,
                   "event_signals": event_signals,
                   "price_anomaly_watchlist": [{"code": item.get("code"), "name": item.get("name"), "pct": item.get("pct")} for item in movers],
                   "risk_event_count": sum(item["risk"] for item in event_signals), "trade_veto": False}}, progress)

    def hypothesis_worker():
        result, error, engine = _codex_hypotheses(context)
        if result:
            return {"summary": result["summary"], "evidence": [f"推理引擎：{engine}（只读、结构化输出）"],
                    "confidence": 0.6, "data_quality": "白名单上下文完整", "output": result, "llm": engine}
        fallback = context.get("factor_candidates", [])[:5]
        return {"status": "降级", "summary": "推理模型不可用，保留白名单候选但不冒充 LLM 假设",
                "evidence": [error or "未知错误"], "confidence": 0.25, "data_quality": "仅白名单回退",
                "output": {"hypotheses": [{"candidate": item["key"], "rationale": "等待 LLM 研究解释", "expectation": "仅进入代码验证"} for item in fallback],
                           "generated_factors": []}, "llm": "unavailable"}
    hypothesis = _execute(run, run_dir, "factor_hypothesis", (lambda: {
        "status": "复用", "summary": "快速会签：复用最近一次因子假设，不重新调用推理引擎",
        "evidence": ["本轮由用户选择跳过研究研发组"], "confidence": 0.6,
        "data_quality": "沿用最近一次研究记录", "output": {"reused": True}
    }) if reuse_research else hypothesis_worker, progress)

    factor_run = None
    def factor_worker(report):
        nonlocal factor_run
        parameters = list(inspect.signature(factor_runner).parameters.values())
        accepts_variadic = any(parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD) for parameter in parameters)
        generated_factors = hypothesis.get("output", {}).get("generated_factors", [])
        if accepts_variadic or len(parameters) >= 2:
            factor_run = factor_runner(report, generated_factors)
        elif parameters:
            factor_run = factor_runner(report)
        else:
            factor_run = factor_runner()
        validation = factor_run.get("validation", {})
        passed = [item for item in validation.get("results", []) if item.get("gate") == "通过"]
        history_bars = validation.get("history_bars", 0)
        return {"status": "复用" if reuse_research else "完成",
                "summary": (f"复用最近一次因子验证：{len(passed)} 个因子通过门槛" if reuse_research else
                            f"{factor_run.get('search_space_size', 0)} 个可执行公式完成验证，其中本轮新生成 {factor_run.get('generated_formula_count', 0)} 个，{len(passed)} 个通过门槛"),
                "evidence": [f"全量确认股池 {factor_run.get('confirmation_universe', 0)} 只", f"历史日线 {history_bars} 根", f"引擎 {factor_run.get('engine_version')}"] ,
                "confidence": 0.75 if passed and history_bars >= 60 else 0.5,
                "data_quality": "样本可用" if history_bars >= 60 else "历史样本偏短",
                "output": {"passed_count": len(passed), "top_factors": [{k: item.get(k) for k in ("key", "name", "formula", "direction", "mean_rank_ic", "icir", "gate")} for item in passed[:12]], "hypothesis_input": hypothesis.get("output", {})}}
    factor_agent = _execute(run, run_dir, "factor_developer", factor_worker, progress)

    _execute(run, run_dir, "model_research", lambda: {
        "status": "复用" if reuse_research else "完成",
        "summary": ("快速会签：沿用最近一次方向调整截面模型" if reuse_research else
                    "采用方向调整后的截面百分位等权模型，用束搜索扩展至最多5个异质因子"),
        "evidence": ["单因子、双因子、三因子分别比较", "按成本后 Sharpe 排名", "因子家族去重，降低共线性"],
        "confidence": 0.65, "data_quality": factor_agent.get("data_quality", "待评估"),
        "output": {"model": "direction-adjusted cross-sectional rank ensemble", "max_factors": 5, "max_positions": 3}
    } if factor_agent["status"] not in BLOCKING_STATUSES else {"status": "阻断", "summary": "因子开发失败，不能设计模型", "evidence": [], "confidence": 0, "data_quality": "不可用"}, progress)

    manifest = context.get("history", {})
    first_date, last_date = manifest.get("first_date"), manifest.get("last_date")
    _execute(run, run_dir, "experiment_design", lambda: {"summary": "采用时间顺序切分和滚动样本外复核；禁止随机打散时间序列",
        "evidence": [f"历史区间 {first_date or '—'} 至 {last_date or '—'}", "15bp × 换手率", "信号T日，T+1建仓，T+2退出"],
        "confidence": 0.55, "data_quality": "规则已定义，等待引擎强制执行",
        "output": {"split": {"train": "前60%", "validation": "中20%", "test": "后20%"},
                   "walk_forward": True, "cost_bps": 15, "leakage_policy": "测试集不得用于选择因子",
                   "enforced_by_current_backtest": False}}, progress)

    backtest_run = None
    def backtest_worker(report):
        nonlocal backtest_run
        parameters = inspect.signature(backtest_runner).parameters.values()
        accepts_progress = any(parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD)
                               for parameter in parameters) or bool(parameters)
        backtest_run = backtest_runner(report) if accepts_progress else backtest_runner()
        top = (backtest_run.get("results") or [{}])[0]
        result = top.get("result", {})
        reliable = bool(result.get("annualization_reliable"))
        return {"status": "复用" if reuse_research else "完成",
                "summary": ((f"复用最近一次 {backtest_run.get('combination_count', 0)} 组回测；最佳成本后 Sharpe {result.get('sharpe', 0):.2f}") if reuse_research else
                            f"完成 {backtest_run.get('combination_count', 0)} 组确定性回测；最佳成本后 Sharpe {result.get('sharpe', 0):.2f}"),
                "evidence": [f"可回测 {backtest_run.get('usable_universe', 0)} 只", f"累计 {_pct(result.get('cumulative_return'))}", f"最大回撤 {_pct(-result.get('max_drawdown', 0))}"],
                "confidence": 0.65 if reliable else 0.45,
                "data_quality": "年化样本可用" if reliable else "短样本，年化不可靠",
                "output": {"top_strategy": top, "engine_version": backtest_run.get("engine_version"), "selection_rule": backtest_run.get("selection_rule")}}
    backtest_agent = _execute(run, run_dir, "backtest", backtest_worker, progress)

    def challenger_worker():
        if not backtest_run or not backtest_run.get("results"):
            return {"status": "阻断", "summary": "没有回测结果可审查", "evidence": []}
        result = backtest_run["results"][0].get("result", {})
        findings = ["当前策略选择与排名仍来自同一完整样本，尚未强制执行独立测试集"]
        severity = "警告"
        if result.get("max_drawdown", 0) > .20: findings.append(f"最大回撤 {_pct(-result['max_drawdown'])} 超过20%")
        if result.get("annual_return", 0) > .50: findings.append(f"年化 {_pct(result['annual_return'])} 偏高，需排查过拟合与可成交性")
        if result.get("days", 0) < 252: findings.append("有效样本不足一年")
        approved = result.get("days", 0) >= 20 and result.get("max_drawdown", 0) <= .35
        if not approved:
            severity = "阻断"
        return {"status": severity, "summary": f"发现 {len(findings)} 项需要人工关注的问题", "evidence": findings,
                "confidence": 0.8, "data_quality": "已审查回测指标与验证缺口",
                "output": {"approved_for_research": approved, "approved_for_live_trading": False, "findings": findings}}
    challenger = _execute(run, run_dir, "challenger", challenger_worker, progress)

    def portfolio_worker():
        if backtest_agent["status"] == "失败" or not backtest_run or not backtest_run.get("results"):
            return {"status": "阻断", "summary": "回测未通过，组合经理不输出股票", "evidence": [], "confidence": 0, "data_quality": "不可用", "output": {"holdings": []}}
        if not challenger.get("output", {}).get("approved_for_research"):
            return {"status": "阻断", "summary": "Challenger 未批准研究用途，组合经理不输出股票",
                    "evidence": challenger.get("evidence", []), "confidence": 0, "data_quality": "未通过压力审查", "output": {"holdings": []}}
        top = backtest_run["results"][0]
        holdings = top.get("result", {}).get("latest_holdings", [])[:3]
        return {"summary": f"选择 #1 策略并输出 {len(holdings)} 只等权研究候选",
                "evidence": [f"{item.get('name')}({item.get('code')})" for item in holdings] + ["Challenger仅批准研究用途"],
                "confidence": 0.55, "data_quality": backtest_agent.get("data_quality", "待评估"),
                "output": {"strategy": {"factors": top.get("factors"), "names": top.get("names")}, "holdings": holdings, "weights": [round(1 / len(holdings), 4)] * len(holdings) if holdings else []}}
    portfolio = _execute(run, run_dir, "portfolio_manager", portfolio_worker, progress)

    holdings = portfolio.get("output", {}).get("holdings", [])
    opinions, opinion_error = fetch_chief_opinions(holdings)
    opinion_refs = []
    for stock in (opinions or {}).get("stocks", []):
        latest = (stock.get("opinions") or [{}])[0]
        if latest.get("title") or latest.get("publish_time_beijing"):
            opinion_refs.append({"code": stock.get("code"), "name": stock.get("name"),
                                 "latest_title": latest.get("title"),
                                 "latest_publish_time": latest.get("publish_time_beijing"),
                                 "opinion_count": stock.get("opinion_count", 0)})
    run["external_sources"]["chief_opinions"].update({
        "available": bool(opinion_refs), "fetched_at": (opinions or {}).get("fetched_at"),
        "source": (opinions or {}).get("source"), "cache_hit": bool((opinions or {}).get("cache_hit")),
        "artifact": str(CHIEF_OPINIONS_LATEST_FILE.relative_to(ROOT)) if opinions else None,
        "error": opinion_error,
    })

    risk = _execute(run, run_dir, "risk_execution", lambda: {"summary": "研究组合已完成量化与首席观点复核，未产生任何订单",
        "evidence": ["最多3只、等权", "剔除近20日成交额最低30%", "科创板/ST排除", "15bp成本",
                     f"首席观点覆盖 {len(opinion_refs)}/{len(holdings)} 只候选", "禁止自动下单"],
        "confidence": 0.9, "data_quality": "硬规则已执行",
        "output": {"max_positions": 3, "position_weight": "1/3", "human_approval_required": True,
                   "chief_opinions_reviewed": opinion_refs, "orders_created": 0, "live_trading_allowed": False}}, progress)

    top_result = ((backtest_run or {}).get("results") or [{}])[0]
    run["decision"] = {"status": "等待人工批准" if holdings else "阻断", "strategy": top_result.get("names", []),
                       "holdings": holdings, "max_positions": 3, "orders_created": 0,
                       "challenger_status": challenger.get("status"), "risk_status": risk.get("status")}
    warnings = list(challenger.get("output", {}).get("findings", []))
    if not event_news.get("output", {}).get("event_signals"):
        warnings.append("事件新闻 Agent 尚未获得可用的事件正文信号")
    if opinion_error:
        warnings.append(opinion_error)
    run["warnings"] = warnings
    run["status"] = "等待人工批准" if holdings else "阻断"
    run["completed_at"] = _now()
    chief = _agent(run, "chief")
    chief["status"] = "完成" if opinion_refs or not holdings else "降级"
    chief["summary"] = f"四个团队已完成会签；输出 {len(holdings)} 只研究候选，关联 {len(opinion_refs)} 只股票的首席观点，订单数 0"
    chief["confidence"] = 0.7 if opinion_refs else 0.55
    chief["data_quality"] = ("内部量化证据 + 缓存首席观点" if opinion_refs and opinion_error else
                             "内部量化证据 + 外部首席观点" if opinion_refs else "外部观点不可用")
    chief["evidence"] = chief.get("evidence", []) + [
        f"{item['name']}：{item.get('latest_title') or '无标题'}（{item.get('latest_publish_time') or '时间未知'}）"
        for item in opinion_refs
    ]
    chief.setdefault("process", []).append({"time": _now(), "stage": "首席观点复核",
                                            "detail": f"匹配 {len(opinion_refs)}/{len(holdings)} 只候选；只作复核，不改写量化分数"})
    chief["output"].update({
        "team_statuses": {item["id"]: item["status"] for item in run["agents"] if item["id"] != "chief"},
        "chief_opinions": {"source": "chief-opinions MCP", "stocks": opinion_refs,
                           "artifact": str(CHIEF_OPINIONS_LATEST_FILE.relative_to(ROOT)) if opinions else None,
                           "error": opinion_error},
        "decision_status": run["status"], "orders_created": 0,
    })
    chief["finished_at"] = run["completed_at"]
    try:
        chief["duration_ms"] = round((datetime.strptime(run["completed_at"], "%Y-%m-%d %H:%M:%S") -
                                      datetime.strptime(chief["started_at"], "%Y-%m-%d %H:%M:%S")).total_seconds() * 1000)
    except (TypeError, ValueError):
        pass
    _write_json(run_dir / "agents" / "chief.json", chief)
    _persist(run, run_dir, progress)
    return run
