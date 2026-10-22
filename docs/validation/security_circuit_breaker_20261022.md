# 异常熔断验收（2026-10-22）

## 实现和自审

现行契约：[异常熔断与安全锁定](../security/circuit_breaker.md)。本轮只增量创建 `security_states`，未连接或升级现有业务库、未重置账本、未提交或发布。保留工作区其他正在进行的幻觉防护、审计和理财改动。

已实现登录限制、跨业务/验证方式的交易锁、明确授权违规锁及注入会话暂停。最终银行受理覆盖转账、理财、卡片、订阅；定时任务不会在解锁后自动恢复。旧授权失效边界独立于用户业务状态，重复成功、未知结果查询及已受理结算继续可用。

自审修订：

1. 验证失败先回滚业务，再保存次数及安全审计；避免旧短信错误路径意外提交调用方变更。
2. 早期编排检查不持安全锁跨HTTP请求；最终银行门禁持行锁直至账务提交。
3. 并发安全事件只触发一次锁，锁中额外请求不延长时间。
4. 连续次数受滚动窗口内有效事件数约束；成功登录重置登录预算，成功MFA只重置连续次数、保留窗口总数。
5. 冷却后旧未受理授权仍不能扣款；定时任务记录阻断原因、不自动重试。
6. 调度器逐条重新读取任务，避免一次回滚使剩余ORM对象过期后引发隐式异步读取错误。
7. 登录锁/会话暂停不展示为用户交易冻结；验证码错误不清理JWT。
8. 验证器绑定先取得安全门禁再重新读取并锁定用户，防止并发读取旧密钥后确认新绑定。

## 测试结果

合并回归：**146 passed，20 skipped，0 failed**。新增安全用例中23项在SQLite通过，4个PostgreSQL参数用例因未配置独立验收库跳过。其他跳过同样保留各原有用例的条件，不宣称全环境验收完成。

覆盖：阈值、时间窗口、成功重置与累计上限；短信/TOTP及不同用途合并；业务回滚后的持久计数；失败不会提交余额变更；未知用户与已注册用户一致的登录锁响应；HTTP恢复时间/Retry-After；会话注入暂停；连续不同工具失败；真实授权越权与正常余额不足的区分；锁中允许主动锁卡/取消订阅及取消定时任务；原号结果恢复；冷却后旧授权失效；并发8次失败只产生一次锁；重新创建数据库引擎并重复建表后状态保留；银行提交与安全锁竞争时的提交顺序。

合并命令（内部凭据是测试占位值，不连接真实银行或模型）：

```sh
INTERNAL_BANK_TOKEN=test-internal-token .venv/bin/python -m pytest \
  tests/security/test_circuit_breaker.py tests/security/test_wealth_circuit_breaker.py \
  tests/security/test_sms_2fa.py tests/unit/test_bank_boundary.py \
  tests/unit/test_nova.py tests/unit/test_nova_stream.py tests/unit/test_subscriptions.py \
  tests/integration/test_cards.py tests/integration/test_cards_controls.py \
  tests/integration/test_task_transfer_flow.py tests/integration/test_wealth_instant.py \
  tests/integration/test_existing_feature_guards.py tests/integration/test_phone_bank_safeguards.py \
  tests/integration/test_bank_readiness.py tests/integration/test_nova_conversation_history.py \
  tests/integration/test_hallucination_guards.py -q --disable-warnings --maxfail=4
```

前端登录会话分类回归：**3 passed**，确认短信/TOTP错误、登录凭据错误和安全锁不误清登录状态，真正JWT认证失败仍要求重新登录。TypeScript检查及 `npm run build` 通过；差异空白检查通过。

```sh
node --experimental-strip-types --test frontend/tests/sessionAuthenticationSecurity.test.ts
cd frontend
npm run build
```

## 原有失败与未验证项

另外尝试 `tests/integration/test_wealth_unified_authorization.py` 时发现4项原有SQLite用例失败：三个整份授权用例在取旧预览 `items` 时失败，一个旧成交夹具返回 `CONTEXT_REQUIRED`。将未修改的Git HEAD导出到独立临时目录，在同一测试环境重新执行，四项出现相同失败；这批旧夹具尚未适配当前绑定账户、即时成交要求。本轮另用有效的当前理财流程覆盖安全锁及统一MFA，不改动这四个旧用例掩盖失败。

NOVA部分接口测试最初返回 `INTERNAL_BANK_TOKEN_MISSING`；使用专用测试凭据重跑，相关用例通过，不放宽生产内部鉴权。

没有运行全仓库所有用例、真实模型/真实短信、生产数据库升级或真实PostgreSQL并发验收。SQLite并发和持久化已验证；PostgreSQL使用相应方言的冲突安全插入及行锁实现，并保留可在独立测试库执行的用例。测试完成后没有重启现有后端或执行交易演示；新版后端启动时按现有初始化流程增量建表。
