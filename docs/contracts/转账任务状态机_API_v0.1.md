# 转账任务状态机 API v0.5

本契约将用户任务（5号）与模拟银行数据（4号）分开。5号禁止导入银行 ORM 或复用银行数据库会话，只能调用本节定义的内部 HTTP API。所有用户写操作必须携带唯一
`request_id`；一笔业务意图在创建时取得 `operation_id`，超时、查询和恢复不得更换该编号。

## 用户任务接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/tasks/transfers` | 根据结构化参数创建任务和确认卡；可选 `scheduled_at` 创建一次性定时转账 |
| POST | `/api/v1/tasks/messages` | 持久化多轮理解，返回 ChatReply（2026-09-22 变更） |
| GET | `/api/v1/tasks/{task_id}` | 查询持久化任务快照；查询不触发转账 |
| GET | `/api/v1/tasks/{task_id}/events` | 查询状态事件，可传 `since_event_id` |
| POST | `/api/v1/tasks/{task_id}/confirm` | 对指定计划版本记录用户同意 |
| POST | `/api/v1/tasks/{task_id}/verify` | 红色路径使用绑定的 TOTP 验证器证明，失败时保持未执行 |
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

一次性定时转账要求 `scheduled_at` 为带时区的未来 ISO 8601 时间。用户确认后任务进入
`WAITING_EXECUTION`，此时允许修改备注或取消；修改生成新计划并需重新确认。到期扫描器通过现有 `accept_task` 重新校验账户和风险，
再沿用原 `operation_id` 提交模拟银行。周期转账和自然语言定时创建不在当前范围内。

## 模拟银行内部接口

