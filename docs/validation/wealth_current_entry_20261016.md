# 当前前端入口核对

基线main：217f722d43d145358f394441c205f9f2ed62beb6。最初为入口说明修正，随后按用户纠正完成下述共用银行账户接线；不删除共用前端，不改原工作区、原数据库或原配置。

- Git实际只跟踪一套正式前端index.html→main.tsx→App.tsx；frontend/dist、node_modules不在Git中。01的portfolio-dev.html、src/dev/portfolioMain.tsx及vite.portfolio.config.ts为独立验收材料，保留供历史验证，不列为用户入口。
- 本机3000容器创建于2026-10-02，前端不挂载最新源码；3010容器挂载原工作区frontend/dist。原工作区HEAD仍5e36e4c并有大量未提交成果，未重置或覆盖。
- 已恢复独立3014/8014。最新组件HTTP 200、前端代理context HTTP 200、B017资格HTTP 200、后端health ok且portfolio_ready=true；初始化28.466秒，目录27070/政策绑定13578。验证使用126隔离账号、未提交订单。原容器、原库和历史记录保留。
- README移除旧端口的前端推荐，仅列3014与对应8014；明确本机必须运行同源码服务。运行指南移除旧60模拟份额/研究面板启动，增加正常backend.app.main及Vite成对端口命令，区分专用测试context和正常真实时钟。
- 默认Docker Compose没有当前固定资料挂载，不能宣称其前端可打开就等于新理财就绪。此次不修改或重建原Docker服务，不冒称已完成新的Docker理财部署。

证据：.deps/final_round/restored-entry-20261016.json及runtime-restored-20261016.err。入口文档阶段复用已恢复入口实测；随后共享账户修复的新增测试另列如下。

## 同一银行账户修正（取代已撤回的404兜底）

此前把理财专用账号作为用户说明并将账户404视为空列表，是错误处理；已撤回该前端兜底，不通过提示掩盖服务端绑定错误。

正式理财通过管理员bind-account绑定本人已有有效借记账户，account_scope=shared_bank；账户、余额、占款和流水与转账/账单/订阅/NOVA共用。保留旧独立沙箱的隔离，防止其发行的夹具资金进入通用场景。正常共享账户不允许重置/调资/删除，不读取冻结测试业务时间。本人归属、资料绑定、风险、窗口、MFA、幂等及available_funds均保留。

### 本轮实际验证
- 新建隔离SQLite：共享绑定不新增账户、不发行资金；同账户申购后通用账户与理财同步占款；占款后不足额转账拒绝、合法转账后理财现金同步减少；多借记账户资产范围；旧沙箱拒绝转账、他人绑定拒绝、测试写工具拒绝共享账户、生产函数使用当前时间。
- 针对性运行记录：共享账户+原理财银行28 passed/28 skipped；共享账户+账单边界+NOVA资产13 passed/8 skipped；前一轮资产摘要8 passed/7 skipped。集合有重叠，不相加冒充独立用例。跳过均为未配置BANK_TEST_DATABASE_URL的PostgreSQL参数，本轮未声称PG实测通过。
- TypeScript与Vite正式构建通过；仅有既有体积警告。git diff --check通过。
- 当前3014/8014实测：U1通用账户ID与理财account_ids均为SA16b32e1980a0a5584c1c15681d0e，账面/可用均2,000,000分；账户、收款人、流水、收款申请及订阅查询HTTP200。B017、000003选品HTTP200；671030因当前用户风险承受能力拒绝，未放宽校验。
- 当前库U1及ROUND2H选中既有账户绑定为共享账户；先备份，再逐表核对accounts/wealth_orders/wealth_lots/wealth_cash_holds/transactions行数及SHA256完全不变。不迁移、删除或合并旧账户，不改变现金和历史成交。仅上下文/资料关联及审计发生预期变化；原工作区和原数据库未动。
- 浏览器实测同账号登录，概览账户一览及转账表显示同一账户和20,000元；理财显示相同资产，旧提示消失；未执行本人账户交易。

### 本地证据
.deps/final_round/before-shared-account-20261016.db为安全备份；shared-account-before.json保存账务哈希；shared-account-binding.json及shared-account-runtime.json保存绑定与运行核对。shared-bank-overview-20261016.png及shared-bank-transfer-20261016.png为浏览器证据。备份、截图和私有运行库不提交Git。

正常启动使用真实当前时间；历史隔离测试上下文及原3010/8010/5432服务保留。只更新当前PR分支，不合并main。
