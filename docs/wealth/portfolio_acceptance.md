# 整体配置独立验收与交付状态

## 第11步当前流程复核（2026-10-10）

当前A交互和来源说明以[portfolio_frontend.md](portfolio_frontend.md)、唯一[portfolio_handoff.md](portfolio_handoff.md)第9节及仓库外本轮回传为准。M01—M14明确隔离来源，比赛入口单独验证认证/资产/准确关闭，不以测试总数替代真实推荐、Plan、金融评价或J联合验收。下文旧阶段矩阵与计数保留其原记录；09历史输入/程序指纹本轮仅复核，未重新计算历史指标。

[比赛银行事实接线与新增来源检查](portfolio_bank_facts.md)：实际正常登录、认证后四组现有字段、生产provider及独立路由关闭已验证；全量可信配置因精确字段缺口仍阻塞，受控测试与实际来源证据分列。

## 结论与边界

2026-10-09，01内部算法、隔离生命周期HTTP和组件交互已验证；正式事实/真实推荐及历史金融评价仍blocked。三个层级分别记录，不以通过测试数替代正式来源或金融效果。中央应用、B页面/交易、公共ActionV1及J01—J23没有联合验收。

保持政策/算法版本：portfolio-policy-r1-r5-v1、portfolio-geometric-projection-v1、portfolio-capped-simplex-v1、portfolio-provider-dpp-geometric-v1；资料特征portfolio-bigram-tfidf-80-10-10-v1、DPP portfolio-greedy-cholesky-v1。η/η_product=.25、Δ=.01、DPP .80/.10/.10和q=1、MMR λ=.75不变。

## 可复现入口

```powershell
$env:PYTHONPATH="$((Get-Location).Path);$((Resolve-Path .deps).Path)"
python -B -m scripts.wealth_portfolio_acceptance --fixture
python -B -m pytest tests/unit/test_wealth_portfolio_acceptance.py -q -p no:cacheprovider
```

输出仅到忽略目录`.deps/wealth_portfolio_acceptance/`：acceptance.json、evaluation_manifest.json、测试/构建日志、browser_results.json及截图。入口禁止外部socket连接，不恢复采集、不读写生产银行库、不调用LLM；实际本地HTTP属于浏览器独立检查，不能称零网络连接。未显式fixture拒绝启动。

独立后端/页面启动与准确handler、HTTP DTO、组件props及错误参考[生命周期](portfolio_lifecycle.md)。评估入口不注册公共接口、不提供生产调参功能。

## 验收矩阵

