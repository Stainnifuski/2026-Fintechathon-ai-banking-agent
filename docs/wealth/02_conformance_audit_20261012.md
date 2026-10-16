# 02 对照检查及 B6 收尾记录｜2026-10-12

基准：`D:/Project/Fintechathon-2026/理财场景完善/02_协作同学_交易与人工操作任务包_v0.3.md`，并核对 00 的 6.5 PortfolioSelection。以当前文件要求为准，不以开发预览右侧步骤提示判定完成。原未跟踪的 20261010 审计文档保留。

结论：当前 B 实现没有发现业务范围扩张；发现两处明确的契约/交互漏项，本轮已修正。B0—B5 主体已实现，B6 已有独立模拟银行及浏览器主链证据，仍不能称为 02 全部完成或 A+B 联合验收完成。

## 范围和证据

| 02 要求 | 当前实现与证据 | 剩余边界 |
|---|---|---|
| B0 原事实、原风险评估、原授权与执行 | 复用原银行、JWT、评估、本人确认、验证码、原操作号；B6 测试夹具与日常账户隔离 | B6 预置评估不能当作本人答题浏览器验收；未重新覆盖全部既有风险问卷 |
| B1 三只候选、家族多选、默认有效 A/例外 C、换批保留、可选详情比较 | ProductPicker、共享选择模块及分项测试；三列显示是用户批准的布局调整 | 正式 A CandidateGroup 与模型输出仍待联调 |
| B2 整体比例、差额准备、七笔一次授权 | 既有 revision/prepare 及 PlanExecution；七笔受理、撤单和六笔结算有独立银行/浏览器证据 | 配置图和稳健/进取联动滑块按 00 分工消费 A 组件，不能以 B 编辑表代替最终联调 |
| B3a 独立订单/持仓、分页选择详情、聊天和工作台复用 | 两个完整表、独立受控状态和共享草稿；当前浏览器九笔订单、六条持仓 | 多入口及角色切换浏览器矩阵尚须收尾，不只看表能显示 |
| B3b 全授权借记账户总资产、冻结不重复、缺值不造零 | 共享 AssetSummary 服务和头部；F18 多账户/冻结/应收分项测试；两页同值已有浏览器证据 | 真实 A 必须消费同一 scope/snapshot，联合 J18 尚未完成 |
| B3c 原理财现金流水保留、账单全部口径排除 | 公共账单过滤、统计/对比/明细/文字回归；原现金流水可查 | 浏览器空账单本身不证明排除正确，须依赖量化后端回归；当前隔离业务时钟晚于现实报告截止时间 |
| B3d 整体/单产品实际收益、四粒度、日期、已赎回贡献 | 实际成交重建 actual-settlement-replay-v1；F20—F24 分项测试、八类查询和日历浏览器证据 | 历史缺资料保持未知；完整 J21—J23 待真实 A+B 联合验证 |
| B4 份额/市值赎回弹窗、占比/费用/到账说明 | 复用 RedemptionDialog，可信换算与 SingleTradePreview；1000 元换算已有后端测试 | 本轮补齐统一入口点击持仓赎回打开弹窗，见下方 |
| B5 严格动作、四快捷、参数、clarify、原 HUMAN 控制 | /actions、WealthActionSession、共享表单/核对视图及原 TaskControlPanel；不调用新 NLP | 本轮补齐可选进取偏好透传；实际 A 输出更新需联合验收 |
| B6 独立全链路、受影响回归、交接 | 两份 B6 银行/浏览器报告；本轮补人工接管及异常恢复回归 | PostgreSQL 未验证；最终角色/异常浏览器矩阵和交接汇总未完成；03 按双方约定联合修订 |

独立 8011/3011 进程、临时 SQLite、固定契约提供者属于 02 明确允许的无 LLM 验收方式，不是新增业务服务或生产推荐算法。未增加保险、跨行划转、新认证、员工代签、自动再平衡、TWR/IRR 或新节点引擎。账单洞察沿用已有导航模块，理财流水边界是 B3c 的要求。

## 本轮修正

