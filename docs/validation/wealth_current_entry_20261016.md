# 当前前端入口核对

基线main：217f722d43d145358f394441c205f9f2ed62beb6。本次为说明修正，不删除共用前端，不改业务、数据库或原配置。

- Git实际只跟踪一套正式前端index.html→main.tsx→App.tsx；frontend/dist、node_modules不在Git中。01的portfolio-dev.html、src/dev/portfolioMain.tsx及vite.portfolio.config.ts为独立验收材料，保留供历史验证，不列为用户入口。
- 本机3000容器创建于2026-10-02，前端不挂载最新源码；3010容器挂载原工作区frontend/dist。原工作区HEAD仍5e36e4c并有大量未提交成果，未重置或覆盖。
- 已恢复独立3014/8014。最新组件HTTP 200、前端代理context HTTP 200、B017资格HTTP 200、后端health ok且portfolio_ready=true；初始化28.466秒，目录27070/政策绑定13578。验证使用126隔离账号、未提交订单。原容器、原库和历史记录保留。
- README移除旧端口的前端推荐，仅列3014与对应8014；明确本机必须运行同源码服务。运行指南移除旧60模拟份额/研究面板启动，增加正常backend.app.main及Vite成对端口命令，区分专用测试context和正常真实时钟。
- 默认Docker Compose没有当前固定资料挂载，不能宣称其前端可打开就等于新理财就绪。此次不修改或重建原Docker服务，不冒称已完成新的Docker理财部署。

证据：.deps/final_round/restored-entry-20261016.json及runtime-restored-20261016.err。纯文档更改复用已恢复入口实测，不重复旧交易或全套业务测试。

## 登录后通用账户提示修复
实测13800000001在当前隔离库为U1，存在两笔理财context专用账户，通用账户接口按既有隔离规则排除它们并返回404；理财资产接口仍正确读取20,000元。登录、收款人、流水、收款请求、订阅接口均200。前端仅将这个明确的账户缺失404作为空通用列表，保留其他错误，并中文引导从侧栏进入理财；不回退或修改银行账户隔离。README及登录页区分通用初始化示例和当前理财验收账号。
新增验证：TypeScript检查与Vite正式构建通过（既有大包提示）；浏览器实际刷新确认中文提示、理财工作台及20,000元资产加载。截图保存在本地.deps/final_round/account-scope-20261016.png；未提交交易、未变更数据库。
