# 卡片管理 API v1

状态：已实现（2026-10-03）。卡片事实由4号模拟银行持有（`cards` 表，`card_service`）；授权记录由3号策略持有（`card_operations` 表，`card_authorization`，不访问银行ORM）。所有金额、状态和上限均由银行返回，前端和 Agent 工具不得自行构造或推断。

## 权限分级

级别由服务端 `CARD_PERMISSION_LEVELS`（`backend/app/integrations/card_authorization.py`）判定，客户端不能指定；测试保证其与共享权限表 `security/common.py` 的 `PERMISSION_MAP` 一致（后端镜像只打包 `backend/`，运行时不能导入根目录 `security` 包）。

| 动作 `action` | 说明 | 级别 | 执行条件 |
|---|---|---|---|
| `query_cards`（`GET /cards`） | 查询本人卡片 | 绿 | 登录即可 |
| `card_apply_virtual` | 申请虚拟借记卡 | 黄 | 本人确认预览摘要 |
| `card_set_transaction_limits` | 设置单笔/单日交易限额 | 黄 | 本人确认预览摘要 |
| `card_adjust_credit_limit` | 调整信用额度 | 红 | 本人确认 + TOTP |
| `card_unlock` | 解锁卡片 | 红 | 本人确认 + TOTP |
| `card_report_lost` | 挂失（终态） | 红 | 本人确认 + TOTP |

红色复用转账同一套 TOTP（`verify_totp_for_operation`，含防重放），不是理财的点击模拟授权。

## 接口（均需 Bearer 令牌，前缀 `/api/v1/cards`）

| 方法与路径 | 说明 |
|---|---|
| `GET ""` | 本人卡片列表 `CardView[]` |
| `POST /operations` | 预览，201。请求 `{operation_id, action, card_id?, account_id?, credit_limit?, single_txn_limit?, daily_txn_limit?}`（金额单位元，最多两位小数）。每个动作必须且只能携带对应字段，否则422 |
| `GET /operations/{id}` | 查询操作 |
| `POST /operations/{id}/confirm` | `{digest}`：黄色→银行受理→`COMPLETED`；红色→`AWAITING_MFA` |
| `POST /operations/{id}/verify` | `{proof: "6位数字"}`：TOTP通过→银行受理→`COMPLETED` |
| `POST /operations/{id}/cancel` | 未结束的操作→`CANCELLED` |

`CardOperationView` 关键字段：`permission_level`、`requires_confirmation`、`requires_mfa`、`digest`、`changes[]`（`{field,label,before,after,unit}`，`unit="fen"` 表示金额分）、`result`（受理后的卡片）、`allowed_actions`、`expires_at`、`message`。

状态：`AWAITING_CONFIRMATION → (黄) COMPLETED | (红) AWAITING_MFA → COMPLETED`；另有 `CANCELLED`、`EXPIRED`（10分钟）、`FAILED`（银行受理拒绝或验证失败5次）。

## 绑定、幂等与版本

- 摘要绑定动作、卡片、卡片 `state_version` 和规范化参数（分）；确认时摘要不符返回 `DIGEST_MISMATCH`。
- `operation_id` 同号同参返回原预览，同号异参 `IDEMPOTENCY_CONFLICT`；已完成操作重复确认返回原结果。虚拟卡以 `source_operation_id` 保证只发一张。
- 银行受理在行锁下重新执行全部规则，并比较 `state_version`；预览后卡片变化返回 `STALE_STATE_VERSION`，该操作记为 `FAILED`，不会以新状态自动重试。
- 授权推进与银行受理分两次提交；授权已生效但受理未完成（如进程中断）时，重新调用 confirm/verify 即继续受理，不会再次要求验证码。

## 银行规则（模拟银行产品参数）

- 虚拟卡：只能关联本人 `savings`/`checking` 账户；每账户最多3张未挂失虚拟卡；初始单笔5,000元、单日20,000元，上限均为50,000元（`VIRTUAL_CARD_PRODUCT`）。
- 交易限额：`0 < 单笔 ≤ 单笔上限`，`0 < 单日 ≤ 单日上限`，单笔不高于单日，且须与当前值不同。
- 信用额度：仅 `CREDIT` 卡；`0 < 新额度 ≤ credit_limit_cap_fen`（银行核定上限），且须与当前值不同。
- 状态：`LOST` 为终态，任何操作返回 `CARD_LOST`；`LOCKED` 只能解锁或挂失；解锁要求当前为 `LOCKED`。

错误码：`CARD_NOT_FOUND` 404、`ACCOUNT_NOT_FOUND` 404、`ACCOUNT_INACTIVE` 403、`ACCOUNT_TYPE_NOT_ELIGIBLE`、`VIRTUAL_CARD_LIMIT_REACHED`、`CARD_LOST`、`CARD_NOT_ACTIVE`、`CARD_NOT_LOCKED`、`NOT_CREDIT_CARD`、`STALE_STATE_VERSION`、`IDEMPOTENCY_CONFLICT`、`DIGEST_MISMATCH`、`AUTHORIZATION_EXPIRED`、`AUTHORIZATION_WRONG_STATE`（409）；`CREDIT_LIMIT_OUT_OF_RANGE`、`CREDIT_LIMIT_UNCHANGED`、`TXN_LIMIT_OUT_OF_RANGE`、`TXN_LIMIT_INCONSISTENT`、`TXN_LIMIT_UNCHANGED`（422）；`TOTP_NOT_ENROLLED` 409、`TOTP_INVALID` 401、`TOTP_REPLAY` 409、`VERIFICATION_LOCKED` 403。

## 审计

授权侧写入 `intent=card_management`、`permission_level=yellow|red` 的确认/验证记录（`mfa_method=totp`）；银行侧以操作号写入 `permission_level=delegated` 的受理事实及前后值。不记录验证码。

## Agent 工具

`agent/tools/card_tool.py` 只提供 `query_cards` 和 `preview_card_operation`；模型不能确认、验证或签发授权，确认卡片在可信页面渲染。

## 演示种子与已知限制

- 张三（U001）：`CARD001` 借记卡（A001，正常）；`CARD002` 信用卡（A002，额度20,000元、核定上限50,000元，初始锁定），用于演示先解锁再调额。
- 未建模：信用卡已用额度/欠款（降额不校验欠款）、刷卡交易对限额的实际扣减、挂失后补卡、实体卡寄送。聊天对话目前仍只处理即时转账，不识别卡片意图。