内部接口要求请求头 `X-Internal-Token`。必须配置独立的 `INTERNAL_BANK_TOKEN`；缺失或旧默认值失败关闭，令牌不进入浏览器。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/internal/bank/transfers/validate` | 校验付款账户归属、双方账户状态，返回收款人名称和付款用户业务日累计转出额 |
| POST | `/api/v1/internal/bank/transfers/resolve` | 按当前用户和收款人姓名解析默认付款/收款账户；不唯一时明确拒绝 |
| POST | `/api/v1/internal/bank/operations` | 按 `operation_id` 幂等受理并记账 |
| GET | `/api/v1/internal/bank/operations/{operation_id}` | 返回银行状态；404 的 code 为 `NOT_FOUND` |
| POST | `/api/v1/internal/bank/totp/verify` | 最新 main 的 TOTP 校验与防重放；5号经内部接口调用，不读银行用户表 |

同一 `operation_id` 加相同参数返回同一结果；同号异参返回 409。银行内部在一个事务中完成
幂等登记、余额校验、付款扣减、收款入账和双边流水。成功凭证为独立 `bank_tx_id`。

账户校验接口只返回编排和风险判断所需的最小事实，不向5号暴露余额表、用户表或 ORM 对象。确认卡展示字段在计划创建时固化，任务快照不回查银行数据库。

## 当前实现范围

- 已实现：黄色风险确认、应用受理、Outbox 记录、同步调度、模拟银行记账、任务查询、取消、
  参数修改、`UNKNOWN` 核对恢复和人工执行权接口。
- 最新 main 已加入 TOTP 验证器绑定、校验和防重放；未绑定或错误证明不能让红色任务执行。生产级密钥加密、跨服务原子证明和多进程审查仍待3号安全接入。
- 仍未实现：独立后台 Outbox worker、SSE 推送和人工处理 UI。
- 银行账户与流水现均以BIGINT整数分持久化；公开API仍以原元字段展示，兼容调用方。完整4号契约、升级与回退说明见[银行接口与数据库交接](../bank/接口与数据库交接_v0.5.md)。


## 2026-09-22：2号即时转账对话契约增补

`POST /api/v1/tasks/messages` 返回结构已从 TaskSnapshot 改为 ChatReply，现有前端 App.tsx 已同步适配。

请求保留 `request_id`、`operation_id`、`message`。首轮不传 session_id；续轮必须传 `session_id`、`session_version`。修改已生成任务还需要绑定 `task_id` 和当前 `state_version`。候选选择传 `candidate_id`（message 可为“选择候选”）；不得传模型猜测的收款账号。每个新消息用新 request_id/operation_id，网络重试必须复用原请求全文和编号。

返回：`session_id`、`session_version`、`mode`（offline/model）、`kind`（clarification/plan/modification/status/unsupported/error）、`message`、`candidates`、可空 `task: TaskSnapshot`。plan/modification 表示5号已保存待确认计划，不代表扣款。权威金额为 task.operation.amount_fen；任务编号、计划版本、授权和风险由5号/3号提供。

候选只暴露 `candidate_id/kind/label`，kind 为 recipient 或 payer；账户映射仅保存在服务端会话内。有效期10分钟，并绑定会话版本；选择时重新查询4号目录。跨用户会话返回404；过期/伪造候选、旧会话版本、消息编号复用不同内容返回409。会话更新、任务创建/修改、消息回执在一个事务提交；相同消息不会重复生成业务。

`GET /api/v1/tasks/conversations/{session_id}` 在可信身份下恢复版本、追问、候选和最新任务快照。自然语言确认/取消不执行写操作，要求使用现有带版本的确认/取消接口。修改未收齐参数时前端禁用旧卡片操作；服务端也阻止已持久化待消歧修改对应的旧卡片确认/受理；完整修改通过5号后旧计划和授权失效。

4号新增内部只读接口 `POST /api/v1/internal/bank/transfers/directory`，使用既有内部令牌认证；请求 `subject_user_id` 以及 `recipient_name` 或 `recipient_phone`，返回 `payers/default_payer/recipients`。每项为 `account_id/label`，label仅姓名、账户类型和脱敏尾号，不含余额。查询范围沿用现有模拟银行用户目录（不是外部通讯录）。默认付款账户沿用原 resolve 的活跃账户ID升序首个规则；适配器不提供默认值且有多账户时追问。多个同名用户/账户不自动选择。

金额兼容接口改为 Decimal 校验，推荐传元字符串；公共 yuan_to_fen 严格拒绝非有限值、零负值、超过两位小数，支持上限2,000,000,000分（存储安全边界，非风险授权限额）。旧元数字输入保持兼容。确认、修改、受理等带版本操作在事务内保留数据库版本锁；3号授权、5号执行与 UNKNOWN 语义不变。


## 2026-09-27：手机号、备注及银行防护（v0.3）

- `POST /api/v1/tasks/messages` 的提取结构增加可空 `recipient_phone`。模型仅逐字提取原文，服务端确认原文存在，再由4号规范化；手机号绝不当作金额。默认接受完整中国大陆手机号与明确 `+86`、空格、连字符格式，掩码、残缺、尾号及其他地区格式报 `INVALID_PHONE`，不存在返回空候选。手机号格式有效不等于号码所有权已验证。
- 银行目录按完整号码精确匹配。姓名与手机号同时提供时必须指向同一客户，否则 `RECIPIENT_MISMATCH`。候选仅对已认证用户的会话显示脱敏标签，10分钟过期；换号清除旧候选，选择时重新向银行核对账户映射及状态。已认证表单可调用 `POST /api/v1/users/recipients/phone`，请求 `{"phone":"13800000002"}`，响应为 `[{"account_id":"A003","label":"李四 · 手机138****0002 · 账户尾号7756"}]`；无公开全量号码查询接口，但演示环境尚无生产级查询限流。
- `POST /api/v1/tasks/transfers` 与 `/{task_id}/modify` 可带 `recipient_phone`、`note_source`；旧客户端不传仍保留旧的 `note=null` 语义。新客户端 `note_source` 为 `manual|automatic|cleared`，`cleared` 要求 `note=null`，`automatic` 且未提供内容时填入“转账”。手动备注优先；明确用途的自动备注仅使用有限确定性规则。备注最长256个字符，不截断，拒绝控制字符和纯空白；备注内容永远是数据。确认卡含 `recipient_phone_masked`、`note`、`note_source`，操作快照与双方流水保存最终备注。
- 手机号规范化值存于计划与参数摘要。创建、受理、银行提交均核对原号码当前仍属于已确认收款账户；映射变化返回 `RECIPIENT_MAPPING_CHANGED`，要求重新规划，不能自动改付其他账户。备注或手机号修改创建新计划版本、新操作号和摘要，撤销旧授权。受理后不可修改；`WAITING_EXECUTION` 的定时任务仍可修改并重新确认。
- 银行对调用方传来的 `param_digest` 重新计算并比对，覆盖账户、整数分金额、币种、备注及存在时的规范化手机号；不同业务参数复用编号返回 `IDEMPOTENCY_CONFLICT` 或 `PARAM_DIGEST_MISMATCH`。付款主体和双方状态在提交时复核。同一账户转给自己返回 `SAME_ACCOUNT`。银行操作、余额与双边流水在同一事务提交；SQLite 通过数据库写锁串行化，PostgreSQL 路径按账户ID顺序取锁（尚未实测）。
- 银行日限额按付款账户计算，用户风险阈值按其所有账户的业务日已完成转出总额加未决占用计算。业务日期默认 `Asia/Shanghai`，可由 `BANK_BUSINESS_TIMEZONE` 配置。成功交易计入银行事实；应用 `HELD` 占用防止未决并发绕过，`SETTLED` 不再重复计入，失败/取消释放，`UNKNOWN` 保留占用直至核对。跨日未决占用不因日期变化自动释放。
- 受理时3号适配器通过 `risk_subject_locks` 按用户数据库行锁串行化复评与额度占用。现有调用点是创建计划和受理前；风险适配器异常返回 `RISK_UNAVAILABLE`，非 `YELLOW/RED` 返回 `RISK_DECISION_INVALID`，两者均阻止扣款。当前黄色要求明确确认；红色需要强验证，并由 TOTP 验证器校验，未绑定或错误证明不能扣款。后续3号可替换评估端口，提供策略版本、稳定原因码和可信证明；5号负责调度与占用释放，不由银行直接读取授权表。
- 升级时新增 `user_phone_registry` 与 `risk_subject_locks` 表。前者对旧用户原号做规范化登记，检测等价号码冲突并停止，不改写客户或账务。重复执行可安全复用。回滚代码不撤销转账或自动删除表；先备份并按账务流程核对。PostgreSQL 迁移和跨进程压力测试未验证。

稳定错误包括 `INVALID_PHONE`、`PHONE_CONFLICT`、`RECIPIENT_MISMATCH`、`RECIPIENT_MAPPING_CHANGED`、`INVALID_NOTE`、`SAME_ACCOUNT`、`PAYER_FORBIDDEN`、`ACCOUNT_INACTIVE`、`CUSTOMER_INACTIVE`、`INSUFFICIENT_FUNDS`、`DAILY_LIMIT_EXCEEDED`、`PARAM_DIGEST_MISMATCH`、`IDEMPOTENCY_CONFLICT`、`BANK_BUSY`、`RISK_UNAVAILABLE`、`RISK_DECISION_INVALID`。客户端以HTTP状态和错误码决定恢复路径，不得据超时猜测成功或重建操作号。

## 理财共享兼容说明（2026-10-01）

原转账状态机、姓名/手机号/备注、定时及AA保留。银行账户新增可选available_fen/held_fen；转账最终扣款与理财占款共用账户锁和可用余额。理财订单/方案有独立状态、披露与绑定授权，不复用转账确认卡或以旧User.risk_level替代本人适当性。详见 [理财API](../wealth/contract.md) 与 [团队交接](../wealth/handoff.md)。
