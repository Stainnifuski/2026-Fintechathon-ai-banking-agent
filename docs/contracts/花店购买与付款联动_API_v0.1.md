# 花店购买与付款联动 API v0.1

NOVA 已支持商品目录、详情报价和本人订单付款状态的只读查询，接入协议见 [NOVA店铺查询](NOVA店铺查询_API_v0.1.md)。原有下单、银行确认和退款接口保持本契约。


## 1. 范围与边界

本契约描述“花语鲜花店”模拟第三方服务的 MVP。花店与银行部署在同一 FastAPI 应用中，但领域保持隔离：

- 花店拥有商品目录、订单和独立的轻量商户工作台，不复用普通个人银行首页。
- 花店只能创建收款申请，不能直接修改账户余额或调用内部银行记账接口。
- 用户必须回到银行接受申请、核对转账任务并明确确认；红色风险路径继续要求 TOTP。
- 银行转账保持单事务原子扣款/入账。花店订单通过 `payment_request_id` 读时对账，属于可重试的最终一致性，不宣称跨领域强事务。
- 日历模块使用独立契约；蛋糕店使用独立契约，不在本契约范围。

## 2. 参与方与演示数据

- 顾客：当前 JWT 登录用户。
- 商户用户：`U007`，名称“花语鲜花店”。
- 商户普通储蓄账户：`A008`。

商户 ID 和收款账户由服务端固定，浏览器不得提交或覆盖。所有账户和资金均为虚构演示数据。

## 3. 公开接口

所有接口使用 `/api/v1` 前缀并要求 Bearer JWT。

### 3.1 查询商品

`GET /flower-shop/products`

返回服务端权威商品目录。每项包含：

- `product_id`、`name`、`description`、`image_key`
- `price_fen`：整数分，记账与校验使用
- `price`：页面展示用元值
- `currency`：固定 `CNY`

### 3.2 创建订单

`POST /flower-shop/orders`

请求：

```json
{
  "product_id": "ROSE_CLASSIC",
  "quantity": 1,
  "idempotency_key": "client-generated-stable-key"
}
```

客户端不得提交金额。服务端按目录快照计算 `unit_price_fen` 和 `total_fen`，先持久化订单，再以稳定的订单级幂等键创建银行收款申请。

响应为订单快照，包含 `order_id`、商品快照、数量、金额、`status`、`state_version`、`payment_request_id` 以及 `payment_request` 摘要。初始状态为 `PENDING_PAYMENT`，关联收款申请状态为 `PENDING`。

同一用户和 `idempotency_key` 重试相同参数返回原订单；参数不同返回 `409 IDEMPOTENCY_CONFLICT`。若订单已保存但收款申请暂时创建失败，客户端必须使用原幂等键重试，不能换号重复下单。

### 3.3 查询订单

- `GET /flower-shop/orders`：按创建时间倒序返回当前用户的订单。
- `GET /flower-shop/orders/{order_id}`：返回并对账当前用户的单笔订单。

### 3.4 商户订单与退款

- `GET /flower-shop/merchant/orders`：仅花店商户 `U007` 可用，返回全部鲜花订单及付款人摘要。
- `POST /flower-shop/merchant/orders/{order_id}/refund`：请求包含订单 `state_version`，对已收款订单执行一次全额原路退款。

退款由银行校验原收款申请、商户账户归属、原付款账户和商户可用余额，并在同一事务中完成商户扣款、顾客入账及双方流水。每个 `payment_request_id` 最多成功退款一次；重复请求返回原退款结果，不重复动账。

其他用户访问订单返回 `403 ORDER_FORBIDDEN`；不存在返回 `404 ORDER_NOT_FOUND`。

## 4. 付款流程

1. 用户调用花店创建订单接口。
2. 花店以 `U007/A008` 为收款方、当前用户为付款方调用银行收款申请服务。
3. 用户回银行，在收款申请区选择本人付款账户并点击“接受申请”。
4. 银行冻结申请参数并创建绑定原 `operation_id` 的安全转账任务；此时不扣款。
5. 用户核对任务确认卡，第二次点击“确认付款”记录授权。超过风险阈值时进入 TOTP 验证。
6. 任务编排只通过受保护的内部银行接口提交。银行原子扣款、入账并把收款申请置为 `PAID`；顾客流水记为 `purchase/shopping` 且交易对手显示“花语鲜花店”，商户流水记为 `merchant_sale`。
7. 查询花店订单时以银行收款申请为权威完成对账，返回 `COMPLETED`。

自然语言“好的”、提交花店订单、前端超时或第三方回调都不能替代银行确认。

## 5. 状态映射

| 银行收款申请 | 花店订单 | 说明 |
|---|---|---|
| `PENDING`、`ACCEPTED` | `PENDING_PAYMENT` | 尚未取得确定付款成功事实 |
| `PAID` | `COMPLETED` | 银行已原子记账 |
| `REJECTED`、`CANCELLED`、`EXPIRED` | `PAYMENT_CLOSED` | 本次付款关闭；重新购买需创建新订单 |
| 已完成商户退款 | `REFUNDED` | 商户扣款与顾客退款入账已原子完成 |

转账任务处于 `UNKNOWN`、`RECONCILING` 或暂时查询失败时，订单保持 `PENDING_PAYMENT`，不得误报成功或失败。订单终态不回退。

## 6. 安全与幂等

- 商品价格、商户身份和收款账户只取服务端配置。
- 金额持久化为整数分；展示元值不参与记账。
- 订单创建幂等键绑定登录用户与 `product_id + quantity` 请求摘要。
- 收款申请幂等键由稳定 `order_id` 派生；付款沿用 `state_version`、固定 `operation_id` 和银行操作幂等规则。
- 浏览器只持有用户 JWT，不接触 `INTERNAL_BANK_TOKEN`。
- 花店服务不导入或修改银行账户、流水 ORM；订单完成依据收款申请公开事实。
- 商户退款使用稳定操作号和银行侧唯一退款事实；顾客侧流水类型为 `refund`、交易对手为“花语鲜花店”，商户侧为 `refund_out`。

## 7. 当前限制

当前版本不包含库存、配送地址、物流、优惠、部分退款、真实外部商户鉴权、独立微服务、Webhook/Outbox 实时履约，也不包含生日提醒或定时购买。退款仅支持整单原路退款。商品目录为服务端固定演示配置；订单通过查询时对账完成，后续可升级为 Outbox 消费者。
