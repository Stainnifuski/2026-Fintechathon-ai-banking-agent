# 卡片管理 API v1

状态：已实现（2026-10-03），2026-10-11 增补锁卡、注销虚拟卡、挂失补卡、渠道开关、按方向定级、信用卡已用额度与模拟用卡交易。卡片事实由4号模拟银行持有（`cards`、`card_authorizations` 表，`card_service`）；授权记录由3号策略持有（`card_operations` 表，`card_authorization`，不访问银行ORM）；预览编排由 `services/card_operations.py` 统一提供给卡片页接口和 NOVA。所有金额、状态和上限均由银行返回，前端和 Agent 工具不得自行构造或推断。

## 权限分级

级别由服务端 `card_authorization.permission_level` 判定，客户端不能指定；`CARD_PERMISSION_LEVELS` 记录每个动作的最高级别，测试保证其与共享权限表 `security/common.py` 的 `PERMISSION_MAP` 一致（后端镜像只打包 `backend/`，运行时不能导入根目录 `security` 包）。

方向型动作（交易限额、信用额度、渠道开关）按银行预检的 `raises_risk` 定级：只降低风险敞口（调低额度/限额、关闭渠道）为黄色，任何一项调高或打开即为红色。

| 动作 `action` | 说明 | 级别 | 执行条件 |
|---|---|---|---|
| `query_cards`（`GET /cards`） | 查询本人卡片 | 绿 | 登录即可 |
| `card_apply_virtual` | 申请虚拟借记卡 | 黄 | 本人确认预览摘要 |
| `card_lock` | 本人锁卡（降低风险） | 黄 | 本人确认 |
| `card_cancel_virtual` | 注销虚拟卡（终态） | 黄 | 本人确认 |
| `card_reissue` | 挂失后补发新卡 | 黄 | 本人确认 |
| `card_set_transaction_limits` | 设置单笔/单日交易限额 | 调低黄 / 调高红 | 红色另需二次验证 |
| `card_adjust_credit_limit` | 调整信用额度 | 降额黄 / 提额红 | 红色另需二次验证 |
| `card_set_channels` | 线上/ATM/境外/免密渠道开关 | 只关闭黄 / 打开红 | 红色另需二次验证 |
| `card_unlock` | 解锁卡片 | 红 | 本人确认 + TOTP/短信 |
| `card_report_lost` | 挂失（终态） | 红 | 本人确认 + TOTP/短信 |

红色复用转账同一套二次验证（`verify_second_factor`，TOTP 含防重放，或 `method="sms"` 加 `challenge_id`），不是理财的点击模拟授权。

## 接口（均需 Bearer 令牌，前缀 `/api/v1/cards`）

| 方法与路径 | 说明 |
|---|---|
| `GET ""` | 本人卡片列表 `CardView[]` |
| `POST /operations` | 预览，201。请求 `{operation_id, action, card_id?, account_id?, credit_limit?, single_txn_limit?, daily_txn_limit?, online_enabled?, atm_enabled?, overseas_enabled?, contactless_enabled?}`（金额单位元，最多两位小数）。每个动作必须且只能携带对应字段，否则422；`card_set_channels` 须同时提交四个开关 |
| `GET /operations/{id}` | 查询操作 |
| `POST /operations/{id}/confirm` | `{digest}`：黄色→银行受理→`COMPLETED`；红色→`AWAITING_MFA` |
| `POST /operations/{id}/verify` | `{proof, method?: "totp"\|"sms", challenge_id?}`：通过→银行受理→`COMPLETED` |
| `POST /operations/{id}/cancel` | 未结束的操作→`CANCELLED` |
| `GET /{card_id}/transactions` | 本卡最近20笔用卡授权 `CardAuthorizationView[]`，含被拒绝记录 |
| `POST /{card_id}/transactions` | 模拟用卡交易，201。请求 `{operation_id, amount, merchant, channel}`，`channel` 取 `pos/online/contactless/atm/overseas`；批准与拒绝都返回201，以 `authorization.approved` 区分 |

