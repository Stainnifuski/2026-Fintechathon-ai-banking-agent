# 整体配置：受保护比赛银行事实接线

> 2026-10-12联合状态：本文件保留01原实现/独立验证说明。当前正式主应用已注册A提供器与规范路由并消费B全授权事实；正式接线、已关闭旧缺口和剩余产品/研究阻塞以 [portfolio_handoff.md](portfolio_handoff.md) 为准。独立入口不替代正式联合验收。

## 第07步契约对齐历史记录（最新状态见文末）

四项A侧修复已实施，公共DTO及路由外形不变：

1. `_access`允许可信`context_epoch=null`，另绑定真实`selection_version`；不填1、不改名。正整数epoch保留。本人/context/scope/账户、epoch/切换版本变化仍使旧Plan或cursor失效，切回原context也不复活旧状态。
2. 独立读取与推荐无需旧task/session。复用已有服务端候选会话及1800秒MemoryPortfolioStore；关联原task的关闭、版本和写锁仍校验。
3. `resolve(write=False)`不要求`can_edit`或遍历历史写锁；已知关闭及数据隔离仍拒绝。创建、修订是写操作，必须有可信编辑依据；未知停止/关闭不能当false。HUMAN、PAUSED、关闭、停止提交继续禁止写入。访问绑定与权限判断分离，读取时缺编辑字段不使合法Plan失效。
4. 当前模拟银行来源可映射内部`local-competition-bank`渠道。`CurrentSimulationEvidence`是A内部事实，不是公共新字段：同产品/披露版本、当前采集时点、模拟`business_at`、销售/暂停/open/stale、募集截止、规则日历、净值日期及可见时点逐项核对。无原生永久有效区间不单独拒绝；实际存在的区间仍验证，未来数据/过期/错源/错版本拒绝。真实基金评级、渠道、时效及未知风险要求不变；持有/赎回许可不默认允许。

快照双读排除任务`nodes`运行日志，保留任务版本、身份、关联和控制字段；实际首测只因四个旧DAG节点attempts变化409，有限诊断证实后修复，不放宽资产/规则版本变化检测。

当前适用验证：Windows Python3.12.14与现有后端容器Python3.11.17各运行同一来源/资格/生命周期/API/新增对齐集合，结果见[交接](portfolio_handoff.md)。集合完全重叠，不相加。受控fixture只证明边界；真实接口另外记录。

实际独立8052路由：GET snapshot **200**，as_of=`2026-10-09T20:50:20.637377Z`，T=2010000分、可支付310000分、冻结1700000分，1账户、0持仓。POST recommendations **200 / unavailable**，仍含`ASSESSMENT_CURRENT_VERSION_UNPROVEN`和产品身份/版本/销售等真实原因，非成功建议。POST plans **503 / PORTFOLIO_WRITE_CONTROL_UNPROVEN**，当前相关任务未公开session_closed/stop_new_submissions。未创建Plan，不用get/revise404冒充通过。本次启动的8052进程已停止，既有3010/8010保留。

可信Plan仍受当前画像核验、真实写控制、银行v4/研究v1及policy_mismatch等必要事实阻塞；普通/归档范围、历史lot版本、真实风险/家族/主题、历史评价及B中央接线仍待完成。以下旧检查数字/响应只作历史记录，不能替代当前结论。


## 当前状态

2026-10-09补充核对以 [内部交接初版](portfolio_handoff.md) 为当前缺口归属依据。A已修原产品字段映射，以及可独立核验的同run全量资产适配；原第07步的“所有字段缺失都必须由B新增”判断不再适用。当前run真实HTTP→source→builder快照已成功，完整方案正向仍关闭。

`_adapt_run_assets`只支持原银行完整本人contexts中明确disposable、ACTIVE、同run_id且peer账户与cash集合完全一致的范围；普通/未知范围、归档peer、漏账户、跨范围明细仍拒绝。按cash/lots/orders重算00 AssetSummary，缺NAV/应收保留null，不采用bank_assets_fen的零填充总额。context_epoch保持null，不把selection.version冒称epoch；快照和scope标adapter/bank-run摘要，仅作事实标识，不作授权。双读一致是变化检测，未声称银行原子事务快照。旧持仓无法匹配历史product_version仍由builder拒绝，不拆version_id猜版本。

`native_product_evidence`沿用原银行product_version的规则/披露字段集及支持日历，映射status、purchase_paused、purchase_open、data_stale，并将当前version/risk_level标为模拟银行评级证据。未知销售状态仍null，停售为false；不填销售有效期、不改变研究版本、正式评估或风险门禁。该销售许可不是下单结果，募集期、价格、风险、金额、本人确认/验证仍由原服务重验。

补充实际证据：本轮一次正常演示登录200，12次GET（两次完整观察）后，当前同run快照T=2010000分，可用310000分、冻结1700000分、应收0，0持仓、12订单，as_of=2026-10-09T20:24:26.179234Z。无fixture，无创建订单/计划；未重新启动8052实测router，不能把source成功写为HTTP snapshot200。旧产品HTTP记录回放B001/B002销售/完整性/模拟评级证据均true，但银行v4、研究v1，评估当前画像仍未证明。读取旧缓存和回放均不代表实时重新拉取产品。

