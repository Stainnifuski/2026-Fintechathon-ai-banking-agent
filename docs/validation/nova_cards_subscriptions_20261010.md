# NOVA 卡片与订阅查询验证

日期：2026-10-10。本轮增加list_cards、get_card、list_subscriptions、get_subscription，当前NOVA共16个只读工具；卡片、订阅页面支持选择对象传入严格页面上下文。契约见 [卡片与订阅](../contracts/NOVA卡片与订阅查询_API_v0.1.md)。

## 自动化验证

运行以下定向组合：

```bash
pytest -q tests/integration/test_nova_cards_subscriptions.py tests/integration/test_cards.py tests/integration/test_cards_time.py tests/unit/test_subscriptions.py tests/unit/test_nova.py tests/integration/test_nova_reads.py tests/integration/test_nova_calendar.py tests/integration/test_nova_shops.py
npm run build --prefix frontend
git diff --check
```

结果：75 passed，前端TypeScript/Vite构建通过，diff格式检查通过。构建保留已有大于500kB分块提示。SQLite异步测试在沙箱外执行，使用隔离内存数据库。

新测试覆盖：

- 与页面服务的状态、限额、周期金额、计划日期等事实一致；卡号和关联账户标签再次脱敏，不返回浮点元金额。
- 卡片类型、状态、虚拟卡、末四位及订阅商户/状态筛选；分页总数与has_more。
- 预计月费按筛选后全部分页计算，排除取消项、非月度及非人民币项；不代表实际扣款。
- 显式ID与伪造页面选择均校验身份；另一用户、未知对象返回同一未找到错误；关联他人账户的脏数据也被排除。
- HTTP登录身份、上下文传递；错误页面、额外身份/事实字段及非法参数拒绝。
- 离线列表/选中详情；操作请求不被转换为成功查询；取消、扣费、解锁、挂失未注册为NOVA工具。
- 查询不修改余额、卡片状态/版本、订阅版本/下次日期，不增加卡片操作或交易流水。

## 真实模型联调

使用项目已配置模型，以隔离内存合成账户、2张卡片、本人3项订阅和另一用户记录验证5个问题。不连接运行数据库，不执行资金操作：

| 问题 | 成功调用 |
|---|---|
| 我有哪些银行卡？ | list_cards |
| 这张卡的信用额度和单笔限额是多少？ | get_card，使用页面选中C2 |
| 我的订阅每月预计要花多少钱？ | list_subscriptions，25元+15元=预计40元，99元取消项排除 |
| 这个订阅什么时候扣费，扣哪个账户？ | get_subscription，使用页面选中S1 |
| 查看云盘会员订阅情况 | list_subscriptions → get_subscription |

5项均以model模式完成，无工具错误；最终余额、锁定卡状态及订阅版本保持原值。模型区分信用额度与现金、预计月费与实际扣款、计划日期与自动执行。密钥及原始模型输出不写入仓库。

## 范围与部署

未运行全仓测试，未做浏览器自动化、截图或窄屏实际验收。未修改运行库、提交远端或重启服务。前端构建通过不等于所有交互已做浏览器验收。

无需数据库迁移。保留已有数据库和.env；Docker部署使用 `docker compose up -d --build backend frontend` 更新，随后刷新页面。源码启动需重启后端并使用最新前端。工作台原有操作和确认/强验证流程保持独立，NOVA只查询。