| 条件 | 状态 | 实際函数/证据 |
|---|---|---|
| F01/F08/F10/F18全授权总资产、冻结、应收、缺估值 | passed | build_snapshot/read_asset_model_facts；snapshot/data/model测试；F18真实HTTP与截图 |
| F02/R1全活钱、T=0、20/30/50→62.5% | passed | allocate/provider.plan/lifecycle；model及本次代表解释；browser R1/R4 |
| 先匹配后资格；普通/类别/可信主题及部分/全超风险/无匹配/停售/未知状态 | passed | pipeline/qualify_purchase；candidates/model旧检查本次重跑；未知外部unavailable有reason_codes |
| 家族、具体份额、A/C例外、每批≤3、不凑数、>5选择/换批 | passed | DppSelector/CandidateService/PortfolioLifecycle；候选、model及重复6家族带入 |
| 原P/P_request/U、固定现金切片、合法手动值、明确偏好、整分上限 | passed | allocate/validate_adjustment/validate_weights；model/lifecycle及三次小调整后越界拒绝 |
| store副本/CAS/空闲到期/重启及本人/context/epoch/scope/session/写锁 | passed | MemoryPortfolioStore/PortfolioAccess；受控CAS/lifecycle/API测试 |
| 评级降低、许可撤回、事实变化/到期与失败不变 | passed | _fresh/qualify_purchase；本次评级降低补查及旧事实变动测试 |
| UI成功回写/失败草稿/只读/在途/旧context迟到、R1/F18/缺source | passed | PortfolioAllocation；14项真实Edge+Playwright本地HTTP |
| 建议/选择/旧仓/现金分列，缺费用/行情不补0 | passed | _decorate/model_view；解释核对、browser及截图 |
| 无预算/期限必填、无模型对话/授权/银行写入 | passed | DTO/default dependency/隔离store；离线连接禁用及side_effects |
| 旧portfolio/provider兼容 | passed | 适用完整回归＋旧rule provider；semantic_graph仅隔离SQLite |
| 全授权正式AssetSummary/正式评估/当前银行规则/context resolver | blocked | 默认503；协议/只读适配已提供，真实依赖未注册 |
| 真实可靠风险/家族/主题证据成立后的推荐 | blocked | 本地资料版本未变；真实匹配19265、模拟44，合格家族均0；空集合指标NA |
| 历史单产品总回报描述 | blocked | 214条现存历史/事件任务原响应审计，尚无经核实总回报定义/完整事件调整口径 |
| 固定初始权重买入持有组合 | blocked | 成分总回报/现金收益未证实，不默认现金0；没有交易收益声明 |
| 真正样本外推荐/配置评价 | blocked | 缺历史决策时点评级/家族/许可/文本/持仓；今天资料不能回填过去 |
| PostgreSQL银行账务双库 | not_run | 本轮纯内存/评价；旧semantic_graph PostgreSQL参数未选择，未验证 |
| 中央接线/B交易/J01—J23 | not_applicable | 不属于独立交付，无联合闭环声明 |

F编号契约静态检查与生产函数执行证据分列；例如F02契约对应R1实际provider/lifecycle/浏览器行为，不能把fixture生成等同于实际执行。

## 结果解释核对

acceptance.json保存R1、R2、R4明确p=.625、可信仅持有旧仓、可购集合缩小及缺资产source的实际结果、来源/时点/版本、政策、b/h、激活约束、x*/U、整分目标及家族投影和短解释。使用确定性计算事实，不调用聊天/LLM。

区间是有效调整范围，不是置信/收益区间；DPP是资料特征差异，不是收益相关性或资产穿透。HHI仅集中度，调整量仅代理，不称金融风险或手续费。R1比例不适用；应收/冻结目标不意味着立即申购。缺source拒绝构造可用计划，不暗换分母。

重复3次固定时钟、fixture/source和独立store流程；完整Plan仅移除随机portfolio_id参与哈希，不省略业务时点、评估、事实版本、金额。各创建/6家族带入/读取/连续3次原U调整/新偏好/风险拒绝、无plan换批及旧仓方向结束状态一致。不是语言Agent或交易结算成绩。

## 压力与离线敏感性

复用停售/许可撤回、评估过期、重复特征/秩不足、家族变少、缺身份/估值、仅持有上限、1分越界、R2/R4零/小权重/A-C、旧版本CAS及迟到UI检查；补评级降低及原U连续小调整后越界场景。关闭状态保留旧记录但不可继续有效修订，不降低风险或替换份额。

新纯评价模块`backend/app/domain/wealth_portfolio_acceptance.py`的allocation_comparison/product_comparison标comparison_only：正式常量无参数入口，用同一生产Problem、几何project、simplex和整分校验＋独立解析区间作参照。η=0/.25/.50、η_product=0/.25/.50、Δ=.005/.01/.02一次只改变一个。记录x*、当前距离、原目标损失、区间/滑块宽度、产品HHI/调整代理及上限；不进入服务/UI用户参数，不重选政策。

Δ不改变x*，只改变U宽度；未知h不填0，η有效0。产品比较同时保留仅持有上限和可购对照，上限绑定时变化可能被约束吸收；不代表金融收益好坏。默认参照与实际allocate/project_products的解和区间通过独立检查。

