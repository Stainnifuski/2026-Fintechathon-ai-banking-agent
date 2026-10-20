# NOVA 转账聊天任务接口 v0.1

更新：2026-10-20。统一 NOVA 对话现在可生成即时人民币转账草稿和待本人确认的银行计划。实际确认、第二因素验证、受理和执行仍使用现有 `/api/v1/tasks/*` 状态机接口。

## 请求与响应

`POST /api/v1/nova/chat` 和 `/chat/stream` 使用 `message`、`page_context`；服务端从银行会话记录读取历史，不信任客户端提供的 `history`。转账消息另传：

```json
{
  "message": "给李四转100元",
  "request_id": "c823ea51-a961-4d2e-b570-d3952d3f9611",
  "session_id": null,
  "session_version": 0,
  "focus_task_id": null,
  "candidate_id": null
}
```

- 首轮不传 `session_id`；后续使用上轮返回的 `session_id` 和 `session_version`。每条新消息使用新 `request_id`，网络结果不确定时必须用**完全相同的请求体及编号**重试。相同编号搭配不同参数返回 `MESSAGE_ID_CONFLICT`。
- `focus_task_id` 指定补充或修改哪笔任务；有多笔可能目标时需要用户选择。`candidate_id` 选择服务端给出的脱敏收款/付款候选，不能自造账户 ID；候选约十分钟过期，并在选择时重新核验。
- 转账响应保留 NOVA 的 `reply`、`mode`、`tool_calls`，新增 `session_id`、`session_version`、`focus_task_id`、`tasks[]`。流式接口通过最终 `done` 事件返回相同结构。每个 `tasks[]` 项包含 `chat_task_id`、`status`、`form`（已知字段及脱敏号码尾号）、脱敏候选、缺失字段、可空的 `transfer_task_id` 及其权威 `task` 快照。聊天草稿没有 `transfer_task_id`。
- `POST /api/v1/bank/nova/conversations` 新建本人对话；`GET /api/v1/bank/nova/conversations` 列出本人历史；`GET /api/v1/bank/nova/conversations/{session_id}` 读取本人对话消息（敏感号码脱敏）。
- `GET /api/v1/nova/task-sessions/{session_id}` 恢复本人会话对应的任务卡；无转账任务时返回空列表。其他用户与未知会话统一返回 404。对话及任务使用同一会话 ID，前端通过银行 API 恢复历史，不依赖 `sessionStorage`。
- `POST /api/v1/nova/task-sessions/{session_id}/tasks/{chat_task_id}/form` 以 `request_id`、`session_version` 和表单字段更新任务；号码留空表示保留已知值，`clear_phone`、`clear_card` 表示清除，变更后重新生成或修改银行确认计划。
- `POST /api/v1/nova/task-sessions/{session_id}/tasks/{chat_task_id}/cancel` 以 `request_id` 和 `session_version` 取消草稿或调用银行状态机取消未执行计划。本人操作且幂等；已无法取消的任务返回冲突。

## 处理约束

模型模式先进行一次严格 Schema 的结构化路由：单一目标返回 `direct_react`；包含多个独立目标（包括同一业务的多个操作对象）或条件依赖时返回 `goal_plan` 及包含目标、依赖、完成条件的 DAG，然后进入 ReAct。单笔转账的工具集合收窄为 `manage_transfer_request` 并使用 required tool choice；执行时固定为空参数 `{}`，收款人、金额和备注只能由转账子模型读取服务端原文，不能被外层模型误填为查询派生字段。直达调用失败会立即返回明确错误，不再重试到步数耗尽。其他相同工具和参数连续出现三次时返回 `AGENT_NO_PROGRESS`，日志只记录请求号、工具名和结果码，不记录敏感参数。计划本身不构成授权或执行结果；批量转账的高层 Goal Plan 也不决定具体任务数量。单轮最多执行 10 次工具调用。

