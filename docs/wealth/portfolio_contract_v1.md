> 第2轮2026-10-14当前修订：见portfolio_handoff.md和wealth_joint_acceptance.md。接受约10—11秒推荐/约20秒Plan；可执行滑块与同context银行快赎已实现；不展示保本字段、不将其作为活钱准入。当前完整操作员UI往返待补，不宣称封板。下列冲突旧描述仅为历史。

> 第1轮最新决定（2026-10-13）：全池离线核心交集13,856，含278货币项目固定1元份额；真实货币NAV仍未知。生产通用选品退出正文DPP／MMR，采用类别轮转／代码升序；原U、ETA/DELTA和权重投影保持。A/C与主题生产模式退出；未知／越级仍拒绝。性能未通过，滑块人工失败，待第2轮；下文冲突旧说明仅为历史。当前权威说明见portfolio_handoff.md。

> 当前权威修订：2026-10-13真实基金资料＋项目分级＋比赛模拟交易。下列A0/A1时期的未注册/默认A/家族前置文字只作历史，冲突以本文件末节及portfolio_handoff.md为准。

# 整体理财共用契约：A0落地（00 v0.3）

> 2026-10-12联合状态：本文件保留01原实现/独立验证说明。当前正式主应用已注册A提供器与规范路由并消费B全授权事实；正式接线、已关闭旧缺口和剩余产品/研究阻塞以 [portfolio_handoff.md](portfolio_handoff.md) 为准。独立入口不替代正式联合验收。

本文件记录00 v0.3的A0契约源；当前资格、正式算法、生命周期及组件已经实现，状态与共同差异以portfolio_handoff.md为准。A0历史描述不覆盖当前交付，B中央交易及联合验收仍待接线。

## 唯一结构定义

代码源：[wealth_portfolio.py](../../backend/app/schemas/wealth_portfolio.py)。独立导出：

```bash
python -m backend.app.services.orchestration.wealth_portfolio_cli --schema
```

| DTO | 主要字段与用途 |
| --- | --- |
| AssetSummary | wealth-asset-summary-v1；scope/account_ids/snapshot_id/as_of/business_at；现金、冻结、可支付现金、持仓市值、赎回应收、总资产、完整性、缺失原因、来源。B提供事实 |
| AssetSnapshot | wealth-asset-snapshot-v1；沿用同一scope/snapshot/total；context可为null；basis_kind=linked_bank_assets，basis_status=confirmed/pending_decision/fixture_only；账户、聚合持仓、应收、订单、正式评估、data_versions/missing_fields |
| PortfolioInput | wealth-portfolio-input-v1；snapshot、products、features、user_preferences。不含本次投入金额，不要求期限或收入负债；未知特征省略或null，不补零 |
| ProductCandidate / ShareClass | 家族ID、名称、板块、理由/证据；明确产品版本、披露版本、A/C份额、真实评级、可售性、起点、费用及带日期/来源/状态的指标 |
| CandidateGroup | 板块/结构化filter/status/items/next_cursor/risk_explanation；同组不能重复家族 |
| PortfolioPlan | wealth-portfolio-plan-v1；provider及模型状态，三板块min/target/max比例和金额、剔除活钱后增长比例、候选、选中目标、现金残余、版本与缺失。正式provider与生命周期已实现，真实事实不足仍拒绝 |
| PortfolioSelection | portfolio_id/expected_version/snapshot_id/context_id、具体产品和版本/份额/总资产目标比例；不保留旧5笔上限 |

金额为非负整数分（拒绝bool/元浮点），份额为四位小数字符串，比例是0—1十进制字符串（`0.625`而非`62.5`）。业绩`value_percent`使用百分数口径，不能混用配置比例；未知业绩为null及原因，不能写0。风险事实1—5，未知为null；不伪造评级。瞬时带时区、输出UTC，净值日期比较采用北京时间。

三板块为cash_management/stable/growth；三板块目标比例合计1、金额合计basis_fen，区间必须可行，单项金额允许1分舍入差。增长滑条分母=总资产−活钱目标金额，分母0时为null。

## 资产口径及来源约束

总资产=全部授权关联借记账户**账面现金＋关联持仓市值＋赎回应收**。冻结是账面现金的子集，不加减两遍。可支付现金=账面现金−冻结；持仓/应收不能作为当前支付现金。信用额度不纳入借记现金。

AssetSummary和明细必须来自同次完整读取；A不直接读取银行ORM、不汇总持仓第一页，也不把旧单context资产当全量。按账户＋product_id＋product_version聚合lot，保留全部lot_ids。version_id是不透明标识，缺少显式product_version时只能用精确目录映射，不能拆字符串猜版本。缺估值时市值/总资产/basis为null并附原因；正式风险评估缺失单独标记，不影响已知现金展示。

旧字段适配见[wealth_portfolio_fields.py](../../backend/app/integrations/wealth_portfolio_fields.py)：产品version→product_version；披露disclosure.version或原生disclosure_version→披露版本；risk_level/product_risk_level保留未知；redeem_fee_bands→redemption_fee_rules。年度综合服务费不能无证据当作C类销售服务费，缺失保留null。不按名称尾缀猜家族或份额；未知status标记unknown，缺已验证trade_eligible时保守不可选，正式资格已由wealth_portfolio_eligibility统一校验。