1. 00 §6.5 的 PortfolioSelection 允许 `growth_ratio_ex_cash?`。此前前端类型及 B 严格动作 schema 漏接此字段，会拒绝 A 合法偏好输入。本轮补可选字符串/null，复用原比例校验（0—1），原样传递给 A handler，不在 B 重算模型。HTTP 回归验证 0.625/null/0/1 透传，布尔、浮点、负数、超过 1 和 nan 拒绝。
2. 02 §5.3 要求“点击赎回打开弹窗”。原工作台符合；统一操作持仓表此前只选中并滚到下方表单。本轮两者共用 RedemptionDialog；统一入口仍通过串行动作会话及 /actions 获取报价，再复用原本人确认/验证码组件。多只持仓分别填写，不自动均摊；账户/任务控制或事实版本变化关闭旧弹窗。下方通用参数表单仍保留供 B5 使用。

浏览器实测：3011 → 统一手动操作 → 查询我的持仓 → 模拟青石债券基金的“赎回”，立即打开输入弹窗。选择市值 1000 元，显示 968.0542 份、总持仓 1907.0485 份、比例 50.7619%、净值及日期、费用 5 元、预计到账 995 元和非保证到账说明。本轮止于预览，未本人授权或提交；回查订单仍九笔，总资产仍 19,712.90 元。截图：`.tmp/02-audit-redemption-modal-20261012.png`。

## 本轮新执行的回归

```text
.venv/Scripts/python.exe -m pytest tests/integration/test_wealth_actions.py tests/integration/test_wealth_handoff_review.py tests/integration/test_wealth_task_controls.py -q -p no:cacheprovider --basetemp=.tmp/02-audit-20261012-regression
cd frontend
node --experimental-strip-types --test tests/wealthActionSession.test.ts tests/wealthIndependentTables.test.ts tests/wealthManualDraft.test.ts
npm run build
```

- 后端 24 通过、23 PostgreSQL 未配置跳过，28.67 秒；前端状态回归 10 通过；TypeScript/Vite 构建通过，既有大 chunk 提示。
- 接管/暂停使未提交旧授权失效；交还后的新预览需要本人重新授权；已受理原号/占用保留；网络未知或银行已有订单不能替代重发；订单取消与任务取消分别验证。
- 这次是受影响范围回归，不是全仓重测。此前 B6 报告结果属于前轮证据，不累加为本轮测试数。

## A/2号交接补充草案

现有统一入口为 `POST /api/v1/wealth/actions`；A 注册映射为 `app.state.wealth_action_handlers`，异步 handler 签名 `handler(db, user_id, context_id, bank, params)`，参数以关键字传入。B 已严格核验本人/context/session/对象范围；A 仍须验证自己的 portfolio/snapshot/version 和模型约束。无提供者返回 blocked，不猜产品。

修订参数示例（引用必须替换为真实 A 输出，不能直接拿本示例下单）：

```json
{"action":"revise_portfolio","params":{"selection":{"portfolio_id":"来自A","snapshot_id":"来自A","context_id":"当前资金环境","expected_version":1,"growth_ratio_ex_cash":"0.625","items":[{"product_id":"来自A","product_version":1,"share_class":"A","target_ratio_total":"0.2"}]}}}
```

该片段只是参数示例，完整 ActionV1 还必须带 schema_version/request_id/context_id 及适用的 session/version。响应、prepare 映射、实际运行步骤分别见 manual_flow_b5_actions.md、manual_flow_b5_shared_tables.md、manual_flow_b6_bank_chain_20261012.md 和 manual_flow_b6_browser_20261012.md。

下一步：补角色切换/旧预览失效等浏览器验收记录，整理 02 §8 的逐项证据及给 Peter 的字段/注册清单。收到 A 的组件/提供者后，在功能分支接入真实输出，再进入 wealth 联合验收，最后才到 main。03 的正式汇总由双方按约定共同修订；当前记录为 B 补充草案。本次不合入或推送共享 wealth/main。

后续补充：客户暂停/继续/重新预览的非空浏览器操作，以及真实 JWT 的客户/操作员领取交还 HTTP 链路已继续验证，见 manual_flow_b6_controls_20261012.md。该报告明确区分接口验证与尚未完成的操作员浏览器矩阵。

第四轮补充：主要操作员领取/完整方案核对/保存说明/交还→退出切换本人→重新校验及整份预览，已在独立 3012 环境验证。修复动作入口二次 prepare 的会话过期/版本未保存问题；10 通过、10 PostgreSQL 跳过。详见 manual_flow_b6_operator_browser_20261012.md；复杂 context/竞态浏览器矩阵和正式 A 联合验收仍另列。
