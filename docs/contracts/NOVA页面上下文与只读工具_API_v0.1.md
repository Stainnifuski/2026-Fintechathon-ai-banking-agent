# NOVA 页面上下文、身份上下文与只读业务工具

NOVA 已在时间查询 Agent Loop 上增加本人账户概览、账单统计、分页流水和单笔流水详情。后续已增加 [本人日历查询工具与日历选择上下文](NOVA日历查询_API_v0.1.md) 及 [鲜花店/蛋糕店只读工具与店铺选择上下文](NOVA店铺查询_API_v0.1.md)及 [卡片/订阅只读查询与选择上下文](NOVA卡片与订阅查询_API_v0.1.md)。前端传递页面状态，服务端独立注入登录身份；页面和工具共用银行查询服务。

## 页面上下文协议

`POST /api/v1/nova/chat` 沿用 Bearer JWT。既有 message/history 限制和错误响应保持不变，新增可省略的 page_context：

```json
{
  "message": "这笔流水是什么？",
  "history": [],
  "page_context": {
    "version": 1,
    "page": "insights",
    "filters": {
      "period": "month",
      "date": "2026-09",
      "account_id": null,
      "category": "dining"
    },
    "details": {"category": "dining", "tx_ids": [], "page": 1},
    "selected": {"type": "transaction", "id": "TX123"},
    "observed_as_of": "2026-10-01T00:00:00+08:00"
  }
}
```

- version 当前仅接受 1。
- page 为 home、transfer、insights、cards、subscriptions、wealth、thirdParty；省略上下文默认为 home。第三方页面可传 calendar 或 shop（互斥），两者各自保存日期/活动或店铺/商品/订单选择。
- filters 为账单页**已应用报告**的 period/date/account_id/category，与既有 BillQuery 同口径。修改筛选表单但未点击“查看账单”时，Agent 仍看到当前报告条件。
- details 描述明细下钻类别、待核对流水集合和页码。tx_ids 最多 10 个，每个 1–32 字符，page 范围 1–10000。
- cards.selected_card_id 仅用于 cards 页面，subscriptions.selected_subscription_id 仅用于 subscriptions 页面；它们仅是ID提示，服务端重新校验归属。
- 顶层 selected 仅支持 transaction；日历使用 calendar.selected_event，店铺使用 shop.selected，详见各扩展契约。流水id 长度 1–32。在账单流水行点击“选择这笔”，随后打开 NOVA，可问“这笔流水是什么？”；再次点击可取消选择。筛选、报告或明细页码变化时清空选择。
- observed_as_of 为带时区的页面报告截止时间，仅作提示；工具读取最新服务端数据，不接受客户端时间冻结账务或模拟推进。
- filters/details/selected/observed_as_of 仅允许出现在 insights 页面。拒绝额外字段，如 user_id、role、balance、token，拒绝其他协议版本。

页面上下文以 user 消息的数据形式发送给模型，不升级为 system 指令，也不作为事实或授权。用户指定“当前筛选”时沿用页面账期；明确“本月／今年”等现实相对时间时由模型确定当前账期。模型只能看见本次页面提示和有限聊天历史，不自动获得其他页面全部数据。

## 服务端身份上下文

路由从 get_current_user 取得 user_id，从 get_db 取得请求会话，构造 `ToolContext(user_id, db, page)`。这个对象只在服务端传递，不发给模型或作为客户端参数接受。

工具参数中禁止 user_id；账户归属校验由银行服务执行。所有业务工具必须有身份上下文，缺少时返回 AUTH_CONTEXT_REQUIRED。工具处理器不直接查询 ORM；银行读取与脱敏 DTO 位于 services/bank/nova_reads.py，账单复用 bill_service，账户复用 data_service.list_accounts 的原有可用资金与占款计算。原有 list_accounts 调用方式保留，并增加可注入会话的选项。

## 工具与查询语义

| 工具 | 参数 | 返回信息 |
|---|---|---|
| get_current_time | timezone，可省略 | 现实 UTC/指定时区时间，保持原有契约 |
| get_financial_overview | 空对象 | 本人账户列表、账面/可用/占款总额、口径说明 |
| get_bill_analysis | period/date/account_id/category，可省略 | 完整统计、上期比较、分类、趋势、异常与报告 |
| list_transactions | 上述筛选及 page/page_size/tx_ids | 已完成流水、总数、分页及 has_more |
| get_transaction | tx_id，可省略 | 本人一笔已完成流水；省略时使用页面选择 |

工具参数使用严格类型，拒绝额外参数。查询字段省略时继承页面 filters；显式 null 清除对应筛选。改变 period 而不提供 date 时，月转年继承原年份，年转月默认现实当前月份。

list_transactions 还继承页面 details 的类别、流水集合、页码；模型可显式传 category:null、tx_ids:[]、page:1 清除下钻并重新分页。page_size 默认 20，范围 1–50，tx_ids 最多 10 个。汇总计算不受分页限制。get_bill_analysis 返回最多 20 条异常，同时提供 anomalies_total、anomalies_has_more；完整总额、分类及趋势不截断。

