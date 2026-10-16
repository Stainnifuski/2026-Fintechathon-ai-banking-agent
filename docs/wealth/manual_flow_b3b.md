# B3b：银行总资产与两页统一展示

日期：2026-10-09。基准：本机最新02 v0.3的B3b，以及00 v0.3第3、6.10节。用户本轮已确认B3a订单和持仓可正常显示；B3b完成代码及独立验证，待用户体验、A接入与联合验收。不是02整体完工。

## 交付给A的读取协议

- `GET /api/v1/wealth/asset-summary`：只读、本人JWT；经受内部凭据保护的`POST /api/v1/internal/bank/wealth/asset-summary`读取银行事实。主体来自JWT，不接受前端指定用户、付款卡或账户集合。内部Subject沿用现有协议。
- `backend/app/schemas/wealth_assets.py`为B-owned `AssetSummary`；可供跨模块核对的JSON Schema为`docs/wealth/asset_summary_v1.schema.json`。字段与00第6.10节一致，不另建A的AssetSnapshot或PortfolioPlan。
- 全量`GET /wealth/assets`也附带`asset_summary`，由相同构建函数生成。内部显式`account_id`的单账户诊断保留旧响应，不附带全量AssetSummary。A应从全量协议消费`account_ids/asset_scope_id/snapshot_id/total_assets_fen`；完整估值时`basis_fen=total_assets_fen`，缺值不得产生完整规划基准。
- 旧`bank_assets_fen`保留兼容；缺估值时旧字段可能只是已知分项的合计，不是canonical总资产。新头部及持仓整体占比分母优先使用AssetSummary，缺值不回退到旧小计。

全部金额为整数分。总资产＝借记账户账面现金＋绑定原产品版本、已公布净值估出的持仓市值＋已定价待到账赎回应收。冻结是现金子集，既不再加一次，也不从总资产扣除；可支付现金独立显示。任何有效持仓缺净值，持仓汇总和总资产均为null，`valuation_complete=false`，携带`missing_fields`；已知现金／应收仍显示。

普通范围包括当前用户全部savings/checking账户及关联理财记录；排除信用额度、其他用户和测试账户。选中一次性模拟轮次时，仅读取同用户、同run的关联账户，不混入普通账户或互斥轮次。不从当前付款卡、持仓第一页或最近流水推算资产，不自动调拨资金。数据来源为`simulated_bank`。

`asset_scope_id`由银行生成，绑定本人、选定环境和排序后的借记账户集合。`snapshot_id`对该范围的完整现金／占用／批次／订单／净值事实及北京时间业务日取稳定摘要；相同事实的反复读取不会因查询秒数变更版本。`as_of`仍更新为本次查询时间，`business_at`保留业务钟。金额、占用、份额、订单状态或有效估值变化会改变版本；这些ID均不具有授权能力，不替代逐笔确认／验证码。

HTTP银行读取使用同一数据库只读快照：SQLite显式BEGIN避免其默认SELECT不启动事务的问题；PostgreSQL使用REPEATABLE READ、READ ONLY。本轮SQLite并发测试已证明现金与持仓不会读到两个不同提交版本；未配置独立PostgreSQL库，不能宣称其行为已实测。直接银行服务调用仍服从调用方原事务。

## 共用页面与刷新

App层拥有单份、绑定登录用户的AssetSummary缓存；概览和理财头部使用同一个展示组件与读取服务，不依赖问卷、聊天、N01/N02或LLM。两页进入即读取，显示总资产、授权范围、查询时点、业务时间、四个分项与冻结说明。其他业务页不持续轮询。

执行、取消订单、切换context及测试资金管理成功后统一失效；既有`onActivity`刷新银行revision也失效。页面活动时每5秒只读更新，覆盖银行后续结算、赎回应收到账等外部变化；同一请求合并，避免两页分别重算。2026-10-09体验修复：已有数据的定时回读静默执行，不插入“正在更新”段落、不改变卡片高度；主动刷新仅在固定宽度的按钮上提示，首次读取仍显示加载状态。失败清空旧数值，401提示重新登录。范围或用户变更后，旧异步响应不能恢复旧缓存。

