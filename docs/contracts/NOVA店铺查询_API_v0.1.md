# NOVA 鲜花店与蛋糕店只读查询 API v0.1

NOVA 支持两家模拟店铺的商品目录、商品详情及多件报价、本人订单列表、具体订单与银行付款状态查询。复用服务端目录、订单价格快照及页面同一套状态映射；不注册下单、付款、取消或退款工具。

## 页面上下文与身份

既有 `POST /api/v1/nova/chat` 的 page_context 在生活服务店铺页增加 shop：

```json
{
  "message": "这个商品多少钱，我选的数量总共多少钱？",
  "page_context": {
    "version": 1,
    "page": "thirdParty",
    "shop": {
      "shop": "cake",
      "selected": {"type": "product", "id": "STRAWBERRY_CREAM", "quantity": 2}
    }
  }
}
```

shop 为 flower/cake；selected 可省略，type 为 product/order，商品ID最多32字符，订单ID最多40字符。商品 quantity 可选，范围1–20；订单上下文禁止 quantity，实际数量由服务端读取。

shop 仅能放在 thirdParty 页面，不能与 calendar 同时传递。拒绝客户身份、商户身份、价格、付款状态等额外字段。前端花店/蛋糕店传递当前店铺，支持“选择商品问 NOVA”“选择订单问 NOVA”；数量修改更新选中商品报价提示，新建订单后可选中新订单。切换店铺、日历或退出登录清空原选择。

模型明确提及店铺时传相应shop，省略时可继承当前店铺；其他页面没有店铺提示也没有显式shop时返回 MISSING_SHOP，请用户说明。显式查询另一家店时不沿用当前店铺的商品/订单ID或数量。

所有工具继续通过服务端 ToolContext 注入登录身份，不接受模型传 user_id 或 merchant_user_id。商品目录对登录用户开放，订单仅查询 customer_user_id 等于当前用户的记录。不存在、其他用户及另一店铺订单统一返回 ORDER_NOT_FOUND。

## 工具

| 工具 | 参数 | 返回信息 |
|---|---|---|
| list_shop_products | shop、keyword、min_price_fen/max_price_fen、page/page_size | 名称、描述、服务端单价、目录总数与分页 |
| get_shop_product | shop、product_id、quantity | 商品详情、数量、估算总价与quote_only=true |
| list_shop_orders | shop、status、keyword、page/page_size | 本人订单，下单快照和当前付款状态；新到旧 |
| get_shop_order | shop、order_id | 本人具体订单及银行付款状态 |

shop 均可省略以继承页面。商品/订单详情省略ID时只能继承同店、同类型的选择，没有选择返回 MISSING_SELECTION，不能默认猜第一项。

商品关键词按名称和描述字面包含匹配；订单关键词按商品名称或订单号包含匹配。价格为人民币整数分，非负、上限100000000，最低价不可高于最高价；quantity 为整数1–20。page默认1、范围1–10000，page_size默认20、范围1–50；has_more说明是否还有下一页。

订单筛选status为 PENDING_PAYMENT/COMPLETED/PAYMENT_CLOSED/REFUNDED，基于当前读取投影筛选，不能仅按尚未对账的旧持久化状态过滤。

商品目录使用当前服务端价格；订单使用下单时保存的 unit_price_fen/total_fen，不被当前商品价格覆盖。get_shop_product 的 estimated_total_fen 是价目表乘数量的只读报价，不表示已创建订单或付款。

## 付款状态与只读投影

两家店现有页面查询会做持久化对账。NOVA 使用纯读取视图，以同一个 effective_order_status 函数映射状态，不执行页面对账写入：

- 银行PAID映射为订单COMPLETED。
- 银行REJECTED/CANCELLED/EXPIRED映射为PAYMENT_CLOSED。
- 其他付款状态仍为PENDING_PAYMENT。
- 已有订单终态COMPLETED/PAYMENT_CLOSED/REFUNDED保留，和页面规则一致；REFUNDED表示整单已退款，原付款记录PAID不能被解释为订单尚未退款。

银行读取在 services/bank.payment_request_service.read_payment_snapshots 内执行，同时约束当前付款人、固定店铺收款主体和本人订单引用的付款申请ID。过期的PENDING在查询时投影为EXPIRED，不写回状态、版本或时间。看不到付款记录时返回 payment_status_available=false/payment_request=null，不能编造付款成功。

订单返回 stored_status、status、status_projected；state_version是实际持久化版本，读取不制造新版本，也不作为授权凭证。客户ID、姓名、付款人姓名、幂等键、银行令牌和验证码不进入模型结果。订单创建/更新时间按已知UTC存储口径补时区。

COMPLETED仅表示模拟银行交易完成，不代表已配送。当前目录/订单没有库存、物流、真实门店地址、营业时间或优惠字段，模型不能编造这些信息，也不能声称页面有配送查询或订单修改功能。

## 返回格式与来源

```json
{
  "shop": "cake",
  "shop_name": "甜时光蛋糕店",
  "source": "shop.product",
  "data_kind": "simulated_merchant",
  "currency": "CNY",
  "amount_unit": "fen",
  "as_of": "2026-10-10T18:44:00Z",
  "data": {
    "product": {
      "product_id": "STRAWBERRY_CREAM",
      "name": "草莓鲜奶蛋糕",
      "description": "6 英寸轻盈奶油与新鲜草莓，适合 2–4 人分享。",
      "price_fen": 9900,
      "currency": "CNY"
    },
    "requested_quantity": 2,
    "estimated_total_fen": 19800,
    "quote_only": true
  }
}
```

source分别为shop.products/shop.product/shop.orders/shop.order。列表data包含items/total/page/page_size/has_more/filters；订单详情data为订单视图，payment_request仅返回必要的申请ID、金额、币种、状态、存储状态、版本与有效期。数据是模拟店铺，不能声称真实商户或真实交易。不同查询和页面加载时间可能不同，as_of用于核对查询时刻。

非法参数返回INVALID_ARGUMENTS；商品不可用返回PRODUCT_NOT_FOUND，店铺不明确MISSING_SHOP，缺对象MISSING_SELECTION，订单不可访问ORDER_NOT_FOUND。业务错误回传模型；整体HTTP协议和预算沿用 [NOVA只读工具](NOVA页面上下文与只读工具_API_v0.1.md)。

## 离线、体验与运行

有限离线支持明确店铺/当前店铺的商品目录、选中商品报价、本人订单和选中订单状态。模型模式还支持按名称和价格范围查找、两店比较及多轮查询。写操作请求不返回假成功；店铺页的账户余额和日历问题继续走原有工具。

可问“蛋糕店有哪些商品？”“鲜花店100元以内有什么花？”“这个商品我选的数量总共多少钱？”“我的蛋糕店订单付款了吗？”。在商品或订单旁选中对象后问“这个商品/订单”，模型按当前页面提示查询服务端事实。

本轮无数据库迁移或依赖变化。源码运行重启后端及前端开发服务；Docker更新执行：

```bash
docker compose up -d --build backend frontend
```

验证见 [店铺查询验收](../validation/nova_shops_20261010.md)，原有操作流程见 [花店契约](花店购买与付款联动_API_v0.1.md)、[蛋糕店契约](蛋糕店购买与付款联动_API_v0.1.md)。
