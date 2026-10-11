# NOVA 卡片与订阅查询及卡片操作预览

更新：2026-10-11。复用现有卡片和订阅查询服务，注册5个工具：4个只读查询，外加只生成待确认单的 `preview_card_operation`。所有查询身份来自JWT，不接收模型或客户端的user_id。

## 页面上下文

POST `/api/v1/nova/chat` 可使用以下上下文（沿用version=1）：

```json
{"version":1,"page":"cards","cards":{"selected_card_id":"C1"}}
```

```json
{"version":1,"page":"subscriptions","subscriptions":{"selected_subscription_id":"SUB1"}}
```

ID可省略；卡片ID长度1–32，订阅ID长度1–40。字段只允许出现在对应页面，拒绝额外字段和客户端事实。点击页面的“选择供 NOVA 查询”设置选择，再点击清空；重新进入页面或退出登录清空选择。选择按钮不执行操作，也不自动发送聊天。请求使用发送时的选择；详情查询总是读取最新银行事实。

## 工具

| 工具 | 参数 | 结果 |
|---|---|---|
| list_cards | status: ACTIVE/LOCKED/LOST/CANCELLED；card_type: DEBIT/CREDIT；is_virtual: bool；last_four: 四位字符串；page/page_size | 本人卡片列表、总数、分页和筛选条件 |
| get_card | card_id，可省略 | 省略时使用页面选中卡片；详情含状态、关联账户、信用额度及已用/可用额度、交易限额及银行上限、有效期、渠道开关 |
| preview_card_operation | action 及该动作的字段（见下文），card_id 可省略 | 开出待本人确认的卡片操作单，不执行 |
| list_subscriptions | status: ACTIVE/CANCELLED；keyword: 商户关键词1–64字符；page/page_size | 本人订阅列表、总数、筛选条件、预计月费汇总 |
| get_subscription | subscription_id，可省略 | 省略时使用页面选中订阅；商户、周期金额、扣费账户、计划扣费日期、状态、取消时间、识别来源流水 |

分页默认page=1/page_size=20，大小1–50，页码1–10000。金额只返回整数分，不返回旧接口的浮点amount。日期带UTC时区。卡号及账户标签只保留末四位。不返回身份材料、完整卡号、确认凭证或操作授权记录。参数严格校验，额外参数拒绝。

结果包裹source（cards.list/cards.detail/subscriptions.list/subscriptions.detail）、data_kind=simulated_bank、currency=CNY、amount_unit=fen、as_of与data。订阅行自身currency为单项币种，模型不得将非人民币订阅金额当人民币。

订阅列表summary基于**筛选后所有分页**，包含active_count、cancelled_count、monthly_commitment_fen、scope和basis。月费仅汇总ACTIVE/MONTHLY/CNY记录，是预计周期支出，不是已扣款；实际支出使用账单流水工具。取消项保留历史计划日期，但不能解释成未来扣费。next_charge_at仅是计划信息，不代表后台一定自动扣款。

卡片信用额度、已用额度、可用额度（available_credit_fen）与银行核定上限、交易限额分别返回；可用信用额度以银行返回值为准，不能自行推算或当作现金。列表与详情都同时验证卡片/订阅及关联账户属于本人。没有选择返回MISSING_SELECTION；不存在和越权统一CARD_NOT_FOUND/SUBSCRIPTION_NOT_FOUND。模型按末四位或商户查询先走列表，多项匹配需询问，不默认选第一项。

## 卡片操作预览

`preview_card_operation` 与卡片页 `POST /api/v1/cards/operations` 共用 `services/card_operations.open_preview`：同一套银行预检、服务端定级、摘要绑定、每卡未结束操作上限，操作号为 `NOVA-` 前缀。参数：

- `action`：`card_apply_virtual`（`account_id`）、`card_lock`/`card_unlock`/`card_report_lost`/`card_cancel_virtual`/`card_reissue`（`card_id`）、`card_set_transaction_limits`（`single_txn_limit`/`daily_txn_limit`）、`card_adjust_credit_limit`（`credit_limit`）、`card_set_channels`（`online_enabled`/`atm_enabled`/`overseas_enabled`/`contactless_enabled`）。
- 金额为元的字符串，最多两位小数。`card_id` 省略时沿用 `cards.selected_card_id`，都没有返回 `MISSING_SELECTION`。
- 设置限额只给一项、设置渠道只给部分开关时，其余字段取银行当前值补全；摘要仍绑定完整参数。
- 字段与动作不匹配返回 `INVALID_ARGUMENTS`；银行规则拒绝返回对应错误码（如 `CARD_NOT_ACTIVE`、`CARD_NOT_FOUND`），不开单。

结果 source=`cards.operation_preview`，data 含 operation_id、action、action_label、status=`AWAITING_CONFIRMATION`、permission_level、requires_mfa、card_id、脱敏 card_number、changes（关联账户只保留末四位）、expires_at、`executed=false` 和 next_step。不返回确认摘要 digest。

NOVA 不注册确认、验证、取消订阅、扣费或用卡交易工具。聊天面板对成功的预览显示“去卡片页核对并确认”，点击后打开卡片页并载入该操作，由本人确认；红色操作还需验证码。卡片接口按 JWT 校验归属，他人无法查看或确认。离线规则不生成预览，锁卡、补卡、开关渠道、调额等操作说法不会被当作查询回答。

取消订阅、模拟扣费仍使用业务工作台。离线规则支持有限的列表、状态/类型/末四位和选中详情查询；自然语言商户定位、复杂筛选、卡片操作预览与多轮推理需要模型。

验证见 [本轮验证记录](../validation/nova_cards_subscriptions_20261010.md)。
