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
