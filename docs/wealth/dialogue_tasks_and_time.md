# 理财任务、时间与测试轮次契约（开发中）

本轮依据2026-10-03 UTC修订任务书。代码已分阶段实现，E01–E40尚未全部验收，运行库尚未发布升级；实际进度见[进度记录](dialogue_ledger_progress.md)。本文不能替代验收结果。

## 任务与控制

`GET /api/v1/wealth/tasks`按本人当前资金上下文返回持久化任务；`GET /tasks/{task_id}`检查本人归属。任务包含会话、账户、上下文、版本、状态、控制归属、操作员、等待原因和固定节点依赖。模型只能产生白名单意图，不能新增SQL或执行工具。

`POST /tasks/{task_id}/{pause|resume|rollback|cancel|handoff}`请求包含`request_id`（8–64字符）、`expected_version`和可选`reason`。主体从登录认证取得，禁止客户端传主体/角色。重复原请求返回原结果，异参重放或旧版本拒绝。

| 状态/控制 | 语义 |
|---|---|
| WAITING | 等待本人确认、模拟授权或银行事实；waiting_reason区分 |
| PAUSED | 不新增提交，已提交订单继续按原号核对 |
| RECONCILING | 未知响应或取消了未提交部分但已有提交仍需核对 |
| COMPLETED | 依赖所需银行结果已确认；ACCEPTED不代表完成 |
| PARTIAL_COMPLETED | 未提交部分停止，已提交部分的银行终态已保存 |
| CANCELLED | 未提交任务取消；不反向撤销银行成交 |
| AGENT / HANDOFF_PENDING / HUMAN | 与执行状态独立的控制归属 |

先卖后买依赖实际SETTLED及重新读取同账户现金；预计赎回应收不能作为买入资金。新买入子图使用新的操作号、预览、本人确认和模拟授权。整份申购仍通过可信批量接口原子占款，worker不擅自拆批。

操作员接口`/operator/tasks`及`/{task_id}/{facts|claim|note|release}`由服务端`WEALTH_OPERATOR_USER_IDS`白名单控制（默认空）。只有领取人可以读取原任务账户事实、记处理说明和交还；不能以操作员身份替用户授权。交还后暂停并重验，旧银行受理入口同样检查任务控制。

## 北京时间与净值

`GET /api/v1/system/time`返回服务端现实UTC瞬时及业务时区`Asia/Shanghai`。顶部时钟以服务端校时加单调时钟推进；模拟业务时钟不改变现实钟、安全有效期或JWT。事件保存/传输UTC，用户展示按北京时间24小时制。

普通账户使用现实时间。只有明确隔离的本人disposable上下文可读取显式带时区的`business_at`。旧全局mock时钟不再影响普通账户。管理推进接口必须指定主体与上下文，不接受全局推进。

schema v5给净值增量增加`available_at`，导入保留数据的可用瞬时。缺少历史字段时采用北京时间净值日18:00的保守**模拟假设**，不称真实基金公布时刻。快赎报价绑定净值行、价格、可用时间、模式、账户、上下文epoch和到账金额，修改后必须重新预览确认。

## 模拟测试工具

`WEALTH_DEMO_TOOLS_ENABLED=false`为默认值。启用后仍只允许本人当前disposable轮次；普通/他人账户不能使用。

- `/demo/current`、`/demo/archives`为读取；`/demo/delete-preview`给范围计数与摘要，不做删除。
- `/demo/reset`归档当前整组run并新建20,000元账户，不复制旧授权或旧成交；完整场景fixture重建尚待验收。
- `/demo/adjust`只接受字符串正金额、最多两位小数；debit不能超过可用现金，不释放占款/在途，不重置快赎额度。
- `/demo/delete`须`confirmed=true`、匹配preview_digest及各范围expected_version；当前轮次、运行租约、未知提交、跨范围引用均阻止删除。所有检查先于同事务删除，保留共享产品/身份评估和最小删除墓碑。

写请求使用原request_id重放；前端按用户在localStorage保留未核对请求，刷新后仍用原请求核对。资金、订单、份额、会话/任务记录及独占审计删除按FK顺序，旧已删除上下文/任务回调返回GONE；其他用户不获得墓碑信息。

## 升级和历史问题

先停止目标库写入、做一致性备份并在副本运行`python -m backend.app.services.bank.wealth_migration`；重复升级只补结构/种子，不重置账户。存在业务事实时拒绝回退删表。

历史核对工具默认dry-run：`python -m backend.app.services.bank.wealth_reconciliation`。`--apply`须先经副本核对。仅同事务银行审计证明的事件瞬时可修复，原时间、业务日期和来源保存在review中；不改变现金、份额、成本或计价日期。普通账户未来计价资料不足时保留流水并限制新增写入，不能通过改日期、隐藏或清库放行。
