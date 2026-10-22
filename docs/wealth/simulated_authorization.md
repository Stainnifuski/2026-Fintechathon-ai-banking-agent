# 理财统一高风险验证（2026-10-05）

本文保留原路径以兼容文档链接，覆盖此前“点击模拟授权”的现行使用约定；历史验收报告按其记录日期保留，不代表当前行为。

## 使用与边界

理财申购、普通赎回和快赎均为红色操作：先登录并本人确认披露，再选择TOTP／短信完成验证码校验。查询仍为绿色；风险问卷本人确认不替代交易验证。前端复用SecondFactor组件，服务端复用三号 `verify_second_factor()`；短信默认mock，仅模拟发送，真实发送通道尚未接入。没有支付密码点击替身、人脸或指纹验证。

当前方案点“一键申购全部”，统一预览和本人确认，输入一次验证码后整份提交，各子单仍保留独立操作号、披露摘要和验证证据。赎回在持仓表选择份额/方式，确认后同样验证。错误验证不成交；在途份额不显示为可赎。验证成功但提交失败时保留原号恢复提交入口，不重复签发操作。

## 异常熔断

统一验证现共享用户级失败预算：10分钟内连续5次或累计10次验证码失败，跨转账、卡片、理财及短信/TOTP合并，交易锁15分钟；重发短信或切换业务不能重置。旧的理财独立审计计数限流已由公共安全状态替代。冷却结束后旧未受理授权仍失效，需重新预览、本人确认和验证；已受理订单可继续查询、结算。详见[统一契约](../security/circuit_breaker.md)。

## 接口

申购准备自动核对当前报价、可用现金及产品条件并返回交易明细，不要求用户另点“生成预览”。首次对外签发授权前，内部草稿可重新绑定当前报价；已签发的授权不覆盖参数摘要。

POST `/api/v1/wealth/plans/{plan_id}/refresh-review` 接收当前`version`，仅重新核对全部尚未提交的BUY方案。它保留本人指定的账户、产品、金额及披露版本，创建新方案版本和逐笔操作号，旧授权失效，返回`{plan,preview}`。前端在确认时遇到报价变化或授权过期会自动调用一次，更新明细并清除旧勾选及验证码；新版本仍需本人确认和统一MFA。已提交、结果未知、已完成、暂停或受人工控制的交易不能借此重新创建。资金不足返回总额、可用金额、差额及修改/补资办法；不产生订单或占款。

- 公共短信：POST `/api/v1/auth/sms/send`，`purpose=wealth`，`reference_id` 为单笔authorization_id或整份preview返回的verification_reference_id；不得使用plan_id代替集合摘要。
- GET `/api/v1/auth/sms/dev-inbox`：仅本人模拟通道收件箱；短信默认5分钟、60秒重发冷却，每小时10条，5次错误锁定，具体可配置。
- 单笔：POST `/api/v1/wealth/previews/{authorization_id}/verify`，`{proof,method:totp|sms,challenge_id?}`，省略method仍兼容旧TOTP调用。
- 整份：POST `/api/v1/wealth/plans/{plan_id}/verify`，上述字段加当前`version`。一次校验完整方案，不能遗漏或重复子单。
- 银行内部 `/verify`、`/verify/batch` 要求内部令牌；再次核对本人、有效期、当前方案版本、完整操作集合、任务控制及确认状态。
- `/verification-mode` 返回UNIFIED_MFA及可用方式。原单笔/整份及内部 `/verify/demo` 路由均删除，返回404；旧开关WEALTH_DEMO_VERIFICATION_ENABLED不再生效。

方案短信reference由完整授权ID和参数摘要生成；金额/版本变化产生新操作和新reference，不能沿用旧挑战。验证码仅提交验证入口，不进入聊天/DeepSeek。数据库存短信HMAC、逐笔方法和证据，不保存验证码明文；默认模拟收件箱是演示例外，生产需关闭。

## 数据库与升级

结构版本6扩展wealth_verifications.method约束以允许SMS，TOTP和历史SIMULATED_CLICK行完整保留。SQLite在写事务中复制校验数量后重建叶表；PostgreSQL仅替换方法CHECK；重复升级无变化。升级前停止写入、做一致性备份；普通启动或wealth_migration按现有方式执行，不清库或重置资金。

新的SMS/TOTP证据provider_version=unified-mfa-v1；SMS mock记录simulated=true、replacement_required=false，表示发送通道模拟，已经执行公共验证码校验，不代表真实运营商认证。TOTP记录simulated=false。历史SIMULATED_CLICK未受理授权一律拒绝，用户需重新生成方案/操作并确认验证；已经受理的订单仍按原操作号返回事实、照常结算，不追溯撤销或重写历史证据。

## 限制

沿用三号公共验证码服务的配置和保障；本次不是生产级安全审计，未新增真实短信供应商。Agent不能签发证明或读取模拟收件箱来代替本人验证。仅显式管理员可处置场景的种子脚本使用mock短信准备虚构夹具，其确认与验证仍标明合成场景，不是用户操作证据。
