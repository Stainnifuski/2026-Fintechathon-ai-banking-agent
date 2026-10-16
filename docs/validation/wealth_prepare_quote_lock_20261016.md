# 差额准备与按需报价锁顺序修复

基线：main 1da00fd5c019574f76ac806d41e0f845e60ed372。本轮不改模型、参数、风险、MFA或银行交易规则。

## 证据与根因
当前专用服务runtime-shared-final-20261016.err记录多次/actions 503，内部/catalog在写wealth_navs时sqlite3.OperationalError: database is locked。结构化dispatch在外部银行调用前持有profile写锁/未提交会话插入，按代码报价使用另一事务更新净值，造成相互等待。前端run捕获并吞掉原始错误，使调用者退化为“差额准备未完成”。

实际复验也发现未采用建议的默认Plan selected_positions为空，advised_positions已有正目标；执行映射只消费明确选择，合计校验拒绝。此情况不能通过自动采用或放宽合计来解决。

## 修改
- 新动作会话先提交身份，使A侧可核实；释放事务后读取银行/报价。结果持久化前重新获取用户锁，核对请求重放、会话归属/范围/关闭状态及版本。原交易和授权服务的锁不变。
- prepare返回后与其他动作统一进行最终会话复核，保持请求幂等；不同参数重号、失效/关闭会话仍拒绝。
- prepare失败向调用组件传递实际原因，银行服务/报价失败给中文下一步，不自动重试。
- 有未纳入选择、但目标不同于现有持仓的建议产品时，前端禁用准备并指引勾选/校验，服务端以PORTFOLIO_SELECTION_REQUIRED明确拒绝。不自动选择产品。

## 实际验证
- actions及prepare隔离SQLite：14 passed，13个未配置PostgreSQL参数跳过，未称PG通过。
- 新增真正分连接SQLite净值写入回归：银行事务可在动作处理期间提交，动作结果同号重放一致；并发修改会话版本仍返回STALE_SESSION。
- 新增未采用建议不能成为隐式交易的拒绝测试。
- 前端会话/中文反馈9项通过；TypeScript/Vite正式构建通过（既有大包警告）。
- 运行服务使用真实当前时钟、当前合法风险和资料：初次默认Plan 24.053秒，未采用建议prepare明确暴露合计缺口（修复前置提示前记录）。明确选择8个合法建议后，Plan 9.758秒、prepare 19.198秒，均HTTP200；准备8个待确认动作，同请求重放一致，账户、订单、持仓、占款和流水未因prepare变化。没有本人确认/MFA/执行，也未产生申购订单。
- 这是实际等待记录，不声称已达到5秒。没有全池补采或写请求自动重试。

本地详细证据：.deps/final_round/prepare-lock-live.json、prepare-lock-selected.json、runtime-quote-lock-20261016.err。原数据库与原服务不变；3014/8014专用服务保持运行。
