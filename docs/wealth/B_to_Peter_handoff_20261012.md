# B → Peter：02 交易与人工操作交接清单｜2026-10-12

状态：**可开始接口对照和正式 A 接入准备；不是 B6/联合验收全部通过声明。**

功能基线：`codex/wealth-manual-flow`，`fb92910`。本轮新增交接文档与样例，未改业务代码、未抓取/推送远端、未合入共享 wealth/main。具体文档提交可由 `git log -1` 查阅。

依据：最新版 `02_协作同学_交易与人工操作任务包_v0.3.md` 的 B6、§8、§9，以及 00 v0.3 的共同 DTO、F/J 清单。02 不规定交接的日历截止日期：B6 前置 B0—B5，在无 LLM 独立链路验收时交付实际接口、运行方式、证据与未验证项。§9 是给 Peter 的六项最小交付内容。GitHub 阶段备份是协作建议，不是额外验收条款。03 按双方约定待 01/02 汇合后共同正式修订；本文件提供 B 侧可纳入 03 的材料。

## 1. PortfolioPlan / CandidateGroup 兼容报告

B 没有创建第二套模型/推荐算法。前端类型暂从共同 F09/F11 recording 推导，运行消费检查分别位于 `frontend/src/services/wealthCandidates.ts`、`wealthPortfolioReview.ts`。正式 A 合并后应改用共同 canonical 类型，并对真实输出执行相同消费检查；目前没有真实 A 字段差异报告，不能把 fixture 兼容说成正式 A 兼容。

| 对象 | B 实际消费/校验 | 请 A 对齐 |
|---|---|---|
| PortfolioPlan | wealth-portfolio-plan-v1；portfolio_id/version、context_id、asset_scope_id、snapshot_id；as_of/provider_id/provider_version/model_status/basis_kind/basis_status；basis_fen、missing_fields/reasons/data_versions | 当前所属主体与最新版本由服务端核验；缺估值时不允许准备交易；不得从浏览器接收可信本金基准 |
| 三板块 | cash_management/stable/growth，min_ratio/target_ratio/max_ratio，target_fen/current_fen | 比例用 0—1 十进制字符串，金额用整数分；目标/区间联动与模型由 A 校验 |
| selected_positions | account_id、family_id、product_id/product_version/disclosure_version、share_class、bucket、target_ratio_total、target_fen/current_fen | 当前一份方案中同 family 不重复；最后执行按具体产品及付款账户绑定；整体比例含保留持仓、现金和应收 |
| CandidateGroup | bucket/status/filter/items/next_cursor/risk_explanation；status 支持 ok/no_theme_match/no_category_match/no_eligible_products/risk_mismatch/unavailable | 类别/主题 filter 原样匹配；换批使用 cursor；无匹配、风险不符、不可售要区分原因，不能返回假成功 |
| candidate/share | family_id/name/bucket/reason/reason_codes/evidence；share_classes 中产品/披露版本、风险、status/tradable、起购额、费用、one_year/latest_day 的状态和值/日期/来源、detail_ref、data_source/data_as_of | 当前目录可售标记为 on_sale；缺风险或不可售不能默认勾选。默认有效 A；无可买 A 时可选合格 C。未知费用/历史指标保留 null/原因，不补零 |
| AssetSnapshot | wealth-asset-snapshot-v1；context/scope/snapshot/basis 与方案一致，account_ids/accounts、聚合 positions、receivable_fen、valuation_complete | A 从与 B 相同的银行授权范围生成；同账户/产品版本的批次应聚合；basis 与同口径 AssetSummary 一致 |
| PortfolioSelection | portfolio_id/expected_version/snapshot_id/context_id/items；可选 growth_ratio_ex_cash | 已补可选偏好透传并校验 0—1 字符串/null；B 不重算权重。配置图和稳健/进取联动滑块仍由 A 提供组件/回调 |

真实完整测试 DTO 及资产/收益响应见 [b_handoff_examples_20261012.json](b_handoff_examples_20261012.json)。这是文档数据，不被运行服务加载为默认方案。

## 2. SelectionContext / PortfolioSelection 事件样例

