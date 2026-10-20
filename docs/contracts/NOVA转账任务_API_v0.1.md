# NOVA 转账聊天任务接口 v0.1

更新：2026-10-20。统一 NOVA 对话现在可生成即时人民币转账草稿和待本人确认的银行计划。实际确认、第二因素验证、受理和执行仍使用现有 `/api/v1/tasks/*` 状态机接口。

## 请求与响应

`POST /api/v1/nova/chat` 和 `/chat/stream` 沿用原有 `message`、`history`、`page_context`，转账消息另传：

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
- 转账响应保留 NOVA 的 `reply`、`mode`、`tool_calls`，新增 `session_id`、`session_version`、`focus_task_id`、`tasks[]`。流式接口通过最终 `done` 事件返回相同结构。每个 `tasks[]` 项包含 `chat_task_id`、`status`、脱敏候选、缺失字段、可空的 `transfer_task_id` 及其权威 `task` 快照。聊天草稿没有 `transfer_task_id`。
- `GET /api/v1/nova/task-sessions/{session_id}` 恢复本人会话及任务卡，其他用户与未知会话统一返回 404。NOVA 面板把会话 ID 保存在当前浏览器的 `sessionStorage`；普通查询历史仍只在前端内存中。

## 处理约束

转账意图先通过确定性路由进入转账处理器，再由现有 NLU 在服务端加载 [`transfer` Skill](../../backend/app/skills/transfer/SKILL.md) 抽取字段。一次消息最多八笔，一个窗口最多三十笔。明确的“给甲转100，给乙转200”和“给甲和乙各转100”可拆为独立任务；含糊笔数或金额需重述。

已保存联系人或本人已完成转账历史可减少输入，使用前重新核验账户。新收款人需要姓名、完整手机号或完整 16 位模拟银行卡号、金额；付款账户由本人银行目录决定。联系人仅已知卡号时不自动把其内部保存的手机号当作用户已知号码。候选同名或多账户时由用户选择。每笔信息齐全后由现有 `create_transfer_task`/`modify_task` 生成确认卡；修改后生成新计划版本并使旧确认失效。

文字“确认”“执行”不扣款。聊天中的“取消”只取消未生成银行计划的草稿；已有计划须在转账任务卡使用现有取消接口。确认、短信/TOTP、受理及执行均不作为模型工具开放。任务结果通过银行状态机快照读取；未知或超时结果不能声称成功。

常见错误：`REQUEST_ID_REQUIRED`（转账消息缺幂等编号）、`STALE_SESSION_VERSION`、`MESSAGE_ID_CONFLICT`、`TASK_SELECTION_REQUIRED`、`STALE_CANDIDATE`、银行原有 `RECIPIENT_MISMATCH`/`INVALID_PHONE` 等。处理器抛错时回滚当前数据库事务；客户端应刷新会话或按原编号安全重试。

本功能创建 `nova_task_sessions`、`nova_task_message_receipts`、`nova_task_messages` 三张表，由启动时 `Base.metadata.create_all` 建表。会话保存草稿槽位，消息表只保存掩去完整手机号/卡号的文本与关联任务 ID；银行计划、授权、操作与账本仍存于现有表。