模型自主决定查询步骤及是否调用 `manage_transfer_request`，不再使用确定性意图路由器。服务端把当前焦点任务、收款人、金额、备注、付款账户和缺失字段作为可信规划上下文提供给外层模型，但不构成授权。该工具读取服务端保存的本轮原始消息，再由转账子模型同时读取**当前会话完整脱敏历史、页面提示、结构化 Goal Plan、本轮此前工具结果、可信历史引用、全部未终结转账任务摘要**与 [`transfer` Skill](../../backend/app/skills/transfer/SKILL.md)，一次输出本轮要新建或调整的任务数组。完整历史中的手机号和银行卡号发送模型前再次脱敏；页面和历史只用于理解省略、纠错与指代，本轮结构化工具结果可作为规划证据，但都不作为授权。模型模式不再先按逗号、“各转”等规则判断转账笔数；任务数量、`create/update` 及已有 `chat_task_id` 均由子模型结构化给出。Task Manager 随后校验任务归属、原文证据、最多八笔、金额、账户和候选，不能由模型改写明示字段或越权定位任务。无法唯一定位多笔待处理任务时先追问。

对于“最近一次转账金额”，外层模型须先查本人流水，并向工具同时提交来源流水 ID 与金额，服务端重新核验它确为本人的已完成转出且金额一致。对于“倒数第几笔转账的人／相应金额”等历史指代，模型先在按时间倒序的 `transfer_out` 结果中定位流水，再向 `manage_transfer_request.history_reference` 提交 `{tx_id, reuse_recipient, reuse_amount}`；服务端只接受本人的已完成转出，并从账本重新派生当前收款账户和原金额，不采信模型改写的人名或账户。对于“第二张卡”等付款账户指代，模型须先查账户并提交账户 ID，服务端重新核验归属及当前可转账状态。一句话可调用多个工具；身份、账户归属、幂等、状态机及授权仍由服务端强制校验。转账工具成功后立即以任务管理器结果结束本轮，避免重复调用及任务已落库但前端收不到卡片。离线模式保留有限确定性拆分，不提供模型式 Goal Plan 或自主任务编排。

一次消息最多八笔，一个窗口最多三十笔。模型模式由转账子模型结合原文与未终结任务判断“给甲转100，给乙转200”“给甲和乙各转100”等请求的任务数量，不依赖预先标点切分；每个提取字段仍须能在原文中复核。离线模式只支持有限明确句式。备注支持“备注为／改成／设置为”等修改表达及明确清空；修改已有任务会更新计划版本并使旧确认失效。含糊笔数或金额需重述。

已保存联系人或本人已完成转账历史可减少输入，使用前重新核验账户。新收款人需要姓名、完整手机号或完整 16 位模拟银行卡号、金额；付款账户由本人银行目录决定。联系人仅已知卡号时不自动把其内部保存的手机号当作用户已知号码。候选同名或多账户时由用户选择。每笔信息齐全后由现有 `create_transfer_task`/`modify_task` 生成确认卡；修改后生成新计划版本并使旧确认失效。

“可用余额的一半”使用银行内部 `/transfers/funding-accounts` 查询本人活跃现金账户的可用金额；多账户先选付款账户，金额按分向下取半。草稿保存相对金额意图及计算依据，补齐收款信息时重新查询；银行计划生成后固定具体金额，确认卡展示给本人核对。备注中的“一半”不触发此规则；同时给出具体金额和“一半”会要求重述。

文字“确认”“执行”不扣款。卡片上的确认按钮由本人调用现有任务 `confirm` 接口，黄级通过后调用 `accept`，红级在卡片完成 TOTP/短信 `verify` 后再调用 `accept`；模型没有这些执行工具。点击卡片叉号取消任务。任务结果通过银行状态机快照读取；未知或超时结果不能声称成功。

常见错误：`REQUEST_ID_REQUIRED`（转账消息缺幂等编号）、`STALE_SESSION_VERSION`、`MESSAGE_ID_CONFLICT`、`TASK_SELECTION_REQUIRED`、`STALE_CANDIDATE`、银行原有 `RECIPIENT_MISMATCH`/`INVALID_PHONE` 等。处理器抛错时回滚当前数据库事务；客户端应刷新会话或按原编号安全重试。

本功能创建 `nova_task_sessions`、`nova_task_message_receipts`、`nova_task_messages` 三张表，由启动时 `Base.metadata.create_all` 建表。会话保存草稿槽位，消息表只保存掩去完整手机号/卡号的文本与关联任务 ID；银行计划、授权、操作与账本仍存于现有表。
