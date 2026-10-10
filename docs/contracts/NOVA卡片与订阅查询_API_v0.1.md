# NOVA 卡片与订阅只读查询

更新：2026-10-10。复用现有卡片和订阅查询服务，注册4个工具。无需迁移数据库。所有查询身份来自JWT，不接收模型或客户端的user_id。

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
| list_cards | status: ACTIVE/LOCKED/LOST；card_type: DEBIT/CREDIT；is_virtual: bool；last_four: 四位字符串；page/page_size | 本人卡片列表、总数、分页和筛选条件 |
| get_card | card_id，可省略 | 省略时使用页面选中卡片；详情含状态、关联账户、信用额度、交易限额及银行上限 |
| list_subscriptions | status: ACTIVE/CANCELLED；keyword: 商户关键词1–64字符；page/page_size | 本人订阅列表、总数、筛选条件、预计月费汇总 |
| get_subscription | subscription_id，可省略 | 省略时使用页面选中订阅；商户、周期金额、扣费账户、计划扣费日期、状态、取消时间、识别来源流水 |

分页默认page=1/page_size=20，大小1–50，页码1–10000。金额只返回整数分，不返回旧接口的浮点amount。日期带UTC时区。卡号及账户标签只保留末四位。不返回身份材料、完整卡号、确认凭证或操作授权记录。参数严格校验，额外参数拒绝。

结果包裹source（cards.list/cards.detail/subscriptions.list/subscriptions.detail）、data_kind=simulated_bank、currency=CNY、amount_unit=fen、as_of与data。订阅行自身currency为单项币种，模型不得将非人民币订阅金额当人民币。

订阅列表summary基于**筛选后所有分页**，包含active_count、cancelled_count、monthly_commitment_fen、scope和basis。月费仅汇总ACTIVE/MONTHLY/CNY记录，是预计周期支出，不是已扣款；实际支出使用账单流水工具。取消项保留历史计划日期，但不能解释成未来扣费。next_charge_at仅是计划信息，不代表后台一定自动扣款。

卡片信用额度与银行核定上限、交易限额分别返回，不能当作可用现金或推算剩余信用额度。列表与详情都同时验证卡片/订阅及关联账户属于本人。没有选择返回MISSING_SELECTION；不存在和越权统一CARD_NOT_FOUND/SUBSCRIPTION_NOT_FOUND。模型按末四位或商户查询先走列表，多项匹配需询问，不默认选第一项。

仅注册只读工具，不接入取消订阅、扣费、卡片申请/调额/限额修改/解锁/挂失。写操作仍使用业务工作台的确认和验证流程。离线规则支持有限的列表、状态/类型/末四位和选中详情查询；自然语言商户定位、复杂筛选与多轮推理需要模型。

验证见 [本轮验证记录](../validation/nova_cards_subscriptions_20261010.md)。