对象引用固定为：产品 `product_id:version`，订单原 `operation_id`，持仓 `account_id:product_id:version`。不能混用列表序号或跨 scope 的引用；上下文或主体切换清空旧选择/草稿。授权引用及验证码不进入共享草稿。

```json
{
  "schema_version":"wealth-action-v1",
  "request_id":"handoff-orders-query-0001",
  "context_id":"legacy-v1",
  "action":"list_orders",
  "params":{"page":1,"page_size":5}
}
```

响应为 wealth-action-result-v1，含 request_id/context_id/session_id/session_version/action/status/message/data/required_fields/allowed_actions。status 为 ok/clarify/blocked。继续同一会话时必须一起传 session_id 与 expected_session_version，并取前一响应的新版本；同 request_id 同参可回放，异参拒绝。分页返回兼容 rows/page/pages/total，另有 table_data 全量授权快照以支持两个完整独立表及跨页选择。

```json
{"object_type":"positions","ids":["A1:MF002:1"]}
```

这是 SelectionContext 参数片段。对应赎回 action 为 preview_redemption，params.redemption 包括 context_id、position_key、input_mode（units/market_value/holding_ratio）及对应的 units 字符串/market_value_fen 整数/holding_ratio 字符串。市值 1000 元传 100000 分；银行返回四位份额、估算市值/费用/到账与占总持仓比例，命令 amount_fen=0。多只持仓只给一笔金额时返回 clarify，不自动均摊。

样例 JSON 的 `bank_chain.revision_action_example_assembled_from_validated_DTO` 给出七产品 PortfolioSelection/SelectionContext 的完整字段示例，明确标注由已验证 DTO 组装，不能直接拿这些测试引用在正常账户提交。进取偏好如需要可在 selection 加 `"growth_ratio_ex_cash":"0.625"`；该值由 A 使用，不是 B 模型默认值。

## 3. prepare → execution plan 与完成后资产

结构化入口 prepare_purchase 或 `POST /api/v1/wealth/portfolio/plans/{portfolio_id}/prepare`，输入为 expected_version/snapshot_id。B 调用 A owned_plan/snapshot，重新读取银行目录、账户/持仓、真实可用现金和任务控制，执行确定性的差额计算。

- 重读 scope/context/snapshot 与目标版本；缺估值或数据失效拒绝。
- 按目标与当前持仓差额生成 InvestmentCommand；未提及持仓保留，不重复购买已有金额。
- portfolio_version 与 execution_plan_version 分开，绑定真实付款账户、产品/披露版本、原操作号；不把全部资产当该卡可支付现金。
- prepare/preview 不冻结、不自动交易。整份七笔一次本人确认与验证码；受理立即占款，撤单释放原占用，结算不双扣。
- 减持后再买入使用原 SETTLEMENT/REREAD_CASH；实际到账后重新核对现金和本人授权，不预支应收。
- 重新修订/准备会重建尚未提交的执行动作与授权；已有银行原号、未知提交不能被替换重发。二次 prepare 的会话过期/返回版本未保存问题已修复并回归。

JSON `operator_browser_reprepare.mapping` 是 3012 隔离数据库中真实 prepare 映射的投影：portfolio_version=10、execution_plan_version=2、七笔 BUY 动作。投影字段与 prepared_view 一致，特意省略完整 execution_plan 和授权引用；不是完整 HTTP 响应录制。

独立银行全链路（另一套隔离库）的完成后 AssetSummary 在 `bank_chain.asset_summary_after_settlement_and_redemptions`：

| 金额口径 | 分 |
|---|---:|
| 账面现金 | 301996 |
| 冻结（已含在现金中） | 0 |
| 可用现金 | 301996 |
| 持仓市值 | 1669294 |
| 赎回应收 | 0 |
| 总资产 | 1971290 |

该例业务时钟为 2026-10-29，来自隔离测试推进，非真实未来收益。与 prepare 投影属于两个独立环境，不能混成同一次银行链路。完成后的表是银行 AssetSummary，不冒充 A 已经重读并修订的新模型方案。

## 4. 独立运行与实际验收证据

| 环境 | 目的与当前状态 |
|---|---|
| 日常 3000/8000 | 原日常演示账户；未为 B6 造单或配置操作员 |
| 3011/8011 | 独立固定 A 契约＋真实模拟银行；七申购/一撤单/两赎回，最终九订单六持仓；浏览器主交易链已验证 |
| 3012/8012 | 独立固定 A 契约＋虚构操作员 U2；领取/核对/建议备注/交还、切回本人重新准备七笔预览已验证；本轮止于预览，零订单冻结 |