get_transaction 直接查询已完成流水并校验账户归属，不受当前报告账期限制。未知流水、其他用户流水、未完成流水统一返回 TRANSACTION_NOT_FOUND；未指定且未选中流水返回 MISSING_SELECTION。其他用户账户筛选返回 ACCOUNT_NOT_FOUND。理财测试上下文账户在账户及账单查询中排除。

账户概览现金总额只汇总 savings 类型，与首页“当前可用资金”一致，信用账户及其他账户展示在列表中但不计入现金总额。卡号仅保留末四位；不返回手机号、身份证、身份验证材料、user_id 或密码。金额统一为人民币整数分，模型及前端展示为元。

业务工具结果统一包裹：

```json
{
  "source": "bills.analysis",
  "data_kind": "simulated_bank",
  "currency": "CNY",
  "amount_unit": "fen",
  "as_of": "2026-10-10T16:52:00Z",
  "data": {}
}
```

source 分别为 accounts、bills.analysis、bills.transactions、bills.transaction；data 为对应业务 DTO。as_of 为查询截止/完成时间，不保证多个工具为同一事务快照。银行错误使用稳定 error/message 回传模型；非法参数 INVALID_ARGUMENTS、未注册工具 UNKNOWN_TOOL、内部失败 TOOL_EXECUTION_FAILED。模型不应根据错误编造金额或成功状态。

接口响应仍为 reply、mode、tool_calls。前端以 Markdown 渲染模型回答，支持标题、加粗、列表、表格和代码；表格可横向滚动，用户输入仍为纯文本。原始 HTML 不渲染，链接沿用渲染库的 URL 校验。工具名称、关键查询事实与截止时间单独展示。NOVA 每轮请求输出预算为4096 token，仍受全局 LLM_MAX_TOKENS 上限约束。默认报告优先给摘要，待核对提示最多举3条并说明总数。模型服务故障返回 503，不自动替换为离线成功；请求限制失败返回 422，未登录返回 401。MODEL_OUTPUT_INCOMPLETE 保留具体停止原因：length 时提示回答超出输出长度限制，其他中断提示未完整生成；服务端仅记录白名单原因、实际输出上限和整数输出token数量，不记录提示词或响应正文。截断的工具调用参数不会执行。

## 离线模式

既有时间查询保留。有限规则新增可用资金/余额、当前账单、账单明细、选中流水查询；调用同一个注册工具，明确标记 offline。账单理解复用既有月份/年份和单分类离线规则。复杂多轮推理、跨地区时间和未覆盖措辞仍需要模型。写操作请求不被当作已成功查询或执行。

## 页面覆盖和边界

| 页面可见信息 | Agent 获取方式 | 本轮覆盖 |
|---|---|---|
| 首页账户余额、可用资金、占款 | get_financial_overview | 已实现，同口径 |
| 账单已应用时间/账户/分类 | page_context.filters | 已实现 |
| 分类/异常下钻与明细页码 | page_context.details | 已实现 |
| 选中流水 | selected + get_transaction | 已实现，服务端归属校验 |
| 账单统计、图表数值、报告 | get_bill_analysis | 已实现，异常最多20条 |
| 账单流水明细 | list_transactions | 已实现，分页 |
| 生活服务日历 | calendar上下文 + 三个日历查询工具 | 已实现，详见日历契约 |
| 鲜花店/蛋糕店商品及本人订单 | shop上下文 + 四个店铺只读工具 | 已实现，同目录、价格快照及状态映射 |
| 其他页面 | page 字段及导航 | 仅页面位置；专用数据工具待后续接入 |

没有新增资金操作、授权、确认工具，也没有数据库迁移。对话面板在宽于1100px时位于业务工作台右侧，两者并排显示，没有遮罩；较窄屏幕采用上下分区，各自滚动。顶部“问 NOVA”可展开/收起，切换业务页面或使用面板导航不关闭对话。页面上下文随业务选择更新；每次发送问题取发送时的页面状态。会话仍只保存在前端内存，收起保留，刷新或退出登录清空。工具记录只随本次响应返回，不持久化。

## 更新运行服务和体验

源码运行需重启后端，前端开发使用 npm run dev；Docker 部署执行：

```bash
docker compose up -d --build backend frontend
```

- 首页打开“问 NOVA”，点击“查看可用资金”。
- 账单页应用某个月份/分类，打开 NOVA 点击“解释当前账单”。
- 在流水行点击“选择这笔”，打开 NOVA 点击“解释选中流水”。
- 问“列出当前账单的流水明细”检查分页查询。

使用示例与故障排查见 [NOVA使用指南](../setup/nova_usage.md)。账户与账单阶段证据见 [验收记录](../validation/nova_read_context_20261010.md)，模型配置见 [统一配置](../setup/model_configuration.md)。
