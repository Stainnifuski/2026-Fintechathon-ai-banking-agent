# 转账任务状态机 API v0.1

本契约将用户任务（5号）与模拟银行记账（4号）分开。所有用户写操作必须携带唯一
`request_id`；一笔业务意图在创建时取得 `operation_id`，超时、查询和恢复不得更换该编号。

## 用户任务接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/tasks/transfers` | 根据结构化参数创建任务和确认卡 |
| POST | `/api/v1/tasks/messages` | 从“转给 李四 100元”创建同类任务 |
| GET | `/api/v1/tasks/{task_id}` | 查询持久化任务快照；查询不触发转账 |
| GET | `/api/v1/tasks/{task_id}/events` | 查询状态事件，可传 `since_event_id` |
| POST | `/api/v1/tasks/{task_id}/confirm` | 对指定计划版本记录用户同意 |
| POST | `/api/v1/tasks/{task_id}/verify` | 强验证接口；当前本地适配器返回 501 |
| POST | `/api/v1/tasks/{task_id}/modify` | 受理前修改参数，旧操作变为 `SUPERSEDED` |
| POST | `/api/v1/tasks/{task_id}/accept` | 原子完成应用受理，然后调度银行提交 |
| POST | `/api/v1/tasks/{task_id}/cancel` | 设置单调的取消标记；不能撤回已记账结果 |
| POST | `/api/v1/tasks/{task_id}/reconcile` | 按原 `operation_id` 查询银行事实 |
| POST | `/api/v1/tasks/{task_id}/human/claim` | 人工领取，占位为 operator/admin 权限 |
| POST | `/api/v1/tasks/{task_id}/human/return` | 人工归还并递增 `control_epoch` |
| GET | `/api/v1/operations/{operation_id}` | 查询用户可见的应用操作观察状态 |

每个快照包含 `phase`、`outcome`、`state_version`、`plan_version`、`control_mode`、
`allowed_actions`、步骤、应用操作、授权、风险占用、银行观察副本以及结构化确认卡。
客户端只能展示并调用 `allowed_actions` 中的动作。

## 模拟银行内部接口

内部接口要求请求头 `X-Internal-Token`，默认开发值为 `dev-internal-token`，部署时必须通过
`INTERNAL_BANK_TOKEN` 修改。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/internal/bank/operations` | 按 `operation_id` 幂等受理并记账 |
| GET | `/api/v1/internal/bank/operations/{operation_id}` | 返回银行状态；404 的 code 为 `NOT_FOUND` |

同一 `operation_id` 加相同参数返回同一结果；同号异参返回 409。银行内部在一个事务中完成
幂等登记、余额校验、付款扣减、收款入账和双边流水。成功凭证为独立 `bank_tx_id`。

## 当前实现范围

- 已实现：黄色风险确认、应用受理、Outbox 记录、同步调度、模拟银行记账、任务查询、取消、
  参数修改、`UNKNOWN` 核对恢复和人工执行权接口。
- 接口完整但暂未实现：真实 MFA、安全锁、独立后台 Outbox worker、SSE 推送和人工处理 UI。
- 红色风险不会被伪造为验证通过；`verify` 明确返回 501。
- 新接口以整数分保存权威金额；现有账户和展示流水仍保留元字段，以兼容演示账本。
