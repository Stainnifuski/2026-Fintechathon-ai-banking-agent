# 转账任务状态机 API v0.2

本契约将用户任务（5号）与模拟银行数据（4号）分开。5号禁止导入银行 ORM 或复用银行数据库会话，只能调用本节定义的内部 HTTP API。所有用户写操作必须携带唯一
`request_id`；一笔业务意图在创建时取得 `operation_id`，超时、查询和恢复不得更换该编号。

## 用户任务接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/tasks/transfers` | 根据结构化参数创建任务和确认卡 |
| POST | `/api/v1/tasks/messages` | 持久化多轮理解，返回 ChatReply（2026-09-22 变更） |
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
| POST | `/api/v1/internal/bank/transfers/validate` | 校验付款账户归属、双方账户状态，返回收款人名称和付款账户累计转账额 |
| POST | `/api/v1/internal/bank/transfers/resolve` | 按当前用户和收款人姓名解析默认付款/收款账户；不唯一时明确拒绝 |
| POST | `/api/v1/internal/bank/operations` | 按 `operation_id` 幂等受理并记账 |
| GET | `/api/v1/internal/bank/operations/{operation_id}` | 返回银行状态；404 的 code 为 `NOT_FOUND` |

同一 `operation_id` 加相同参数返回同一结果；同号异参返回 409。银行内部在一个事务中完成
幂等登记、余额校验、付款扣减、收款入账和双边流水。成功凭证为独立 `bank_tx_id`。

账户校验接口只返回编排和风险判断所需的最小事实，不向5号暴露余额表、用户表或 ORM 对象。确认卡展示字段在计划创建时固化，任务快照不回查银行数据库。

## 当前实现范围

- 已实现：黄色风险确认、应用受理、Outbox 记录、同步调度、模拟银行记账、任务查询、取消、
  参数修改、`UNKNOWN` 核对恢复和人工执行权接口。
- 接口完整但暂未实现：真实 MFA、安全锁、独立后台 Outbox worker、SSE 推送和人工处理 UI。
- 红色风险不会被伪造为验证通过；`verify` 明确返回 501。
- 新接口以整数分保存权威金额；现有账户和展示流水仍保留元字段，以兼容演示账本。


## 2026-09-22：2号即时转账对话契约增补

`POST /api/v1/tasks/messages` 返回结构已从 TaskSnapshot 改为 ChatReply，现有前端 App.tsx 已同步适配。

请求保留 `request_id`、`operation_id`、`message`。首轮不传 session_id；续轮必须传 `session_id`、`session_version`。修改已生成任务还需要绑定 `task_id` 和当前 `state_version`。候选选择传 `candidate_id`（message 可为“选择候选”）；不得传模型猜测的收款账号。每个新消息用新 request_id/operation_id，网络重试必须复用原请求全文和编号。

返回：`session_id`、`session_version`、`mode`（offline/model）、`kind`（clarification/plan/modification/status/unsupported/error）、`message`、`candidates`、可空 `task: TaskSnapshot`。plan/modification 表示5号已保存待确认计划，不代表扣款。权威金额为 task.operation.amount_fen；任务编号、计划版本、授权和风险由5号/3号提供。

候选只暴露 `candidate_id/kind/label`，kind 为 recipient 或 payer；账户映射仅保存在服务端会话内。有效期10分钟，并绑定会话版本；选择时重新查询4号目录。跨用户会话返回404；过期/伪造候选、旧会话版本、消息编号复用不同内容返回409。会话更新、任务创建/修改、消息回执在一个事务提交；相同消息不会重复生成业务。

`GET /api/v1/tasks/conversations/{session_id}` 在可信身份下恢复版本、追问、候选和最新任务快照。自然语言确认/取消不执行写操作，要求使用现有带版本的确认/取消接口。修改未收齐参数时前端禁用旧卡片操作；服务端也阻止已持久化待消歧修改对应的旧卡片确认/受理；完整修改通过5号后旧计划和授权失效。

4号新增内部只读接口 `POST /api/v1/internal/bank/transfers/directory`，使用既有内部令牌认证；请求 `subject_user_id/recipient_name`，返回 `payers/default_payer/recipients`。每项为 `account_id/label`，label仅姓名、账户类型和脱敏尾号，不含余额。查询范围沿用现有模拟银行用户目录（不是外部通讯录）。默认付款账户沿用原 resolve 的活跃账户ID升序首个规则；适配器不提供默认值且有多账户时追问。多个同名用户/账户不自动选择。

金额兼容接口改为 Decimal 校验，推荐传元字符串；公共 yuan_to_fen 严格拒绝非有限值、零负值、超过两位小数，支持上限2,000,000,000分（存储安全边界，非风险授权限额）。旧元数字输入保持兼容。确认、修改、受理等带版本操作在事务内保留数据库版本锁；3号授权、5号执行与 UNKNOWN 语义不变。
