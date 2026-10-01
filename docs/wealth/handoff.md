# 第4号理财增量数据库与联调交接

以任务书正文v1.1为准。完整接口和状态见 [理财契约](contract.md)，实施决策见 [设计](decisions.md)。这是虚构理财扩展，旧现金账本、转账API、风险等级API保留。

## 数据对象及主责

| 对象 | 主责及用途 |
|---|---|
| wealth_products/product_versions/navs | 4号当前产品索引、历史规则与披露、唯一营业日净值；已用历史版本不覆盖 |
| wealth_orders/lots | 4号权威受理订单、份额批次、剩余成本、解锁日期 |
| wealth_cash_holds/unit_holds | 4号现金/逐批份额占用；account锁与全部共享扣款同一串行边界 |
| wealth_settlements/clocks/schema_versions | 4号唯一计价/到账分录、隔离业务钟、升级版本 |
| wealth_assessments/authorizations | 3号本人量表结果、原始答案受控存储、本人确认与强验证绑定；银行独立核验 |
| wealth_profiles/goals/plans/conversations/executions | 5号画像、用途标签、方案快照及版本、有限意图恢复、网络提交观察；不是银行账务事实 |
| audit_logs/transactions | 复用现有公共审计及现金流水；investment_purchase/redemption区别于消费 |

银行订单号与授权操作号唯一；分录事件键operation_id:pricing/cash唯一；占款与批次都有非负/正值约束。所有账务与银行审计同事务。主体读写先验归属，银行账户锁按ID排序；PG FOR UPDATE，SQLite写锁。自动结算、取消、重复请求不能绕开同一账户锁。

## 迁移步骤

1. 停止写入，保存数据库备份；先还原到隔离副本。不要执行 `docker compose down -v`，不要覆盖现有.env或资金文件。
2. 用当前代码、显式DATABASE_URL执行（仓库根包名backend.app；Compose镜像为app）：

```sh
python -m backend.app.services.bank.wealth_migration
# Docker Compose镜像部署时
# docker compose run --rm --no-deps backend python -m app.services.bank.wealth_migration
```

3. 增量添加wealth_*表，schema版本1；PG将transactions.type从20拓宽到32。目录/NAV只补缺，不播种账户、余额或流水。CLI单事务包含DDL及种子；SQLite显式BEGIN IMMEDIATE。重复执行核对版本并保留历史价格。启动也会执行兼容升级；正式切换前仍推荐停机跑CLI及副本核对。
4. 核对账户和流水行数、金额、所有字段指纹；检查占款、份额、流水、审计及版本；再启新版后端/前端。旧代码不能与新占款账本混跑。
5. 本次已有真实PG运行账本副本实际升级两次、空业务回退及重新升级：7账户/7流水，账户分合计22000000、流水分合计1603300，所有字段指纹一致；源库也未变。备份留本地忽略目录 `.deps/wealth-migration/before.dump`，不提交Git。

空业务回退：

```sh
python -m backend.app.services.bank.wealth_migration --rollback
```

只允许理财个人/业务表完全空，目录表可删。存在评估、画像、会话、授权、占款、订单、份额或结算任何记录就拒绝删表；不能运行旧代码忽略冻结款，也不能用旧备份覆盖新业务。无新业务时才可按已核对备份恢复；有业务保留新版并核对，不自动反向交易。type32拓宽无损保留，无需缩窄。

## 其他组员须注意

- 1/2号：用公开API和银行返回卡片，不由模型编金额/份额/状态；验证码只走可信组件。对话只生成意图，不能代答问卷或同意协议。页面初始确认未勾选。刷新从previews/plans/messages恢复。
- 3号：申购和赎回都RED。每笔授权独立；产品风险、本人承受能力和动作安全级别不同。旧User.risk_level不能申购，强验证不能提升适当性；画像改版须重评。沿用TOTP防重放，每笔等新时间步。
- 5号：只通过HttpWealthBankApi访问4号；纯购买计划1—5笔原子batch，不能按子单拆开占款。修改plan_version使旧授权失效；原号查询404仍不能判断永未受理；保留未知/成功事实。
- 共享扣款：使用available_fen，不仅balance。转账、定时、AA最终付款复用转账扣款，订阅同样检查占款；不得另写直接减余额路径。
- 账单：投资流水是资金流，消费原额仍仅purchase/subscription，避免把调仓误判消费。时间范围仍按真实账单时钟；隔离演示业务钟推进到未来时，未来流水不会提前出现在“截至现在”报告。
- 目录更新必须创建新规则/披露版本；已受理订单使用原版本与固定计价日。后台只结算已受理，不自动配置、买入或卖出。管理员不授给AI。

## 数据与秘密保留

本任务没有真实资料、行情、付费模型调用或第三方UI资产。原始答案存在受控评估表，凭据和答案不进对话/业务审计；审计留规则版本和确认事实，不存隐藏思维链。测试秘密、隔离数据库、备份和截图均被Git忽略。财务画像只允许虚构CNY数据；无自动删用户/金融记录功能，清除隔离演示和审计保留需分别决定。

模拟简化及真实上线待审查见 [安全自评](security.md)，不能把本次验收当作监管许可或生产安全认证。