DEV预览继续显示明确标注的虚构资产样例，与上方本人模拟银行事实区分。样例使用相同组件，数据不会写入银行，也不会产生交易。`asset_summary_cases.json`仅由DEV预览加载，未进入生产JS产物。

## 样例与独立验证

共享录制：`tests/fixtures/wealth_portfolio_contract_v1/asset_summary_cases.json`，与既有F01/F11/F18/F10的范围和快照ID一致。

- F18：账面现金800000、冻结100000、可用700000、持仓150000、应收50000、总资产1000000分。
- F10：相同已知现金与应收，持仓与总资产null；缺失字段保留。
- F01/F11：空持仓现金1000000分，供原选择／比例流程继续使用。录制标`contract_fixture_only`，不模拟A算法。

后端验证命令（仓库根目录，全部使用临时独立数据库）：

```powershell
.venv\Scripts\python.exe -m pytest tests/integration/test_wealth_asset_summary.py tests/integration/test_wealth_tables.py tests/integration/test_wealth_portfolio_consumer_baseline.py tests/integration/test_wealth_portfolio_prepare.py tests/integration/test_wealth_unified_authorization.py tests/integration/test_wealth_cancellation_contracts.py tests/integration/test_wealth_quick.py tests/integration/test_wealth_product_detail.py -q --disable-warnings
.venv\Scripts\python.exe -m pytest tests/integration/test_wealth_bank.py tests/integration/test_wealth_demo_runs.py -q --disable-warnings -k "not test_real_jwt_ownership_and_injected_description_cannot_authorize and not test_cancel_submitted_task_keeps_settlement_observation and not test_cancelled_sell_blocks_buy_dependency_and_human_direct_cancel"
```

分别37通过／35跳过、34通过／34跳过／2排除。跳过为未配置PostgreSQL；第二组只包含其中一个既有失败函数的两个数据库参数，另外两个旧失败函数不在此次文件集合。旧失败及原因见B2文档，不宣称已修复。最后补强真实JWT、只读不创建画像／授权及单账户诊断边界后，重跑asset_summary＋tables：12通过／11跳过。

新增资产验证覆盖全量多账户／信用排除／本人隔离、实际HTTP读取、相同事实稳定快照、未来／缺失估值、不重复计算受理冻结／撤单／结算／赎回应收到账、不同模拟轮次隔离、并发原子事实读取、整数金额／不完整契约。测试禁止LLM调用；真实JWT不能用query覆盖主体，未登录401。

前端31项通过：原picker12、allocation5、tables8，加资产缓存／缺值展示6项。TypeScript及Vite生产构建通过；已有大chunk提示保留。可运行`npm run test:asset-summary`；其他三个原test脚本保留。

浏览器：概览与理财本人总资产均¥50,000，DOM范围和快照ID完全一致。信用账户¥20,000未计入。F18为¥10,000，冻结¥1,000不重复相加；F10显示不完整、现金¥8,000与应收¥500仍可见。刷新成功，原演示数据库未重置；没有代用户问卷、确认、验证码或交易。

截图：`D:/Project/Fintechathon-2026/.tmp/wealth-b3b-home-20261009.jpg`、`wealth-b3b-f18-20261009.jpg`、`wealth-b3b-f10-20261009.jpg`。

## 下一阶段

2026-10-09刷新修复验证：资产缓存8项测试通过（新增后台回读不发布临时加载、首读／失败／主动刷新语义）；浏览器45次采样跨越18:38:39／44／49三个查询时点，卡片高度均293.5px，没有临时更新段落。主动刷新按钮仍可用；读取／授权范围及银行行为未改变。

B3c核对实际investment_purchase/redemption资金流水，公共账单过滤剔除理财但保留消费／工资／转账规则；B3d实际收益、B4市值赎回、B5结构化动作／人工视图、B6全链路仍待完成。A必须适配实际AssetSummary并进行A+B模拟银行联合验证后，才可宣称J18的模型基准一致及整个02交付。