补充最终验证：本机来源/候选/生命周期/API **189 passed（8.18s）**；现有后端容器来源 **41 passed（4.95s）**。旧341/118/1项属于第07步历史证据，未在补充阶段重复。未运行本轮浏览器或PostgreSQL、真实完整方案/交易。唯一警告为本机现有Starlette/httpx弃用提示。

```text
python -m pytest tests/unit/test_wealth_portfolio_bank_facts.py tests/integration/test_wealth_portfolio_bank_source.py tests/unit/test_wealth_portfolio_candidates.py tests/unit/test_wealth_portfolio_lifecycle.py tests/integration/test_wealth_portfolio_lifecycle_api.py -q -p no:cacheprovider
```

以下保留原第07步检查说明和历史结果；缺口分类及解除条件以交接初版为准，不将旧全量字段清单继续派给B。

独立入口已绑定只读HTTP adapter、生产CandidateService/PortfolioModelProvider/PortfolioLifecycle及独立MemoryPortfolioStore。中央应用未改；fixture入口保持隔离。已用README公开虚构演示账户通过原银行正常登录，实际读取资产、正式评估、78个银行产品和context/tasks。当前响应不含全授权AssetSummary及绑定版本，context/tasks不含完整会话与写锁证明；对应操作关闭。没有合法Bearer时HTTP401，不提供开发身份或令牌签发入口。现有字段读取通过，完整可信事实仍部分阻塞，不声明真实推荐通过。

## 来源与字段

模块 `backend/app/integrations/wealth_portfolio_bank_facts.py`：

| 受保护GET | 原字段及适配 | 缺口/处理 |
|---|---|---|
| `/api/v1/auth/me` | user_id核对每次读取的本人 | 不匹配403；原银行校验JWT及用户存在性 |
| `/api/v1/wealth/assets` | cash: account_id/type/balance_fen/held_fen/available_fen；lots: lot_id/account_id/version_id/units/redeemable_units/market_value_fen/nav/nav_date；orders及receivable_fen | 同run集合可证明时A组装summary；否则503。bank_assets_fen对缺估值用0，不采用此总额。旧lot历史版本仍可能不可得 |
| `/api/v1/wealth/assessments/current` | assessment_id、capacity_level、confirmed、purchase_allowed、expires_at；本人由认证请求保证 | 完整响应SHA256标`adapter:`为派生版本；profile_version不是当前画像版本验证。未提供current_profile_verified则assessment_current_proven=false，购买资格关闭 |
| `/api/v1/wealth/products` | product_id/version/product_risk_level；rules.min/max_amount_fen；disclosure.version；status/open/stale/暂停 | 原字段映射销售、完整性及当前模拟评级，证据摘要A派生；valid_from/until及未知具体份额/持有许可仍null；官方真实评级不由此生成 |
| `/api/v1/wealth/contexts` | selected与请求context核对，items描述当前环境 | selection.version不冒称可信context epoch；缺全授权scope/account_ids/session绑定 |
| `/api/v1/wealth/tasks` | 当前context全部任务status/control_mode/plan_id | 与既有控制规则一致拒绝PAUSED及非AGENT写入；缺stop_new_submissions/session_closed/完整plan_write_control仍503，只有完整可信当前任务查询确无适用锁才可据其否定事实放行A写入；未知列表不作证明 |

金额为整数分，份额精确四位小数字符串。研究索引版本与银行规则派生版本分开。来源模式simulated仅说明比赛模拟银行性质，不把真实基金目录变成合格模拟产品。银行原有family默认product_id不作为可靠家族证据。

`BankPortfolioFacts.read(user_id)`消费同协议summary包，或经上述严格范围核对适配原同run完整响应；不把cash第一页或所选卡响应改名。完整读取两轮（每轮本人、资产、评估、产品、contexts、tasks），业务内容变化拒绝BANK_FACTS_CHANGED409，不无限重试。read_at/as_of不参与内容摘要；business_at转北京时间业务日，金额、范围、产品和评估版本仍参与。稳定双读是变化检测；完整范围由服务端全查询语义和context/账户集合另行证明。

全量包存在或能够可信适配时复用build_snapshot核对：T=借记卡账面现金+所有持仓市值+赎回应收，冻结不另加减，应收不可支付，快赎持仓不重复。当前同run已通过，通用银行汇总及中央两页接线仍待B；未知估值不补零。

`read_current(user_id, at) -> CurrentFacts`独立读取评估/具体产品；源异常与单产品未知分开。`BankPortfolioAccess.resolve(user_id, context_id, *, write, portfolio_id=None)`核对本人和当前context、可见任务控制；独立只读不强制旧session/编辑许可；null epoch须真实切换版本，缺必要scope或写控制仍拒绝。本实现不直接读取银行ORM，不修改原控制实现。

