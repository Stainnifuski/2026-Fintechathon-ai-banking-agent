# NOVA 分层规划与转账任务编排验证（2026-10-20）

## 本次范围

- 单一目标直接进入 ReAct；多个独立目标或条件依赖先通过 `declare_goal_plan` 登记结构化 DAG。
- Goal Plan 必须作为第一次且唯一一次工具调用，登记后从后续工具集合移除；计划不授权银行操作。
- 转账子模型接收本轮原文、转账 Skill 和当前会话全部未终结任务的脱敏摘要，一次输出 `create/update` 任务数组。
- 转账子模型同时接收当前会话完整脱敏历史、页面上下文、Goal Plan 和本轮此前工具结果；测试验证历史手机号在子模型调用前被再次隐藏。
- 模型模式不再先用标点、连词或“各转”规则决定转账任务数量；Task Manager 继续校验任务归属、原文证据、数量上限、账户和金额。
- `manage_transfer_request.history_reference` 支持复用本人已完成历史转出的收款账户和／或金额；测试只向工具提交流水 ID，收款账户及金额由服务端重新派生。
- 前置结构化路由将单笔转账收窄为唯一 required 工具 `manage_transfer_request`；相同调用第三次出现时以 `AGENT_NO_PROGRESS` 熔断，并仅记录工具名和结果码。
- 真实请求日志确认外层模型曾把原文金额误填进查询派生字段，导致连续 `INVALID_ARGUMENTS`；单笔直达现在强制把执行参数规范化为 `{}`，定向测试覆盖模型传入非法 `amount_fen` 仍按空参数执行。

## 自动验证

- `python -m pytest tests/integration/test_nova_transfer_manager.py -q -k 'not legacy_transfer_account_code'`
  - 结果：27 passed，1 deselected。
- 定向覆盖 Goal Plan 顺序、转账子模型自主任务数量、全部未终结任务上下文及查询派生金额/付款账户复核：
  - 结果：4 passed。
- IDE 诊断：本次修改的 Python 文件无新增诊断。
- 真实配置只读模型检查：`给李四转100元` 路由为 `direct_react/transfer`；required 单工具调用返回 `manage_transfer_request`。
- 最新后端镜像重建后 `/health` 为 `ok`，`portfolio_ready=true`。

未纳入上述通过数的既有 `test_legacy_transfer_account_code_is_displayed_as_verified_person_name` 当前因调用已变更的 `bill_service.transactions(..., now=...)` 参数而失败，与本次编排改动无关。全量单元组合另有既有数据库连接跨事件循环及已移除 `settlement_loop` 的测试夹具问题，本次未把这些失败记为通过。