环境、全新 .tmp 路径限制、公开登录、启动参数/命令详见 [操作员浏览器报告](manual_flow_b6_operator_browser_20261012.md)。固定提供者只由 tests/browser/wealth_b6_server.py 注册；不在普通 main 启动中注册，不读取真实模型生成推荐。测试说明仅在理财页，姓名为正常虚构姓名，其他模块不显示测试术语。

建议按新临时目录运行受影响套件，不删除或复用已有库：

```powershell
.venv/Scripts/python.exe -m pytest tests/integration/test_wealth_structured_portfolio.py tests/integration/test_wealth_portfolio_prepare.py tests/integration/test_wealth_b6_manual_chain.py tests/integration/test_wealth_b6_controls.py -q -p no:cacheprovider --basetemp=<全新临时目录>
```

已执行结果：10 passed / 10 PostgreSQL 未配置 skipped，18.19 秒，见第四轮报告。前轮 B6 八文件受影响回归 36 passed / 30 skipped；前端 wealth/AssetSummary 状态测试 66 passed；数字属于不同轮次，不能累加称为一次全仓验证。最近显示修正 `npm run build` 通过；既有大 chunk 提示。

```powershell
# 在 frontend 目录
$wealthTests = @(Get-ChildItem -Path tests/wealth*.test.ts | ForEach-Object { $_.FullName })
node --experimental-strip-types --test @wealthTests tests/assetSummary.test.ts
npm run build
```

实际 B6 API 转录还可由 frontend/tests/verifyB6Evidence.ts 消费，每次测试产生的 b6-evidence.json 位置以当次临时目录为准。

### 02 §8 逐项证据与保留项

| 验收要求 | 已有证据 | 未完成/限制 |
|---|---|---|
| 未合入 A 时 fixture 独立完整链路 | test_wealth_b6_manual_chain；银行、浏览器主链两报告 | 固定方案不是正式 A 输出 |
| 三只组/换批保留/多选/A-C/可选详情比较 | B1 报告；ProductSelection/Candidates 前端测试；详情比较浏览器记录 | 正式 A 候选/状态/换批全部组合待联调 |
| 整体比例、保留项目、已有持仓不重复买 | AllocationReview、difference_actions；portfolio_prepare/consumer_baseline/差额 unit 测试 | A 配置图与联动滑块组件尚待接入 |
| 七笔一次确认/验证码 | prepare/unified授权及 B6 HTTP、浏览器整份确认链 | 真实 A 的任意有效七产品需联合验证 |
| preview无占用/受理HELD/撤单释放/结算不双扣 | B6 银行与浏览器连续链；原银行回归 | 未声明全部 PG 路径通过 |
| 独立双表/分页/多选/详情/操作 | B3a、B5 shared_tables；双表/状态测试；九订单六持仓浏览器 | 极端竞态浏览器矩阵非穷尽 |
| 市值/份额/占比/到账提示两个入口 | B4 输入与 HTTP 测试；统一/原 RedemptionDialog；1000元弹窗实测 | 部分费用未知仍显示未知，不能承诺到账 |
| 1000元与订单取消/任务取消区分 | redemption_input、task_controls、原撤单资格测试及报告 | 新自然语言解析按约定后置 |
| 禁用LLM四快捷+全人工链 | B6 provider 分派、连续银行链、浏览器报告；模型调用即失败的测试 | 不把当前旧 NLP 可用性当成新流程接好 |
| 主体/context隔离与HUMAN约束 | action/session/manualDraft状态、接管 HTTP/JWT；操作员浏览器第四轮 | 完整 context/并发角色浏览器组合尚未穷尽 |
| SQLite/PG | SQLite受影响套件通过；PG跳过明确登记 | 独立 PG 库未配置，不声称双库验收 |

F18 有多账户/冻结/应收/缺估值分项证据；F19 有普通业务保留与全报告排除的量化回归；F20—F24 有本金/到账不当利润、费用不双扣、关闭产品贡献保留和缺历史不补零测试。J18—J23 在 B 独立层已有对应事实/页面证据，**真实 A+B 联合通过仍未登记**，不得自动打勾 J01—J23。

