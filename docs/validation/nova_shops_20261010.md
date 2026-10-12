# NOVA 模拟鲜花店与蛋糕店查询验收（2026-10-10）

本轮新增list_shop_products/get_shop_product/list_shop_orders/get_shop_order及shop页面上下文。商品和订单页面增加选择按钮、当前店铺/选中商品报价/本人订单状态快捷提问。工具只读，不接入下单、付款、取消或退款。

## 自动验证

- `python -m pytest tests/unit/test_nova.py tests/unit/test_llm_service.py tests/unit/test_ai_understanding.py -q`：99 passed。
- `pytest tests/integration/test_nova_shops.py tests/unit/test_flower_shop.py tests/unit/test_cake_shop.py tests/integration/test_flower_shop_payment_flow.py tests/integration/test_cake_shop_payment_flow.py tests/integration/test_nova_reads.py tests/integration/test_nova_calendar.py -q`：43 passed，隔离SQLite，沙箱外执行。
- 最后调整店铺页余额/日历离线路由和订单DTO字段白名单后，`pytest tests/integration/test_nova_shops.py -q`：20 passed。
- frontend下npm run build：TypeScript/Vite通过，仍有既有大包提示。
- git diff --check通过。

覆盖：两家店目录与页面价格/描述一致；100元价格筛选、选中商品两件报价、报价不创建订单或扣款；订单价格不随当前目录变价；新到旧分页、总数及has_more；本人/他人/跨店订单隔离；缺店铺、缺选择、非法数量和客户端身份拒绝；银行付款人/店铺主体与付款申请绑定校验；缺付款信息不能编造成功；付款PENDING/ACCEPTED/PAID/REJECTED/CANCELLED/EXPIRED、只读过期投影和REFUNDED优先级；读取前后订单状态/版本/更新时间、付款状态/版本与账户余额不变；模型输入不包含客户身份字段；JWT HTTP工具闭环和店铺页面协议；店铺页账户余额、日历问题仍走原工具。

现有两店下单、付款、退款集成和NOVA账单/日历回归通过。原订单对账仍写回状态；本轮提取effective_order_status供页面对账与NOVA读取共用，NOVA不调用原写回流程。

首轮测试的退款用例读取了异步ORM尚未刷新的更新时间，另一个错误付款关联样例违反订单付款申请唯一约束；修正为显式刷新及未关联的他人收款申请后通过，未为测试修改业务约束。

## 真实模型联调

项目现有模型配置 + 独立内存合成用户、模拟商品和订单，未访问运行客户或运行数据库。

| 问题 | 实际工具 | 结果 |
|---|---|---|
| 蛋糕店有哪些商品？ | list_shop_products | 草莓99元、巧克力199元、双层1299元，规格与服务端一致 |
| 鲜花店100元以内有什么花？ | list_shop_products | 经典红玫瑰99元，符合条件1项 |
| 这款蛋糕我选的数量总共多少钱？ | get_shop_product | 沿用选中草莓蛋糕及数量2，报价198元，说明未下单 |
| 这个订单付款了吗？ | get_shop_order | 合成银行付款PAID，读取映射COMPLETED，金额198元 |
| 我在鲜花店的订单现在是什么状态？ | list_shop_orders | 花店本人订单99元，PENDING_PAYMENT/银行PENDING，不沿用当前蛋糕店订单 |

五项均为model模式，来源明确为模拟店铺。联调结束检查订单仍为持久化PENDING_PAYMENT、版本与创建后基线一致，合成顾客余额100000分不变。首次脚本误将订单创建后版本固定为1，而原创建流程在绑定付款申请时会升到2；改为创建后实际版本作基线后，完整重跑通过。

## 边界与运行

没有依赖或数据库迁移，没有新增权限、商户全量订单工具、支付/退款能力。模型不能编造库存、物流、门店地址和营业时间。只读状态投影与已加载页面可能因查询时刻不同而有差异，结果提供as_of和stored_status/status_projected；state_version仍是存储版本。

未进行浏览器截图/交互自动化，未运行全仓库测试。源码及本地前端构建已更新，未重启已有服务或重建Docker。更新方式见 [店铺查询契约](../contracts/NOVA店铺查询_API_v0.1.md)。