`CardView` 新增字段：`used_credit_fen`、`available_credit_fen`（信用卡，`credit_limit_fen - used_credit_fen`）、`valid_thru`（`YYYY-MM`，含当月）、四个渠道开关、`replaced_by_card_id`、`cancelled_at`；`status` 增加 `CANCELLED`。

`CardOperationView` 关键字段：`permission_level`、`requires_confirmation`、`requires_mfa`、`digest`、`changes[]`（`{field,label,before,after,unit}`，`unit="fen"` 表示金额分，渠道开关为布尔值）、`result`（受理后的卡片；补卡、申请虚拟卡为新卡）、`allowed_actions`、`expires_at`、`message`。

状态：`AWAITING_CONFIRMATION → (黄) COMPLETED | (红) AWAITING_MFA → COMPLETED`；另有 `CANCELLED`、`EXPIRED`（10分钟）、`FAILED`（银行受理拒绝或验证失败5次）。

## 绑定、幂等与版本

- 摘要绑定动作、卡片、卡片 `state_version` 和规范化参数（分）；确认时摘要不符返回 `DIGEST_MISMATCH`。
- `operation_id` 同号同参返回原预览，同号异参 `IDEMPOTENCY_CONFLICT`；已完成操作重复确认返回原结果。虚拟卡与补卡以 `source_operation_id` 保证只发一张。
- 银行受理在行锁下重新执行全部规则，并比较 `state_version`；预览后卡片变化返回 `STALE_STATE_VERSION`，该操作记为 `FAILED`，不会以新状态自动重试。
- 授权推进与银行受理分两次提交；授权已生效但受理未完成（如进程中断）时，重新调用 confirm/verify 即继续受理，不会再次要求验证码。
- 同一张卡最多同时存在3个未过期的待确认操作（`MAX_OPEN_OPERATIONS_PER_CARD`），超出返回 `CARD_OPERATION_PENDING`。每次开单时惰性清扫本人已过期的未结束操作并记为 `EXPIRED`。
- 用卡交易以 `operation_id` 幂等：同号同参返回原授权结果，同号异参 `IDEMPOTENCY_CONFLICT`。

## 银行规则（模拟银行产品参数）

- 虚拟卡：只能关联本人 `savings`/`checking` 账户；每账户最多3张未挂失、未注销的虚拟卡；初始单笔5,000元、单日20,000元，上限均为50,000元（`VIRTUAL_CARD_PRODUCT`），有效期3年。签发卡号避开已存在的卡号。
- 交易限额：`0 < 单笔 ≤ 单笔上限`，`0 < 单日 ≤ 单日上限`，单笔不高于单日，且须与当前值不同。
- 信用额度：仅 `CREDIT` 卡；`0 < 新额度 ≤ credit_limit_cap_fen`（银行核定上限），不低于 `used_credit_fen`，且须与当前值不同。
- 渠道开关：至少改变一项，否则 `CHANNELS_UNCHANGED`。新卡默认线上、ATM、免密开启，境外关闭。
- 状态：`LOST`、`CANCELLED` 为终态，除补卡外任何操作返回 `CARD_LOST`/`CARD_CANCELLED`；`LOCKED` 只能解锁、挂失或注销（虚拟卡）；锁卡、调整限额/额度/渠道要求 `ACTIVE`；注销只适用于虚拟卡；补卡要求 `LOST` 且尚未补卡，新卡继承限额、上限、渠道、信用额度及已用额度（旧卡已用额度清零），实体卡有效期5年。

## 模拟用卡交易

银行按以下顺序授权，首个不满足的规则即为拒绝原因（`decline_code`）：

1. 卡片状态：`CARD_LOCKED` / `CARD_LOST` / `CARD_CANCELLED`
2. 有效期：当前北京时间月份晚于 `valid_thru` → `CARD_EXPIRED`
3. 渠道开关：`online/atm/overseas/contactless` 对应开关关闭 → `CHANNEL_DISABLED`（`pos` 不受开关限制）
4. 单笔限额 `SINGLE_LIMIT_EXCEEDED`；本营业日（`BANK_BUSINESS_TIMEZONE`，默认北京时间）已批准金额加本笔超过单日限额 `DAILY_LIMIT_EXCEEDED`
5. 关联账户不可用 `ACCOUNT_INACTIVE`；借记卡可用现金（扣除理财占款）不足 `INSUFFICIENT_FUNDS`；信用卡可用额度不足 `CREDIT_LIMIT_EXCEEDED`