收益交接字段：data_source=`simulated_bank_actual_settlements`，formula_version=`actual-settlement-replay-v1`；coverage_start、valuation_as_of、start_date/end_date、status、missing_fields 随实际范围返回。覆盖起点是服务端可重建记录起点，不固定为某个演示日期。缺净值/历史成本返回 partial/unavailable/null，不伪造早期零收益；未来日期禁止；累计含已赎回贡献。完整实际月收益样例随 JSON 提供。

## 5. 公共文件范围与 A 注册位置

公共修改依据见 [范围审计](02_scope_boundary_audit_20261012.md)：App 首页与理财页共享总资产是 B3b；BillAnalysis/bill_service 的理财边界是 B3c；NOVA 仅同步此前授权的现金/总资产口径。转账、卡片、订阅和生活服务业务模块未被 B 改写。主线 NOVA 面板、上下文及流式输出保持。

A 需提供两组服务端注册：

```python
app.state.wealth_action_handlers = {
    "plan_portfolio": plan_handler,
    "recommend_products": recommend_handler,
    "revise_portfolio": revise_handler,
}
# 每个 async handler(*, db, user_id, context_id, bank, params) -> A 的共同 DTO
app.state.wealth_portfolio_source = canonical_source
# async owned_plan(db, user_id, portfolio_id) -> 当前归属、已校验 PortfolioPlan
# async snapshot(db, user_id, context_id, bank) -> 可信 AssetSnapshot
```

正式启动注册位置由 A 合并时共同确定；不要把 tests 的 register_recorded_handlers 装进生产。B 校验入口身份/范围，A 仍必须校验其数据归属、版本、资格与模型约束。默认缺注册时返回 PORTFOLIO_PROVIDER_UNAVAILABLE/blocked，不用假数据掩盖。

真实入口：POST /api/v1/wealth/actions、GET /portfolio/snapshot、POST /portfolio/plans/{id}/prepare、GET /asset-summary、GET /returns、POST /redemptions/preview（均在 /api/v1/wealth 下）。确认/验证/执行继续原 /previews 与 /plans 接口；原 /tasks pause/resume/handoff/repreview 和 /operator/tasks claim/note/release 保留。A 自己的创建/修订直达路由不得因为前端辅助函数出现就认定已由 B 实现；当前统一入口通过上述 handler 注册调用 A。

### 接入前请 Peter 提供/双方确认

1. 01 实现的具体 commit/功能分支、canonical DTO 和正式 provider 模块/注册方式。
2. owned_plan/snapshot 与三个 handler 的实际签名及返回样例，绑定当前真实账户 scope/snapshot/basis；缺数据/过期版本明确处理。
3. 配置图、稳健/进取联动滑块的组件位置、输入 props、修订回调；零分母/null 偏好和完整区间校验由 A 提供。
4. 候选 A/C、风险/可售状态、filter/cursor、主题无匹配/全超风险等返回差异；用真实目录版本对照。
5. 成交、赎回或 scope 变动后如何让 A 重读事实并输出新整体方案；不继续使用测试中的固定旧目标。

不是要求用户手工实现这些接口。B 与 Peter 拿真实实现对照；缺项留清单，不复制模型算法到 B。

## 6. 03 汇总与下一阶段

本文件＋JSON 是 03 的 B 侧素材，包含实际 DTO/事件/路由、映射、快照、运行方式、提交、证据和未验证项。03 正式文件目前未覆盖重写，待双方按已约定的流程共同修订。

下一阶段：在功能分支拿真实 A 输出替换服务端提供者、接组件；从真实模拟账户读快照→A生成→B选择/修订/prepare→本人确认验证码→银行受理/结算→重读资产/配置，运行 J01—J23，特别核对多账户付款限制与 J18—J23。联合验收后经 wealth 汇合，最后到 main。J17 和双方约定通过后，2号才把新自然语言解析映射到同一 ActionV1，不绕过授权，不把 ActionV1 直接塞给原 /subtasks-v1。

本轮只完成交接清单整理；不是整项项目验收结束，也不是 GitHub 备份或共享分支合并。
