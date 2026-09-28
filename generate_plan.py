from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.section import WD_SECTION
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.enum.style import WD_STYLE_TYPE

OUT = '多Agent股票分析与选股交易系统规划文档.docx'

def set_cell_shading(cell, fill):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = tcPr.find(qn('w:shd'))
    if shd is None:
        shd = OxmlElement('w:shd')
        tcPr.append(shd)
    shd.set(qn('w:fill'), fill)

def set_cell_border(cell, color='D9D9D9', sz='6'):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    borders = tcPr.first_child_found_in('w:tcBorders')
    if borders is None:
        borders = OxmlElement('w:tcBorders')
        tcPr.append(borders)
    for edge in ('top','left','bottom','right','insideH','insideV'):
        tag = 'w:' + edge
        el = borders.find(qn(tag))
        if el is None:
            el = OxmlElement(tag)
            borders.append(el)
        el.set(qn('w:val'), 'single')
        el.set(qn('w:sz'), sz)
        el.set(qn('w:space'), '0')
        el.set(qn('w:color'), color)

def set_cell_margins(cell, top=100, start=120, bottom=100, end=120):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcMar = tcPr.first_child_found_in('w:tcMar')
    if tcMar is None:
        tcMar = OxmlElement('w:tcMar')
        tcPr.append(tcMar)
    for m, v in [('top', top), ('start', start), ('bottom', bottom), ('end', end)]:
        node = tcMar.find(qn('w:' + m))
        if node is None:
            node = OxmlElement('w:' + m)
            tcMar.append(node)
        node.set(qn('w:w'), str(v)); node.set(qn('w:type'), 'dxa')

def set_repeat_table_header(row):
    trPr = row._tr.get_or_add_trPr()
    tblHeader = OxmlElement('w:tblHeader')
    tblHeader.set(qn('w:val'), 'true')
    trPr.append(tblHeader)

def set_keep(paragraph, keep_next=False):
    pPr = paragraph._p.get_or_add_pPr()
    keep = OxmlElement('w:keepNext' if keep_next else 'w:keepLines')
    pPr.append(keep)

def add_page_number(paragraph):
    run = paragraph.add_run()
    fldChar1 = OxmlElement('w:fldChar'); fldChar1.set(qn('w:fldCharType'), 'begin')
    instrText = OxmlElement('w:instrText'); instrText.set(qn('xml:space'), 'preserve'); instrText.text = ' PAGE '
    fldChar2 = OxmlElement('w:fldChar'); fldChar2.set(qn('w:fldCharType'), 'end')
    run._r.append(fldChar1); run._r.append(instrText); run._r.append(fldChar2)

