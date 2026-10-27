# 账单工作台与人工监管映射

日期：2026-10-25。

## 页面结构

沿用统一工作台、NOVA 侧栏和确认卡。手操功能分为六个一级页签；二级功能使用同一页面内的原生 `details/summary` 折叠区块，可同时展开，无二级页面跳转。区块首次展开时才加载，访问过的区块保留挂载，避免收起或切换页签时丢失编辑内容。

| 一级功能 | 同页内容 | 一级定位 |
|---|---|---|
| 账单总览 | 收支、消费去向、趋势与变化依据 | `#bill-panel-overview` |
| 交易明细 | 流水表格与选中交易详情，详情内修正分类、编辑标签 | `#bill-panel-transactions` |
| 预算管理 | 独立预算月份、预算列表、新建和编辑 | `#bill-panel-budgets` |
| 异常与订阅核对 | 异常核对、订阅扣费核对 | `#bill-panel-checks` |
| 周期与预测 | 周期收支、现金流预测、预测复盘 | `#bill-panel-forecast` |
| 账单整理 | 商户管理、分类与标签 | `#bill-panel-organize` |

一级入口是 `#bill-tab-{section}`，具有 `role=tab` 与 `aria-controls`，支持方向键、Home、End。页签为 `role=tabpanel`；隐藏区块带 `hidden`，不通过已隐藏的表单办理。

## 二级区块映射

| 功能 | 稳定区块 ID | `data-bill-feature` | 手操及现有服务 |
|---|---|---|---|
| 异常核对 | `bill-feature-anomalies` | `anomalies` | 从 `bills/analysis` 读取异常，`bills/anomaly-reviews` 保存核对 |
| 订阅扣费核对 | `bill-feature-subscriptions` | `subscriptions` | 扣费差异、关联流水，转入订阅管理；`bills/subscription-insights` |
| 周期收支 | `bill-feature-recurring` | `recurring` | 手动周期计划、预测开关、历史关联刷新；`bills/recurring` 与既有基础整理服务 |
| 现金流预测 | `bill-feature-cashflow` | `cashflow` | 储蓄账户选择、情景假设、保存基准；既有现金流与预测服务 |
| 预测复盘 | `bill-feature-history` | `history` | 基准预测列表、原区间实际收支比较；既有预测复盘服务 |
| 商户管理 | `bill-feature-merchants` | `merchants` | 别名、归并、按交易勾选拆分、变更记录和撤销；`bills/merchants` |
| 分类与标签 | `bill-feature-rules` | `rules` | 删除商户分类规则、标签目录、跳转关联流水；既有规则与目录服务 |

自动化可先点击对应一级入口，再对 `#bill-feature-{id} > summary` 展开；通过 `details.open` 判断展开状态。多个区块允许同时展开，NOVA 当前子功能取最近聚焦的已展开区块；关闭该区块时回到同组其他展开区块。

## 统一 NOVA 和状态

- “问 NOVA”打开共用侧栏；“让 NOVA 分析当前内容”使用同一会话和排队通道，无额外账单聊天输入框。
- `page_context.bill_workspace` 传递当前一级功能、子功能及选中对象。选中流水、预算、异常、周期记录、商户、分类规则、标签分别隔离；切换功能不携带其他功能的旧选择。
- 历史报告筛选只用于总览、明细、异常与订阅核对。预算使用已应用的独立月份；现金流使用独立储蓄账户。未提交的筛选、金额、备注和情景草稿不冒充银行事实。
- 当前选中对象仅供指代，服务端重新核对归属、当前数据和版本。模型生成修改预览，用户仍在统一确认卡点击保存。
- 确认卡显示字段及修改前后对照，可展开完整说明；兼容没有结构化字段的旧卡。成功保存后刷新手操区。
- 草稿保留范围是当前账单工作台内的页签切换与折叠；刷新浏览器或离开账单模块不承诺恢复未保存表单。
- 商户拆分展示分页交易选择（时间、交易方和金额），保留所选参数绑定预览及银行身份保护，无需填写内部流水号。

## 自检记录

- 相关后端回归：143 passed、17 skipped、1 deselected。跳过项含环境相关分支；既有 `test_offline_business_same_tools` 排除，未计为本轮通过。此前已使用未修改版本复现该离线测试问题。
- 浏览器工作台：31 项通过。覆盖六页签、多个同页折叠、预算/标签/分类/异常/周期/情景/商户草稿保留、真实手操保存、拆分选择与预览、NOVA 上下文隔离、标签定位、390px 手机布局、账户余额不变、页面无运行时异常和隐藏图表无零尺寸告警。
- 连续提问：三问按序各发送一次、忙碌时排队、各自保留提交时页面上下文。仅模型流使用模拟响应。
- 确认卡：7 项通过。服务端真实预览、修改前后字段、预览不写入、手机布局、本人确认仅保存一次、预算区同步、刷新后完成状态恢复；不调用模型。
- 商户拆分刷新：3 项通过。保存后同页候选自动刷新、目录与交易归属同步、账户余额不变。修复候选列表未随商户整理刷新问题。
- 前端构建通过；保留已有大体积构建块提示。测试使用独立虚拟 SQLite 数据库和本机服务，不修改原演示库，不调用付费模型。
- 推送前同步队友的 `b641e7c`（统一 NOVA 外框及人工接管），账单相关与任务展示回归 132 passed、1 deselected，前端重新构建通过；确认卡 7 项浏览器检查再次通过，完成后沿用共享外框的自动折叠和展开恢复。
- 当前 Docker daemon 未运行，未进行容器重建；本记录不代表运行环境已部署。真实模型全链路和生产数据库验证不在本轮浏览器验收范围。

浏览器脚本：`tests/browser/bill_workspace_check.js`、`bill_merchant_split_refresh_check.js`、`nova_bill_question_queue_check.js`、`nova_bill_insights_check.js`；后端覆盖：`tests/integration/test_bill_workspace.py`。接口扩展见 [NOVA 账单契约](../contracts/NOVA账单洞察_API_v1.md)。