## B接线边界

独立只读路由：`GET /api/v1/wealth/portfolio/snapshot`，返回AssetSnapshot；用户由现有JWT依赖取得，source仅服务端注入。source接口为`AssetSnapshotSource.read(user_id)`，返回授权全量汇总和匹配明细。B后续注册路由并提供正式source；本轮不修改main.py、共享wealth路由或交易模块。

未注入source返回503 `ASSET_SUMMARY_UNAVAILABLE`，不自动使用测试数据；越权403，明细/汇总冲突409 `SNAPSHOT_INCONSISTENT`。FixtureSnapshotSource必须明确注入，限定fixture用户且强制fixture_only。实际服务目前没有接此路由；独立ASGI测试已覆盖。

## 共用fixtures与阶段责任

目录：[F01—F24](../../tests/fixtures/wealth_portfolio_contract_v1/)，生成器：[generate_wealth_portfolio_fixtures.py](../../scripts/generate_wealth_portfolio_fixtures.py)。每条含fixture_id、contract_version、owner、input、expected、data_status=fixture_only。生成可重现。

A0验证全部24个文件格式/可重现性及已有DTO输入输出；A1实际构建F01/F08/F10/F18。F03—F05筛选、F09默认份额/资格回退等行为留A2；F11仅验证7项选择DTO，不宣称批量成交；F13账务、F15完整手工交易及F19—F24收益/流水由B验收。固定20/30/50是协议样例，不能当正式参数。全部fixture文件存在不等于全部后续业务已完成。

阶段交付、实际数据来源和限制见[A0/A1交付](portfolio_a0_a1_20261005.md)。
## 2026-10-13：已批准的真实资料／比赛模拟交易契约修订

本节覆盖00 v0.3中冲突要求，工程协议字段保持兼容，不更改既定DPP、ETA、DELTA、风险配置区间或投影参数。当前有效交接与联合结果见 portfolio_handoff.md / wealth_joint_acceptance.md；旧段落仅作历史。

- 分类核实后项目等级：货币R1、债券R2、混合R3、股票R4。项目等级独立于发行人/渠道公开评级；公开更高可靠评级或分类冲突必须拒绝复核，不降低等级。政策按版本与具体代码/材料/报价指纹绑定，不新增30天期限，不新增单笔2万元上限。额度沿用银行产品、账户及现有支付能力校验。
- 删除：A/C切换操作、默认A、仅C例外、强制家族登记前置。公开的A/C关系仍用于已知同基金去重；未知关系使用exact-code操作身份，不宣称独立基金家族或风险来源。share_class兼容字段可为null，具体代码及版本才是购买引用；不从名称后缀编造份额事实。
- 调整J03/J05/J08：按具体代码推荐/换批/多选/申购；保留最终R2购买R4拒绝、手动选择、0/极小权重绕行检查；不再把A/C切换作为完成条件。
- 同时保留：正式本人风险问卷、最新画像/评估版本、本人确认、统一红色MFA、账户/作用域/会话/控制权限、预览期限、参数绑定、幂等、唯一账务与审计。清聊天/修改草稿不撤回已提交订单。
- ProductFact.source_kind保持real。execution_policy标underlying_source_kind=real、execution_source_kind=simulated；BankPurchaseFact带同版本policy，CurrentSimulationEvidence带pricing_mode=frozen-public-nav-v1。公共原值/日期/SHA与模拟规则分开记录。
- 项目模拟条件：最低100元，最高沿用现有普通基金银行规则；申购/赎回0费用，无额外销售服务费。现实完整费用未知不补0。北京15:00截止、现有中国交易日历、申购T+1确认，普通赎回T+1确认/T+2到账，holding_days=0，计价前按既有规则撤。
- 报价为不可变版本最后已取得公开NAV的模拟成交基准。source_nav_date、simulated_pricing_date与price_reference_sha256分别保存；不生成未来NAV或冒称真实T日成交价。MM-DD年份解释有独立标注，不改原值。收益缺数据维持partial/null。
- 不新增生产七代码白名单：准备程序检查全目录资料/价格交集，版本化证据登记用于拒绝未核实或篡改的等级/分类。joint_samples仅验收，不进入推荐或购买过滤。可用证据增加时显式准备新版本。

### Canonical引用与当前操作卡

selected_positions仍是四字段SelectionItem，不含金额/账户。advised_positions新增可选disclosure_version，持仓/account_ids和整数target_fen原样供B差额消费。显式多选Plan中的candidate_groups保存全部已核实的已选代码卡，与A05短推荐批次分开；不是新推荐算法。唯一借记账户可解析，多账户B prepare要求显式payment_account_id；多账户减持分配未知受控拒绝。旧未提交A版本在B preview/confirm/verify/execute均再验；受理后的原号恢复不依赖易失A草稿。