批准时：借记卡扣减关联账户余额，信用卡增加 `used_credit_fen`（不改动信用账户余额）；两者都在关联账户写入 `type=purchase` 的已完成流水（ATM 为 `channel=atm`、分类 other，其余 `channel=pos`、分类 shopping），授权记录以 `tx_id` 关联流水。拒绝时不改动余额或额度，只保留授权记录。借记卡账户处于理财待核对状态时返回 `LEDGER_RECONCILIATION_REQUIRED`（409），不留授权记录。

错误码：`CARD_NOT_FOUND` 404、`ACCOUNT_NOT_FOUND` 404、`ACCOUNT_INACTIVE` 403、`ACCOUNT_TYPE_NOT_ELIGIBLE`、`VIRTUAL_CARD_LIMIT_REACHED`、`CARD_LOST`、`CARD_CANCELLED`、`CARD_NOT_ACTIVE`、`CARD_NOT_LOCKED`、`CARD_NOT_LOST`、`CARD_ALREADY_REISSUED`、`NOT_VIRTUAL_CARD`、`NOT_CREDIT_CARD`、`STALE_STATE_VERSION`、`IDEMPOTENCY_CONFLICT`、`DIGEST_MISMATCH`、`AUTHORIZATION_EXPIRED`、`AUTHORIZATION_WRONG_STATE`、`CARD_OPERATION_PENDING`（409）；`CREDIT_LIMIT_OUT_OF_RANGE`、`CREDIT_LIMIT_UNCHANGED`、`CREDIT_LIMIT_BELOW_USED`、`TXN_LIMIT_OUT_OF_RANGE`、`TXN_LIMIT_INCONSISTENT`、`TXN_LIMIT_UNCHANGED`、`CHANNELS_UNCHANGED`（422）；`TOTP_NOT_ENROLLED` 409、`TOTP_INVALID` 401、`TOTP_REPLAY` 409、`VERIFICATION_LOCKED` 403；`CARD_NUMBER_EXHAUSTED` 503。

## 审计

授权侧写入 `intent=card_management`、`permission_level=yellow|red` 的记录：确认（`_confirm`）、验证成功（`_verify`，`mfa_method=totp|sms`）、每次验证失败（`_verify_failed_N`，result=failed）、验证锁定（`_verify_locked`，blocked）、取消（`_cancel`，cancelled）、过期（`_expire`，cancelled）、银行受理拒绝（`_bank_rejected`，failed）。银行侧以操作号写入 `permission_level=delegated` 的受理事实及前后值；用卡交易写入 `action=card_purchase`，批准为 success、拒绝为 blocked 并附拒绝原因。不记录验证码。

## 旧库升级

`init_db` 调用 `upgrade_card_columns` 为旧 `cards` 表补齐新增列（已用额度默认0、有效期为空即不校验、渠道开关取产品默认值）；`card_authorizations` 为新表，由 `create_all` 创建。

## Agent 与 NOVA

- `agent/tools/card_tool.py` 只提供 `query_cards` 和 `preview_card_operation`。
- NOVA 注册 `preview_card_operation`（见 [NOVA 卡片与订阅](NOVA卡片与订阅查询_API_v0.1.md)），与卡片页共用 `open_preview`，操作号前缀 `NOVA-`。
- 模型不能确认、验证或签发授权，也不能发起模拟用卡交易；确认卡片在可信页面渲染。

## 演示种子与已知限制

- 张三（U001）：`CARD001` 借记卡（A001，正常，有效期2029-08）；`CARD002` 信用卡（A002，额度20,000元、已用3,200元、核定上限50,000元，初始锁定，有效期2028-12），用于演示先解锁再调额、降额不能低于已用。
- 未建模：信用卡还款与账单周期（已用额度只增不减，可通过补卡转移）、实体卡寄送与激活、查看完整卡号/CVV、商户退款冲正已用额度。
