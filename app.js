const state = { selected: null, starred: false, dashboard: null, activeSector: '', visibleLimit: 100, expandedAgent: null, opinionCache: new Map(), agentRunStarting: false, agentRunError: '', agentRunMode: 'full', wyckoff: null, wyckoffPhase: '', wyckoffSelected: null, refreshPolling: false };
const titles = { overview:'市场驾驶舱', agents:'Agent 协作台', factors:'因子实验室', backtest:'回测实验', wyckoff:'威科夫策略', review:'推荐复盘' };
function uiIcon(name, className='') {
  const definition=window.lucide?.icons?.[name];
  if(!definition)return '';
  const element=window.lucide.createElement(definition);
  element.setAttribute('class',`ui-icon ${className}`.trim());
  element.setAttribute('aria-hidden','true');
  element.setAttribute('width','18');
  element.setAttribute('height','18');
  return element.outerHTML;
}
function setAgentRunButtons(running, starting=false) {
  const full=document.getElementById('agentRunButton'), quick=document.getElementById('agentQuickRunButton');
  full.disabled=running; quick.disabled=running;
  full.innerHTML=uiIcon(running?'LoaderCircle':'Play',running?'spin':'')+(starting?'正在启动':running?'运行中':'全量运行');
  quick.innerHTML=uiIcon('Zap')+'快速会签';
  full.title='同步行情，重新研发因子并运行回测'; quick.title='同步行情，复用已有因子与回测结果';
}
function initializeWorkspace() {
  const overview=document.getElementById('overviewView'), settings=overview.querySelector('.overview-settings');
  overview.insertBefore(document.getElementById('marketStrip'),settings);
  overview.insertBefore(overview.querySelector('.market-breadth-band'),settings);
  const council=document.getElementById('agentCollabView');
  ['.page-heading','.agent-mission-strip','#agentRunProgress','.agent-chief-line','.agent-control-grid','#agentDetail','.agent-critic-panel','.agent-strategy-settings','#agentOpinions'].forEach(selector=>council.append(council.querySelector(selector)));
  window.lucide?.createIcons();
}
function renderMarketBreadth(data) {
  const stocks=(data.stocks||[]).filter(stock=>typeof stock.pct==='number'&&Number.isFinite(stock.pct));
  const up=stocks.filter(stock=>stock.pct>0).length, down=stocks.filter(stock=>stock.pct<0).length, flat=stocks.length-up-down;
  setText('overviewSession',`${data.latest_date||'—'} · ${data.refreshing?'同步中':'最新数据'}`);
  setText('overviewStrategySummary',data.agent_selection?.name||'技术综合排名');
  setText('breadthRatio',stocks.length?`${(up/stocks.length*100).toFixed(1)}%`:'—');
  setText('breadthLegend',stocks.length?`上涨 ${up} · 平盘 ${flat} · 下跌 ${down}`:'暂无涨跌数据');
  document.getElementById('breadthMeter').innerHTML=stocks.length?`<i class="rising" style="flex:${up}"></i><i class="flat" style="flex:${flat}"></i><i class="falling" style="flex:${down}"></i>`:'';
  const sorted=stocks.map(stock=>stock.pct).sort((a,b)=>a-b), middle=Math.floor(sorted.length/2);
  const median=sorted.length?(sorted.length%2?sorted[middle]:(sorted[middle-1]+sorted[middle])/2):null;
  setText('breadthMedian',median==null?'中位数 —':`中位数 ${fmtPct(median)}`);
  const bins=[[-Infinity,-5,'<-5%'],[-5,-3,'-5~−3'],[-3,-1,'-3~−1'],[-1,0,'-1~0'],[0,1,'0~1'],[1,3,'1~3'],[3,5,'3~5'],[5,Infinity,'>5%']];
  const counts=bins.map(([lo,hi])=>stocks.filter(stock=>stock.pct>=lo&&stock.pct<hi).length), max=Math.max(1,...counts);
  document.getElementById('breadthHistogram').innerHTML=bins.map(([lo,hi,label],index)=>`<div class="histogram-bin ${hi<=0?'falling':'rising'}" title="${label}%：${counts[index]} 只"><span>${counts[index]}</span><div><i style="height:${stocks.length?Math.max(2,counts[index]/max*100):0}%"></i></div><small>${label}</small></div>`).join('');
  const sectors=(data.tech_universe?.sectors||[]).map(sector=>{
    const members=stocks.filter(stock=>(stock.tech_sectors||[]).includes(sector.key));
    return {...sector,average:members.length?members.reduce((sum,stock)=>sum+stock.pct,0)/members.length:null};
  }).filter(sector=>sector.average!==null).sort((a,b)=>b.average-a.average).slice(0,3);
  document.getElementById('sectorLeaders').innerHTML=sectors.length?sectors.map(sector=>`<button type="button" data-sector="${safe(sector.key)}"><span>${safe(sector.name)}</span><strong class="${pctClass(sector.average)}">${fmtPct(sector.average)}</strong>${uiIcon('ArrowUpRight')}</button>`).join(''):'<span class="data-loading">暂无板块数据</span>';
  document.querySelectorAll('#sectorLeaders button').forEach(button=>button.addEventListener('click',()=>{state.activeSector=button.dataset.sector;state.selected=null;renderSectorRankingTabs(data);renderCandidates(data);document.getElementById('candidateTable').scrollIntoView({behavior:'smooth',block:'start'});}));
}
const toast = (message) => { document.getElementById('toastText').textContent = message; document.getElementById('toast').classList.add('show'); window.clearTimeout(window.__toast); window.__toast = window.setTimeout(() => document.getElementById('toast').classList.remove('show'), 2600); };
const fmtPct = (value) => `${value >= 0 ? '+' : ''}${Number(value).toFixed(2)}%`;
const fmtPrice = (value) => value ? `¥ ${Number(value).toFixed(2)}` : '—';
const signed = (value) => `${value >= 0 ? '+' : ''}${Number(value).toFixed(1)}`;
const factorScore = (value) => 50 + 12 * (Number(value) || 0);
const pctClass = (value) => Number(value) >= 0 ? 'up' : 'negative-text';
function setText(id, value) { const el = document.getElementById(id); if (el) el.textContent = value; }
function setLoading(message) { document.querySelectorAll('.data-loading').forEach((el) => { el.textContent = message; }); }
function renderTechUniverse(data) {
  const universe=data.tech_universe||{}, selected=new Set(universe.selected_sectors||[]), target=document.getElementById('techSectorSelector');
  if(!target)return;
  target.innerHTML=(universe.sectors||[]).map((sector)=>`<label class="tech-sector-option"><input type="checkbox" value="${sector.key}" ${selected.has(sector.key)?'checked':''}><span>${sector.name}<b>${sector.count||0}</b></span></label>`).join('');
  setText('universeMeta',`${data.universe_size||0} 只真实科技股 · ${universe.source||'东方财富行业与概念分类'} · 排除科创板与 ST`);
  renderSectorRankingTabs(data);
}
function stocksForActiveSector(data) { return state.activeSector ? data.stocks.filter((stock)=>(stock.tech_sectors||[]).includes(state.activeSector)) : data.stocks; }
function renderSectorRankingTabs(data) {
  const target=document.getElementById('sectorRankingTabs'), sectors=(data.tech_universe?.sectors||[]).filter((item)=>item.count>0 && (data.tech_universe?.selected_sectors||[]).includes(item.key));
  if(!target)return;
  const buttons=[{key:'',name:'科技总榜',count:data.universe_size||0},...sectors];
  target.innerHTML=buttons.map((item)=>`<button class="ranking-tab ${state.activeSector===item.key?'active':''}" data-sector="${item.key}">${item.name}<span>${item.count}</span></button>`).join('');
  target.querySelectorAll('.ranking-tab').forEach((button)=>button.addEventListener('click',()=>{state.activeSector=button.dataset.sector||'';state.selected=null;state.visibleLimit=100;renderSectorRankingTabs(data);renderCandidates(data);toast(`已切换：${button.childNodes[0].textContent.trim()}`);}));
}
function renderStrategySelect(data) {
  const run=data.backtest_factor_run, select=document.getElementById('strategySelect'); if(!select||!run)return;
  const resolved=data.selected_strategy_resolved||{};
  const options=run.results.map((item,index)=>({item,index})).filter(({item})=>item.holding_days===1).sort((a,b)=>Number(Boolean(b.item.research_only))-Number(Boolean(a.item.research_only)));
  select.innerHTML='<option value="">选择排名策略</option>'+options.map(({item,index})=>`<option value="${index}" ${resolved.strategy_id?item.strategy_id===resolved.strategy_id?'selected':'':item.factors.join(',')===(resolved.factors||[]).join(',')&&item.holding_days===resolved.holding_days?'selected':''}>${safe(item.name||item.names.join(' + '))}${item.research_only?' · 历史收盘模型留档（非当前执行规则）':` · 持有 ${item.holding_days} 日`}</option>`).join('');
  setText('activeStrategyName',data.agent_selection?.name || '请选择排名策略'); setText('activeStrategyRule',data.agent_selection ? `已为 ${data.agent_selection.scored_count||0} 只股票评分 · 应用于科技总榜和全部分类榜` : '应用于科技总榜和全部分类榜');
  const activeFactors=data.agent_selection?.factors||data.selected_strategy_factors||[], match=(run.results||[]).find((item)=>item.holding_days===1&&(item.research_only||!run.stale_engine)&&(resolved.strategy_id?item.strategy_id===resolved.strategy_id:(item.factors||[]).join(',')===activeFactors.join(',')&&item.holding_days===resolved.holding_days)), result=match?.result||{}, metrics=document.getElementById('activeStrategyMetrics');
  if(match?.research_only&&metrics){
    const test=match.segments?.test||{}, selection=data.agent_selection||{}, market=selection.market_state||{};
    const labels=['策略类型','累计收益（全历史）','折算年化（全历史）','全历史回撤','全历史 Sharpe','全历史组合胜率'];
    const values=['行情择时',reviewPct(result.cumulative_return),reviewPct(result.annual_return),result.max_drawdown==null?'—':reviewPct(-result.max_drawdown),result.sharpe==null?'—':Number(result.sharpe).toFixed(2),result.win_rate==null?'—':`${(result.win_rate*100).toFixed(2)}%`];
    metrics.querySelectorAll('div').forEach((node,index)=>{node.querySelector('span').textContent=labels[index];node.querySelector('strong').textContent=values[index];});
    metrics.querySelectorAll('div')[2].title=`折算年化 = (1 + 全历史累计收益)^(252 / ${result.days||0}) - 1；包含空仓日，不是预期收益`;
    setText('activeStrategyName',selection.name||match.name);
    setText('activeStrategyRule',`${selection.data_date||'—'} 收盘信号 · ${selection.gate_open?'行情通过，只选前三只':'行情未通过，不开新仓'} · 上涨比例 ${market.up_fraction==null?'—':`${(market.up_fraction*100).toFixed(2)}%`} · ${market.above_ma20?'高于':'未高于'} MA20`);
    setText('activeStrategyBacktestMeta',`全历史 ${result.start_date||'—'} 至 ${result.end_date||'—'} · ${result.days||0} 个交易日（含空仓） · ${result.winning_rounds||0}/${result.closed_rounds||0} 组盈利 · 出手率 ${result.decision_rate==null?'—':`${(result.decision_rate*100).toFixed(2)}%`} · ${result.missing_mark_days?`缺价 ${result.missing_mark_days} 日按模型延迟退出，回撤与 Sharpe 不展示 · `:''}含训练与验证，非独立检验 · 同收盘价近似，非实盘收益。最后检验 ${test.start_date||'—'} 至 ${test.end_date||'—'}：累计 ${reviewPct(test.cumulative_return)} · 年化 ${reviewPct(test.annual_return)} · 组合胜率 ${test.win_rate==null?'—':`${(test.win_rate*100).toFixed(2)}%`}`);
    if(state.timingWeekReview?.mode==='last_entries')renderNextDaySummary(state.timingWeekReview);
    return;
  }
  if(metrics)metrics.querySelectorAll('div span').forEach((node,index)=>{node.textContent=['回测排名','累计收益','年化收益','最大回撤','Sharpe','日收益胜率'][index];});
  if(match&&metrics){const values=[`#${match.rank||'—'} / ${(run.results||[]).length}`,fmtPct(Number(result.cumulative_return||0)*100),fmtPct(Number(result.annual_return||0)*100),fmtPct(-Number(result.max_drawdown||0)*100),Number(result.sharpe||0).toFixed(2),`${(Number(result.win_rate||0)*100).toFixed(1)}%`];metrics.querySelectorAll('strong').forEach((node,index)=>{node.textContent=values[index];node.className=index===1||index===2?'positive-text':index===3?'negative-text':'';});setText('activeStrategyBacktestMeta',`${result.days||0} 个交易日 · ${result.cost_bps??run.cost_bps??0}bp 成本 · 截止 ${run.data_date||'—'} · ${match.rank_metric||run.ranking_metric||'成本后 Sharpe'}排名`);}else if(metrics){metrics.querySelectorAll('strong').forEach((node)=>{node.textContent='—';node.className='';});setText('activeStrategyBacktestMeta','当前策略尚无匹配的回测结果');}
}
function renderNextDaySummary(r) {
  const latest=r.latest_decision, metrics=document.getElementById('activeStrategyMetrics');
  setText('activeStrategyName','顺势缩量 Top3 · 14:50 买 → 次交易日 14:50 卖');
  setText('activeStrategyRule',`新规则历史回放 · 只持一组三只 · ${latest?.date||'—'} 14:50 ${latest?.gate_open?'行情通过':'行情未通过，不开新仓'} · 非实时成交指令`);
  const labels=['持有期','累计收益（最近7次）','模型盈亏（2万元）','已平仓组','盈利组','组合胜率'];
  const values=['1 个交易日',reviewPct(r.net_return),`${r.pnl==null?'—':Number(r.pnl).toFixed(2)} 元`,r.closed_rounds,r.rounds.filter(t=>t.status==='closed'&&t.return>0).length,reviewPct(r.win_rate)];
  metrics?.querySelectorAll('div').forEach((node,i)=>{node.querySelector('span').textContent=labels[i];node.querySelector('strong').textContent=values[i];node.removeAttribute('title');});
  setText('activeStrategyBacktestMeta',`当日14:50买，下一交易日14:50卖；遇休市顺延。最近 ${r.actual_entries} 次出手、${r.trades.length} 笔股票买卖 · 旧541天收盘模型仅留档，不属于本次结果 · 标准份额，非实盘收益`);
}
function renderMarket(data) {
  renderStrategySelect(data);
  renderTechUniverse(data);
  renderMarketBreadth(data);
  const hs = data.market.hs300, cyb = data.market.cyb;
  setText('hs300Pct', hs ? fmtPct(hs.pct) : '无数据'); setText('hs300Price', hs ? fmtPrice(hs.price) : '接口未返回');
  setText('cybPct', cyb ? fmtPct(cyb.pct) : '无数据'); setText('cybPrice', cyb ? fmtPrice(cyb.price) : '接口未返回');
  document.getElementById('hs300Pct').className = hs ? pctClass(hs.pct) : ''; document.getElementById('cybPct').className = cyb ? pctClass(cyb.pct) : '';
  const quoteClock = data.quote_time ? `${data.quote_time.slice(0,4)}-${data.quote_time.slice(4,6)}-${data.quote_time.slice(6,8)} ${data.quote_time.slice(8,10)}:${data.quote_time.slice(10,12)}:${data.quote_time.slice(12,14)}` : (data.generated_at ? data.generated_at.slice(11) : '后台刷新中'); setText('breadth', `${data.breadth.up} / ${data.breadth.down}`); setText('dataSource', data.source_label || data.source); setText('dataTimestamp', `${data.latest_date || '—'} · ${quoteClock}`); setText('scanTimestamp', `${quoteClock} 更新`); setText('systemStatus', data.refreshing ? '后台刷新中' : '系统健康'); setText('systemSync', `${data.source_label || data.source} ${quoteClock}`); setText('marketClockLabel', '行情时间'); setText('marketClockTime', quoteClock);
  const avg = [hs?.pct, cyb?.pct].filter((v) => typeof v === 'number'), positive = avg.length && avg.reduce((a,b) => a+b, 0) / avg.length >= 0;
  setText('regimeText', avg.length ? (positive ? '结构性偏多' : '结构性偏弱') : '指数无数据'); setText('regimeConfidence', avg.length ? `${Math.min(99, Math.round(50 + Math.abs(avg.reduce((a,b) => a+b, 0) / avg.length) * 12))}%` : '—');
  setText('regimeNote', `数据截止 ${data.latest_date || '—'}；来源：${data.source_label || data.source}。`); document.querySelector('#overviewView .page-heading p').textContent = `${data.universe_size} 只科技股完成扫描 · 已排除科创板 688/689`;
  document.querySelector('.market-strip-main .regime-dot').style.background = positive ? '#4ca67d' : '#be6c64';
  const counts = [data.stocks.length, data.stocks.filter((s) => statusFor(s) === '可建仓').length, data.stocks.filter((s) => statusFor(s) === '等待触发').length, data.stocks.filter((s) => s.factors.volatility20 > .8).length]; document.querySelectorAll('.filter-button span').forEach((el, i) => { el.textContent = counts[i]; });
}
const agentBlueprint={
  '市场情报组':[['market_regime','市场状态 Agent'],['industry_chain','科技产业链 Agent'],['event_news','事件新闻 Agent']],
  '研究研发组':[['factor_hypothesis','因子假设 Agent'],['factor_developer','因子开发 Agent'],['model_research','模型研究 Agent']],
  '实验验证组':[['experiment_design','实验设计 Agent'],['backtest','回测 Agent'],['challenger','Challenger Agent']],
  '决策执行组':[['portfolio_manager','组合经理 Agent'],['risk_execution','风控执行 Agent']]
};
const agentExecutionOrder=['chief','market_regime','industry_chain','event_news','factor_hypothesis','factor_developer','model_research','experiment_design','backtest','challenger','portfolio_manager','risk_execution'];
const terminalAgentStatuses=new Set(['完成','复用','警告','降级','数据受限','失败','阻断']);
const safe=(value)=>String(value??'').replace(/[&<>"']/g,(char)=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const concise=(value,max=38)=>{const text=String(value??'').replace(/\s+/g,' ').trim();return text.length>max?`${text.slice(0,max)}…`:text;};
const formatDuration=(value)=>value==null?'运行中':value>=60000?`${(value/60000).toFixed(1)} 分钟`:`${(value/1000).toFixed(1)} 秒`;
const agentIcons={chief:'Network',market_regime:'ChartNoAxesCombined',industry_chain:'Layers',event_news:'Newspaper',factor_hypothesis:'Lightbulb',factor_developer:'CodeXml',model_research:'BrainCircuit',experiment_design:'FlaskConical',backtest:'ChartNoAxesColumnIncreasing',challenger:'ShieldAlert',portfolio_manager:'BriefcaseBusiness',risk_execution:'ShieldCheck'};
function agentTone(status) {
  return ['失败','阻断'].includes(status)?'risk':['警告','降级','数据受限'].includes(status)?'warn':status==='运行中'?'running':['完成','复用'].includes(status)?'complete':'idle';
}
function renderCouncilPipeline(run, preparing) {
  const target=document.getElementById('councilPipeline'), byId=Object.fromEntries((preparing?[]:run?.agents||[]).map(item=>[item.id,item]));
  const stages=[['首席调度',[['chief']]],...Object.entries(agentBlueprint).map(([name,members])=>[name,members])];
  const markup=stages.map(([name,members],index)=>{
    const items=members.map(([id])=>byId[id]), done=items.filter(item=>terminalAgentStatuses.has(item?.status)).length;
    const tone=items.some(item=>item?.status==='运行中')?'running':items.some(item=>['失败','阻断'].includes(item?.status))?'risk':done===members.length?(items.some(item=>['警告','降级','数据受限'].includes(item?.status))?'warn':'complete'):'idle';
    return `<li class="pipeline-stage ${tone}" ${tone==='running'?'aria-current="step"':''}><span class="pipeline-marker">${tone==='complete'?uiIcon('Check'):tone==='running'?uiIcon('LoaderCircle','spin'):String(index+1).padStart(2,'0')}</span><div><strong>${name}</strong><small>${tone==='running'?'正在处理':tone==='risk'?'已阻断':`${done} / ${members.length} 完成`}</small></div><span class="pipeline-link" aria-hidden="true"><i></i></span></li>`;
  }).join('');
  if(target.dataset.markup!==markup){target.innerHTML=markup;target.dataset.markup=markup;}
}
function updateAgentElapsed() {
  const run=state.dashboard?.multi_agent_run, panel=document.getElementById('agentRunProgress');
  if(state.agentRunStarting){setText('agentElapsed','准备数据');return;}
  const start=Date.parse((run?.started_at||'').replace(' ','T'));
  const finish=panel.classList.contains('running')?Date.now():Date.parse((run?.completed_at||run?.updated_at||'').replace(' ','T'));
  setText('agentElapsed',Number.isFinite(start)&&Number.isFinite(finish)?`用时 ${formatDuration(Math.max(0,finish-start))}`:'尚未运行');
}
function renderAgentRunProgress(run) {
  const panel=document.getElementById('agentRunProgress');
  if(!panel)return;
  const agents=state.agentRunStarting?[]:run?.agents||[], byId=Object.fromEntries(agents.map(item=>[item.id,item]));
  const completed=agents.filter(item=>terminalAgentStatuses.has(item.status)).length;
  const active=agentExecutionOrder.map(id=>byId[id]).find(item=>item?.status==='运行中');
  const lastStarted=[...agentExecutionOrder].reverse().map(id=>byId[id]).find(item=>item?.started_at);
  const failed=agents.find(item=>['失败','阻断'].includes(item.status));
  const running=state.agentRunStarting||Boolean(active)||run?.status==='运行中';
  const isDone=Boolean(run&&['等待人工批准','阻断'].includes(run.status)&&!running);
  const percent=state.agentRunStarting&&!agents.length?2:Math.max(running?4:0,Math.min(100,Math.round(completed/12*100)));
  let title='研究团队就绪', detail='尚无本轮运行记录';
  if(state.agentRunError){title='本轮启动失败';detail=state.agentRunError;}
  else if(state.agentRunStarting){const full=state.agentRunMode==='full',prep=state.agentPrep;title=prep?.stage|| (full?'正在启动全量重算':'正在启动快速会签');detail=prep?.detail||(full?'先同步新增 K 线，再增量计算因子与回测…':'先同步新增 K 线；市场情报重新运行，研发与回测复用最近结果…');}
  else if(active){const step=(active.process||[]).at(-1);title=`${active.name} · ${step?.stage||'运行中'}`;detail=step?.detail||active.summary||'正在处理真实输入';}
  else if(isDone){title=failed?`本轮结束 · ${failed.name}${failed.status}`:'本轮研究已完成';detail=`${run.execution_mode||'全量运行'} · ${run.status} · ${run.run_id||'—'}`;}
  else if(run){title=`最近一轮：${run.status||'状态未知'}`;detail=`运行 ${run.run_id||'—'} · 更新于 ${run.updated_at||'—'}`;}
  panel.className=`agent-run-progress ${running?'running':state.agentRunError||failed?'failed':isDone?'complete':'idle'}`;
  setText('agentProgressCount',`${completed}/12`);setText('agentProgressTitle',title);setText('agentProgressDetail',detail);setText('agentProgressTime',running?'实时更新':run?.updated_at||'尚未运行');
  const bar=document.getElementById('agentProgressBar');if(bar)bar.style.width=`${percent}%`;
  bar?.parentElement.setAttribute('aria-valuenow',String(completed));
  document.getElementById('agentCollabView').classList.toggle('council-running',Boolean(running));
  document.getElementById('agentTeams').setAttribute('aria-busy',String(Boolean(running)));
  setText('navAgentState',running?'运行中':failed?'阻断':isDone?'已会签':'待命');
  document.getElementById('navAgentState').classList.toggle('is-running',Boolean(running));
  renderCouncilPipeline(run,state.agentRunStarting); updateAgentElapsed();
  const recent=document.getElementById('agentProgressRecent');
  if(recent){const item=active||lastStarted;const steps=(item?.process||[]).slice(-3);recent.innerHTML=steps.length?steps.map(step=>`<span><b>${safe(step.stage)}</b>${safe(step.detail)}</span>`).join(''):'<span>等待本轮阶段日志</span>';}
}
function renderAgentDetail(item) {
  const panel=document.getElementById('agentDetail');
  if(!panel)return;
  if(!item){panel.hidden=true;panel.innerHTML='';return;}
  const process=item.process||[], evidence=item.evidence||[], chiefOpinions=item.id==='chief'?item.output?.chief_opinions:null;
  panel.hidden=false;
  panel.innerHTML=`<div class="agent-detail-head"><div><span class="eyebrow">AGENT STEP DETAIL</span><h3>${safe(item.name)}</h3><p>${safe(item.role||'')}</p></div><button class="agent-detail-close" type="button" title="关闭详情" aria-label="关闭详情">×</button></div><div class="agent-detail-meta"><span>状态 <b>${safe(item.status)}</b></span><span>耗时 <b>${formatDuration(item.duration_ms)}</b></span><span>数据 <b>${safe(item.data_quality||'待评估')}</b></span></div>${chiefOpinions?`<section class="agent-opinion-usage"><strong>首席观点输入</strong><p>量化候选产生后，按股票代码读取本地缓存；缓存缺失时才调用 MCP。观点用于复核与风险提示，不直接改写因子分数，也不会自动下单。</p><div>${(chiefOpinions.stocks||[]).map(stock=>`<span>${safe(stock.name||stock.code)} · ${stock.opinion_count||0} 条 · ${safe(stock.latest_publish_time||'时间未知')}</span>`).join('')||'<span>本轮未匹配到候选观点</span>'}</div></section>`:''}<div class="agent-detail-grid"><section><strong>运行过程</strong><div class="agent-process-list">${process.length?process.map((step,index)=>`<div><span>${String(index+1).padStart(2,'0')}</span><p><b>${safe(step.stage)}</b>${safe(step.detail)}</p><time>${safe(step.time||'')}</time></div>`).join(''):`<div class="agent-detail-empty">${item.status==='等待'?'等待前置任务':'本次旧记录未保存阶段日志'}</div>`}</div></section><section><strong>证据</strong><ul class="agent-evidence-compact">${evidence.length?evidence.map(text=>`<li>${safe(text)}</li>`).join(''):'<li>暂无证据输出</li>'}</ul></section></div><details class="agent-output-json"><summary>结构化单步结果</summary><pre>${safe(JSON.stringify(item.output??{status:item.status,summary:item.summary},null,2))}</pre></details>`;
  panel.querySelector('.agent-detail-close').addEventListener('click',()=>{state.expandedAgent=null;renderAgentDetail(null);});
}
function opinionText(record) {
  if(record?.content)return String(record.content);
  try { const parsed=JSON.parse(record?.opi_all||'{}');return String(parsed.content||parsed.description||record?.opi_all||''); }
  catch { return String(record?.opi_all||'暂无正文'); }
}
function cleanOpinionText(record) { return opinionText(record).replace(/^#{1,6}\s*/gm,'').replace(/^>\s?/gm,'').replace(/\*\*(.*?)\*\*/g,'$1').trim(); }
function opinionFullTemplate(stock,payload) {
  const opinions=payload?.opinions||[];
  if(!opinions.length)return `<article class="agent-opinion-full missing"><header><div><strong>${safe(stock.name||stock.code)}</strong><span>${safe(stock.code)}</span></div><small>暂无匹配观点</small></header><p>本地缓存中没有该候选的观点，不影响量化排名。</p></article>`;
  return `<article class="agent-opinion-full"><header><div><strong>${safe(stock.name||payload.name||stock.code)}</strong><span>${safe(stock.code)}</span></div><small>${opinions.length} 条观点</small></header>${opinions.map((record)=>{
    // 观点源是行业级文章，一篇文章覆盖多只标的，标题讲的常是别的主体；
    // 摘要型记录也只有摘要没有全文。两者都必须如实标出来。
    const tags=[record.content_kind?`<i class="opinion-tag">${safe(record.content_kind)}</i>`:'',record.mentions_stock===false?'<i class="opinion-tag warn">未提及本标的</i>':''].join('');
    return `<details><summary><span>${safe(record.title||'无标题观点')}</span>${tags}<time>${safe(record.publish_time_beijing||record.opi_pubtime||'时间未知')}</time></summary><div class="agent-opinion-body">${record.mentions_stock===false?'<p class="opinion-warning">此观点标题与正文均未出现该股票，可能只是同行业/同板块的相关文章，请勿据此判断个股。</p>':''}${safe(cleanOpinionText(record))}</div></details>`;
  }).join('')}</article>`;
}
async function hydrateChiefOpinions(candidates) {
  const target=document.getElementById('agentOpinionCards');
  if(!target||!candidates.length)return;
  const signature=candidates.map(stock=>stock.code).join(',');target.dataset.signature=signature;
  const results=await Promise.all(candidates.map(async stock=>{
    const code=String(stock.code||'');
    if(!code)return [stock,null];
    if(state.opinionCache.has(code))return [stock,state.opinionCache.get(code)];
    try { const response=await fetch(`/api/chief-opinions?stock=${encodeURIComponent(code)}`,{cache:'no-store'});const payload=await response.json();const value=response.ok&&!payload.error?payload:null;state.opinionCache.set(code,value);return [stock,value]; }
    catch { state.opinionCache.set(code,null);return [stock,null]; }
  }));
  if(target.dataset.signature!==signature)return;
  target.innerHTML=results.map(([stock,payload])=>opinionFullTemplate(stock,payload)).join('');
}
function renderChiefOpinions(run, chief, holdings) {
  const target=document.getElementById('agentOpinions');
  if(!target)return;
  const source=run?.external_sources?.chief_opinions||{}, opinionData=chief?.output?.chief_opinions||{}, opinions=opinionData.stocks||[], byCode=Object.fromEntries(opinions.map(item=>[String(item.code),item]));
  const candidates=holdings.length?holdings:opinions.map(item=>({code:item.code,name:item.name}));
  const sourceLabel=source.available?(source.cache_hit||source.error?'本地观点库':'MCP 本次获取'):'暂无可用观点';
  target.innerHTML=`<div class="agent-opinions-head"><div><span class="eyebrow">CHIEF OPINIONS</span><h3>首席观点全文</h3></div><div class="agent-opinions-source"><b>${opinions.length}/${candidates.length||0} 覆盖</b><span>${safe(sourceLabel)}</span></div></div><p class="agent-opinions-rule">量化候选产生后按股票代码匹配最近观点，仅用于风险复核和首席结论，不修改因子分数与排名。观点默认收起，点击标题查看完整正文。</p><div class="agent-opinion-cards" id="agentOpinionCards">${candidates.length?'<div class="data-loading">正在读取完整观点…</div>':'<div class="agent-opinions-empty">本轮尚未生成候选，产生 Top 3 后自动匹配首席观点。</div>'}</div>`;
  if(candidates.length)hydrateChiefOpinions(candidates);
}
function renderAgentCollaboration(data) {
  const run=data.multi_agent_run, council=run||{}, allAgents=state.agentRunStarting?[]:run?.agents||[], byId=Object.fromEntries(allAgents.map((item)=>[item.id,item])), waiting={status:'等待',summary:'等待首席研究 Agent 调度',evidence:[]};
  const chief=byId.chief||{...waiting,name:'首席研究 Agent',role:'制定任务、检查依赖并签发最终研究结论'};
  const decision=state.agentRunStarting?{}:run?.decision||{}, holdings=(decision.holdings||[]).slice(0,3), completed=run&&['等待人工批准','阻断'].includes(run.status);
  setText('agentMission',run?.mission||'十二 Agent、四团队量化研究会签');
  setText('agentMissionMeta',run?`更新于 ${run.updated_at||'—'}`:'等待首次运行');
  setText('agentResearchDate',run?.data_date||data.latest_date||'—'); setText('agentUniverseCount',`${run?.universe_size||data.universe_size||0} 只`); setText('agentLoopState',state.agentRunStarting?'准备数据':run?.status||'未运行'); setText('agentRunTime',run?.updated_at||'等待运行记录');
  renderAgentRunProgress(run);
  const chiefTone=chief.status==='失败'||chief.status==='阻断'?'risk':chief.status==='运行中'?'running':chief.status==='等待'?'idle':'complete';
  document.getElementById('agentChief').innerHTML=`<button class="agent-chief-open" type="button" data-agent-id="chief"><span class="agent-chief-avatar">${uiIcon('Network')}</span><span><small>首席研究 · ORCHESTRATOR</small><strong>${safe(concise(chief.summary,72))}</strong></span><b class="agent-status ${chiefTone}">${safe(chief.status)}</b></button>`;
  renderChiefOpinions(state.agentRunStarting?null:run,chief,holdings);
  renderStrategyPicker(data);
  const board=document.getElementById('agentTeams');
  const boardMarkup=Object.entries(agentBlueprint).map(([team,members],teamIndex)=>{
    const items=members.map(([id,name])=>state.agentRunStarting?{...waiting,id,name}:byId[id]||{...waiting,id,name});
    const done=items.filter(item=>terminalAgentStatuses.has(item.status)).length, active=items.some(item=>item.status==='运行中');
    return `<section class="agent-team-column ${active?'team-running':''}"><header><span>0${teamIndex+1}</span><strong>${team}</strong><small>${done}/${members.length}</small></header><div class="agent-team-flow">${items.map(item=>{
      const tone=agentTone(item.status), step=(item.process||[]).at(-1), summary=item.status==='等待'?'等待上游结果':concise(item.status==='运行中'?(step?.detail||item.summary):(item.summary||item.role||'暂无结论'),76);
      return `<article class="agent-work-node ${tone}" data-agent-node="${item.id}"><div class="agent-node-heading"><span class="agent-node-icon">${uiIcon(agentIcons[item.id]||'Workflow')}</span><div class="agent-work-top"><strong>${safe((item.name||'').replace(' Agent',''))}</strong><b><span class="node-state-dot"></span>${safe(item.status)}</b></div></div><p title="${safe(item.summary||'')}">${safe(summary)}</p><div class="agent-node-footer"><span class="node-duration">${item.status==='运行中'?'<span class="activity-bars" aria-hidden="true"><i></i><i></i><i></i><i></i></span>处理中':item.duration_ms==null?'尚未开始':formatDuration(item.duration_ms)}</span><button class="agent-inspect" type="button" data-agent-id="${item.id}" aria-label="查看${safe((item.name||'').replace(' Agent',''))}${item.status==='运行中'?'运行过程':'结果'}">${item.status==='运行中'?'运行过程':'详情'}${uiIcon('ArrowUpRight')}</button></div></article>`;
    }).join('')}</div></section>`;
  }).join('');
  // Keep unchanged nodes mounted so polling does not restart their motion or steal focus.
  if(board.dataset.markup!==boardMarkup){board.innerHTML=boardMarkup;board.dataset.markup=boardMarkup;}
  document.querySelectorAll('[data-agent-node]').forEach(node=>node.classList.toggle('selected',node.dataset.agentNode===state.expandedAgent));
  document.querySelectorAll('[data-agent-id]').forEach((button)=>{button.onclick=(event)=>{event.stopPropagation();state.expandedAgent=button.dataset.agentId;document.querySelectorAll('[data-agent-node]').forEach(node=>node.classList.toggle('selected',node.dataset.agentNode===state.expandedAgent));renderAgentDetail(byId[state.expandedAgent]||null);document.getElementById('agentDetail')?.scrollIntoView({behavior:'smooth',block:'nearest'});};});
  renderAgentDetail(state.expandedAgent?byId[state.expandedAgent]:null);
  const strategy=(decision.strategy||[]).join(' + ');
  setText('agentStrategySummary',strategy||'系统自动选择');
  const staleNote=council.stale?`⚠ ${council.stale_reason||'会签结论已过期'}`:'';
  setText('agentDecisionTitle',strategy?`本轮研究组合${decision.holding_days?` · ${decision.holding_days} 日`:''}`:'等待十二 Agent 会签');
  setText('agentDecisionRule',staleNote||(holdings.length?'最多3只、等权研究组合；Challenger 未批准实盘，必须人工确认。':'没有完成全部真实验证前，组合经理不会输出股票。'));
  const decisionPanel=document.getElementById('agentDecisionRule');
  if(decisionPanel) decisionPanel.classList.toggle('stale-warning',Boolean(staleNote));
  document.getElementById('agentTop3').innerHTML=holdings.length?holdings.map((stock,index)=>`<button class="agent-pick" data-symbol="${safe(stock.code)}"><span>0${index+1}</span><div><strong>${safe(stock.name)}</strong><small>${safe(stock.code)}</small></div>${uiIcon('ArrowUpRight')}</button>`).join(''):`<div class="agent-empty-decision">${run?.status==='阻断'?'会签已阻断':'等待组合经理输出'}</div>`;
  document.querySelectorAll('.agent-pick').forEach((button)=>button.addEventListener('click',()=>{document.querySelector('[data-view="overview"]').click();selectStock(button.dataset.symbol);}));
  const warnings=state.agentRunStarting?[]:run?.warnings||[]; setText('criticCount',`${warnings.length} 项`);
  document.getElementById('agentCritiques').innerHTML=warnings.length?warnings.slice(0,3).map((text,index)=>`<div class="agent-critique-row ${index===0?'risk':'warn'}"><span>!</span><div><strong>${index===0?'关键风险':'执行限制'}</strong><small>${safe(concise(text,72))}</small></div></div>`).join(''):'<div class="agent-clear-state">当前无关键风险</div>';
  const running=Boolean(run&&!completed&&run.status==='运行中');
  setAgentRunButtons(running||state.agentRunStarting,state.agentRunStarting);
}
async function pollAgentCouncil(previousRunId, allowSameRun=false) {
  for(let attempt=0;attempt<360;attempt++){
    const response=await fetch('/api/agents',{cache:'no-store'}), payload=await response.json();
    if(!response.ok||payload.error)throw new Error(payload.error||'运行状态读取失败');
    const isCurrent=Boolean(payload.run&&(allowSameRun||payload.run.run_id!==previousRunId));
    if(payload.preparing){state.agentPrep=payload.preparing;state.agentRunStarting=true;renderAgentCollaboration(state.dashboard);if(payload.preparing.error)throw new Error(`K 线同步失败：${payload.preparing.error}`);}
    else if(payload.running||isCurrent){state.agentPrep=null;state.agentRunStarting=false;if(payload.run){state.dashboard.multi_agent_run=payload.run;renderAgentCollaboration(state.dashboard);}}
    if(!payload.running&&isCurrent)return payload.run;
    await new Promise((resolve)=>setTimeout(resolve,1000));
  }
  throw new Error('十二 Agent 运行超时，请刷新查看已落盘状态');
}
async function runAgentCollaboration(mode='full') {
  if(!state.dashboard)return toast('行情尚未加载完成');
  const previousRunId=state.dashboard.multi_agent_run?.run_id||null;
  state.agentRunMode=mode;state.agentRunStarting=true;state.agentRunError='';setAgentRunButtons(true,true);renderAgentCollaboration(state.dashboard);
  try {
    const response=await fetch('/api/agents/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode})}), payload=await response.json();if(!response.ok||payload.error)throw new Error(payload.error||'启动失败');
    toast('十二 Agent 已启动，进度正在实时更新');const run=await pollAgentCouncil(previousRunId,payload.status==='运行中');
    state.agentRunStarting=false;state.dashboard.multi_agent_run=run;renderAgentCollaboration(state.dashboard);toast(`十二 Agent 会签完成：${run?.status||'已落盘'}`);
  } catch(error){state.agentRunStarting=false;state.agentRunError=error.message;renderAgentRunProgress(state.dashboard?.multi_agent_run);toast(`Agent 会签未完成：${error.message}`);}
  finally{state.agentPrep=null;setAgentRunButtons(state.dashboard?.multi_agent_run?.status==='运行中');}
}
function scoreFor(stock,useStrategy=Boolean(state.dashboard?.agent_selection)) { const value=useStrategy?stock.agent_combo_score:stock.score; return value==null||!Number.isFinite(Number(value))?null:Number(value); }
function statusFor(stock,useStrategy=Boolean(state.dashboard?.agent_selection)) { const score=scoreFor(stock,useStrategy); if(useStrategy&&state.dashboard?.agent_selection?.strategy_id==='market-gated-top3-nextday-v2')return score==null?'数据不足':stock.strategy_trade_state||'观察'; return score==null?'数据不足':score>=70?'可建仓':score>=55?'等待触发':'观察'; }
function directionMark(direction) { return direction && String(direction).includes('反') ? '反' : '正'; }
function rowTemplate(stock, index, useCombo=false) {
  const colors = ['red', 'orange', 'blue', 'purple'], f = stock.factors;
  const comboTags = useCombo ? (stock.agent_combo_breakdown || []).map((item) => `<span class="factor ${item.score >= 50 ? 'positive' : 'negative'}" title="${item.name} ${item.direction}"><b>${item.name}</b><em>${directionMark(item.direction)}</em><i>${Number(item.score).toFixed(0)}</i></span>`).join('') : '';
  const tags = useCombo ? (comboTags || '<span class="factor neutral">策略数据不足</span>') : `<span class="factor ${f.momentum20 >= 0 ? 'positive' : 'negative'}" title="20 日动量"><b>动量</b><i>${signed(factorScore(f.momentum20)-50)}</i></span><span class="factor ${f.trend >= 0 ? 'positive' : 'negative'}" title="5/20 日趋势"><b>趋势</b><i>${signed(factorScore(f.trend)-50)}</i></span><span class="factor ${f.volume_ratio >= 1 ? 'positive' : 'neutral'}" title="5/20 日量比"><b>量比</b><i>${Number(f.volume_ratio).toFixed(2)}x</i></span>`;
  const displayScore = scoreFor(stock,useCombo), status=statusFor(stock,useCombo), statusClass=status==='可建仓'?'ready':status==='等待触发'?'wait':'watch';
  return `<div class="table-row candidate-row ${index === 0 ? 'selected' : ''}" data-symbol="${stock.code}"><div class="stock-cell"><div class="stock-logo ${colors[index % colors.length]}">${stock.initial}</div><div><strong>${stock.name}</strong><small>${stock.code} · ${stock.industry||stock.market}</small></div></div><div class="score-cell"><strong>${displayScore==null?'—':displayScore.toFixed(1)}</strong><div class="score-track"><span style="width:${displayScore==null?0:displayScore.toFixed(1)}%"></span></div></div><div class="factor-cell">${tags}</div><div><span class="status ${statusClass}">${status}</span></div><div class="chevron">→</div></div>`;
}
function renderCandidates(data) {
  const useStrategy=Boolean(data.agent_selection), sectorStocks=stocksForActiveSector(data), shortlist=[...sectorStocks].sort((a,b)=>(scoreFor(b,useStrategy)??-1)-(scoreFor(a,useStrategy)??-1));
  state.useComboScore=useStrategy; const scoreLabel=useStrategy?'策略评分':'技术综合分';
  const visible=shortlist.slice(0,state.visibleLimit), tableTitle=state.activeSector?'细分板块策略排名':'科技总榜'; const table = document.getElementById('candidateTable'); table.innerHTML = `<div class="table-row table-header"><span>${tableTitle}</span><span>${scoreLabel}</span><span>策略因子</span><span>交易状态</span><span></span></div>${visible.map((stock,index)=>rowTemplate(stock,index,useStrategy)).join('')}`;
  setText('rankingScoreNote',`${scoreLabel}降序`);
  const loadMore=document.getElementById('loadMore'); loadMore.disabled=visible.length>=shortlist.length; loadMore.innerHTML=`已显示 ${visible.length} / ${shortlist.length} 只真实科技股 ${visible.length<shortlist.length?'<span>继续加载 ↗</span>':''}`; loadMore.onclick=()=>{state.visibleLimit+=100;renderCandidates(data);};
  table.querySelectorAll('.candidate-row').forEach((row) => row.addEventListener('click', () => selectStock(row.dataset.symbol))); selectStock(visible[0]?.code);
}
function sparklinePaths(stock) { const points = stock.klines || []; if (points.length < 2) return { area:'', line:'' }; const closes = points.map((x) => x.close), min = Math.min(...closes), max = Math.max(...closes), span = max - min || 1; const coords = closes.map((v, i) => `${(i/(closes.length-1))*460},${110 - ((v-min)/span)*98}`).join(' L'); return { line:`M${coords}`, area:`M${coords} L460,120 L0,120Z` }; }
function renderInsight(stock) {
  if (!stock) return; state.selected = stock.code; document.querySelectorAll('.candidate-row').forEach((row) => row.classList.toggle('selected', row.dataset.symbol === stock.code));
  const useCombo=Boolean(state.useComboScore), displayedScore=scoreFor(stock,useCombo), displayedStatus=statusFor(stock,useCombo);
  setText('selectedName', `${stock.name} ${stock.code}`); setText('selectedPrice', fmtPrice(stock.price)); setText('selectedPct', fmtPct(stock.pct)); setText('selectedScoreLabel',useCombo?'策略评分':'技术综合分'); setText('selectedScore', displayedScore==null?'—':displayedScore.toFixed(1)); document.getElementById('selectedPct').className = pctClass(stock.pct);
  document.getElementById('signalMeta').innerHTML = `<span class="status ${displayedStatus === '可建仓' ? 'ready' : displayedStatus === '等待触发' ? 'wait' : 'watch'}">${displayedStatus}</span><span class="meta-sep">·</span><span>${stock.source_label || '真实日线因子'} · K线截止 ${stock.last_date || '—'}</span>`; setText('confidenceLabel', displayedScore==null?'—':displayedScore >= 70 ? '高' : displayedScore >= 55 ? '中' : '低'); const qt = stock.quote_time ? `${stock.quote_time.slice(8,10)}:${stock.quote_time.slice(10,12)}:${stock.quote_time.slice(12,14)}` : ''; setText('priceCaption', `${stock.source_label || '真实行情'}${qt ? ` · ${qt}` : ''}`);
  const paths = sparklinePaths(stock); document.getElementById('sparkPath').setAttribute('d', paths.line); document.getElementById('sparkArea').setAttribute('d', paths.area); const k = stock.klines || []; const labels = [k[0]?.date, k[Math.floor(k.length/3)]?.date, k[Math.floor(k.length*2/3)]?.date, k[k.length-1]?.date].map((x) => x ? x.slice(5) : '—'); document.getElementById('chartLabels').innerHTML = labels.map((x) => `<span>${x}</span>`).join('');
  const f = stock.factors, comboRows = useCombo ? (stock.agent_combo_breakdown || []).map((item) => `<div class="agent-row"><div class="agent-avatar quant">F</div><div><strong>${item.name}</strong><small>${item.direction}使用 · 截面排名分 ${Number(item.score).toFixed(1)}</small></div><span class="agent-score ${item.score >= 50 ? 'good' : 'caution'}">${Number(item.score).toFixed(1)}</span></div>`).join('') : '';
  const scoreSummary = useCombo ? `<div class="agent-row"><div class="agent-avatar data">D</div><div><strong>当前排名策略</strong><small>${state.dashboard?.agent_selection?.name||'所选因子等权合成'}</small></div><span class="agent-score ${displayedScore!=null&&displayedScore>=55?'good':'caution'}">${displayedScore==null?'数据不足':displayedScore.toFixed(1)}</span></div>` : `<div class="agent-row"><div class="agent-avatar data">D</div><div><strong>基础技术模型</strong><small>动量、趋势、成交与风险综合评分</small></div><span class="agent-score ${displayedScore!=null&&displayedScore>=55?'good':'caution'}">${displayedScore==null?'数据不足':displayedScore.toFixed(1)}</span></div>`;
  document.getElementById('agentList').innerHTML = `${scoreSummary}${comboRows}<div class="agent-row"><div class="agent-avatar data">D</div><div><strong>数据质量 Agent</strong><small>${stock.last_date ? `日线已更新至 ${stock.last_date}` : '无日线数据'}</small></div><span class="agent-score good">${stock.last_date ? '通过' : '阻断'}</span></div><div class="agent-row"><div class="agent-avatar bear">R</div><div><strong>基础风险因子</strong><small>20D 年化波动 ${fmtPct(f.volatility20*100)}</small></div><span class="agent-score caution">${signed(factorScore(f.volatility20)-50)}</span></div>`; renderFactorMap(stock,useCombo); renderChecks(stock);
}
function renderFactorMap(stock,useCombo=Boolean(state.useComboScore)) { const f = stock.factors, combo = useCombo ? (stock.agent_combo_breakdown || []) : []; document.getElementById('factorTimestamp').textContent = stock.last_date || '—'; document.getElementById('factorModel').textContent = useCombo ? '当前排名策略' : 'Technical Cross-Section v0.1'; if(useCombo&&!combo.length){document.getElementById('factorMap').innerHTML='<div class="data-error">当前策略所需历史数据不足，未生成策略评分。</div>';return;} const items = combo.length ? combo.map((item) => [item.key, `${item.name} · ${item.direction}`, '策略截面排名', item.score]) : [['momentum20','20D 动量','return / cross-sectional',factorScore(f.momentum20)],['trend','5/20D 趋势','moving-average spread',factorScore(f.trend)],['volume_ratio','5/20D 量比','volume confirmation',factorScore(f.volume_ratio-1)],['volatility20','20D 年化波动','risk penalty',50-factorScore(f.volatility20)+50]]; document.getElementById('factorMap').innerHTML = items.map(([, label, desc, score]) => { const positive = score >= 50, width = Math.max(5, Math.min(100, score)); return `<div class="factor-map-row"><div class="factor-name">${label}<small>${desc}</small></div><div class="factor-bar"><span class="${positive ? 'positive-bar' : 'negative-bar'}" style="width:${width}%"></span></div><strong class="${positive ? 'positive-text' : 'negative-text'}">${Number(score).toFixed(1)}</strong></div>`; }).join(''); }
function renderChecks(stock) { const checks = [{ok:!!stock.last_date,title:'数据时点有效',note:stock.last_date ? `最后日线：${stock.last_date}` : '没有返回日线数据'},{ok:stock.price>0 && stock.volume>0,title:'行情字段完整',note:stock.price>0 && stock.volume>0?'价格与成交量已返回':'价格或成交量为空'},{ok:stock.factors.volatility20<.8,title:'波动率约束',note:`20D 年化波动 ${fmtPct(stock.factors.volatility20*100)}`},{ok:false,title:'人工确认',note:'真实交易计划尚未确认'}], passed = checks.filter((x)=>x.ok).length; setText('riskSummary',`${passed} / ${checks.length} 已通过`); document.getElementById('checkList').innerHTML = checks.map((x,i)=>`<div class="check-row"><span class="check-icon ${x.ok?'pass':i===3?'block':'warn'}">${x.ok?'✓':i===3?'×':'!'}</span><div><strong>${x.title}</strong><small>${x.note}</small></div>${i===3?'<button class="confirm-button" id="confirmButton">去确认</button>':`<span class="check-tag ${x.ok?'pass-text':'warn-text'}">${x.ok?'通过':'注意'}</span>`}</div>`).join(''); document.getElementById('confirmButton').addEventListener('click',(event)=>{event.currentTarget.textContent='已确认';event.currentTarget.style.color='#438368';event.currentTarget.style.borderColor='#9ed1b5';event.currentTarget.style.background='#f2fbf5';toast('仅记录研究确认，不会下单');}); }
function renderFactorLab(data) {
  const catalog = data.factor_lab_agent_catalog || data.factor_catalog || [], visibleCatalog = catalog.slice(0, 8), sample = state.selected ? data.stocks.find((s) => s.code === state.selected) : data.stocks[0], agent = data.agent_factor_run;
  setText('labModelName', data.factor_model?.name || 'Technical Cross-Section'); setText('labModelNote', agent?.stale ? `${data.source_label || data.source} · 历史结果，当前股池已变化` : `${data.source_label || data.source} · 最近一次 Agent 研究结果`); setText('labFactorCount', catalog.length); setText('labLatestDate', sample?.last_date || data.latest_date || '—'); setText('labSource', data.source_label || data.source);
  const clusters=data.factor_clusters||{};
  const dedupBanner = clusters.input_count ? `<div class="factor-dedup">因子去共线：${clusters.input_count} 个通过门槛的因子，按真实截面相关性（|ρ| ≥ ${clusters.threshold}）并簇后剩 <strong>${clusters.independent_count}</strong> 个独立因子，${(clusters.merged||[]).length} 个被判定为重复。</div>` : '';
  document.getElementById('factorCatalog').innerHTML = dedupBanner + visibleCatalog.map((factor, index) => { const isAgent = factor.category === 'Agent 挖掘'; const value = sample?.factors?.[factor.key]; const display = isAgent ? `${factor.status || '研究中'} · ${factor.direction || '待定'}` : factor.key === 'volume_ratio' ? `${Number(value || 0).toFixed(2)}x` : fmtPct(Number(value || 0) * 100); const extra = isAgent ? `Rank IC ${Number(factor.rank_ic || 0).toFixed(4)} · ICIR ${Number(factor.icir || 0).toFixed(3)}` : `权重 ${Math.round((factor.weight || 0)*100)}%`; const dup = factor.duplicate_of ? `<span class="factor-dup" title="与代表因子高度相关">重复 · 并入「${safe(factor.duplicate_of_name || factor.duplicate_of)}」ρ=${Number(factor.duplicate_correlation||0).toFixed(2)}</span>` : (isAgent ? '<span class="factor-keep">独立因子</span>' : ''); return `<div class="factor-catalog-row${factor.duplicate_of?' is-dup':''}"><div class="factor-index">${String(index+1).padStart(2,'0')}</div><div class="factor-catalog-copy"><div><strong>${factor.name}</strong><span class="factor-category">${factor.category}</span>${dup}</div><p>${factor.description}</p><code>${factor.formula}</code></div><div class="factor-catalog-value"><strong>${display}</strong><small>${extra}</small></div></div>`; }).join('');
  const incremental=agent?.incremental_cache, cacheText=incremental?` 历史复用 ${incremental.reused_metric_points||0} 条，本轮新增或刷新 ${incremental.computed_metric_points||0} 条。`:'';
  document.getElementById('formulaGrid').innerHTML = visibleCatalog.map((factor) => `<div class="formula-chip${factor.duplicate_of?' is-dup':''}"><span>${factor.name}</span><code>${factor.formula}</code></div>`).join('') + (agent ? `<div class="mine-warning">展示前 8 个候选，共 ${catalog.length} 个已入库；本次 Agent 搜索空间 ${agent.search_space_size} 个公式。${clusters.input_count?`其中 ${clusters.input_count} 个通过门槛，去掉重复后为 ${clusters.independent_count} 个独立因子。`:''}${cacheText}</div>` : '');
}
function renderStrategyPicker(data) {
  const list=document.getElementById('strategyPickList'); if(!list) return;
  const run=data?.backtest_factor_run||{}, results=[...(run.results||[])].sort((a,b)=>Number(Boolean(b.research_only))-Number(Boolean(a.research_only))).slice(0,10);
  const resolved=data?.selected_strategy_resolved||{}, selection=data?.selected_strategy||null;
  const auto=!selection;
  setText('strategyPickSource',auto?'系统自动':`你已选定 · ${selection?.selected_at||''}`);
  setText('strategyPickName',resolved.name|| (resolved.names?.length?`#${resolved.rank||'—'} ${resolved.names.join(' / ')} · 持有 ${resolved.holding_days||'—'} 日`:'按成本后 Sharpe 自动选择'));
  if(data.strategy_unavailable)setText('strategyPickName','原选策略未匹配新回测，请重新选择');
  const clearButton=document.getElementById('strategyClearButton');
  if(clearButton) clearButton.hidden=auto;
  if(run.stale_engine){list.innerHTML='<div class="data-error">旧回测口径已失效。下一交易日成交与收益验证修正后，需要重新运行回测。</div>';return;}
  if(!results.length){ list.innerHTML='<div class="data-loading">还没有回测结果，先到「回测实验」跑一次组合回测。</div>'; return; }
  const activeKeys=(resolved.factors||[]).join(',');
  list.innerHTML=results.map((item)=>{
    const r=(item.research_only?item.segments?.test:item.result)||{}, passes=(r.days||0)>=20&&r.max_drawdown!=null&&r.max_drawdown<=0.35;
    const picked=!auto && (resolved.strategy_id?item.strategy_id===resolved.strategy_id:item.factors.join(',')===activeKeys&&item.holding_days===resolved.holding_days);
    return `<button type="button" class="strategy-option ${picked?'picked':''} ${passes?'':'over-limit'}" data-strategy-id="${item.strategy_id||''}" data-factors="${item.factors.join(',')}" data-holding="${item.holding_days}">
      <span class="strategy-rank">${item.research_only?'择时':`#${item.rank||'—'}`}</span>
      <span class="strategy-name"><strong>${safe((item.names||[]).join(' / '))}</strong><small>持有 ${item.holding_days||'—'} 个交易日 · ${passes?'在风控阈值内':'回撤超 35%'}</small></span>
      <span class="strategy-metric"><b>${fmtPct(Number(r.annual_return||0)*100)}</b><small>年化</small></span>
      <span class="strategy-metric"><b>${fmtPct(-Number(r.max_drawdown||0)*100)}</b><small>回撤</small></span>
      <span class="strategy-metric"><b>${Number(r.sharpe||0).toFixed(2)}</b><small>Sharpe</small></span>
      <span class="strategy-pick-mark">${picked?'当前':'选它'}</span>
    </button>`;
  }).join('');
  list.querySelectorAll('.strategy-option').forEach((button)=>button.addEventListener('click',()=>selectStrategy(button.dataset.factors.split(','),Number(button.dataset.holding),button.dataset.strategyId)));
  const active=list.querySelector('.strategy-option.picked');
  if(active) active.scrollIntoView({block:'nearest'});
}
async function selectStrategy(factors,holdingDays,strategyId) {
  try {
    const response=await fetch('/api/strategy/select',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({factors,holding_days:holdingDays,strategy_id:strategyId||undefined})});
    const result=await response.json();
    if(!response.ok||result.error) throw new Error(result.error||'选择失败');
    toast(`已选定：#${result.rank} ${(result.names||[]).join(' / ')} · 持有 ${result.holding_days} 日${result.passes_gate?'':'（回撤超限，会签会标为风险）'}`);
    await loadDashboard(false);
  } catch(error) { toast(`选择失败：${error.message}`); }
}
function renderValidation(run) {
  const stats=document.getElementById('validationStats'), folds=document.getElementById('validationFolds');
  if(!stats||!folds) return;
  if(!run||run.stale_engine){ stats.innerHTML='<div class="data-loading">等待新口径回测结果；旧样本外验证已失效。</div>'; folds.innerHTML=''; return; }
  const oos=run.out_of_sample||{}, mt=run.multiple_testing||{}, full=((run.results||[])[0]||{}).result||{};
  const o=oos.out_of_sample||{};
  const tone=(value)=>value==null?'':(value>0?'positive-text':'negative-text');
  const items=[
    ['全样本（样本内）',full.sharpe==null?'—':Number(full.sharpe).toFixed(2),`年化 ${reviewPct(full.annual_return)} · 回撤 ${reviewPct(-Number(full.max_drawdown||0))}`],
    ['样本外 Sharpe',o.sharpe==null?'—':Number(o.sharpe).toFixed(2),oos.status==='完成'?`年化 ${reviewPct(o.annual_return)} · 回撤 ${reviewPct(-Number(o.max_drawdown||0))}`:(oos.status||'未生成')],
    ['样本外折数',oos.fold_count==null?'—':`${oos.fold_count} 折`,`样本外 ${oos.out_of_sample_days||0} 个交易日`],
    ['过拟合缺口',full.sharpe==null||o.sharpe==null?'—':Number(full.sharpe-o.sharpe).toFixed(2),'全样本 Sharpe − 样本外 Sharpe'],
    ['试算次数',mt.trials==null?'—':`${mt.trials} 组`,'组合搜索实际评过的组合数'],
    ['运气期望上限',mt.expected_max_sharpe==null?'—':Number(mt.expected_max_sharpe).toFixed(2),`试算结果离散度 ${mt.trial_sharpe_dispersion==null?'—':Number(mt.trial_sharpe_dispersion).toFixed(2)}`],
    ['DSR 近似置信度',mt.probability==null?'—':`${(Number(mt.probability)*100).toFixed(1)}%`,mt.deflated_sharpe==null?'非盈利概率':`非盈利概率 · 校正 Sharpe ${Number(mt.deflated_sharpe).toFixed(3)}`],
  ];
  stats.innerHTML=items.map(([label,value,note])=>`<div><span>${label}</span><strong>${value}</strong><small>${note}</small></div>`).join('');
  const rows=oos.folds||[];
  folds.innerHTML=`<div class="review-row review-head oos-row"><span>训练期</span><span>测试期</span><span>窗口内候选</span><span>选中组合</span><span>持有期</span><span>训练 Sharpe</span><span>测试 Sharpe</span><span>测试年化</span><span>测试回撤</span></div>`+
    (rows.length?rows.map((row)=>`<div class="review-row oos-row"><span>${safe(row.train?.[0]||'—')}~${safe(row.train?.[1]||'')}</span><span>${safe(row.test?.[0]||'—')}~${safe(row.test?.[1]||'')}</span><span>${row.gated_count==null?'—':`${row.gated_count} 个 / 去重 ${(row.gated_count||0)-(row.merged_count||0)}`}</span><span class="review-names">${safe((row.selected_names||[]).join(' / ')||row.status||'—')}</span><span class="muted">${row.holding_days?`${row.holding_days} 个交易日`:'—'}</span><span class="muted">${row.train_sharpe==null?'—':Number(row.train_sharpe).toFixed(2)}</span><span class="${tone(row.test_sharpe)}">${row.test_sharpe==null?'—':Number(row.test_sharpe).toFixed(2)}</span><span class="${tone(row.test_annual)}">${reviewPct(row.test_annual)}</span><span class="muted">${row.test_max_drawdown==null?'—':reviewPct(-Number(row.test_max_drawdown))}</span></div>`).join(''):'<div class="data-loading">还没有生成样本外结果。</div>');
}
function renderBacktest(data) {
  const run=data.backtest_factor_run;if(!run)return;
  if(run.stale_engine){setText('backtestDate','次日退出 · 组合结果待重算');setText('backtestCost','买卖各 7.5bp');const timing=(run.results||[]).find(item=>item.research_only&&item.holding_days===1);document.getElementById('backtestResults').innerHTML=(timing?renderTimingBacktest(timing,data):'')+'<div class="data-error">旧组合与样本外结果已停用；重新运行回测只计算下一交易日退出。</div>';renderValidation(run);return;}
  setText('backtestInput',`${run.candidate_count} 个候选 · 最多 ${run.max_factors||3} 因子`);
  setText('backtestUniverse',`${run.universe_name||'科技股池'} · ${run.confirmation_universe||0} 只`);
  setText('backtestUniverseNote',(run.selected_sectors||[]).join(' / ')||'排除科创板 688/689');
  setText('backtestCount',(run.combination_count||0)+(run.registered_strategy_count||0));setText('backtestDate',`${run.data_date||'—'} · ${run.generated_at||''}`);setText('backtestCost',`${run.cost_bps}bp`);
  const regularResults=(run.results||[]).filter(item=>!item.research_only&&item.holding_days===1);
  const groups=[...new Set(regularResults.map((item)=>item.kind))].sort((a,b)=>parseInt(a)-parseInt(b));
  const renderItem=(item)=>{const r=item.result||{},names=(item.names||[]).map((name,i)=>`${safe(name)} · ${item.directions?.[i]||'待定'}`).join(' / '),holdings=(r.latest_holdings||[]).slice(0,3).map((x)=>safe(x.name)).join('、');return `<div class="backtest-result" data-factors="${item.factors.join(',')}" title="应用该策略到候选池"><div class="backtest-rank">#${item.rank||'—'}</div><div class="backtest-strategy"><strong>${safe(item.kind)}${item.holding_days?` · 持有 ${item.holding_days} 日`:''}</strong><small>${names}</small></div><div class="backtest-metric"><span>累计收益</span><strong>${fmtPct(Number(r.cumulative_return||0)*100)}</strong></div><div class="backtest-metric"><span>Sharpe</span><strong>${Number(r.sharpe||0).toFixed(2)}</strong></div><div class="backtest-metric"><span>最大回撤</span><strong>${fmtPct(-Number(r.max_drawdown||0)*100)}</strong></div><div class="backtest-metric"><span>胜率</span><strong>${fmtPct(Number(r.win_rate||0)*100)}</strong></div><div class="backtest-holdings"><b>最新候选</b>${holdings||'—'}</div></div>`;};
  const groupHtml=groups.map((group)=>{const items=regularResults.filter((item)=>item.kind===group),visible=items.slice(0,8),rest=items.slice(8);return `<section class="backtest-group"><h3>${safe(group)}<small>${items.length} 个结果</small></h3>${visible.map(renderItem).join('')}${rest.length?`<details><summary class="backtest-more">显示其余 ${rest.length} 个结果</summary>${rest.map(renderItem).join('')}</details>`:''}</section>`;}).join('');
  const timing=(run.results||[]).find(item=>item.strategy_id==='market-gated-top3-nextday-v2');
  const timingHtml=timing?`<details><summary class="backtest-more">旧收盘价回测（留档，非本次 14:50 规则）</summary>${renderTimingBacktest(timing,data)}</details>`:'';
  const incremental=`复用 ${run.reused_combination_count||0} 组 · 新算 ${run.computed_combination_count ?? run.combination_count ?? 0} 组`;
  document.getElementById('backtestResults').innerHTML=`<details><summary class="backtest-more">历史收盘模型留档（非当前次日14:50卖出规则）</summary>${timingHtml}<div class="mine-warning"><strong>${safe(run.ranking_metric||'成本后 Sharpe')}</strong> · ${incremental} · 旧收盘研究结果，非本次分时回放</div>${groupHtml}<div class="mine-warning">${safe(run.warning||'')}</div></details>`;
  document.getElementById('timingApply')?.addEventListener('click',()=>selectStrategy(timing.factors,timing.holding_days,timing.strategy_id));
  document.querySelectorAll('.backtest-result').forEach((row)=>row.addEventListener('click',()=>selectBacktestStrategy(row.dataset.factors.split(','))));
  renderValidation(run);
}
function renderTimingBacktest(item,data) {
  const segments=item.segments||{}, selected=data.selected_strategy_resolved?.strategy_id===item.strategy_id;
  const rows=[['完整历史',item.result],['训练段',segments.train],['验证段',segments.validation],['最后检验',segments.test],['延迟成交对照',segments.next_close_control],['冻结后新增样本',segments.forward]].filter(([,r])=>r&&r.holding_days===1);
  return `<section class="backtest-group timing-strategy"><div class="timing-heading"><div><h3>${safe(item.name)}<small>${selected?'当前默认':'固定研究策略'}</small></h3><p>40日高位距离 ↑ · 3日动量 ↑ · 3/20日量比 ↓ · 前三只 · 下一交易日卖出</p></div><button class="button primary" id="timingApply" ${selected?'disabled':''}>${selected?'已启用':'应用策略'}</button></div><div class="mine-warning">上涨比例 >45% 且等权指数 >MA20 才出手；其余交易日空仓。主结果为同收盘价近似，延迟成交对照另列；买卖各 7.5bp。</div><div class="review-table-wrap"><table class="review-detail-table timing-results"><thead><tr><th>样本</th><th>时间范围</th><th>交易日</th><th>盈利组 / 已平仓组</th><th>组合胜率</th><th>出手率</th><th>累计模型收益</th><th title="(期末净值 / 期初净值)^(252 / 交易日数) - 1，含空仓日">折算年化</th><th>最大回撤</th></tr></thead><tbody>${rows.map(([label,r])=>`<tr><td>${label}${r.missing_mark_days?`<small>缺价 ${r.missing_mark_days} 日 · 延迟退出模型</small>`:''}</td><td>${safe(r.start_date)} ~ ${safe(r.end_date)}</td><td>${r.days||'—'}</td><td>${r.winning_rounds||0} / ${r.closed_rounds||0}</td><td>${reviewPct(r.win_rate)}</td><td>${reviewPct(r.decision_rate)}</td><td>${reviewPct(r.cumulative_return)}</td><td title="${r.annualization_reliable?'按全部交易日折算':'不足一年，折算外推不稳定'}">${reviewPct(r.annual_return)}</td><td>${r.max_drawdown==null?'—':reviewPct(-r.max_drawdown)}</td></tr>`).join('')}</tbody></table></div><div class="mine-warning">胜率按扣成本后的整组三只计算。年化按全部交易日（含空仓）折算，短样本外推不稳定。中途缺价按模型延迟退出，期末报价恢复后显示累计收益，回撤仍不展示；期末仍缺价则不显示收益。全历史含训练与验证，非独立检验；不代表 2 万元实盘可实现收益。</div></section>`;
}
async function loadTimingWeekReview() {
  const target=document.getElementById('timingWeekReview');
  try {
    const response=await fetch('/api/timing-week-review',{cache:'no-store'}), r=await response.json();
    if(!response.ok||r.error)throw new Error(r.error||'分时报告读取失败');
    if(r.status==='pending'){target.innerHTML='<div class="data-loading">独立 14:50 周回放尚未生成，旧报告保留。</div>';return;}
    state.timingWeekReview=r;
    if(state.dashboard){renderExecutionPlan(state.dashboard);renderStrategySelect(state.dashboard);}
    const money=v=>v==null?'—':Number(v).toFixed(2);
    const artifactRoot=r.mode==='last_entries'?'.cache/intraday_1450_entries':'.cache/intraday_1450';
    const priceDownload=`<a href="/${artifactRoot}/${safe(r.report_id)}/prices_1450.csv" download>14:50 价格明细 CSV</a>`;
    target.innerHTML=`<div class="timing-heading"><div><h3>14:50 最近一周 · 独立新报告</h3><p>${safe(r.calendar_week_start)} 至 ${safe(r.end_date)} · ${r.trading_days} 个交易日 · 当日 14:50 买，次交易日 14:50 卖</p></div><a class="button" href="/.cache/intraday_1450/${safe(r.report_id)}/report.md" download>下载逐笔报告</a></div><div class="mine-warning">本金 20,000 元 · 只持一组三只，卖完再买 · ${safe(r.price_basis)} · 旧 K 线和历史回测未覆盖</div><div class="factor-lab-summary"><div><span>期末模型资产</span><strong>${money(r.ending_equity)} 元</strong></div><div><span>模型盈亏</span><strong>${money(r.pnl)} 元</strong></div><div><span>已平仓组 / 胜率</span><strong>${r.closed_rounds} / ${r.win_rate==null?'无样本':reviewPct(r.win_rate)}</strong></div><div><span>独立资金核对</span><strong>${r.cash_reconciled?'通过':'未通过'}</strong></div></div><div class="review-table-wrap"><table class="review-detail-table timing-results"><thead><tr><th>交易日14:50</th><th>上涨比例</th><th>等权指数 > MA20</th><th>数据覆盖</th><th>决策</th><th>观察前三只（未成交不算买入）</th></tr></thead><tbody>${r.decision_log.map(d=>`<tr><td>${safe(d.date)}</td><td>${reviewPct(d.market.up_fraction)}</td><td>${d.market.above_ma20?'是':'否'}</td><td>${d.stocks} / ${reviewPct(d.coverage)}</td><td>${safe(d.reason)}</td><td>${d.top3.map(s=>`${safe(s.name)} ${money(s.price)}`).join('<br>')}</td></tr>`).join('')}</tbody></table></div><h3>模拟买卖明细</h3><div class="review-table-wrap"><table class="review-detail-table timing-results"><thead><tr><th>股票</th><th>买入日14:50</th><th>买价</th><th>卖出日14:50</th><th>卖价</th><th>标准份额</th><th>成本</th><th>净盈亏</th></tr></thead><tbody>${r.trades.length?r.trades.map(t=>`<tr><td>${safe(t.name)} ${safe(t.code)}</td><td>${safe(t.buy_date)}</td><td>${money(t.buy_price)}</td><td>${safe(t.sell_date||'未到期/缺价')}</td><td>${money(t.sell_price)}</td><td>${money(t.units)}</td><td>${money(t.fees)}</td><td>${money(t.pnl)}</td></tr>`).join(''):'<tr><td colspan="8">本周行情门槛未通过，没有买卖记录；胜率不可计算，不能判断策略准不准。</td></tr>'}</tbody></table></div><div class="mine-warning">${r.warnings.map(s=>safe(s)).join(' ')}</div>`;
    if(r.mode==='last_entries'){
      target.querySelector('.timing-heading h3').textContent='最近 7 次实际出手 · 14:50 独立新报告';
      target.querySelector('.timing-heading p').textContent=`实际 ${r.actual_entries} 次出手 · ${r.start_date} 起 · 当日 14:50 买，下一交易日 14:50 卖 · 单组三只`;
      target.querySelector('a[download]').href=`/${artifactRoot}/${r.report_id}/report.md`;
      target.querySelector('.timing-heading').insertAdjacentHTML('beforeend',`<a class="button" href="/${artifactRoot}/${safe(r.report_id)}/trades.csv" download>21 笔买卖明细 CSV</a>`);
      target.querySelector('table th:last-child').textContent='本次买入前三只（模拟成交）';
      const summary=`<h3>7 次出手汇总</h3><div class="review-table-wrap"><table class="review-detail-table timing-results"><thead><tr><th>买入日14:50</th><th>卖出日14:50</th><th>股票数</th><th>整组净盈亏</th><th>整组收益</th></tr></thead><tbody>${r.entry_log.map(g=>`<tr><td>${safe(g.buy_date)}</td><td>${safe(g.sell_date||'未平仓')}</td><td>${g.stocks}</td><td>${money(g.pnl)} 元</td><td>${reviewPct(g.return)}</td></tr>`).join('')}</tbody></table></div>`;
      target.querySelectorAll('h3')[1].insertAdjacentHTML('beforebegin',summary);
    }
    target.insertAdjacentHTML('beforeend',`<div class="mine-warning">${priceDownload}</div>`);
  } catch(error){target.innerHTML=`<div class="data-error">${safe(error.message)}</div>`;}
}
function wyckoffPercent(value) { return value==null?'—':fmtPct(Number(value)*100); }
function renderWyckoffDetail(item) {
  const target=document.getElementById('wyckoffDetail');
  if(!item){target.innerHTML='<div class="data-error">当前阶段没有可展示的真实候选。</div>';return;}
  state.wyckoffSelected=item.code;
  const labels={structure:'区间结构',spring:'Spring',sos:'SOS',lps:'LPS',accumulation:'吸筹',volume:'量能确认',risk_penalty:'风险扣分'};
  const components=Object.entries(item.signals||{}).map(([key,value])=>`<div class="wyckoff-component"><span>${labels[key]||safe(key)}</span><i><b style="width:${Math.max(0,Math.min(100,Number(value)||0))}%"></b></i><strong>${Number(value||0).toFixed(1)}</strong></div>`).join('');
  target.innerHTML=`<div class="wyckoff-detail-head"><div><span class="wyckoff-phase">${safe(item.phase)}</span><h3>${safe(item.name)}</h3><small>${safe(item.code)} · ${safe(item.industry||'未分类')} · ${safe(item.date||'—')}</small></div><div class="wyckoff-detail-score"><strong>${Number(item.wyckoff_score).toFixed(1)}</strong><span>威科夫评分</span></div></div><div class="wyckoff-levels"><div><span>区间支撑</span><strong>${Number(item.range_low).toFixed(2)}</strong></div><div><span>现价</span><strong>${Number(item.price).toFixed(2)}</strong></div><div><span>区间阻力</span><strong>${Number(item.range_high).toFixed(2)}</strong></div></div><div class="wyckoff-evidence">${(item.evidence||[]).map((note)=>`<div>${safe(note)}</div>`).join('')}</div><div class="wyckoff-components">${components}</div>`;
  document.querySelectorAll('.wyckoff-candidate').forEach((row)=>row.classList.toggle('active',row.dataset.code===item.code));
}
function renderWyckoff(data) {
  if(!data)return;
  const backtest=data.backtest||{}, model=data.model||{};
  setText('wyckoffModel',model.name||'Wyckoff Price-Volume v1');setText('wyckoffModelNote',model.inputs||'仅使用前复权日线 OHLCV');
  setText('wyckoffDate',data.bar_date||data.data_date||'—');setText('wyckoffAnalyzed',`${data.analyzed_count||0} 只`);setText('wyckoffCandidateCount',`${data.candidate_count||0} 只`);setText('wyckoffPeriods',backtest.periods?`${backtest.periods} 期`:'数据不足');
  setText('wyckoffCumulative',wyckoffPercent(backtest.cumulative_return));setText('wyckoffAnnual',wyckoffPercent(backtest.annual_return));setText('wyckoffDrawdown',backtest.max_drawdown==null?'—':fmtPct(-Number(backtest.max_drawdown)*100));setText('wyckoffSharpe',backtest.sharpe==null?'—':Number(backtest.sharpe).toFixed(2));setText('wyckoffWinRate',wyckoffPercent(backtest.win_rate));
  setText('wyckoffBacktestRule',backtest.return_definition?`${backtest.return_definition}；成本 ${backtest.cost_bps||15}bp × 换手率。`:'历史样本不足，尚未生成可用回测。');
  setText('wyckoffTimestamp',`${data.bar_date||data.data_date||'—'} · 成交量单位 手 · ${data.cache_hit?'缓存命中':'本轮计算'}${data.data_stale?' · 行情缓存待刷新':''}`);
  const staleCount=data.stale_count||0;
  setText('wyckoffRule',`${model.rule||''} ${backtest.return_definition||''}${staleCount?` 已剔除 ${staleCount} 只停牌或无最新行情的标的。`:''}`);
  const list=(data.candidates||[]).filter((item)=>!state.wyckoffPhase||item.phase_key===state.wyckoffPhase), target=document.getElementById('wyckoffTable');
  target.innerHTML=`<div class="wyckoff-table-header"><span>证券</span><span>评分</span><span>阶段</span><span>区间位置</span><span>量比</span></div>${list.length?list.map((item)=>`<div class="wyckoff-candidate" data-code="${safe(item.code)}"><div class="wyckoff-stock"><strong>${safe(item.name)}</strong><small>${safe(item.code)} · ${safe(item.industry||'未分类')}</small></div><div class="wyckoff-score">${Number(item.wyckoff_score).toFixed(1)}</div><span class="wyckoff-phase">${safe(item.phase)}</span><span class="wyckoff-range">${Math.round(Number(item.range_position)*100)}%</span><span class="wyckoff-volume">${Number(item.volume_ratio).toFixed(2)}x</span></div>`).join(''):'<div class="data-error">该阶段当前没有候选。</div>'}`;
  target.querySelectorAll('.wyckoff-candidate').forEach((row)=>row.addEventListener('click',()=>renderWyckoffDetail(list.find((item)=>item.code===row.dataset.code))));
  const selected=list.find((item)=>item.code===state.wyckoffSelected)||list[0];renderWyckoffDetail(selected);
}
async function loadWyckoff(force=false) {
  const target=document.getElementById('wyckoffTable');target.innerHTML='<div class="data-loading">正在读取真实 K 线并运行独立威科夫识别与回测…</div>';
  try{const response=await fetch(`/api/wyckoff${force?'?refresh=1':''}`,{cache:'no-store'}),data=await response.json();if(!response.ok||data.error)throw new Error(data.error||'威科夫接口失败');state.wyckoff=data;renderWyckoff(data);toast(`威科夫策略完成：识别 ${data.candidate_count||0} 只候选`);}catch(error){target.innerHTML=`<div class="data-error">威科夫策略未完成：${safe(error.message)}</div>`;toast(`威科夫策略未完成：${error.message}`);}
}
async function selectBacktestStrategy(factors) { const item=state.dashboard?.backtest_factor_run?.results?.find(row=>row.factors.join(',')===factors.join(','));if(item)await selectStrategy(item.factors,item.holding_days,item.strategy_id); }
async function runBacktest() { const target=document.getElementById('backtestResults'); target.innerHTML='<div class="data-loading">正在搜索 1–5 因子组合…</div>'; try { const response=await fetch('/api/backtest',{method:'POST'}), data=await response.json(); if(!response.ok||data.error)throw new Error(data.error||'回测失败'); state.dashboard.backtest_factor_run=data; renderBacktest(state.dashboard); toast(`组合回测完成：${data.combination_count} 个组合`); } catch(error) { target.innerHTML=`<div class="data-error">真实回测未完成：${error.message}</div>`; toast('真实组合回测未完成'); } }
async function applyTechUniverse() {
  const sectors=[...document.querySelectorAll('#techSectorSelector input:checked')].map((input)=>input.value), button=document.getElementById('applyUniverseButton');
  if(!sectors.length)return toast('至少选择一个科技细分板块');
  button.disabled=true; button.textContent='重建中…'; toast('正在重建真实科技股池');
  try {
    const response=await fetch('/api/universe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sectors})}), data=await response.json();
    if(!response.ok||data.error)throw new Error(data.error||'股池更新失败');
    state.dashboard=data; state.selected=null; renderMarket(data); renderCandidates(data); renderFactorLab(data);
    document.getElementById('backtestResults').innerHTML='<div class="mine-warning">科技股池已变化。请先重新运行 Agent 因子闭环，再运行组合回测；系统不会沿用旧股池结果。</div>';
    setText('backtestInput','等待重新挖掘'); setText('backtestCount','—');
    toast(`科技股池已更新：${data.universe_size} 只，已排除科创板`);
  } catch(error) { toast(`股池更新失败：${error.message}`); }
  finally { button.disabled=false; button.textContent='更新股池'; }
}
function selectStock(code) { const stock = state.dashboard?.stocks.find((item) => item.code === code); if (stock) { renderInsight(stock); if (state.dashboard) renderFactorLab(state.dashboard); toast(`已切换至 ${stock.name} 的真实因子信号`); } }
async function runFactorMining() {
  const target = document.getElementById('mineResults');
  target.innerHTML = '<div class="data-loading">正在使用真实历史 K 线计算 Rank IC 与分层收益…</div>';
  try {
    const response = await fetch('/api/mine/agent', {method:'POST'}), data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || '挖掘接口失败');
    const validation = data.validation || {}, ranked = (validation.results || []).filter((item) => item.mean_rank_ic !== undefined).slice(0, 8);
    const incremental=data.incremental_cache||{};
    target.innerHTML = `<div class="mine-result-meta">${data.baseline_formula_count||data.search_space_size||validation.candidate_count} 个基线公式 + 本轮新生成 ${data.generated_formula_count||0} 个 · ${validation.usable_stocks} 只股票 · ${validation.history_bars || 0} 个共同历史样本 · 数据截止 ${data.data_date || '—'} · 历史复用 ${incremental.reused_metric_points||0} 条 / 新增或刷新 ${incremental.computed_metric_points||0} 条</div><div class="mine-agent-stages">${(data.stages || []).map((stage) => `<span>${stage.name}：${stage.status}</span>`).join('')}</div>${ranked.map((item) => { const bt=item.backtest||{}; return `<div class="mine-result-row"><strong>${item.name}</strong><span>${item.gate || item.status} · ${item.direction || '待定'}使用</span><small>${item.hypothesis || item.formula || ''}<br>Rank IC ${Number(item.mean_rank_ic).toFixed(4)} · ICIR ${Number(item.icir).toFixed(2)} · 方向调整后多空 ${fmtPct(Number(item.effective_long_short ?? item.mean_long_short) * 100)} · 回测年化 ${fmtPct(Number(bt.annual_return || 0) * 100)} · 回测回撤 ${fmtPct(Number(bt.max_drawdown || 0) * 100)}</small></div>`; }).join('')}<div class="mine-warning">${data.feedback}</div>`;
    toast(`Agent 闭环完成：${validation.candidate_count} 个公式已验证`);
  } catch (error) {
    target.innerHTML = `<div class="data-error">真实挖掘未完成：${error.message}</div>`;
    toast('真实因子挖掘未完成');
  }
}
function renderExecutionPlan(data) {
  if(!data) return;
  const selection=data.agent_selection||{}, runs=Array.isArray(data.backtest_factor_run&&data.backtest_factor_run.results)?data.backtest_factor_run.results:[];
  const selected=(data.selected_strategy_factors||selection.factors||[]).join(',');
  const combo=runs.find((item)=>String(item.factors||'')===selected&&item.holding_days===data.selected_strategy_resolved?.holding_days)||runs[0]||{};
  const bt=(combo.research_only?combo.segments?.test:combo.result)||{}, council=data.multi_agent_run||{}, decision=council.decision||{};
  const challenger=(council.agents||[]).find((item)=>item.id==='challenger')||{};
  const approved=(challenger.output||{}).approved_for_research;
  const drawdown=Number(bt.max_drawdown||0), limit=0.35;
  const gatePass=Number(bt.days||0)>=20&&drawdown<=limit;
  const decisionBlocked=decision.status==='阻断'||approved===false;
  // 会签结论是上一次运行的快照。如果回测之后变了、新策略已经过闸门，
  // 就不能继续显示"已阻断"，否则用户会以为系统仍然拒绝给出结论。
  const staleBacktest=Boolean(data.backtest_factor_run?.stale_engine||data.strategy_unavailable);
  const blocked=staleBacktest||(decisionBlocked&&!gatePass)||(selection.strategy_id==='market-gated-top3-nextday-v2'&&!selection.gate_open);
  const awaiting=decisionBlocked&&gatePass;
  const decisionHoldings=decision.holdings||[], backtestHoldings=bt.latest_holdings||[];
  const holdings=blocked?[]:(selection.strategy_id==='market-gated-top3-nextday-v2'?selection.top3||[]:decisionHoldings.length?decisionHoldings:backtestHoldings);

  setText('executionState',blocked?'已阻断':(awaiting?'待重新会签':(decision.status==='等待人工批准'?'待人工批准':'研究候选')));
  const stateEl=document.getElementById('executionState');
  if(stateEl) stateEl.className='execution-state '+(blocked?'block':(awaiting?'wait':(decision.status==='等待人工批准'?'wait':'ready')));

  const run=data.backtest_factor_run||{};
  const holdingDays=combo.holding_days||bt.holding_days||null;
  const holdingLabel=(run.holding_labels||{})[holdingDays]||'';

  setText('executionSummary',blocked
    ?`当前策略未通过风控闸门，系统不输出可执行标的。以下为通过审查后的建仓口径。`
    :(awaiting
      ?`回测已更新且当前策略在风控阈值内；上一次会签的阻断结论已过期，需重新运行会签才会输出标的。`
      :`研究模拟口径：收盘后信号，下一交易日收盘成交，成交后持有 ${holdingDays||'—'} 个交易日；未连接券商。`));

  setText('executionPick',`Top ${bt.holding_count||3} 等权`);
  setText('executionPickNote',`${selection.name?selection.name+' · ':''}T 日尾盘算因子 → 剔除近 20 日成交额最低 30% → 截面排名取前 ${bt.holding_count||3} 只`);

  setText('executionEntry','T+1 收盘模拟成交');
  setText('executionEntryNote','T 日收盘后信号；未成交资金保持现金，不假设按信号日价格买入');

  setText('executionExit',holdingDays?`成交后第 ${holdingDays} 日收盘`:'等待回测');
  setText('executionExitNote',holdingDays
    ?`持有 ${holdingDays} 个交易日（${holdingLabel||'收盘卖出'}），到期收盘无条件卖出，不设个股止损（组合层用回撤闸门）`
    :'等待回测给出持有期');

  setText('executionSizing','本金 20,000 元 · 整手股数');
  setText('executionSizingNote','三只目标预算各约 6,667 元；账本按实际股数、成交价、费用和剩余现金记账');

  setText('executionGate',`回撤 ${fmtPct(-drawdown*100)} / 上限 ${fmtPct(-limit*100)}`);
  setText('executionGateNote',`${drawdown>limit?'已超过上限，Challenger 否决':'在阈值内'} · 年化 ${fmtPct(Number(bt.annual_return||0)*100)} · Sharpe ${Number(bt.sharpe||0).toFixed(2)}`);
  if(staleBacktest){setText('executionSummary',data.strategy_unavailable?'原选策略未匹配新回测，请重新选择；当前没有可执行策略。':'旧回测成交与验证口径已失效；需要重算，当前没有可执行策略。');setText('executionGate',data.strategy_unavailable?'策略待确认':'旧结果失效');setText('executionGateNote','原结果不再作为风控依据');}

  setText('executionList',holdings.length?holdings.map((item)=>item.name).join(' / '):'—');
  setText('executionListNote',holdings.length
    ?`${holdings.length} 只，尾盘等权买入；数据日 ${bt.data_date||selection.data_date||'—'}`
    :(blocked&&backtestHoldings.length
      ?`阻断中不输出标的。回测最新持仓为 ${backtestHoldings.map((item)=>item.name).join(' / ')}，仅供参考，不是本轮推荐。`
      :(awaiting
        ?'需重新运行会签后才会输出本轮标的。'
        :'阻断中不输出标的；解除阻断后此处显示具体名单')));

  setText('executionDisclaimer',`仅用于研究，不构成投资建议。该策略要求 T 日尾盘按收盘价买入、下一交易日卖出；实盘需自行承担尾盘无法按理想价成交、涨跌停、停牌与冲击成本；系统不会自动下单。${council.stale?' 注意：股池已变化，需重新会签。':''}`);
  if(selection.strategy_id==='market-gated-top3-nextday-v2'){
    const week=state.timingWeekReview, latest=week?.latest_decision||week?.decision_log?.at(-1);
    setText('executionEntry','当日 14:50');
    setText('executionEntryNote','只用截至 14:50 的行情选股；新分时回放与旧收盘回测分开保存');
    setText('executionExit','下一交易日 14:50');
    setText('executionExitNote','先卖旧一组三只，再判断当天行情；不因信号变差提前更改持有期');
    setText('executionSizing','本金 20,000 元 · 单组持仓');
    setText('executionSizingNote','只持一组三只，初始每只预算约 6,667 元；卖完再买，不分批轮换。模型为标准份额，实际股数与费用另记');
    setText('executionDisclaimer','新规则：当日14:50买、下一交易日14:50卖。五分钟线末价不是实际成交回报，未验证整手、最低佣金与涨跌停成交；系统不会自动下单。旧收盘模型收益不代表本规则收益。');
    setText('executionState',latest?(latest.gate_open?'分时研究候选':'不开新仓'):'分时回放待确认');
    setText('executionSummary',latest?`${latest.date} 14:50 · ${latest.reason}；新报告见回测页，旧收盘信号仅留档。`:'请查看新分时回放，不使用旧收盘信号替代14:50信号。');
    setText('executionGate',latest?(latest.gate_open?'行情通过':'行情未通过'):'待分时数据');
    setText('executionGateNote','14:50 上涨比例 >45% 且等权指数 >MA20');
    setText('executionList',latest?.gate_open?latest.top3.map(s=>s.name).join(' / '):'—');
    setText('executionListNote',latest?.gate_open?'本次分时研究候选，未连接券商':'不输出新开仓名单，观察候选不计作成交。');
  }
}
function reviewPct(value) { return value==null?'—':fmtPct(Number(value)*100); }
let currentReview = null;
const reviewViewCache=new Map();
let reviewRequestId=0,reviewAbortController=null;
let reviewPeriod='all';
let reviewRangeLinked=true;
const reviewPeriodLabels={'1d':'近 1 日','3d':'近 3 日','7d':'近 7 日','1m':'近 1 个月',all:'9 月至今'};
function filteredReviewRounds() {
  const start=document.getElementById('reviewStartDate').value||'2026-09-01';
  const end=document.getElementById('reviewEndDate').value;
  const kind=document.getElementById('reviewKind').value;
  const query=document.getElementById('reviewSearch').value.trim().toLowerCase();
  return [...(currentReview?.rounds||[]),...(currentReview?.replay_rounds||[])]
    .filter(row=>(row.data_date>=start||reviewPeriod!=='all'&&(row.detail||[]).some(item=>item.exits?.[currentReview?.selected_strategy?.holding_days||1]?.date>=start))&&(!end||row.data_date<=end)&&(kind==='all'||(kind==='live'?row.kind!=='replay':row.kind===kind)))
    .filter(row=>!query||(row.detail||[]).some(item=>`${item.name||''} ${item.code||''}`.toLowerCase().includes(query)))
    .sort((a,b)=>b.data_date.localeCompare(a.data_date)||a.kind.localeCompare(b.kind));
}
function filteredReviewStocks() { return filteredReviewRounds().flatMap(row=>(row.detail||[]).slice(0,3).map(item=>({...item,round:row}))); }
function reviewSource(kind) { return kind==='replay'?'历史回放':kind==='council'?'会签候选':'榜单前三'; }
function renderReviewStocks() {
  const target=document.getElementById('reviewStockDetails'), rows=filteredReviewStocks();
  const price=value=>value==null?'—':Number(value).toFixed(4).replace(/0{1,2}$/,'');
  const tone=value=>value==null||value===0?'':value>0?'positive-text':'negative-text';
  renderReviewPortfolio();
  renderCapitalReview();
  if(!rows.length) { target.innerHTML='<div class="data-loading">当前策略在此范围内没有记录。</div>'; return; }
  target.innerHTML=`<table><thead><tr><th>信号日期</th><th>来源</th><th>股票 / 排名</th><th>基准收盘价</th><th>最新收盘价</th><th>累计涨跌幅</th>${[1].map(h=>`<th>${h}日涨跌幅</th>`).join('')}<th>1日净收益</th><th>行情截至</th><th>状态</th></tr></thead><tbody>`+
    rows.map(item=>`<tr><td>${safe(item.round.data_date)}</td><td><span class="review-source ${item.round.kind==='replay'?'replay':''}" title="${safe(item.round.strategy||'')}">${reviewSource(item.round.kind)}</span></td><td class="review-stock-name"><strong>${safe(item.name||item.code)}</strong><small>${safe(item.code)} · #${item.rank||'—'}</small></td><td>${price(item.entry_close)}</td><td>${price(item.latest_close)}</td><td class="${tone(item.latest_return)}">${reviewPct(item.latest_return)}</td>${[1].map(h=>{const value=item.returns?.[h],exit=item.exits?.[h];return `<td class="${tone(value)}" title="${exit?safe(`${exit.date} 收盘 ${price(exit.close)}`):'持有期未到或行情不足'}">${reviewPct(value)}</td>`;}).join('')}<td class="${tone(item.net?.['1'])}">${reviewPct(item.net?.['1'])}</td><td>${safe(item.latest_date||'—')}</td><td>${item.status==='已回填'?(item.returns?.['1']==null?'待后续行情':`已跟踪 ${item.elapsed_days||0} 日`):safe(item.status||'待回填')}</td></tr>`).join('')+'</tbody></table>';
}
function renderCapitalReview() {
  setText('capitalCacheStatus','已缓存');
  const simulation=currentReview?.capital_equity?.[document.getElementById('reviewKind').value];
  const period=simulation?.periods?.[reviewPeriod];
  const amount=value=>value==null?'—':`${Number(value).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})} 元`;
  const tone=value=>value==null||value===0?'':value>0?'positive-text':'negative-text';
  document.getElementById('capitalPeriods').innerHTML=Object.entries(reviewPeriodLabels).map(([key,label])=>{
    const pnl=simulation?.periods?.[key]?.pnl;
    return `<button type="button" data-period="${key}" aria-pressed="${key===reviewPeriod}" class="${key===reviewPeriod?'active':''}"><span>${label}</span><strong class="${tone(pnl)}">${amount(pnl)}</strong></button>`;
  }).join('');
  setText('capitalPnlLabel',`${reviewPeriodLabels[reviewPeriod]}盈亏`);
  setText('capitalPnl',amount(period?.pnl));
  document.getElementById('capitalPnl').className=tone(period?.pnl);
  setText('capitalReturn',`区间收益率 ${reviewPct(period?.net_return)}`);
  setText('capitalStarting',amount(period?.starting_equity));
  setText('capitalEnding',amount(period?.ending_equity));
  setText('capitalCash',amount(period?.cash));
  const dates=period?`${period.start_date} 至 ${period.end_date} · `:'';
  const missing=simulation?.missing_entries?.length||0;
  const entryLabel=simulation?.entry_offset===0?'信号当日收盘近似买入（有前视局限）':'信号次日收盘模拟买入';
  setText('capitalBasis',simulation?.as_of?`${dates}本金 20,000 元 · ${entryLabel}，成交后持有 ${simulation.holding_days} 日 · 标准化份额估算，未计整手与最低佣金 · 买卖各 ${(simulation.cost_bps/2).toFixed(1)}bp${missing?` · ${missing} 个候选缺少合格成交价，预算留现金`:''}${period?.complete===false?' · 持仓报价缺失，暂停报告区间收益':''}`:'该来源暂无记录，请选择历史回放；这里不使用空仓账本的 0 元代替策略收益。');
}
function renderReviewPortfolio() {
  const target=document.getElementById('reviewTable'), rounds=filteredReviewRounds();
  const horizon=currentReview?.selected_strategy?.holding_days||1;
  const tone=value=>value==null||value===0?'':value>0?'positive-text':'negative-text';
  const cost=(currentReview?.cost_bps??15)/10000;
  const evaluated=rounds.map(row=>{
    const detail=(row.detail||[]).slice(0,3),values=detail.map(item=>item.returns?.[horizon]);
    const gross=detail.length===3&&values.every(value=>value!=null)?values.reduce((a,b)=>a+b,0)/3:null;
    return {row,detail,gross,net:gross==null?null:gross-cost};
  });
  const settled=evaluated.filter(item=>item.net!=null),average=settled.length?settled.reduce((sum,item)=>sum+item.net,0)/settled.length:null;
  setText('reviewTimestamp',`${currentReview?.generated_at||'—'} · 三只等权 · 已结算 ${settled.length}/${rounds.length} 轮`);
  const equity=currentReview?.portfolio_equity?.[document.getElementById('reviewKind').value];
  const period=equity?.periods?.[reviewPeriod];
  document.getElementById('reviewPeriods').innerHTML=Object.entries(reviewPeriodLabels).map(([key,label])=>`<button type="button" data-period="${key}" aria-pressed="${key===reviewPeriod}" class="${key===reviewPeriod?'active':''}"><span>${label}</span><strong class="${tone(equity?.periods?.[key]?.net_return)}">${reviewPct(equity?.periods?.[key]?.net_return)}</strong></button>`).join('');
  setText('reviewReturnLabel',`${reviewPeriodLabels[reviewPeriod]}组合净收益`);
  setText('reviewReturnValue',reviewPct(period?.net_return));
  document.getElementById('reviewReturnValue').className=tone(period?.net_return);
  setText('reviewReturnBasis',period?`${period.start_date} 至 ${period.end_date} · ${period.trading_days} 个交易日 · ${reviewSource(document.getElementById('reviewKind').value)} · 三只等权 · ${period.complete===false?'缺少部分持仓行情，暂不报告收益':'含已收盘浮动收益与成本'}`:'当前来源暂无可计算的组合收益');
  const stats=[['区间最大回撤',period?.max_drawdown==null?'—':reviewPct(-period.max_drawdown),reviewPeriodLabels[reviewPeriod]],['平均每轮净收益',reviewPct(average),`持有 ${horizon} 个交易日 · 按明细范围`],['组合胜率',settled.length?`${(settled.filter(item=>item.net>0).length/settled.length*100).toFixed(1)}%`:'—','明细范围内已结算组合'],['已结算',`${settled.length} / ${rounds.length} 轮`,'三只行情均到齐才结算'],['最新收益日期',equity?.as_of||'—','仅用已收盘日线，不预测'],['每只权重','33.33%','每轮仅前三只']];
  document.getElementById('reviewStats').innerHTML=stats.map(([label,value,note])=>`<div><span>${label}</span><strong>${value}</strong><small>${note}</small></div>`).join('');
  setText('reviewCoverage',`${rounds.length} 轮 · 每轮前三只等权 · 持有 ${horizon} 日 · 毛收益 = 三只涨幅均值，净收益再扣 ${currentReview?.cost_bps??15}bp · 未到期显示 —`);
  if(!rounds.length){target.innerHTML='<div class="data-loading">该策略尚无此来源的记录，可选择历史回放。</div>';return;}
  target.innerHTML=`<table class="review-portfolio-table"><thead><tr><th>信号日期</th>${[1,2,3].map(i=>`<th>第 ${i} 只 / ${horizon}日涨幅</th>`).join('')}<th>三只组合毛收益</th><th>三只组合净收益</th><th>退出日期</th><th>来源 / 状态</th></tr></thead><tbody>`+evaluated.map(({row,detail,gross,net})=>`<tr><td>${safe(row.data_date)}</td>${[0,1,2].map(i=>{const item=detail[i];return item?`<td class="review-stock-name"><strong>${safe(item.name||item.code)}</strong><small>${safe(item.code)} · 权重 1/3</small><b class="${tone(item.returns?.[horizon])}">${reviewPct(item.returns?.[horizon])}</b></td>`:'<td>缺少候选</td>';}).join('')}<td class="${tone(gross)}">${reviewPct(gross)}</td><td class="${tone(net)}">${reviewPct(net)}</td><td>${safe(detail[0]?.exits?.[horizon]?.date||'—')}</td><td><span class="review-source ${row.kind==='replay'?'replay':''}">${reviewSource(row.kind)}</span><small class="review-round-status">${net==null?'未结算':`持有 ${horizon} 日已结算`}</small></td></tr>`).join('')+'</tbody></table>';
}
function exportReviewStocks() {
  const rounds=filteredReviewRounds(), horizon=currentReview?.selected_strategy?.holding_days||1;
  const headers=['信号日期','来源','策略','持有交易日数','第1只','第1只代码','第1只涨幅','第2只','第2只代码','第2只涨幅','第3只','第3只代码','第3只涨幅','三只等权毛收益','三只等权净收益'];
  const csvCell=value=>`"${String(value??'').replace(/^[=+@-]/,'\'\$&').replace(/"/g,'""')}"`;
  const records=rounds.map(row=>{const items=(row.detail||[]).slice(0,3), values=items.map(item=>item.returns?.[horizon]);const gross=items.length===3&&values.every(value=>value!=null)?values.reduce((a,b)=>a+b,0)/3:null;return [row.data_date,reviewSource(row.kind),row.strategy,horizon,...[0,1,2].flatMap(i=>[items[i]?.name,items[i]?.code,reviewPct(values[i])]),reviewPct(gross),reviewPct(gross==null?null:gross-(currentReview.cost_bps??15)/10000)];});
  const url=URL.createObjectURL(new Blob(['\ufeff'+[headers,...records].map(row=>row.map(csvCell).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}));
  const link=document.createElement('a'); link.href=url; link.download='策略前三只组合复盘.csv'; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function renderReview(review, dashboard) {
  const target=document.getElementById('reviewTable'); if(!target) return;
  if(!review) { target.innerHTML='<div class="data-error">推荐归因数据不可用。</div>'; return; }
  currentReview=review;
  const identity=review.selected_strategy?.id;
  if(identity&&reviewViewCache.get(identity)?.data!==review){
    reviewViewCache.set(identity,{data:review,receivedAt:Date.now()});
    if(reviewViewCache.size>150)reviewViewCache.delete(reviewViewCache.keys().next().value);
  }
  if(reviewRangeLinked){
    const period=review.portfolio_equity?.[document.getElementById('reviewKind').value]?.periods?.[reviewPeriod];
    if(period){document.getElementById('reviewStartDate').value=period.start_date;document.getElementById('reviewEndDate').value=period.end_date;}
  }
  const strategySelect=document.getElementById('reviewStrategy'), selected=review.selected_strategy;
  strategySelect.innerHTML=(review.strategies||[]).map(item=>`<option value="${safe(item.id)}" ${item.id===selected?.id?'selected':''}>#${item.rank||'—'} ${safe(item.name)} · 持有 ${item.holding_days} 日</option>`).join('')||'<option value="">暂无已验证策略</option>';
  setText('reviewStrategyMeta','每日前三只 · 三只等权');
  setText('reviewTimestamp',`${review.generated_at||'—'} · 成本 ${review.cost_bps||15}bp · 已结算 ${review.filled_rounds||0}/${review.total_rounds||0} 轮`);
  setText('reviewReplayWarning',review.replay_warning||'历史回放不代表当时真实推荐，不纳入真实留痕统计。');
  setText('reviewNote',review.error?`最近一次回填失败：${review.error}`:'涨跌幅以信号日收盘价为基准，不代表真实成交。逐只涨幅未扣成本，持有期净收益按 15bp 成本估算。服务运行期间每 15 分钟回填，页面每分钟同步。');
  // 复权基准直接决定收益算得对不对：不复权序列在除权日会出现假跌幅
  const counts=((dashboard||{}).history_storage||{}).adjustment_counts||{};
  const adjusted=Number(counts.qfq||0), raw=Number(counts.none||0);
  setText('reviewMethodSummary',raw?'收益口径与数据说明 · 注意：部分行情为不复权日线':'收益口径与数据说明');
  const basisEl=document.getElementById('reviewBasis');
  if(basisEl){
    if(!adjusted&&!raw){ basisEl.textContent=''; }
    else {
      const warn=raw>0;
      basisEl.className='review-basis'+(warn?' warn':'');
      basisEl.textContent=warn
        ? `⚠ 当前 ${raw} 只为不复权日线（前复权 ${adjusted} 只）：除权日会被算成真实跌幅，归因结果含此偏差。`
        : `数据基准：全部 ${adjusted} 只为前复权日线。`;
    }
  }
  const hold=(review.horizons||[]).find((item)=>item.days===1)||{};
  const tone=(value)=>value==null?'':(value>0?'positive-text':'negative-text');
  const stats=[
    ['累计留痕',`${review.total_rounds||0} 轮`,`${review.first_date||'—'} ~ ${review.latest_date||'—'}`],
    ['已结算',`${review.filled_rounds||0} 轮`,`已有实际收益，待回填 ${review.pending_rounds||0} 轮`],
    ['持有 1 日净收益',reviewPct(hold.mean),`策略口径，样本 ${hold.count||0} 轮`],
    ['胜率',hold.win_rate==null?'—':`${(Number(hold.win_rate)*100).toFixed(1)}%`,'净收益 > 0 的比例'],
    ['同期基准',reviewPct(hold.benchmark),'同股池等权，同持有期'],
    ['超额',reviewPct(hold.excess),'推荐 − 基准'],
  ];
  document.getElementById('reviewStats').innerHTML=stats.map(([label,value,note])=>`<div><span>${label}</span><strong class="${label==='胜率'?'':(typeof value==='string'&&value.indexOf('%')>=0?(value.indexOf('-')===0?'negative-text':'positive-text'):'')}">${value}</strong><small>${note}</small></div>`).join('');

  const costs=review.cost_sensitivity||[];
  const costTarget=document.getElementById('reviewCosts');
  if(costTarget) costTarget.innerHTML=costs.length?costs.map((row)=>
    `<div class="review-cost-row${row.bps===(review.cost_bps||15)?' current':''}"><span>${row.bps}bp${row.bps===(review.cost_bps||15)?'（当前口径）':''}</span><strong class="${tone(row.mean)}">${reviewPct(row.mean)}</strong><small>超额 <b class="${tone(row.excess)}">${reviewPct(row.excess)}</b> · 样本 ${row.count||0} 轮</small></div>`
  ).join(''):'<div class="data-loading">暂无已结算样本</div>';

  const horizons=(review.horizons||[]).filter((item)=>item.count>0);
  const decayTarget=document.getElementById('reviewDecay');
  if(decayTarget){
    if(!horizons.length){ decayTarget.innerHTML='<div class="data-loading">暂无已结算样本</div>'; }
    else {
      const span=Math.max(0.005,...horizons.map((item)=>Math.abs(item.mean||0)),...horizons.map((item)=>Math.abs(item.excess||0)));
      decayTarget.innerHTML=horizons.map((item)=>{
        const width=Math.max(2,Math.abs(item.mean||0)/span*100);
        return `<div class="decay-row"><span class="decay-label">持有 ${item.days} 日</span><div class="decay-track"><i class="${(item.mean||0)>=0?'up':'down'}" style="width:${width.toFixed(1)}%"></i></div><strong class="${tone(item.mean)}">${reviewPct(item.mean)}</strong><small>超额 <b class="${tone(item.excess)}">${reviewPct(item.excess)}</b> · n=${item.count}</small></div>`;
      }).join('');
    }
  }

  renderReviewStocks();
}
async function reloadReview(force=false) {
  const target=document.getElementById('reviewTable');
  const button=document.getElementById('reviewRefreshButton');
  const strategy=document.getElementById('reviewStrategy').value;
  const requestId=++reviewRequestId;
  reviewAbortController?.abort();
  if(force)reviewViewCache.clear();
  const cached=reviewViewCache.get(strategy);
  if(!force&&cached&&Date.now()-cached.receivedAt<60000){
    button.disabled=false;
    if(state.dashboard)state.dashboard.recommendation_review=cached.data;
    renderReview(cached.data,state.dashboard);
    return;
  }
  reviewAbortController=new AbortController();
  button.disabled=force;
  target.innerHTML=`<div class="data-loading">${force?'正在更新历史行情与归因…':'正在读取策略缓存…'}</div>`;
  ['capitalPnl','capitalReturn','capitalStarting','capitalEnding','capitalCash'].forEach(id=>setText(id,'—'));
  setText('capitalBasis',force?'正在更新行情与归因…':'正在读取所选策略收益…');
  setText('capitalCacheStatus',force?'更新中':'读取中');
  try {
    const params=new URLSearchParams();if(force)params.set('refresh','1');if(strategy)params.set('strategy',strategy);
    const response=await fetch(`/api/recommendations?${params}`,{cache:'no-store',signal:reviewAbortController.signal}), data=await response.json();
    if(requestId!==reviewRequestId)return;
    if(!response.ok||data.error) throw new Error(data.error||'回填失败');
    if(state.dashboard) state.dashboard.recommendation_review=data;
    renderReview(data,state.dashboard); if(force)toast(`推荐归因已回填：${data.filled_rounds||0}/${data.total_rounds||0} 轮`);
  } catch(error) {
    if(error.name==='AbortError'||requestId!==reviewRequestId)return;
    target.innerHTML=`<div class="data-error">推荐归因回填失败：${safe(error.message)}</div>`;
    setText('capitalBasis',`策略读取失败：${error.message}`);
    setText('capitalCacheStatus','读取失败');
  } finally { if(requestId===reviewRequestId)button.disabled=false; }
}
async function pollRefresh() {
  if(state.refreshPolling) return;
  state.refreshPolling=true;
  const deadline=Date.now()+15*60*1000;
  try {
    while(Date.now()<deadline){
      await new Promise((resolve)=>setTimeout(resolve,5000));
      let status;
      try { status=await (await fetch('/api/refresh-status',{cache:'no-store'})).json(); }
      catch(error){ continue; }
      if(status.refreshing) continue;
      await loadDashboard(false);
      toast(status.ok===false?`行情刷新失败：${status.error||'未知错误'}`:`行情刷新完成 · ${status.data_date||'—'}${status.stale_stocks?` · ${status.stale_stocks} 只联网失败保留旧缓存`:''}`);
      return;
    }
    toast('后台刷新仍在进行，可稍后手动刷新');
  } finally { state.refreshPolling=false; }
}
async function loadDashboard(force=false) { setLoading(force?'正在后台拉取最新行情…':'正在读取真实行情与日线 K 线…'); try { const response = await fetch(`/api/dashboard${force?'?refresh=1':''}`,{cache:'no-store'}), data = await response.json(); if (!response.ok || data.error) throw new Error(data.error || '接口请求失败'); state.dashboard = data; renderMarket(data); renderExecutionPlan(data); renderCandidates(data); renderFactorLab(data); renderAgentCollaboration(data); renderReview(data.recommendation_review, data); renderBacktest(data); if(force&&data.refreshing){ toast('已在后台拉取最新行情，完成后会自动更新'); pollRefresh(); } else { toast(force?'行情已是最新':`已接入${data.source_label || '真实行情'} · ${data.latest_date || '无最新交易日'}`); } } catch (error) { document.getElementById('candidateTable').innerHTML = `<div class="data-error">真实数据接口不可用：${error.message}<br><small>页面不会使用模拟数据。请确认通过 server.py 启动，并检查网络。</small></div>`; document.getElementById('agentList').innerHTML='<div class="data-error">无真实数据，不生成分析结论。</div>'; document.getElementById('factorMap').innerHTML='<div class="data-error">无真实数据，不计算因子。</div>'; document.getElementById('factorCatalog').innerHTML='<div class="data-error">无真实数据，不展示因子目录。</div>'; document.getElementById('formulaGrid').innerHTML='<div class="data-error">无真实数据，不展示公式。</div>'; document.getElementById('agentTeams').innerHTML='<div class="data-error">无真实数据，十二 Agent 协作已阻断。</div>'; document.getElementById('checkList').innerHTML='<div class="data-error">无真实数据，不允许进入交易确认。</div>'; setText('dataSource','未连接'); setText('systemStatus','数据阻断'); setText('systemSync','未同步'); setText('marketClockTime','未连接'); toast('真实数据连接失败，已阻断模拟内容'); } }
function switchView(view){const button=document.querySelector(`.nav-item[data-view="${view}"]`);if(!button)return;document.body.dataset.workspaceView=view;document.querySelectorAll('.nav-item').forEach((item)=>item.classList.toggle('active',item===button));setText('viewTitle',titles[view]);const views={overview:document.getElementById('overviewView'),agents:document.getElementById('agentCollabView'),factors:document.getElementById('factorLabView'),backtest:document.getElementById('backtestView'),wyckoff:document.getElementById('wyckoffView'),review:document.getElementById('reviewView')};Object.entries(views).forEach(([key,element])=>{if(element)element.hidden=key!==view;});if(view==='agents'&&state.dashboard)renderAgentCollaboration(state.dashboard);if(view==='factors'&&state.dashboard)renderFactorLab(state.dashboard);if(view==='backtest'&&state.dashboard)renderBacktest(state.dashboard);if(view==='wyckoff'){if(state.wyckoff)renderWyckoff(state.wyckoff);else loadWyckoff();}if(view==='review'&&state.dashboard)renderReview(state.dashboard.recommendation_review,state.dashboard);try{const url=new URL(window.location.href);if(view==='overview')url.searchParams.delete('view');else url.searchParams.set('view',view);history.replaceState(null,'',url);}catch(error){/* file:// 等环境忽略 */}}
document.querySelectorAll('.nav-item').forEach((button)=>button.addEventListener('click',()=>switchView(button.dataset.view)));
window.addEventListener('popstate',()=>{try{switchView(new URL(window.location.href).searchParams.get('view')||'overview');}catch(error){switchView('overview');}});
document.getElementById('starButton').addEventListener('click',(event)=>{if(!state.selected)return toast('暂无真实候选可收藏');state.starred=!state.starred;event.currentTarget.textContent=state.starred?'★':'☆';event.currentTarget.classList.toggle('starred',state.starred);toast(state.starred?'已加入观察列表':'已移出观察列表');});
document.getElementById('refreshButton').addEventListener('click',(event)=>{const icon=event.currentTarget.querySelector('.refresh-icon');icon.animate([{transform:'rotate(0)'},{transform:'rotate(360deg)'}],{duration:600});loadDashboard(true);});
document.getElementById('factorRefreshButton').addEventListener('click',()=>loadDashboard(true)); document.getElementById('mineFactorButton').addEventListener('click',runFactorMining); document.getElementById('mineRunButton').addEventListener('click',runFactorMining);
document.getElementById('runBacktestButton').addEventListener('click',runBacktest);
document.getElementById('wyckoffRefreshButton').addEventListener('click',()=>loadWyckoff(true));
document.getElementById('reviewRefreshButton').addEventListener('click',()=>reloadReview(true));
['reviewStartDate','reviewEndDate','reviewKind','reviewSearch'].forEach(id=>document.getElementById(id).addEventListener(id==='reviewSearch'?'input':'change',renderReviewStocks));
['reviewStartDate','reviewEndDate'].forEach(id=>document.getElementById(id).addEventListener('change',()=>{reviewRangeLinked=false;}));
document.getElementById('reviewExport').addEventListener('click',exportReviewStocks);
document.getElementById('reviewStrategy').addEventListener('change',()=>reloadReview(false));
function selectReviewPeriod(event){
  const button=event.target.closest('[data-period]');if(!button)return;
  reviewPeriod=button.dataset.period;
  reviewRangeLinked=true;
  const period=currentReview?.portfolio_equity?.[document.getElementById('reviewKind').value]?.periods?.[reviewPeriod];
  if(period){document.getElementById('reviewStartDate').value=period.start_date;document.getElementById('reviewEndDate').value=period.end_date;}
  renderReviewStocks();
}
document.getElementById('reviewPeriods').addEventListener('click',selectReviewPeriod);
document.getElementById('capitalPeriods').addEventListener('click',selectReviewPeriod);
document.querySelectorAll('#wyckoffTabs [data-phase]').forEach((button)=>button.addEventListener('click',()=>{state.wyckoffPhase=button.dataset.phase;document.querySelectorAll('#wyckoffTabs [data-phase]').forEach((item)=>item.classList.toggle('active',item===button));if(state.wyckoff)renderWyckoff(state.wyckoff);}));
document.getElementById('agentRunButton').addEventListener('click',()=>runAgentCollaboration('full'));
document.getElementById('agentQuickRunButton').addEventListener('click',()=>runAgentCollaboration('quick'));
document.getElementById('agentRefreshButton').addEventListener('click',()=>loadDashboard(true));
const strategyClear=document.getElementById('strategyClearButton');
if(strategyClear) strategyClear.addEventListener('click',async()=>{try{const response=await fetch('/api/strategy/select',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({factors:[]})});if(!response.ok) throw new Error('取消失败');toast('已恢复为系统自动选择策略');await loadDashboard(false);}catch(error){toast(error.message);}});
document.getElementById('applyUniverseButton').addEventListener('click',applyTechUniverse);
document.getElementById('applyStrategyButton').addEventListener('click',()=>{const value=document.getElementById('strategySelect').value;if(value==='')return toast('请先选择一个回测策略');const item=state.dashboard?.backtest_factor_run?.results?.[Number(value)];if(item)selectStrategy(item.factors,item.holding_days,item.strategy_id);});
document.querySelectorAll('.filter-button').forEach((button)=>button.addEventListener('click',()=>{document.querySelectorAll('.filter-button').forEach((item)=>item.classList.remove('active'));button.classList.add('active');if(!state.dashboard)return toast('暂无真实数据可筛选');const useStrategy=Boolean(state.dashboard.agent_selection);state.useComboScore=useStrategy;setText('rankingScoreNote',`${useStrategy?'策略评分':'技术综合分'}降序`);const base=[...stocksForActiveSector(state.dashboard)].sort((a,b)=>(scoreFor(b,useStrategy)??-1)-(scoreFor(a,useStrategy)??-1)),label=button.childNodes[0].textContent.trim(),filtered=label==='可建仓'?base.filter((s)=>statusFor(s,useStrategy)==='可建仓'):label==='待确认'?base.filter((s)=>statusFor(s,useStrategy)==='等待触发'):label==='高风险'?base.filter((s)=>s.factors.volatility20>.8):base,table=document.getElementById('candidateTable');table.innerHTML=`<div class="table-row table-header"><span>证券</span><span>${useStrategy?'策略评分':'技术综合分'}</span><span>策略因子</span><span>交易状态</span><span></span></div>${filtered.map((stock,index)=>rowTemplate(stock,index,useStrategy)).join('')}`;table.querySelectorAll('.candidate-row').forEach((row)=>row.addEventListener('click',()=>selectStock(row.dataset.symbol)));if(filtered.length)selectStock(filtered[0].code);toast(`已筛选 ${filtered.length} 个真实候选`);}));
initializeWorkspace();
window.setInterval(()=>{if(document.getElementById('agentRunProgress')?.classList.contains('running'))updateAgentElapsed();},1000);
let reviewSyncing=false;
window.setInterval(async()=>{
  if(document.hidden||document.body.dataset.workspaceView!=='review'||reviewSyncing||document.getElementById('reviewRefreshButton').disabled)return;
  reviewSyncing=true;
  const syncRequestId=reviewRequestId;
  try {
    const strategy=document.getElementById('reviewStrategy').value;
    const response=await fetch(`/api/recommendations${strategy?'?strategy='+encodeURIComponent(strategy):''}`,{cache:'no-store'}),data=await response.json();
    if(!response.ok||data.error)throw new Error(data.error||'同步失败');
    if(syncRequestId!==reviewRequestId||strategy!==document.getElementById('reviewStrategy').value)return;
    if(state.dashboard)state.dashboard.recommendation_review=data;
    renderReview(data,state.dashboard);
  } catch(error) { setText('reviewTimestamp',`自动同步失败 · ${error.message} · 保留上次数据`); }
  finally {reviewSyncing=false;}
},60000);
let ledgerData={accounts:[]},ledgerAccountId='';
const ledgerMoney=value=>value==null?'—':Number(value).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
function ledgerTable(headers,rows){return `<table><thead><tr>${headers.map(h=>`<th>${safe(h)}</th>`).join('')}</tr></thead><tbody>${rows.length?rows.map(row=>`<tr>${row.map(cell=>`<td>${cell}</td>`).join('')}</tr>`).join(''):`<tr><td colspan="${headers.length}">暂无记录</td></tr>`}</tbody></table>`;}
function renderLedger(data){
  ledgerData=data;
  const accounts=data.accounts||[];
  if(!accounts.some(a=>a.id===ledgerAccountId))ledgerAccountId=accounts[0]?.id||'';
  document.getElementById('ledgerAccount').innerHTML=accounts.length?accounts.map(a=>`<option value="${a.id}" ${a.id===ledgerAccountId?'selected':''}>${safe(a.name)} · ${ledgerMoney(a.initial_cash)}元</option>`).join(''):'<option value="">未建立模拟账户</option>';
  const account=accounts.find(a=>a.id===ledgerAccountId);
  document.querySelectorAll('#ledgerOrderForm input,#ledgerOrderForm select,#ledgerOrderForm button,#ledgerFillForm input,#ledgerFillForm select,#ledgerFillForm button').forEach(el=>el.disabled=!account);
  document.getElementById('ledgerStats').innerHTML=account?[
    ['本金',ledgerMoney(account.initial_cash)],['现金',ledgerMoney(account.cash)],['总资产',ledgerMoney(account.equity)],
    ['累计盈亏',ledgerMoney(account.total_pnl)],['已实现盈亏',ledgerMoney(account.realized_pnl)],['累计费用',ledgerMoney(account.fees)]
    ].map(([label,value])=>`<div><span>${label}</span><strong>${value}</strong><small>元</small></div>`).join(''):'';
  if(account)document.getElementById('ledgerStats').insertAdjacentHTML('beforeend',`<div><span>账户收益率</span><strong>${reviewPct(account.return)}</strong><small>按账户本金</small></div>`);
  setText('ledgerStatus',!account?'本金 20,000 元 · 尚无成交':account.valuation_complete?'手工模拟成交 · 现价估值 · 订单未成交不改变持仓':'持仓估值缺失，累计收益暂停显示');
  document.getElementById('ledgerPositions').innerHTML=ledgerTable(['股票','持仓股数','含费成本价','现价 / 日期','市值','浮动盈亏'],(account?.positions||[]).map(p=>[
    `${safe(p.name)} <small>${safe(p.code)}</small>`,p.quantity,ledgerMoney(p.average_cost),`${ledgerMoney(p.mark_price)} / ${safe(p.mark_date||'—')}`,ledgerMoney(p.market_value),ledgerMoney(p.unrealized_pnl)]));
  const orders=account?.orders||[],pending=orders.filter(o=>['pending','partial'].includes(o.status)),status={pending:'未成交',partial:'部分成交',filled:'已成交',cancelled:'已撤销'};
  document.getElementById('ledgerFillOrder').innerHTML=pending.length?pending.map(o=>`<option value="${o.id}">${safe(o.name)} · ${o.side==='buy'?'买':'卖'} · 剩余${o.quantity-o.filled_quantity}股</option>`).join(''):'<option value="">无待成交订单</option>';
  document.querySelector('#ledgerFillForm button').disabled=!pending.length;
  document.getElementById('ledgerOrders').innerHTML=ledgerTable(['订单股票','方向','计划 / 成交股数','信号日期','状态','操作'],orders.slice().reverse().map(o=>[
    `${safe(o.name)} <small>${safe(o.code)}</small>`,o.side==='buy'?'买入':'卖出',`${o.quantity} / ${o.filled_quantity}`,safe(o.signal_date),status[o.status]||safe(o.status),['pending','partial'].includes(o.status)?`<button type="button" class="button icon-button" data-cancel-order="${o.id}" title="撤销剩余订单" aria-label="撤销${safe(o.code)}剩余订单"><i data-lucide="x"></i></button>`:'—']));
  const events=(account?.events||[]).filter(e=>e.kind==='fill');
  document.getElementById('ledgerEvents').innerHTML=ledgerTable(['成交日期','股票','方向','股数','成交价','成交额','费用'],events.slice().reverse().map(e=>{
    const o=orders.find(o=>o.id===e.order_id);return [safe(e.date),safe(o?.name||''),o?.side==='buy'?'买入':'卖出',e.quantity,ledgerMoney(e.price),ledgerMoney(Number(e.price)*e.quantity),ledgerMoney(e.fees)];}));
  if(window.lucide)window.lucide.createIcons();
}
async function loadLedger(){try{const response=await fetch('/api/trading-ledger',{cache:'no-store'}),data=await response.json();if(!response.ok)throw new Error(data.error||'读取失败');renderLedger(data);}catch(error){setText('ledgerStatus',`账本读取失败：${error.message}`);}}
async function ledgerAction(action,values,form){
  const button=form?.querySelector('button[type="submit"]');if(button)button.disabled=true;
  const requestId=form?.dataset.requestId||crypto.randomUUID();if(form)form.dataset.requestId=requestId;
  try{const response=await fetch('/api/trading-ledger',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,account_id:ledgerAccountId,request_id:requestId,...values})}),data=await response.json();if(!response.ok){if(form)delete form.dataset.requestId;throw new Error(data.error||'记账失败');}if(action==='create')ledgerAccountId=data.accounts.at(-1)?.id||'';if(form)delete form.dataset.requestId;renderLedger(data);toast('模拟账本已保存');}catch(error){toast(error.message);}finally{if(button)renderLedger(ledgerData);}
}
document.getElementById('ledgerCreate').addEventListener('submit',event=>{event.preventDefault();ledgerAction('create',{initial_cash:document.getElementById('ledgerCapital').value},event.currentTarget);});
document.getElementById('ledgerAccount').addEventListener('change',event=>{ledgerAccountId=event.target.value;renderLedger(ledgerData);});
document.getElementById('ledgerRefresh').addEventListener('click',loadLedger);
document.getElementById('ledgerOrderForm').addEventListener('submit',event=>{event.preventDefault();const code=document.getElementById('ledgerCode').value,stock=state.dashboard?.stocks?.find(s=>s.code===code);ledgerAction('order',{code,name:stock?.name||code,side:document.getElementById('ledgerSide').value,quantity:Number(document.getElementById('ledgerOrderQuantity').value),signal_date:document.getElementById('ledgerSignalDate').value,strategy_id:document.getElementById('reviewStrategy').value||'manual'},event.currentTarget);});
document.getElementById('ledgerFillForm').addEventListener('submit',event=>{event.preventDefault();ledgerAction('fill',{order_id:document.getElementById('ledgerFillOrder').value,quantity:Number(document.getElementById('ledgerFillQuantity').value),price:document.getElementById('ledgerFillPrice').value,date:document.getElementById('ledgerFillDate').value,fees:document.getElementById('ledgerFillFees').value},event.currentTarget);});
document.getElementById('ledgerOrders').addEventListener('click',event=>{const button=event.target.closest('[data-cancel-order]');if(button)ledgerAction('cancel',{order_id:button.dataset.cancelOrder});});
const ledgerNow=new Date(),ledgerToday=[ledgerNow.getFullYear(),String(ledgerNow.getMonth()+1).padStart(2,'0'),String(ledgerNow.getDate()).padStart(2,'0')].join('-');['ledgerSignalDate','ledgerFillDate'].forEach(id=>{document.getElementById(id).value=ledgerToday;document.getElementById(id).max=ledgerToday;});
loadLedger();
loadTimingWeekReview();
loadDashboard(performance.getEntriesByType('navigation')[0]?.type === 'reload');
try { const initialView = new URL(window.location.href).searchParams.get('view'); if (initialView && initialView !== 'overview') switchView(initialView); } catch (error) { /* 非 http 环境忽略 */ }
