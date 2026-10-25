# NOVA 账单洞察接入契约

日期：2026-10-25。复用账单 v3/v4 服务，不改金融账本口径。

## 核对结果

原有 `get_bill_analysis`、`list_transactions`、`get_transaction`、订阅洞察、预算、周期与现金流/预测复盘查询已注册到 NOVA，并在 bills 路由可用。缺口是：账单页提问使用独立 `/bills/messages`；分类/标签/异常/商户整理缺少完整工具入口；部分关联和核对字段没有进入后端生成的回答证据。

现在账单页提问经 App 传递一次性请求和页面上下文快照，使用同一 NOVA 会话及 `/api/v1/nova/chat/stream`。上下文只含已应用报告筛选、下钻、选中对象、当前功能和截止时间，不含用户身份或可执行授权。不自动把未提交筛选表单当查询条件。

## 工作台页面上下文（可选扩展）

`page_context.version` 仍为 1，仅 `page=insights` 可携带 `bill_workspace`。旧客户端可不传。所有层级禁止额外字段；对象必须与当前功能匹配。

```json
{"version":1,"page":"insights","bill_workspace":{"section":"budgets","budget_month":"2026-10","selected":{"type":"budget","id":"本人已查询的预算ID"}}}
```

- `section`：`overview / transactions / budgets / checks / forecast / organize`。
- `subsection`：checks 下 `anomalies / subscriptions`；forecast 下 `recurring / cashflow / history`；organize 下 `merchants / rules`。二级区块同页折叠，允许多开，当前项是最近聚焦的已展开区块。
- `budget_month`：仅 budgets，合法月份且年份 2000—2099；与历史账单月份独立。
- `account_id`：独立预测账户，仅 forecast/cashflow，可为 null（全部）。账户必须归本人且符合现有预测规则。
- `selected.type`：`transaction / budget / anomaly / recurring / merchant / category_rule / tag`；`id` 只作引用。若同时传旧版顶层 `selected`，必须是同一笔 transaction。

查询工具先按本人银行数据重新解析引用，不信任页面提供的身份、金额或版本。预算、异常、周期查询支持可选 `budget_id / anomaly_id / stream_id`；目录支持 `object_id`。显式工具查询范围优先于继承的页面选择，显式 null 可清除相应继承条件。手操布局和稳定定位见 [人工监管映射](../frontend/bill_workspace_20261025.md)。

## 工具

| 工具 | 能力 |
|---|---|
| get_bill_analysis / list_transactions / get_transaction | 月/年统计、变化依据与趋势；流水、分类来源/版本、标签、退款与互转关联 |
| get_subscription_insights / get_budget_status | 订阅差异说明；净消费预算、预警及样本不足提示 |
| list_recurring_records | 分页查询周期计划与证据，继承页面账户，可显式 null 清除账户筛选 |
| project_cashflow / list_forecast_reviews | 只读现金流情景；保存的基准预测与实际收支比较 |
| get_bill_catalog | 分页查询本人商户、别名、标签、分类规则和最近50项商户变更历史；支持精确名称匹配 |
| list_bill_anomalies | 按报告筛选分页查询异常，保留核对状态、备注、版本和证据指纹 |
| preview_bill_merchant_change | 归并范围预览；拆分流水分页及所选参数绑定摘要 |
| prepare_bill_change | 生成本人确认卡；严格判别联合 Schema，只接受支持的整理/预测设置 |

模型按 Schema 理解意图及参数；新代码不使用自然语言关键词或正则分流。修改必须逐字引用本轮原文；对象、版本和摘要从查询中取得。确认接口未注册成模型工具。

## 确认卡

支持13种 action：`tags`、`classification`、`remove_category_rule`、`budget`、`anomaly_review`、`recurring_plan`、`recurring_enabled`、`merchant_alias`、`merchant_merge`、`merchant_split`、`merchant_undo`、`save_forecast`、`refresh_foundations`。

生成卡片时在回滚保存点中运行现有业务校验，不保存标签、规则、计划、预测或元数据事件；仅持久化待确认卡。卡片展示真实对象、原值和新设置；参数及用户文本加密存储。用户有15分钟核对，模型和聊天文字不能确认。

卡片响应增加可选 `fields` 数组：每项为 `{"label":"字段","value":"内容"}` 或 `{"label":"字段","before":"修改前","after":"修改后"}`。字段由服务端类型化变更和真实旧数据生成；前端按字段对照展示，`lines` 保留完整说明。旧卡无 fields 时回退展示 lines，不改变确认接口及有效期。

- `GET /api/v1/nova/conversations/{session_id}/bill-actions`：本人会话最近100张卡，含 pending/completed/cancelled/expired 状态，刷新后可恢复。
- `POST /api/v1/nova/conversations/{session_id}/bill-actions/{action_id}/decision`：请求仅含 `{"decision":"confirm"}` 或 `{"decision":"cancel"}`。禁止额外参数；实际应用的是服务端加密保存的预览。

会话及卡片均核验登录归属；用户锁和卡片锁串行化确认。同一卡片重复确认返回原结果，业务变更与完成回执原子提交。版本冲突、商户摘要变化和过期卡拒绝保存；取消后的卡不能再确认。删除对话清理卡片，不回滚已保存业务设置和审计事实。多操作队列绑定实际卡片状态，不能凭模型声称完成而解锁后续步骤。

确认成功通知 App 刷新，账单报告、预算与目录共享现有页面服务。响应丢失时重新读取原卡，不自动创建第二笔操作。

## 手操商户拆分候选

既有 `GET /api/v1/bills/merchants/{merchant_id}/split-preview` 增加 `page`（1—10000，默认 1），响应保留原预览字段，追加 `items / total / page / has_more`。每页 20 笔，仅本人当前商户的已完成流水；items 使用既有交易详情并增加 `eligible`，有银行付款身份依据的交易不可选。候选查询不保存拆分，实际拆分仍须所选参数绑定预览、摘要及服务端归属校验。

## 边界与部署

这些修改不转账、退款、支付或执行扣费，不改原始流水金额。周期预测开关不取消银行订阅。订阅取消与金融交易仍使用既有确认流程。保存预测只接受基准，不把临时假设作为校准事实。

新增 `nova_bill_actions` 表由既有 `Base.metadata.create_all` 增量创建。审计登记新表及确认路由，并补齐已有 NOVA 队列、引用和任务切换状态表的分类及路由清单。加密使用既有 `NOVA_CONVERSATION_KEY` 配置。Docker 从源码构建，运行中修改必须按 AGENTS.md 重建后端和前端。
