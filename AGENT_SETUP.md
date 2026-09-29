# 十二 Agent 配置

## 首席观点 MCP

`首席研究 Agent` 会在组合经理产出最多三只研究候选后，调用只读工具
`get_stock_chief_opinions`。每只股票默认读取最近三条观点，原始 `opi_all`
和发布时间保存在 `data/chief_opinions/`；认证信息不会写入数据文件。

PowerShell 当前会话配置：

```powershell
$env:CHIEF_OPINIONS_TOKEN = "替换为新 Token"
$env:CHIEF_OPINIONS_MCP_URL = "https://aizg.abctougu.com:18443/chief-opinions/"
$env:CHIEF_OPINIONS_LIMIT = "3"
python server.py
```

若未配置 Token，系统只会使用与本轮候选股票代码匹配的本地缓存；没有匹配缓存时，
首席 Agent 会明确降级，不会伪造外部观点。

全股票池首次同步或手动增量刷新：

```powershell
python chief_opinions_sync.py --limit 1 --workers 2 --max-age-hours 20
```

全股票池默认只缓存每只股票最新 1 条，以控制每日同步时间；进入最终组合的股票可由首席
Agent 按需保留最近 3 条。缓存按股票拆分在 `data/chief_opinions/stocks/<代码>.json`，汇总索引位于
`data/chief_opinions/index.json`。同步支持失败重试和断点续跑；20 小时内已更新的股票默认跳过。

服务运行后可以零网络读取任意已缓存股票：

```text
GET /api/chief-opinions?stock=603861
```

建议每天 `18:30` 运行上面的命令。计划任务必须在同一 Windows 用户下运行，并能读取
`CHIEF_OPINIONS_TOKEN` 环境变量；不要把 Token 直接写进计划任务命令行。

安装 Windows 每日计划任务：

```powershell
[Environment]::SetEnvironmentVariable("CHIEF_OPINIONS_TOKEN", "替换为新 Token", "User")
./install_chief_opinions_task.ps1 -At "18:30" -Workers 2
```

同步器允许最高 16 路并发，但当前 MCP 实测在 8–16 路时会出现服务端排队，推荐使用 2 路稳定续跑。

## Agent 输出合同

十二个 Agent 都会记录：

- `depends_on`：上游依赖，失败或阻断时自动停止下游。
- `duration_ms`：执行耗时。
- `confidence`：对当前结论的置信度，不代表收益概率。
- `data_quality`：输入和验证质量说明。
- `evidence` 与 `artifact`：证据摘要和独立 JSON 产物。

系统仍然只生成研究候选，不创建订单。独立测试集尚未由当前回测引擎强制执行，
因此 Challenger 会持续给出警告，且任何结果都不允许直接用于自动实盘。
