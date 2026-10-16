# B3a：独立订单与持仓双表

开发基准：本地最新 `02_协作同学_交易与人工操作任务包_v0.3.md`，5.2、B3a及B10O/P—B17O/P；共享00 v0.3的6.8。完成记录日期：2026-10-09。B3b总资产、B3c账单边界、B3d实际收益、B4金额赎回、B5结构化入口和B6联合验收继续按02推进。

## 本阶段交付

- `OrderTable`／`PositionTable`及各自适配器、分页、选中与详情状态。银行全量assets在前端分别分页，稳定排序，每页5／10／20行，全选只影响当前页，翻页和切换两表保留各自选择。用户、context或账户范围变化立即失效；消失的对象从选中状态中移除。
- 订单保留已受理、待计价、待到账、已结算、已撤等记录；订单ID不进入持仓赎回。名称、份额类别、产品／披露版本来自原订单版本。`cancellation-eligibility`返回ALLOWED且digest吻合才启用逐行取消，UNKNOWN／DENIED显示原因。多选后重新逐笔核对，本人确认完整订单，再逐笔调用原受保护cancel，结果分别记录。
- 持仓按账户＋产品＋版本合并lot，四位份额用整数万分位相加，保留可赎／锁定／占用与批次详情。市值、浮动收益缺数据保留null；整体估值不完整时不生成完整占比。订单待确认份额不加入持仓。
- 两表详情均可打开既有产品详情；新增可选`product_version`查询，历史记录不会被当前版本替换。没有该版本或当前数据集不提供该版本则明确拒绝。
- 最新行情来自原版本最近两条已公布净值，展示两个估值日期；未公布净值排除，缺两个观察点不补0，货币类显示不适用说明。表格和产品历史行情均不冒充本人实际收益。
- 持仓单行／多选普通份额赎回入口复用原preview→本人confirm→统一TOTP/SMS→verify→execute。多选逐产品填写，不自动均摊、不缩小超限值；预览不冻结。原银行工作台的单笔快速赎回v2保留，只有对应当前产品版本支持quick规则时才允许快速模式。
- 原银行工作台和DEV预览的实际银行入口使用同一双表组件。登录账户读取失败时禁用操作，过期提示重新登录；不同用户的缓存事实不展示给新用户。

## 服务与接入边界

`GET /api/v1/wealth/assets`保留原外形、金额和查询范围，只向orders／lots补充`product_id,product_name,product_version,disclosure_version,share_class,daily_metric`及lot的原`version_id`。内部事实补充在银行服务完成，外层继续通过内部HTTP读取；无LLM调用。

`GET /api/v1/wealth/products/{id}/detail?period=1y&product_version=1`在当前授权数据范围内读取绑定版本。原请求不传版本时行为不变。

`POST /api/v1/wealth/orders/{id}/cancel`继续严格本人确认、原digest及披露版本。当前资产范围检查也在银行用户锁内执行，避免查询后切换context仍释放旧范围占用；HUMAN／HANDOFF_PENDING／PAUSED禁止从旧入口撤单。撤单只释放原占用，不给账面余额再加钱。

前端B-owned `OrderTableData`／`PositionTableData`暂为全量assets到表格的适配形状，`scope_key`是本地状态隔离键，不替代A的`asset_scope_id`／`snapshot_id`。B5将同一组件接到ActionResult聊天卡片；本阶段尚未实现`POST /actions`和聊天注册，不宣称完整DAG联调完成。A的PortfolioPlan／AssetSnapshot schema不在本阶段重建。

## F23记录与限制

`tests/fixtures/wealth_portfolio_contract_v1/f23.json`明确录制12笔虚构订单、10个买入批次聚合为9行持仓、两个账户、同产品不同版本、全锁定行、ALLOWED／UNKNOWN／DENIED及两个明确公布净值点。v1产品资料的净值时点与F23表一致；未录制v2详情明确拒绝，不以v1替代。

DEV地址：`http://127.0.0.1:3000/?wealth-dev=1`，选择“查看订单”／“查看持仓”及“查看 F23 双表样例”。录制模块不进入生产构建；不发送任何虚构ID到银行，不取消银行订单、不生成预览或授权。切换“查询登录账户模拟银行”才读取当前账户事实。浏览器验证没有代用户答风险问卷、确认交易或输入验证码。

收益两个入口目前显示B3d接入说明；实际日／月／年／累计视图、已赎回部分收益、日期切换尚未实现。市值赎回／百分比与估算到账的完整转换属于B4。因此本阶段只验证F23双表状态部分，不宣称F23全案例、J21或02全任务通过。

## 验证：2026-10-09

前端：`npm run test:wealth-picker` 12通过，`test:wealth-allocation` 5通过，`test:wealth-tables` 8通过；TypeScript及Vite生产构建通过，产物不含DEV／F23录制。已有大体积chunk警告保留。

后端（仓库根目录，隔离SQLite）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q --tb=short -o cache_dir=.tmp/pytest-cache tests/integration/test_wealth_tables.py tests/integration/test_wealth_portfolio_prepare.py tests/integration/test_wealth_portfolio_consumer_baseline.py tests/integration/test_wealth_unified_authorization.py tests/unit/test_wealth_portfolio_differences.py
.\.venv\Scripts\python.exe -m pytest -q --tb=short -o cache_dir=.tmp/pytest-cache tests/integration/test_wealth_quick.py tests/integration/test_wealth_product_detail.py tests/unit/test_wealth_chart.py
```

分别31通过／19跳过、13通过／8跳过。跳过为未配置PostgreSQL，不能宣称该数据库已验证。覆盖全授权账户、原产品版本、未发布净值、缺行情、取消拒绝假同意／错digest／错披露版本、释放原冻结不增加余额、原号重复取消、旧context及人工／暂停限制，另验证原quick与产品详情／历史图兼容。未重跑全仓旧基线；B2记录的既有失败不宣称修复。

浏览器核对：订单第一页全选5笔、第二页再选1笔→共6笔；持仓独立第二页选中5笔；切回订单仍为第二页6笔。同账户S1 v1两个lot合为101.0000份、可赎80.8766，S1 v2／另一账户仍独立。撤单说明、版本产品详情、超限份额拒绝与有效份额仅录制提示、范围变化清空选择／弹窗均通过。当前本人银行读取成功，无订单／持仓，保留原数据库。窄容器有横向滚动提示。

截图：`D:/Project/Fintechathon-2026/.tmp/wealth-b3a-position-detail-20261009.jpg`。