## 历史评价口径与manifest

只读取`.deps/a1_collection/progress.sqlite`成功历史/事件记录及其原始压缩响应；214逻辑任务/214唯一响应，包括30单位净值、29累计净值、28累计收益率、70排名、5份分红送配、2货币收益、28年度分红和22年度拆分。每条记录来源函数/参数、观测时点、日期范围、列名、行数、hash和排除原因；不输出完整原数据。

本地AKShare累计收益率分支只是LJSYLZS列解析，未记载分红再投/拆分调整方法；不能仅凭列名认证总回报。年度事件表及少量份额详情未形成已核实的完整份额事件链；缺事件不能当无分红。货币收益/排名/累计NAV都不代替总回报。

另登记固定模拟历史manifest及78个模拟条目，来源为simulation，未来预生成行不消费，不计作真实金融样本。现有数据/目录范围不缩减。

manifest先于任何真实金融指标固定，当前合格样本0，共同窗口和真实指标NA。没有看收益后挑样本、填缺值、虚构现金收益或未来资料回填。解除需可信总回报定义或完整事件调整证据；年化还需交易日完整性；样本外还需决策时点可得资料。

指标纯函数total_return_metrics对正值总回报G计算窗口回报、ddof=1样本波动和最大回撤；至少2个有效回报才有波动，未确认连续交易日不年化。buy_and_hold_series使用初始权重×成分归一指数，不每日恒定权重加权。没有现金序列而现金权重>0拒绝；显式假设现金指数须在manifest另列，不进正式事实。解析例100/110/99/118.8→回报18.8%、样本方差7/300、最大回撤10%，独立检查符号与峰值。解析例通过仅证明指标实现，不证明金融评价通过。

## 实际修复与兼容增量

复现：资料未知导致内部no_eligible_products，外部正确转换unavailable但丢失原因。修复：CandidateGroup增加默认空reason_codes；adapt_candidate_group仅非ok合并实际原因，ok不携带排除警告；TS可选字段兼容旧输出。新增实际source/生产adapter检查防回归。没有放宽购买资格、算法或参数。

浏览器脚本增加可选PORTFOLIO_EVIDENCE_DIR，仅更换本轮证据输出目录，原默认/交互不变。其他新增为评估/验收纯函数、入口与实质空白测试；公共代码、银行ORM和.env不变。

## 构建与环境限制

已有Python3.11 Docker和主机3.12、Node24.19、React18/TS5/Vite5.4、已有Playwright及Edge。正常npm命令本机不可用，执行同等tsc＋公共Vite build＋独立Vite build；不称npm run build通过，不装npm/pnpm、不改lock。现有chunk与httpx弃用提示不作无关重构。

本轮最终数目/命令/日志见仓库外阶段回传及本地忽略证据；没有复写前阶段通过数代替实测。仅停止本轮核对命令行的8051/3021进程，原3010/8010保留。

## 第09步实际历史口径核验（当前增量）

前文为第06步验收历史记录；本节及 [portfolio_evaluation.md](portfolio_evaluation.md) 是当前历史结果。原指标要求不改写：单产品含分红总回报、固定组合总回报、真正样本外仍blocked，具体缺口分别记录；补充NAV描述10份额成功不能解除前三者。全部214原响应/128 symbol代码重新只读核对，旧指纹保留；已实现编码、日期、单位、份额绑定、累计收益转换保护、显式gross现金/费用情景与时点拒绝，并实际双环境重复离线评价。

本轮受影响集合为acceptance/history/data/evidence/candidates/model六文件，宿主和既有容器各230 passed/0 failed/0 skipped，交集230；不是过去318项或金融效果成绩。没有重复浏览器、银行实际账户或02交易验收。新入口 scripts.wealth_portfolio_history freeze/evaluate 与旧 --fixture 入口分开；真实输入没有fixture回退。
