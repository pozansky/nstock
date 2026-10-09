const state = { selected: null, starred: false, dashboard: null, activeSector: '', visibleLimit: 100, expandedAgent: null, opinionCache: new Map(), agentRunStarting: false, agentRunError: '', agentRunMode: 'full', wyckoff: null, wyckoffPhase: '', wyckoffSelected: null };
const titles = { overview:'市场驾驶舱', agents:'Agent 协作台', factors:'因子实验室', backtest:'回测实验', wyckoff:'威科夫策略' };
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
  select.innerHTML='<option value="">选择排名策略</option>'+run.results.map((item,index)=>`<option value="${item.factors.join(',')}">${item.kind} · ${item.names.join(' + ')}</option>`).join('');
  setText('activeStrategyName',data.agent_selection?.name || '请选择排名策略'); setText('activeStrategyRule',data.agent_selection ? `已为 ${data.agent_selection.scored_count||0} 只股票评分 · 应用于科技总榜和全部分类榜` : '应用于科技总榜和全部分类榜');
  const activeFactors=data.agent_selection?.factors||data.selected_strategy_factors||[], match=(run.results||[]).find((item)=>(item.factors||[]).join(',')===activeFactors.join(',')), result=match?.result||{}, metrics=document.getElementById('activeStrategyMetrics');
  if(match&&metrics){const values=[`#${match.rank||'—'} / ${(run.results||[]).length}`,fmtPct(Number(result.cumulative_return||0)*100),fmtPct(Number(result.annual_return||0)*100),fmtPct(-Number(result.max_drawdown||0)*100),Number(result.sharpe||0).toFixed(2),`${(Number(result.win_rate||0)*100).toFixed(1)}%`];metrics.querySelectorAll('strong').forEach((node,index)=>{node.textContent=values[index];node.className=index===1||index===2?'positive-text':index===3?'negative-text':'';});setText('activeStrategyBacktestMeta',`${result.days||0} 个交易日 · ${result.cost_bps??run.cost_bps??0}bp 成本 · 截止 ${run.data_date||'—'} · ${match.rank_metric||run.ranking_metric||'成本后 Sharpe'}排名`);}else if(metrics){metrics.querySelectorAll('strong').forEach((node)=>{node.textContent='—';node.className='';});setText('activeStrategyBacktestMeta','当前策略尚无匹配的回测结果');}
}
function renderMarket(data) {
  renderStrategySelect(data);
  renderTechUniverse(data);
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
function renderAgentRunProgress(run) {
  const panel=document.getElementById('agentRunProgress');
  if(!panel)return;
  const agents=run?.agents||[], byId=Object.fromEntries(agents.map(item=>[item.id,item]));
  const completed=agents.filter(item=>terminalAgentStatuses.has(item.status)).length;
  const active=agentExecutionOrder.map(id=>byId[id]).find(item=>item?.status==='运行中');
  const lastStarted=[...agentExecutionOrder].reverse().map(id=>byId[id]).find(item=>item?.started_at);
  const failed=agents.find(item=>['失败','阻断'].includes(item.status));
  const running=state.agentRunStarting||Boolean(active)||run?.status==='运行中';
  const isDone=Boolean(run&&['等待人工批准','阻断'].includes(run.status)&&!running);
  const percent=state.agentRunStarting&&!agents.length?2:Math.max(running?4:0,Math.min(100,Math.round(completed/12*100)));
  let title='等待启动十二 Agent 闭环', detail='点击“运行十二 Agent 闭环”后，这里会实时显示当前 Agent 与步骤。';
  if(state.agentRunError){title='本轮启动失败';detail=state.agentRunError;}
  else if(state.agentRunStarting){const full=state.agentRunMode==='full',prep=state.agentPrep;title=prep?.stage|| (full?'正在启动全量重算':'正在启动快速会签');detail=prep?.detail||(full?'先同步新增 K 线，再增量计算因子与回测…':'先同步新增 K 线；市场情报重新运行，研发与回测复用最近结果…');}
  else if(active){const step=(active.process||[]).at(-1);title=`${active.name} · ${step?.stage||'运行中'}`;detail=step?.detail||active.summary||'正在处理真实输入';}
  else if(isDone){title=failed?`闭环结束 · ${failed.name}${failed.status}`:`本轮 12 Agent 已完成 · ${run.execution_mode||'强制重算'}`;detail=`运行 ${run.run_id||'—'} · ${run.completed_at||run.updated_at||'时间未知'} · ${run.status}`;}
  else if(run){title=`最近一轮：${run.status||'状态未知'}`;detail=`运行 ${run.run_id||'—'} · 更新于 ${run.updated_at||'—'}`;}
  panel.className=`agent-run-progress ${running?'running':state.agentRunError||failed?'failed':isDone?'complete':'idle'}`;
  setText('agentProgressCount',`${completed}/12`);setText('agentProgressTitle',title);setText('agentProgressDetail',detail);setText('agentProgressTime',running?'实时更新':run?.updated_at||'尚未运行');
  const bar=document.getElementById('agentProgressBar');if(bar)bar.style.width=`${percent}%`;
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
  return `<article class="agent-opinion-full"><header><div><strong>${safe(stock.name||payload.name||stock.code)}</strong><span>${safe(stock.code)}</span></div><small>${opinions.length} 条观点</small></header>${opinions.map((record)=>`<details><summary><span>${safe(record.title||'无标题观点')}</span><time>${safe(record.publish_time_beijing||record.opi_pubtime||'时间未知')}</time></summary><div class="agent-opinion-body">${safe(cleanOpinionText(record))}</div></details>`).join('')}</article>`;
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
  const run=data.multi_agent_run, allAgents=run?.agents||[], byId=Object.fromEntries(allAgents.map((item)=>[item.id,item])), waiting={status:'等待',summary:'等待首席研究 Agent 调度',evidence:[]};
  const chief=byId.chief||{...waiting,name:'首席研究 Agent',role:'制定任务、检查依赖并签发最终研究结论'};
  const decision=run?.decision||{}, holdings=(decision.holdings||[]).slice(0,3), completed=run&&['等待人工批准','阻断'].includes(run.status);
  setText('agentMission',run?.mission||'十二 Agent、四团队量化研究会签');
  setText('agentMissionMeta',run?`更新于 ${run.updated_at||'—'}`:'等待首次运行');
  setText('agentResearchDate',run?.data_date||data.latest_date||'—'); setText('agentUniverseCount',`${run?.universe_size||data.universe_size||0} 只`); setText('agentLoopState',run?.status||'未运行'); setText('agentRunTime',run?.updated_at||'等待运行记录');
  renderAgentRunProgress(run);
  const chiefTone=chief.status==='失败'||chief.status==='阻断'?'risk':chief.status==='运行中'?'running':chief.status==='等待'?'idle':'complete';
  document.getElementById('agentChief').innerHTML=`<button class="agent-chief-open" type="button" data-agent-id="chief"><span class="agent-chief-avatar">CIO</span><span><small>首席结论</small><strong>${safe(concise(chief.summary,56))}</strong></span><b class="agent-status ${chiefTone}">${safe(chief.status)}</b></button>`;
  renderChiefOpinions(run,chief,holdings);
  document.getElementById('agentTeams').innerHTML=Object.entries(agentBlueprint).map(([team,members],teamIndex)=>`<section class="agent-team-column"><header><span>0${teamIndex+1}</span><strong>${team}</strong><small>${members.length} 席</small></header><div class="agent-team-flow">${members.map(([id,name])=>{const item=byId[id]||{...waiting,name};const tone=['失败','阻断'].includes(item.status)?'risk':['警告','降级','数据受限'].includes(item.status)?'warn':item.status==='运行中'?'running':['完成','复用'].includes(item.status)?'complete':'idle';const summary=item.status==='等待'?'等待前置结果':concise(item.summary||item.role||'暂无结论');return `<article class="agent-work-node ${tone} ${state.expandedAgent===id?'selected':''}" data-agent-id="${id}"><div class="agent-work-top"><strong>${safe((item.name||name).replace(' Agent',''))}</strong><b>${safe(item.status)}</b></div><p>${safe(summary)}</p><button class="agent-inspect" type="button" data-agent-id="${id}">${item.status==='运行中'?'查看过程':'查看结果'}</button></article>`}).join('')}</div></section>`).join('');
  document.querySelectorAll('[data-agent-id]').forEach((button)=>button.addEventListener('click',(event)=>{event.stopPropagation();state.expandedAgent=button.dataset.agentId;renderAgentDetail(byId[state.expandedAgent]||null);document.getElementById('agentDetail')?.scrollIntoView({behavior:'smooth',block:'nearest'});}));
  renderAgentDetail(state.expandedAgent?byId[state.expandedAgent]:null);
  const strategy=(decision.strategy||[]).join(' + ');
  setText('agentDecisionTitle',strategy?`组合经理：${strategy}`:'等待十二 Agent 会签'); setText('agentDecisionRule',holdings.length?'最多3只、等权研究组合；Challenger 未批准实盘，必须人工确认。':'没有完成全部真实验证前，组合经理不会输出股票。');
  document.getElementById('agentTop3').innerHTML=holdings.length?holdings.map((stock,index)=>`<button class="agent-pick" data-symbol="${safe(stock.code)}"><span>0${index+1}</span><div><strong>${safe(stock.name)}</strong><small>${safe(stock.code)}</small></div><b>查看 →</b></button>`).join(''):`<div class="agent-empty-decision">${run?.status==='阻断'?'会签已阻断':'等待组合经理输出'}</div>`;
  document.querySelectorAll('.agent-pick').forEach((button)=>button.addEventListener('click',()=>{document.querySelector('[data-view="overview"]').click();selectStock(button.dataset.symbol);}));
  const warnings=run?.warnings||[]; setText('criticCount',`${warnings.length} 项`);
  document.getElementById('agentCritiques').innerHTML=warnings.length?warnings.slice(0,3).map((text,index)=>`<div class="agent-critique-row ${index===0?'risk':'warn'}"><span>!</span><div><strong>${index===0?'关键风险':'执行限制'}</strong><small>${safe(concise(text,72))}</small></div></div>`).join(''):'<div class="agent-clear-state">当前无关键风险</div>';
  const running=Boolean(run&&!completed&&run.status==='运行中'), fullButton=document.getElementById('agentRunButton'), quickButton=document.getElementById('agentQuickRunButton');
  fullButton.disabled=running;quickButton.disabled=running;
  fullButton.innerHTML=running?'Agent 运行中…':'全量重算 · 含研发组 <span>↗</span>';quickButton.textContent=running?'运行中…':'快速会签 · 复用因子';
}
async function pollAgentCouncil(previousRunId, allowSameRun=false) {
  for(let attempt=0;attempt<360;attempt++){
    const response=await fetch('/api/agents',{cache:'no-store'}), payload=await response.json();
    if(!response.ok||payload.error)throw new Error(payload.error||'运行状态读取失败');
    const isCurrent=Boolean(payload.run&&(allowSameRun||payload.run.run_id!==previousRunId));
    if(payload.preparing){state.agentPrep=payload.preparing;state.agentRunStarting=true;renderAgentRunProgress(state.dashboard?.multi_agent_run);if(payload.preparing.error)throw new Error(`K 线同步失败：${payload.preparing.error}`);}
    else if(payload.running||isCurrent){state.agentPrep=null;state.agentRunStarting=false;if(payload.run){state.dashboard.multi_agent_run=payload.run;renderAgentCollaboration(state.dashboard);}}
    if(!payload.running&&isCurrent)return payload.run;
    await new Promise((resolve)=>setTimeout(resolve,1000));
  }
  throw new Error('十二 Agent 运行超时，请刷新查看已落盘状态');
}
async function runAgentCollaboration(mode='full') {
  const fullButton=document.getElementById('agentRunButton'), quickButton=document.getElementById('agentQuickRunButton'), previousRunId=state.dashboard?.multi_agent_run?.run_id||null;
  state.agentRunMode=mode;state.agentRunStarting=true;state.agentRunError='';fullButton.disabled=true;quickButton.disabled=true;fullButton.textContent='正在启动…';quickButton.textContent='正在启动…';renderAgentRunProgress(state.dashboard?.multi_agent_run);
  try {
    const response=await fetch('/api/agents/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode})}), payload=await response.json();if(!response.ok||payload.error)throw new Error(payload.error||'启动失败');
    toast('十二 Agent 已启动，进度正在实时更新');const run=await pollAgentCouncil(previousRunId,payload.status==='运行中');
    state.agentRunStarting=false;state.dashboard.multi_agent_run=run;renderAgentCollaboration(state.dashboard);toast(`十二 Agent 会签完成：${run?.status||'已落盘'}`);
  } catch(error){state.agentRunStarting=false;state.agentRunError=error.message;renderAgentRunProgress(state.dashboard?.multi_agent_run);toast(`Agent 会签未完成：${error.message}`);}
  finally{state.agentPrep=null;fullButton.disabled=false;quickButton.disabled=false;fullButton.innerHTML='全量重算 · 含研发组 <span>↗</span>';quickButton.textContent='快速会签 · 复用因子';}
}
function scoreFor(stock,useStrategy=Boolean(state.dashboard?.agent_selection)) { const value=useStrategy?stock.agent_combo_score:stock.score; return value==null||!Number.isFinite(Number(value))?null:Number(value); }
function statusFor(stock,useStrategy=Boolean(state.dashboard?.agent_selection)) { const score=scoreFor(stock,useStrategy); return score==null?'数据不足':score>=70?'可建仓':score>=55?'等待触发':'观察'; }
function rowTemplate(stock, index, useCombo=false) {
  const colors = ['red', 'orange', 'blue', 'purple'], f = stock.factors;
  const comboTags = useCombo ? (stock.agent_combo_breakdown || []).map((item) => `<span class="factor ${item.score >= 50 ? 'positive' : 'negative'}">${item.name} ${item.direction} ${Number(item.score).toFixed(0)}</span>`).join('') : '';
  const tags = useCombo ? (comboTags || '<span class="factor neutral">策略数据不足</span>') : `<span class="factor ${f.momentum20 >= 0 ? 'positive' : 'negative'}">动量 ${signed(factorScore(f.momentum20)-50)}</span><span class="factor ${f.trend >= 0 ? 'positive' : 'negative'}">趋势 ${signed(factorScore(f.trend)-50)}</span><span class="factor ${f.volume_ratio >= 1 ? 'positive' : 'neutral'}">量比 ${Number(f.volume_ratio).toFixed(2)}x</span>`;
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
  document.getElementById('factorCatalog').innerHTML = visibleCatalog.map((factor, index) => { const isAgent = factor.category === 'Agent 挖掘'; const value = sample?.factors?.[factor.key]; const display = isAgent ? `${factor.status || '研究中'} · ${factor.direction || '待定'}` : factor.key === 'volume_ratio' ? `${Number(value || 0).toFixed(2)}x` : fmtPct(Number(value || 0) * 100); const extra = isAgent ? `Rank IC ${Number(factor.rank_ic || 0).toFixed(4)} · ICIR ${Number(factor.icir || 0).toFixed(3)}` : `权重 ${Math.round((factor.weight || 0)*100)}%`; return `<div class="factor-catalog-row"><div class="factor-index">${String(index+1).padStart(2,'0')}</div><div class="factor-catalog-copy"><div><strong>${factor.name}</strong><span class="factor-category">${factor.category}</span></div><p>${factor.description}</p><code>${factor.formula}</code></div><div class="factor-catalog-value"><strong>${display}</strong><small>${extra}</small></div></div>`; }).join('');
  const incremental=agent?.incremental_cache, cacheText=incremental?` 历史复用 ${incremental.reused_metric_points||0} 条，本轮新增或刷新 ${incremental.computed_metric_points||0} 条。`:'';
  document.getElementById('formulaGrid').innerHTML = visibleCatalog.map((factor) => `<div class="formula-chip"><span>${factor.name}</span><code>${factor.formula}</code></div>`).join('') + (agent ? `<div class="mine-warning">展示前 8 个候选，共 ${catalog.length} 个已入库；本次 Agent 搜索空间 ${agent.search_space_size} 个公式。${cacheText}</div>` : '');
}
function renderBacktest(data) {
  const run=data.backtest_factor_run;if(!run)return;
  setText('backtestInput',`${run.candidate_count} 个候选 · 最多 ${run.max_factors||3} 因子`);
  setText('backtestUniverse',`${run.universe_name||'科技股池'} · ${run.confirmation_universe||0} 只`);
  setText('backtestUniverseNote',(run.selected_sectors||[]).join(' / ')||'排除科创板 688/689');
  setText('backtestCount',run.combination_count);setText('backtestDate',`${run.data_date||'—'} · ${run.generated_at||''}`);setText('backtestCost',`${run.cost_bps}bp`);
  const groups=[...new Set((run.results||[]).map((item)=>item.kind))].sort((a,b)=>parseInt(a)-parseInt(b));
  const renderItem=(item)=>{const r=item.result||{},names=(item.names||[]).map((name,i)=>`${safe(name)} · ${item.directions?.[i]||'待定'}`).join(' / '),holdings=(r.latest_holdings||[]).slice(0,3).map((x)=>safe(x.name)).join('、');return `<div class="backtest-result" data-factors="${item.factors.join(',')}" title="应用该策略到候选池"><div class="backtest-rank">#${item.rank||'—'}</div><div class="backtest-strategy"><strong>${safe(item.kind)}</strong><small>${names}</small></div><div class="backtest-metric"><span>累计收益</span><strong>${fmtPct(Number(r.cumulative_return||0)*100)}</strong></div><div class="backtest-metric"><span>Sharpe</span><strong>${Number(r.sharpe||0).toFixed(2)}</strong></div><div class="backtest-metric"><span>最大回撤</span><strong>${fmtPct(-Number(r.max_drawdown||0)*100)}</strong></div><div class="backtest-metric"><span>胜率</span><strong>${fmtPct(Number(r.win_rate||0)*100)}</strong></div><div class="backtest-holdings"><b>最新候选</b>${holdings||'—'}</div></div>`;};
  const groupHtml=groups.map((group)=>{const items=(run.results||[]).filter((item)=>item.kind===group),visible=items.slice(0,8),rest=items.slice(8);return `<section class="backtest-group"><h3>${safe(group)}<small>${items.length} 个结果</small></h3>${visible.map(renderItem).join('')}${rest.length?`<details><summary class="backtest-more">显示其余 ${rest.length} 个结果</summary>${rest.map(renderItem).join('')}</details>`:''}</section>`;}).join('');
  const incremental=`复用 ${run.reused_combination_count||0} 组 · 新算 ${run.computed_combination_count ?? run.combination_count ?? 0} 组`;
  document.getElementById('backtestResults').innerHTML=`<div class="mine-warning"><strong>${safe(run.ranking_metric||'成本后 Sharpe')}</strong> · ${incremental} · T 日信号，T+1 建仓，T+2 退出 · ${run.cost_bps||15}bp × 换手率</div>${groupHtml}<div class="mine-warning">${safe(run.warning||'')}</div>`;
  document.querySelectorAll('.backtest-result').forEach((row)=>row.addEventListener('click',()=>selectBacktestStrategy(row.dataset.factors.split(','))));
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
  setText('wyckoffDate',data.data_date||'—');setText('wyckoffAnalyzed',`${data.analyzed_count||0} 只`);setText('wyckoffCandidateCount',`${data.candidate_count||0} 只`);setText('wyckoffPeriods',backtest.periods?`${backtest.periods} 期`:'数据不足');
  setText('wyckoffCumulative',wyckoffPercent(backtest.cumulative_return));setText('wyckoffAnnual',wyckoffPercent(backtest.annual_return));setText('wyckoffDrawdown',backtest.max_drawdown==null?'—':fmtPct(-Number(backtest.max_drawdown)*100));setText('wyckoffSharpe',backtest.sharpe==null?'—':Number(backtest.sharpe).toFixed(2));setText('wyckoffWinRate',wyckoffPercent(backtest.win_rate));
  setText('wyckoffBacktestRule',backtest.return_definition?`${backtest.return_definition}；成本 ${backtest.cost_bps||15}bp × 换手率。`:'历史样本不足，尚未生成可用回测。');
  setText('wyckoffTimestamp',`${data.data_date||'—'} · ${data.cache_hit?'缓存命中':'本轮计算'}`);setText('wyckoffRule',`${model.rule||''} ${backtest.return_definition||''}`);
  const list=(data.candidates||[]).filter((item)=>!state.wyckoffPhase||item.phase_key===state.wyckoffPhase), target=document.getElementById('wyckoffTable');
  target.innerHTML=`<div class="wyckoff-table-header"><span>证券</span><span>评分</span><span>阶段</span><span>区间位置</span><span>量比</span></div>${list.length?list.map((item)=>`<div class="wyckoff-candidate" data-code="${safe(item.code)}"><div class="wyckoff-stock"><strong>${safe(item.name)}</strong><small>${safe(item.code)} · ${safe(item.industry||'未分类')}</small></div><div class="wyckoff-score">${Number(item.wyckoff_score).toFixed(1)}</div><span class="wyckoff-phase">${safe(item.phase)}</span><span class="wyckoff-range">${Math.round(Number(item.range_position)*100)}%</span><span class="wyckoff-volume">${Number(item.volume_ratio).toFixed(2)}x</span></div>`).join(''):'<div class="data-error">该阶段当前没有候选。</div>'}`;
  target.querySelectorAll('.wyckoff-candidate').forEach((row)=>row.addEventListener('click',()=>renderWyckoffDetail(list.find((item)=>item.code===row.dataset.code))));
  const selected=list.find((item)=>item.code===state.wyckoffSelected)||list[0];renderWyckoffDetail(selected);
}
async function loadWyckoff(force=false) {
  const target=document.getElementById('wyckoffTable');target.innerHTML='<div class="data-loading">正在读取真实 K 线并运行独立威科夫识别与回测…</div>';
  try{const response=await fetch(`/api/wyckoff${force?'?refresh=1':''}`,{cache:'no-store'}),data=await response.json();if(!response.ok||data.error)throw new Error(data.error||'威科夫接口失败');state.wyckoff=data;renderWyckoff(data);toast(`威科夫策略完成：识别 ${data.candidate_count||0} 只候选`);}catch(error){target.innerHTML=`<div class="data-error">威科夫策略未完成：${safe(error.message)}</div>`;toast(`威科夫策略未完成：${error.message}`);}
}
async function selectBacktestStrategy(factors) { try { const response=await fetch('/api/backtest/select',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({factors})}), data=await response.json(); if(!response.ok||data.error)throw new Error(data.error||'策略选择失败'); state.dashboard.agent_selection=data.selection;state.dashboard.selected_strategy_factors=factors;state.dashboard.stocks.forEach((stock)=>{stock.agent_combo_score=data.selection.scores[stock.code]??null;stock.agent_combo_breakdown=data.selection.breakdowns[stock.code]||[];stock.agent_combo_factors=factors;});state.selected=null;state.visibleLimit=100;renderStrategySelect(state.dashboard);renderMarket(state.dashboard);renderCandidates(state.dashboard);toast(`策略已应用到总榜和全部分类：${data.selection.name}`); } catch(error) { toast(`策略选择失败：${error.message}`); } }
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
async function loadDashboard() { setLoading('正在读取真实行情与日线 K 线…'); try { const response = await fetch('/api/dashboard',{cache:'no-store'}), data = await response.json(); if (!response.ok || data.error) throw new Error(data.error || '接口请求失败'); state.dashboard = data; renderMarket(data); renderCandidates(data); renderFactorLab(data); renderAgentCollaboration(data); toast(`已接入${data.source_label || '真实行情'} · ${data.latest_date || '无最新交易日'}`); } catch (error) { document.getElementById('candidateTable').innerHTML = `<div class="data-error">真实数据接口不可用：${error.message}<br><small>页面不会使用模拟数据。请确认通过 server.py 启动，并检查网络。</small></div>`; document.getElementById('agentList').innerHTML='<div class="data-error">无真实数据，不生成分析结论。</div>'; document.getElementById('factorMap').innerHTML='<div class="data-error">无真实数据，不计算因子。</div>'; document.getElementById('factorCatalog').innerHTML='<div class="data-error">无真实数据，不展示因子目录。</div>'; document.getElementById('formulaGrid').innerHTML='<div class="data-error">无真实数据，不展示公式。</div>'; document.getElementById('agentTeams').innerHTML='<div class="data-error">无真实数据，十二 Agent 协作已阻断。</div>'; document.getElementById('checkList').innerHTML='<div class="data-error">无真实数据，不允许进入交易确认。</div>'; setText('dataSource','未连接'); setText('systemStatus','数据阻断'); setText('systemSync','未同步'); setText('marketClockTime','未连接'); toast('真实数据连接失败，已阻断模拟内容'); } }
document.querySelectorAll('.nav-item').forEach((button)=>button.addEventListener('click',()=>{const view=button.dataset.view;document.querySelectorAll('.nav-item').forEach((item)=>item.classList.toggle('active',item===button));setText('viewTitle',titles[view]);const views={overview:document.getElementById('overviewView'),agents:document.getElementById('agentCollabView'),factors:document.getElementById('factorLabView'),backtest:document.getElementById('backtestView'),wyckoff:document.getElementById('wyckoffView')};Object.entries(views).forEach(([key,element])=>{element.hidden=key!==view;});if(view==='agents'&&state.dashboard)renderAgentCollaboration(state.dashboard);if(view==='factors'&&state.dashboard)renderFactorLab(state.dashboard);if(view==='backtest'&&state.dashboard)renderBacktest(state.dashboard);if(view==='wyckoff'){if(state.wyckoff)renderWyckoff(state.wyckoff);else loadWyckoff();}}));
document.getElementById('starButton').addEventListener('click',(event)=>{if(!state.selected)return toast('暂无真实候选可收藏');state.starred=!state.starred;event.currentTarget.textContent=state.starred?'★':'☆';event.currentTarget.classList.toggle('starred',state.starred);toast(state.starred?'已加入观察列表':'已移出观察列表');});
document.getElementById('refreshButton').addEventListener('click',(event)=>{const icon=event.currentTarget.querySelector('.refresh-icon');icon.animate([{transform:'rotate(0)'},{transform:'rotate(360deg)'}],{duration:600});loadDashboard();});
document.getElementById('factorRefreshButton').addEventListener('click',()=>{loadDashboard();toast('正在刷新真实因子');}); document.getElementById('mineFactorButton').addEventListener('click',runFactorMining); document.getElementById('mineRunButton').addEventListener('click',runFactorMining);
document.getElementById('runBacktestButton').addEventListener('click',runBacktest);
document.getElementById('wyckoffRefreshButton').addEventListener('click',()=>loadWyckoff(true));
document.querySelectorAll('#wyckoffTabs [data-phase]').forEach((button)=>button.addEventListener('click',()=>{state.wyckoffPhase=button.dataset.phase;document.querySelectorAll('#wyckoffTabs [data-phase]').forEach((item)=>item.classList.toggle('active',item===button));if(state.wyckoff)renderWyckoff(state.wyckoff);}));
document.getElementById('agentRunButton').addEventListener('click',()=>runAgentCollaboration('full'));
document.getElementById('agentQuickRunButton').addEventListener('click',()=>runAgentCollaboration('quick'));
document.getElementById('agentRefreshButton').addEventListener('click',()=>{loadDashboard();toast('正在刷新 Agent 真实运行状态');});
document.getElementById('applyUniverseButton').addEventListener('click',applyTechUniverse);
document.getElementById('applyStrategyButton').addEventListener('click',()=>{const value=document.getElementById('strategySelect').value;if(!value)return toast('请先选择一个回测策略');selectBacktestStrategy(value.split(','));});
document.querySelectorAll('.filter-button').forEach((button)=>button.addEventListener('click',()=>{document.querySelectorAll('.filter-button').forEach((item)=>item.classList.remove('active'));button.classList.add('active');if(!state.dashboard)return toast('暂无真实数据可筛选');const useStrategy=Boolean(state.dashboard.agent_selection);state.useComboScore=useStrategy;setText('rankingScoreNote',`${useStrategy?'策略评分':'技术综合分'}降序`);const base=[...stocksForActiveSector(state.dashboard)].sort((a,b)=>(scoreFor(b,useStrategy)??-1)-(scoreFor(a,useStrategy)??-1)),label=button.childNodes[0].textContent.trim(),filtered=label==='可建仓'?base.filter((s)=>statusFor(s,useStrategy)==='可建仓'):label==='待确认'?base.filter((s)=>statusFor(s,useStrategy)==='等待触发'):label==='高风险'?base.filter((s)=>s.factors.volatility20>.8):base,table=document.getElementById('candidateTable');table.innerHTML=`<div class="table-row table-header"><span>证券</span><span>${useStrategy?'策略评分':'技术综合分'}</span><span>策略因子</span><span>交易状态</span><span></span></div>${filtered.map((stock,index)=>rowTemplate(stock,index,useStrategy)).join('')}`;table.querySelectorAll('.candidate-row').forEach((row)=>row.addEventListener('click',()=>selectStock(row.dataset.symbol)));if(filtered.length)selectStock(filtered[0].code);toast(`已筛选 ${filtered.length} 个真实候选`);}));
loadDashboard();