def add_table(doc, headers, rows, widths=None):
    table = doc.add_table(rows=1, cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = 'Table Grid'
    hdr = table.rows[0]
    set_repeat_table_header(hdr)
    for i, text in enumerate(headers):
        c = hdr.cells[i]; c.text = text; set_cell_shading(c, '1F4E79'); set_cell_border(c); set_cell_margins(c)
        c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        for p in c.paragraphs:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            for r in p.runs: r.font.bold = True; r.font.color.rgb = RGBColor(255,255,255); r.font.size = Pt(9)
    for ri, row in enumerate(rows):
        cells = table.add_row().cells
        for i, text in enumerate(row):
            c = cells[i]; c.text = str(text); set_cell_border(c); set_cell_margins(c); c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            if ri % 2 == 1: set_cell_shading(c, 'F2F6FA')
            for p in c.paragraphs:
                p.paragraph_format.space_after = Pt(2); p.paragraph_format.line_spacing = 1.05
                for r in p.runs: r.font.size = Pt(9)
    if widths:
        for row in table.rows:
            for i, width in enumerate(widths): row.cells[i].width = Inches(width)
    doc.add_paragraph().paragraph_format.space_after = Pt(1)
    return table

def add_bullet(doc, text, level=0):
    p = doc.add_paragraph(style='List Bullet' if level == 0 else 'List Bullet 2')
    p.paragraph_format.space_after = Pt(3); p.paragraph_format.line_spacing = 1.08
    p.add_run(text)
    return p

def add_para(doc, text, bold_lead=None):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(6); p.paragraph_format.line_spacing = 1.16
    if bold_lead and text.startswith(bold_lead):
        p.add_run(bold_lead).bold = True; p.add_run(text[len(bold_lead):])
    else: p.add_run(text)
    return p

doc = Document()
sec = doc.sections[0]
sec.top_margin = Inches(0.72); sec.bottom_margin = Inches(0.65); sec.left_margin = Inches(0.78); sec.right_margin = Inches(0.78)

styles = doc.styles
styles['Normal'].font.name = 'Microsoft YaHei'; styles['Normal']._element.rPr.rFonts.set(qn('w:eastAsia'), 'Microsoft YaHei'); styles['Normal'].font.size = Pt(10.5)
for name, size, color in [('Title', 24, '000000'), ('Heading 1', 16, '000000'), ('Heading 2', 12.5, '000000'), ('Heading 3', 11, '000000')]:
    st = styles[name]; st.font.name = 'Microsoft YaHei'; st._element.rPr.rFonts.set(qn('w:eastAsia'), 'Microsoft YaHei'); st.font.size = Pt(size); st.font.bold = True; st.font.color.rgb = RGBColor.from_string(color)
styles['Heading 1'].paragraph_format.space_before = Pt(15); styles['Heading 1'].paragraph_format.space_after = Pt(7)
styles['Heading 2'].paragraph_format.space_before = Pt(10); styles['Heading 2'].paragraph_format.space_after = Pt(5)
styles['Heading 3'].paragraph_format.space_before = Pt(7); styles['Heading 3'].paragraph_format.space_after = Pt(3)

header = sec.header.paragraphs[0]; header.text = '多 Agent 股票分析与选股交易系统规划'; header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
for r in header.runs: r.font.size = Pt(8); r.font.color.rgb = RGBColor(100,100,100)
footer = sec.footer.paragraphs[0]; footer.alignment = WD_ALIGN_PARAGRAPH.CENTER; footer.add_run('内部产品规划  |  '); add_page_number(footer)

p = doc.add_paragraph(style='Title'); p.add_run('多 Agent 股票分析与选股交易系统规划')
p.alignment = WD_ALIGN_PARAGRAPH.LEFT
sub = doc.add_paragraph(); sub.paragraph_format.space_after = Pt(16); sub.add_run('面向中国 A 股的研究决策与交易执行蓝图').font.size = Pt(13)
meta = doc.add_paragraph(); meta.paragraph_format.space_after = Pt(18); meta.add_run('版本 1.0  |  2026 年 9 月  |  适用对象：产品、量化、研究、工程与交易运营团队').font.size = Pt(9); meta.runs[0].font.color.rgb = RGBColor(90,90,90)

add_para(doc, '本文件的结论是：系统的核心产品不是“让多个模型各说一句话”，而是把数据校验、独立研究、冲突裁决、组合构建、风控拦截和盘后复盘串成一条有证据、有时点、有责任边界的交易决策链。系统首期应定位为“研究增强与交易前决策平台”，默认人工确认下单；只有在经过纸面交易、滚动回测、模拟盘和小资金实盘四道闸门后，才考虑开放有限自动化。')
add_para(doc, '本规划默认市场为中国 A 股，覆盖沪深主板、创业板、科创板和北交所时需分别配置交易规则。默认策略周期为日线选股、盘中执行与盘后复盘；系统不承诺收益，不替代投资者判断，也不允许用未经验证的模型输出直接触发真实下单。')

doc.add_heading('一 交易目标与产品边界', level=1)
add_para(doc, '系统要解决的不是“预测明天涨跌”这一单点问题，而是四个可验证的问题：今天哪些股票值得进入研究池，为什么；哪些信号足够强且尚未被价格完全反映；在账户约束下买多少、何时失效；如果判断错误，如何把损失控制在预先接受的范围内。')
add_table(doc, ['目标', '可交付结果', '不可接受的做法'], [
    ['研究提效', '每只候选股都有带时间戳的证据链、风险清单和反方观点', '只展示自然语言结论，不提供来源与数据时点'],
    ['选股可执行', '输出候选池、入选原因、触发条件、失效条件、计划仓位', '输出“强烈看好”但没有价格、周期或风险边界'],
    ['组合可控', '考虑相关性、行业暴露、流动性、涨跌停、停牌和账户限制', '按单票评分简单排序后等权买入'],
    ['可复盘', '记录每次推荐时的输入、版本、决策、执行与结果', '事后修改口径，把结果好的案例当作系统能力'],
])
doc.add_heading('二 端到端交易闭环', level=1)
add_para(doc, '产品必须围绕一个不可跳过的闭环组织页面和服务：行情与基本面数据进入数据层后先做质量闸门；通过闸门的股票进入多 Agent 独立研究；裁决层将结论转换为带置信度和条件的交易计划；组合与风控层决定是否允许进入订单候选；人工确认后才进入执行；盘后将成交、滑点、持仓变化和结果回写，形成可审计的复盘样本。')
add_table(doc, ['阶段', '关键问题', '必须留痕的字段'], [
    ['数据闸门', '数据是否完整、及时、可追溯？', '数据源、采集时间、交易日、延迟、缺失率、修订版本'],
    ['候选生成', '股票为什么进入研究池？', '筛选规则、宇宙范围、过滤原因、排序版本'],
    ['多 Agent 研究', '基本面、技术面、资金面和事件是否一致？', 'Agent 版本、输入快照、引用来源、结论、置信度'],
    ['裁决与组合', '信号能否转化为风险可控的仓位？', '分歧、否决项、目标仓位、最大损失、持有周期'],
    ['执行与监控', '实际成交是否偏离计划？', '委托、成交、滑点、撤单、异常、人工确认人'],
    ['复盘与学习', '预测和执行哪里出了问题？', '预测快照、实际结果、归因、模型/规则变更'],
])

doc.add_heading('三 多 Agent 角色设计', level=1)
add_para(doc, 'Agent 必须按信息来源和失败模式拆分，而不是按“牛市观点”“熊市观点”做表演式辩论。每个 Agent 只负责自己的证据域，输出结构化结论，不能直接下单；所有 Agent 都要标注数据截止时间和证据等级。')
add_table(doc, ['Agent', '职责', '主要输入', '输出'], [
    ['数据质量 Agent', '检查缺失、延迟、复权、异常值、口径变化', '行情、财务、公告、资金流数据', '数据质量分、禁用字段、可用时间窗'],
    ['基本面 Agent', '分析盈利、现金流、资产负债表、估值与财报质量', '财报、业绩预告、估值序列、行业基准', '经营趋势、估值区间、财务红旗'],
    ['行业与宏观 Agent', '识别行业景气、政策、利率、商品和风格环境', '指数、行业数据、宏观与政策文本', '行业状态、顺风/逆风判断、风险情景'],
    ['技术与量价 Agent', '识别趋势、波动、成交、支撑阻力和交易结构', '日线/分钟线、成交量、波动率、指数相对强弱', '入场条件、止损参考、信号失效条件'],
    ['资金与情绪 Agent', '分析主力/北向/融资融券/龙虎榜等可用资金信息', '资金流、两融、市场广度、涨停结构', '拥挤度、资金确认或背离'],
    ['事件与舆情 Agent', '提取公告、问询、诉讼、监管、舆情的事实与影响', '公告、交易所披露、合规新闻源', '事件影响、时间窗、可信度、黑天鹅标签'],
    ['反方与压力测试 Agent', '主动寻找反例、失效路径和不可交易因素', '前述 Agent 输出和原始数据', '反方论点、压力情景、否决建议'],
    ['裁决 Agent', '按固定规则整合分歧，生成研究结论而非无条件买卖建议', '所有结构化输出、组合状态、账户约束', '等级、置信度、条件化交易计划、理由链'],
    ['组合风控 Agent', '约束仓位、行业集中、相关性、流动性和损失预算', '候选计划、持仓、账户、交易规则', '允许/拒绝、目标仓位、风险预算、调整原因'],
])
add_para(doc, '裁决 Agent 不应通过“多数投票”简单决定。建议使用可解释的加权框架：数据质量是硬门槛；基本面、行业、量价、资金、事件是证据分；反方 Agent 负责扣分与触发否决；组合风控负责最终准入。任何硬否决项出现时，即使总分高也不得生成可执行订单。')

doc.add_heading('四 选股与交易规则', level=1)
doc.add_heading('4.1 股票池与硬过滤', level=2)
add_para(doc, '先定义可交易宇宙，再谈排序。每日开盘前生成股票池快照，明确剔除原因。默认硬过滤包括：停牌、退市整理、流动性不足、上市时间过短、财务或监管红旗、涨跌停导致无法按计划成交、数据质量不达标以及账户或策略明确禁止的标的。')
add_heading('4.2 分层评分', level=2)
add_table(doc, ['层级', '建议权重', '核心问题', '示例信号'], [
    ['数据质量', '硬门槛', '结论是否值得相信？', '价格连续性、财报是否过期、公告是否完整'],
    ['经营质量', '25%', '公司是否在改善？', '收入/利润/现金流趋势、ROIC、应收与存货质量'],
    ['估值与赔率', '20%', '上涨空间是否足以补偿风险？', '历史分位、同业比较、情景估值、预期差'],
    ['行业与宏观', '15%', '环境是否支持？', '行业景气、政策方向、利率/商品敏感度'],
    ['趋势与量价', '20%', '现在是否适合交易？', '相对强弱、趋势、成交确认、波动与支撑'],
    ['资金与情绪', '10%', '交易拥挤还是有增量？', '资金流、市场广度、情绪极端度'],
    ['反方风险扣分', '动态', '什么会让判断失效？', '业绩下修、监管、流动性、相关性、事件风险'],
])
add_para(doc, '评分只能决定研究优先级，不能直接等价为收益概率。每个候选必须同时输出三种结果：研究评级、交易状态和下一步动作。交易状态至少分为“观察”“等待触发”“可建仓”“持有”“减仓”“禁止交易”，并给出有效期。')
doc.add_heading('4.3 交易计划模板', level=2)
add_table(doc, ['字段', '要求'], [
    ['交易方向与周期', '明确做多/减仓/退出，持有周期为日内、数日、数周或更长'],
    ['触发条件', '用可观测条件表达，如收盘确认、成交额、相对强弱或事件落地'],
    ['入场区间', '给出价格区间或条件，不用一个无法解释的精确点位'],
    ['初始止损与失效', '基于波动、结构或事件定义，写清不可继续持有的原因'],
    ['目标与分批机制', '至少区分第一目标、后续跟踪与退出条件'],
    ['计划仓位', '由风险预算、止损距离、流动性和组合暴露共同决定'],
    ['不交易条件', '涨停无法买入、开盘跳空超限、数据过期、重大公告未消化等'],
    ['证据与反方', '至少两类独立证据，列出最强反方观点及验证方式'],
])

doc.add_heading('五 风控与执行控制', level=1)
add_para(doc, '真正可交易的系统必须先定义“什么时候不做”。风控不是页面上的红色标签，而是能够阻断订单候选生成的程序化约束。')
add_table(doc, ['风险层', '首期规则建议', '触发后的动作'], [
    ['单票风险', '单笔计划亏损不超过账户净值的 0.25%–0.50%，参数可配置', '自动反推仓位；超过上限则拒绝'],
    ['组合暴露', '限制总仓位、单行业、单主题、单因子暴露及高相关持仓', '压缩新增仓位或要求先减仓'],
    ['流动性', '按近 20 日成交额、盘口深度和预估参与率约束订单规模', '拆单、延后或禁止交易'],
    ['事件风险', '财报、重大公告、停牌风险窗口配置黑名单或降仓规则', '冻结新增仓位或仅允许退出'],
    ['市场状态', '识别极端波动、连续跌停、指数风控状态和流动性收缩', '切换防守模式、降低风险预算'],
    ['模型与数据', '数据延迟、源冲突、版本漂移、Agent 超时都作为风险事件', '降级为人工研究或停止生成信号'],
])
add_para(doc, '仓位计算可先采用简单可解释的风险预算：计划仓位 = 单笔可承受亏损 ÷（入场价 − 止损价）× 风险调整系数。风险调整系数应同时考虑流动性、数据质量、信号分歧、组合相关性和市场状态。具体参数必须通过历史和模拟盘验证，不能直接把经验值当作最优值。')
doc.add_heading('六 回测与验证闸门', level=1)
add_para(doc, '任何“策略有效”的结论都必须回答：在什么样本、什么信息时点、什么交易成本和什么约束下有效。回测服务要保存数据快照和代码版本，禁止把未来修订后的财报、收盘后公告或不可成交价格泄漏到当时的决策中。')
add_table(doc, ['闸门', '通过条件', '失败处理'], [
    ['数据审计', '无未来函数；复权、停牌、涨跌停、交易日和公告时点可复现', '阻断回测，列出数据问题'],
    ['样本外与滚动', '训练、验证、样本外按时间切分；滚动窗口重跑结果方向稳定', '降低复杂度或回到规则基线'],
    ['成本敏感性', '纳入佣金、印花税、滑点、冲击成本和无法成交情形', '重新评估收益/换手/容量'],
    ['压力测试', '熊市、震荡、急跌、行业崩塌和数据中断下不会失控', '降低仓位或增加硬限制'],
    ['模拟盘', '连续至少一个完整市场周期，信号、执行和告警稳定', '修复执行偏差后重新开始'],
    ['小资金实盘', '有限额度、人工确认、可随时停止，结果与模拟盘差异可解释', '回滚到模拟盘并复盘'],
])
add_para(doc, '必须同时保留一个简单基线，例如宽基指数、行业动量或固定规则选股，作为对照。多 Agent 系统只有在扣除交易成本和容量约束后持续优于基线，或显著降低回撤、换手和研究时间，才有上线价值。')

doc.add_heading('七 页面信息架构', level=1)
add_para(doc, '页面不应把所有 Agent 的长文本堆在一个聊天窗口里。首屏要回答“现在是什么市场、有哪些候选、能否交易、风险在哪里”；用户再逐层展开证据和原始材料。')
add_table(doc, ['页面', '首屏必须展示', '关键动作'], [
    ['市场驾驶舱', '指数与市场状态、风险预算、今日信号数、数据健康、待确认事项', '切换市场/策略/日期，进入候选或风控'],
    ['多 Agent 选股', '候选池、评级、触发状态、评分拆解、行业与组合暴露', '筛选、对比、加入观察、生成研究任务'],
    ['个股分析工作台', '结论、证据链、反方观点、价格结构、交易计划、失效条件', '查看来源、追问 Agent、确认/否决计划'],
    ['组合与交易计划', '当前持仓、目标仓位、风险贡献、订单候选、异常限制', '模拟调仓、人工确认、生成执行清单'],
    ['回测与实验', '策略版本、样本区间、成本、基线、收益与回撤、稳定性', '运行实验、比较版本、锁定发布'],
    ['复盘与审计', '推荐与实际结果、预测偏差、滑点、归因、模型变更', '标记错误类型、提交改进、导出审计'],
])
doc.add_heading('八 数据与系统架构', level=1)
add_para(doc, '建议采用“事件驱动 + 可回放”的架构。原始数据只追加不覆盖；标准化层生成带版本的事实表；特征层记录计算时点；Agent 层读取冻结快照；决策层写入不可变审计日志；执行层与券商接口隔离，先输出订单候选，再由人工确认。')
add_table(doc, ['服务', '职责', '最低要求'], [
    ['数据接入与质量', '行情、财务、公告、资金、宏观接入与校验', '时点、来源、版本、缺失与冲突可追溯'],
    ['特征与指标', '计算因子、技术指标、行业基准和状态变量', '避免未来函数，支持历史重放'],
    ['Agent 编排', '并行调用、超时、重试、权限、成本和版本管理', '结构化 JSON 输出，失败可降级'],
    ['证据与知识库', '保存原文、片段、摘要和引用关系', '来源优先级、发布时间、原文链接'],
    ['决策与风控', '评分、否决、仓位、组合限制和信号生命周期', '硬规则优先于语言模型'],
    ['执行适配', '模拟盘、人工确认、券商接口、成交回写', '幂等、撤单、异常停止、权限分层'],
    ['观测与审计', '延迟、错误、漂移、命中率、回撤、成本和告警', '按版本和决策 ID 可回放'],
])
doc.add_heading('九 关键数据合同与输出格式', level=1)
add_para(doc, 'Agent 输出必须结构化，禁止把关键字段藏在长文本中。以下字段是首期最低合同，所有时间均使用交易所时区并明确是事件发生时间、数据可见时间还是系统处理时间。')
add_table(doc, ['字段', '说明'], [
    ['decision_id', '一次研究/裁决/交易计划的唯一 ID，贯穿到成交与复盘'],
    ['symbol 与 market', '证券代码、市场、板块、可交易状态'],
    ['as_of_time 与 visible_at', '分析截止时点与数据对系统可见时点'],
    ['action_state', '观察、等待触发、可建仓、持有、减仓、禁止交易'],
    ['score_breakdown', '分层得分、权重、扣分与缺失项'],
    ['evidence', '来源、发布时间、引用片段、数据快照 ID'],
    ['thesis 与 counter_thesis', '正向假设、最强反方和验证方式'],
    ['entry、stop、target、expiry', '入场、止损、目标、计划失效时间'],
    ['position_plan', '目标仓位、最大风险、流动性约束、组合影响'],
    ['approval_status', '模型生成、待人工确认、已确认、拒绝、已执行'],
])

doc.add_heading('十 监控指标与上线验收', level=1)
add_table(doc, ['维度', '核心指标', '告警例子'], [
    ['数据', '新鲜度、缺失率、源冲突、修订率、解析成功率', '关键字段过期或两源价格不一致'],
    ['Agent', '成功率、延迟、成本、引用覆盖率、结构化解析率', '超时升高、无引用结论、输出格式漂移'],
    ['研究', '候选覆盖、分歧度、信号生命周期、人工采纳率', '高分候选频繁被否决'],
    ['交易', '成交率、滑点、换手、容量、撤单率、订单异常', '实际滑点超过回测假设'],
    ['风险', '组合回撤、风险贡献、行业集中、止损执行、异常敞口', '任一硬限额被突破'],
    ['效果', '样本外收益、超额、回撤、胜率、盈亏比、稳定性', '仅在单一时期有效或成本后失效'],
])
add_para(doc, '上线验收必须以“能否安全停止”为前置条件：任何关键数据源异常、风控服务不可用、订单状态不一致或审计日志缺失，系统应停止生成可执行订单，但可以继续提供只读研究。')

doc.add_heading('十一 分阶段实施路线', level=1)
add_table(doc, ['阶段', '范围', '完成标准'], [
    ['阶段 0 规则与数据基线', '确定股票池、交易规则、风险参数、数据字典和基线策略', '能重放一个历史交易日，所有输入有版本'],
    ['阶段 1 研究工作台', '市场驾驶舱、个股分析、证据链、基本面/量价/事件 Agent', '人工可用，结论可引用、可追问、可审计'],
    ['阶段 2 选股与组合', '候选池、评分、反方 Agent、组合暴露、交易计划', '每日生成稳定候选，能解释入选和拒绝'],
    ['阶段 3 回测与模拟盘', '时间切分回测、成本模型、模拟执行、盘后复盘', '通过样本外、压力测试和连续模拟盘'],
    ['阶段 4 小资金实盘', '人工确认、有限标的、有限额度、实时告警', '实盘偏差可解释，具备一键停止和完整审计'],
    ['阶段 5 受控自动化', '仅对经过验证的策略开放有限自动执行', '策略、风险和权限独立审批，持续监测漂移'],
])
add_para(doc, '首个可用版本建议只做三件事：每日生成经过硬过滤的候选池；对单只股票提供证据化分析和交易计划；对组合给出风险拦截与人工确认清单。不要在第一版同时接入所有市场、所有 Agent 和自动下单。')

doc.add_heading('十二 首期待确认决策', level=1)
for item in [
    '市场范围：先做沪深 A 股，还是首期只做沪深主板；创业板、科创板、北交所的规则何时纳入。',
    '交易周期：日线选股 + 盘中执行是否作为默认，是否需要分钟级策略。',
    '数据预算：哪些数据源是必须付费购买，哪些只能作为辅助信息，公告和舆情的合规边界是什么。',
    '账户约束：初始净值、最大总仓位、单票/行业/主题上限、可接受回撤和每日损失限额。',
    '上线权限：首期是否只读研究，何时允许生成订单候选，人工确认由谁承担。',
    '评价标准：优先优化超额收益、回撤、研究时间、成交质量还是组合稳定性。',
]: add_bullet(doc, item)

doc.add_heading('结论', level=1)
add_para(doc, '这个产品的可信度来自可回放的数据和明确的风险边界，而不是 Agent 数量或回答长度。建议先把“数据时点—证据链—交易计划—风控拦截—复盘归因”五个环节做成闭环，再逐步增加 Agent 和策略复杂度。只有当系统能在真实约束下稳定地说明为什么买、买多少、何时不买、错了怎么办，页面才真正具备指导交易的价值。')
add_para(doc, '使用声明：本文档是产品与交易系统设计方案，不构成对任何证券的投资建议或收益承诺。真实交易前应由具备相应资质和权限的人员完成合规、风险与适当性审查。')

doc.save(OUT)
print(OUT)