## 独立运行

使用现有Python依赖，在仓库根设置PYTHONPATH包含根和`.deps`，再运行：

```powershell
$env:PORTFOLIO_BANK_BASE_URL='http://127.0.0.1:8010'
$env:PORTFOLIO_DATA_VERSION='e2d8031f0970c7e68d41fb1874b9af1f4d9ab9a9312b89d86439b8842247b469'
$env:PORTFOLIO_DAILY_VERSION='f864ea516c9adee6a450f5b4257c48f18dee38c81021618fdd337896d992295b'
python -m scripts.wealth_portfolio_bank_dev
```

仅监听127.0.0.1:8052，端口占用则退出，不终止其他服务。使用原银行正常登录所得现有Bearer访问`http://127.0.0.1:8052/api/v1/wealth/portfolio/snapshot`；凭据只放请求头，不写文档/日志。原银行保持运行；入口不读.env、不seed、不迁移、不结算。只读GET白名单，拒绝外部host和重定向。正常停止Ctrl+C；后台运行仅在核对自己保存的PID/命令行后Stop-Process。

原`scripts.wealth_portfolio_dev`是显式fixture8051入口；本入口没有bootstrap或fixture回退。store单进程1800秒空闲清理，重启/到期404，中央默认依赖503保持。缺证据时不能恢复过期方案或扩大付款权限。

## 实际检查与限制

本地8052真实HTTP：snapshot/recommend/create/get/revise无登录401；无效令牌转发原银行/auth/me后401 BANK_READ_HTTP_401。之后正常登录HTTP200，认证后snapshot503 ASSET_SUMMARY_UNAVAILABLE，recommend/create503 PORTFOLIO_CONTEXT_UNPROVEN，未创建方案get/revise404 PORTFOLIO_NOT_FOUND。没有用fixture替代这些关闭结果。

实际资金环境只有一张savings卡：账面2010000分、冻结1700000分、可用310000分，无lots，应收0；scope=disposable_simulation。不能把2010000分称为本人全授权T。正式评估capacity_level=5、confirmed=true、purchase_allowed=true、profile_version=1，2027-10-01到期；缺当前画像存在/版本及评估适用范围证明。contexts选择版本6，不冒称context epoch。tasks14条，列表仍缺session_closed/stop_new_submissions。产品78条，B001版本4、风险R1、起购1分、上限200000000分，规则来源official_reference_simulation_v1，缺份额渠道及有效区间。独立生产provider实际返回unavailable，并保留ASSESSMENT_CURRENT_VERSION_UNPROVEN及具体份额/版本/规则缺口原因。

实际双读首次发生BANK_FACTS_CHANGED409，额外有限一对内容核验摘要相等（changed_paths=[]）；不猜测首次变化原因，不重试到成功。对应资产仍因全量契约缺失关闭。本地探针直接观察到53次请求，其中adapter计数39次；独立服务器的额外转发及原银行内部HTTP未独立计数，不把53当全链路总数。LLM、外部数据和银行交易写入请求在本步调用路径均为0；原数据库全量差分未测量，不宣称无人修改原库。

新增受控来源检查覆盖身份、缺完整资产、错误源结构、产品未知、评估映射、控制拒绝、双读变化及稳定摘要；这些不代表当前银行事实。完整旧适用回归与本机复验另列运行证据。未改前端不重跑浏览器构建；PostgreSQL未验证。

```text
python -m pytest tests/unit/test_wealth_portfolio_bank_facts.py tests/integration/test_wealth_portfolio_bank_source.py -q -p no:cacheprovider
```

解除条件按交接初版拆分：B共享全量summary/完整明细（当前run适配已完成）、必要历史持仓版本、当前画像匹配证明、相关操作的真实写控制；A研究与当前产品版本/政策绑定；A额外epoch/session/模拟有效期条件本轮已完成修复，不能要求B全部补同名字段。合法登录、当前run快照已实际通过。研究目录可靠风险/家族/主题证据及历史评价分别为后续工作，本接线不补齐它们。

## 当前绑定与最终实测

当前受保护产品事实可派生成A资料层，旧研究版本不改；`CurrentSimulationEvidence.product_content_sha256`来自实际产品身份/类别/风险/规则/披露内容，不含变化中的观察时刻或NAV。资格函数和当前资料内容摘要核对，未知或相异拒绝BANK_PRODUCT_CONTENT_MISMATCH；净值/销售时效继续单独按模拟business_at、北京时间日历验证。不是新增B授权字段，不允许客户端填事实。

最终完整资料版本与运行环境参数、三个独立真实推荐结果、源GET计数及318项双环境验证见唯一portfolio_handoff.md §6。当前同run快照200；普通/债券unavailable，高股息no_theme_match，全部0候选。评估当前画像匹配及真实写控制仍缺，未创建Plan。新产品身份/版本绑定已解决，不再要求B将v4降成研究v1；投资正文缺失、B017/B018保本、真实份额的在售/评级等缺口保持。
