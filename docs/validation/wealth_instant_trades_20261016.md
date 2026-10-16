# 比赛理财模拟即时成交验收

基线：main `58dbe2b507be9b013f802e0eb74e217c9930f66f`；本地分支`codex/wealth-instant-trades`。本轮未fetch、推送、创建PR或合并main。

## 执行契约

- 正式共用银行账户的新交易采用`competition-instant-v1`，预览不记账。确认/MFA后买入扣款并建持仓；赎回扣减已解锁可赎批次并增加现金。原份额精度、尾差、申赎费用、FIFO成本计算继续复用。
- 使用最近已公布有效NAV，保留真实来源净值日期；执行日期为当前业务日。v2命令`nav_quote_id`绑定政策、产品/披露版本、context/epoch及报价ID/日期/价格，原digest继续绑定方向、金额/份额、账户和方案版本。确认或执行时改价拒绝，须重新准备和授权；不自动重试写请求。
- 现金、风险、持有期、募集/开放周期及暂停校验保留。现金管理即便选普通赎回，也必须经过原快赎额度/窗口校验，避免即时到账旁路。一般基金没有获得快赎能力。
- 接受时同事务消费授权、改现金/份额、唯一流水/结算事件和审计；申购组合原子完成。后台最小订单事实仍用于原号重放和恢复，前端订单表/撤单组件及专属fixture loader已删除。新成功成交不可撤。
- 静态原条款/政策及历史成交不改写。原条款政策和本轮实际执行政策分字段留存；不修改固定离线包内容或静态披露指纹。
- 历史沙盒及既有在途按原路径处理；没有将已有占款订单改价、变现或批量迁移。

## 实际检查

|检查|结果|证据/边界|
|---|---|---|
|新增即时成交隔离测试|6 passed / 6 skipped|`tests/integration/test_wealth_instant.py`；SQLite独立库。包含整份公开HTTP预览→确认→真实TOTP计算验证→执行；两笔均SETTLED、两笔流水/持仓、无占款；另覆盖普通赎回费用/FIFO、重放、旧预览拒绝、未来报价排除、持有期、资金不足组合回滚、R2/R4拒绝及现金管理标准入口窗口/额度防绕过|
|相关prepare、共享账户、公开确认MFA、披露绑定回归|17 passed / 16 skipped|四文件加公开MFA单例命令见下；包括新测试中的3项，不能与6简单相加当独立用例数|
|既有快赎/旧沙盒结算回归|7 passed / 6 skipped|`test_wealth_quick.py`；旧普通赎回T+n仍供历史隔离记录使用|
|第一次银行回归|31 passed，3 failed，34 skipped|3个失败分别为新测试回滚后读取过期ORM、共享测试旧字段名、确认路由回滚后读取ORM。均已修复；上述相关回归和新增测试已通过。未再执行整个旧银行集合，未受影响的31项结果复用|
|前端定向测试|17 passed|表格精度/范围/缺估值、会话串行/不自动重试、中文反馈；撤单专属fixture检查随功能移除，持仓版本检查保留|
|TypeScript和Vite正式构建|通过|仅既有大chunk提示，无构建错误|
|正式浏览器|通过页面范围检查|3014登录→理财，显示模拟即时成交说明；无订单查询/撤单入口，持仓查询保留。没有使用用户账户做申赎；成交闭环证据来自独立HTTP测试|
|独立PostgreSQL|未运行|未配置`BANK_TEST_DATABASE_URL`；参数化用例如实skip，不声明双库通过|

主要命令（仓库根目录；使用项目Python及依赖路径）：

```powershell
python -m pytest tests/integration/test_wealth_instant.py -q
python -m pytest tests/integration/test_wealth_instant.py tests/integration/test_wealth_shared_accounts.py tests/integration/test_wealth_bank.py::test_public_personal_confirmation_totp_and_lost_response tests/integration/test_wealth_portfolio_prepare.py tests/integration/test_wealth_disclosure_binding.py -q
python -m pytest tests/integration/test_wealth_quick.py -q
# frontend目录
node --experimental-strip-types --test tests/wealthTables.test.ts tests/wealthActionSession.test.ts tests/wealthFeedback.test.ts
node node_modules/typescript/bin/tsc
node node_modules/vite/bin/vite.js build
```

第二条命令的17项结果是在新增后3项前取得，后3项由第一条全6项再次验证。原始日志留在本地`.deps/final_round/instant-*.txt`，不提交缓存或运行库。

## 运行库保护及人工操作

重启前通过SQLite backup API备份当前专用验收库；账户、订单、持仓、现金/份额占用、流水、结算7表按主键排序内容SHA256前后完全一致。全库订单状态仍为7 ACCEPTED、1 CANCELLED、5 SETTLED。用户U1原4笔占款合计14,400元未动；新行为不会释放其旧占款，也不将其变成新即时交易。

3014/8014专用入口保持运行，原3010/8010/5432保持。原业务库、原工作区配置和全局时钟未改。专用测试问卷两项次数开关继续保留，未替用户答卷；正常应用默认2/8不变。

人工访问`http://127.0.0.1:3014/`，重新登录→理财→选择当前合格产品→生成/采用配置→准备差额→整份预览→一次本人确认和MFA→查看成功结果及刷新后的持仓/余额。赎回从持仓表选择已解锁份额，预览/确认/MFA后立即到账；未满足期限、额度或窗口会明确拒绝。新即时成交无需推进时钟。模拟长期持有产品需要等待真实期限，或在另一个受控隔离测试账户推进；不得推进共用账户全局时钟。演示凭据位置仍为登录页和`tests/browser/wealth_joint_server.py`，本文不输出密码。
