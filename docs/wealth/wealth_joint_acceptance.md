# 理财01／02：联合检查与人工验收当前状态

更新2026-10-13，代码 `813894c6a6e670229ad8381a3c6367f3998f5b18`。**前置真实基金链未通过，完整联合投资正向未开始，不可封板。** 当前停止条件是新风险/产品/交易政策需用户决定；不是等待用户重复验空页面。

## 1. 本轮实际检查（不累加重叠集合）

|证据|实际范围/结果|
|---|---|
|real_close/current-real-chain.json|HTTP真实池27,998，范围19,265，当前SKU/版本/购买资格0；S/B78→34份额→18家族→0材料|
|final-affected.log|147通过：PG29/SQLite29/其他89，独立bank_test数据库及逐例schema；实际收益、目录可见性、现金更新与生命周期|
|fee-final-tests.log|216通过；最新费用/候选/模型/生命周期、JointFacts和统一MFA；两库都启用|
|snapshot-final-tests.log / serialization-final-tests.log|最后现金双读变化拒绝116通过（含两库）；移除无消费的大目录重复序列化42通过，资格/算法输出不变|
|intern-final-tests.log|181通过；相同未知字段仅在不可变源复用、修改拒绝、内容SHA一致，可变注入仍独立；候选/模型/生命周期|
|final-tests.log|257通过，中间性能修复证据；不替代更新后的147/216。first-tests.log的1失败为可变内存资料指纹错误，已修复复验|
|shared-regression.log|转账、NOVA读取、卡片/订阅及统一认证29通过、5个PG例未配置跳过；后续216的统一认证两库例已运行，旧skip仍如实保留|
|normal-http.json/normal-release.err|正常main lifespan、canonical注册与同scope/snapshot/T，HTTP200；无录制source，新隔离SQLite|
|http-three.json/recommend-three.json|配置3.170s / 2.576s / 2.604s，推荐1.245s / 1.227s / 1.958s；28,076目录；当前材料家族0，非真实七基金正向性能|
|repro-result.json|新目录实际导入28,076；88公开文件SHA；包2,053,835字节；篡改/缺包拒绝，日期不续期|
|browser/browser_results.json|正式客户UI SQLite阻塞检查3通过，runtime/network0、新LLM0；无七产品购买/新问卷浏览器证据|
|types-final.log/build-final.log/build-independent.log/build-fixture.log/frontend.log|类型/正式与两独立构建通过、66前端状态；正式大chunk为提示|

上述日志位于检出ignored `.deps/real_close/`；之前原始日志在原工作区 `.deps/wealth_joint_20261012/`。私有数据库/缓存不提交。测试入口使用项目依赖：

```powershell
python -B -m pytest tests/integration/test_wealth_catalog_batch.py tests/integration/test_wealth_actual_returns.py tests/integration/test_wealth_asset_summary.py tests/integration/test_wealth_b6_controls.py tests/integration/test_wealth_joint_provider.py tests/unit/test_wealth_portfolio_lifecycle.py tests/unit/test_wealth_portfolio_model.py -q
python -B -m pytest tests/integration/test_wealth_joint_provider.py tests/unit/test_wealth_portfolio_candidates.py tests/unit/test_wealth_portfolio_model.py tests/unit/test_wealth_portfolio_lifecycle.py tests/integration/test_wealth_unified_authorization.py -q
# PG必须配置独立bank_test_*库的BANK_TEST_DATABASE_URL；不能指日常库
```

## 2. J01—J23当前逐项状态

前轮以下逐项证据继续有效但均限定其范围；日期较旧不当作新正向。当前新增检查没有将blocked/partial升级为完整passed。七真实家族前置未过，不能执行或勾选完整J08；B录制/隔离交易事实和正式真实正向分开。

|编号|当前状态|Codex实际证据与缺口|人工点击（仅前置闭合后）|预期与失败复验|
|---|---|---|---|---|
|J01|partial|正式无预算/期限Action与20,000全现金A图已验，非投资正向|点击理财→直接选择操作→理财规划|全scope三板块；不询问本次金额/期限；0候选不得显示购买成功|
|J02|partial|A模型/生命周期及组件状态通过；正式全现金无非活钱滑块，20/30/50联动仅隔离|看三板块与比例；有非活钱方案时调整滑块|T分母、守恒、普通调整原U不漂移；不能手造比例算正式通过|
|J03|partial|推荐资格与实际最终R2→R4零/微权重/C负向通过；正式合法候选仍0|不可购买产品保持禁用并解释；恶意参数由Codex测试|具体份额等级拒绝且无订单/占款/流水/扣款；已有超风险持仓允许减持|
|J04|partial|结构筛选资格/API/前端状态通过，正式unavailable原因已取证，真实正向缺交易映射/评级政策及必要材料|改推荐方式/类别/主题→按条件推荐|参数化筛选；无匹配/超风险/停售/来源缺失区分|
|J05|blocked|跨批/多选/A-C前端和A单元证据；正式池0不能实际换批>5|有合格池后勾选→换批→可选详情/比较→切份额|每批至多3家族、选择保留、失败不改旧选择、同具体份额身份/费用|
|J06|partial|未知字段与资格检查通过；没有正式合格产品指标浏览器证据|真实链闭合后产品资料→对应具体基金→详情|显示真实名称/具体份额/日期，null未知；NAV描述不是总回报|
|J07|partial|A修订和B前端比例/预览状态测试通过；真实投资比例与旧授权失效完整浏览器未闭合|有合格方案后调整比例→整体预览→再调整|原Plan失败保留、整数分守恒，旧预览/授权拒绝，受理订单不回滚|
|J08|blocked|实际正式A合格数0，未走7项购买；B录制7项测试不能计本项|材料闭合后选7项→整份确认→统一验证→提交|完整绑定七子单，单次确认，不漏项/重复扣款；当前禁止勾通过|
|J09|partial|B两库预留/撤单/结算不双扣及共享汇总通过；未串正式A正向浏览器|J08可运行后预览/提交/撤单各看余额及订单|prepare无占款；受理占用；撤单释放；结算不重复|
|J10|partial|B金额/份额输入和真实银行部分赎回/到账收益测试；联合持仓浏览器未跑|有合法持仓后选持仓→选择市值/份额/比例→核对→授权|四位份额、冻结/可赎分开、到账提示；退出不套买入等级|
|J11|partial|双表分页/独立选择前端66集合覆盖，通用Action查询HTTP通过；实际多页浏览器未造数|查询订单/持仓→各自翻页多选→切context|跨页保留、当前页全选、切context清空、不同ID不串|
|J12|partial|新按钮流程及A调用无messages/NLP请求；API禁止模型；整交易浏览器主线blocked|仅按钮与表单完成操作，勿输入聊天|无模型请求不等于主线交易已完成；查询不得提交交易|
|J13|passed（SQLite/PG API）|B缺NAV/冻结/应收原子汇总与A F18精确scope/lot版本通过|看资产总额/可付现金/冻结/应收；缺估值显示未知|T=账面现金+持仓市值+赎回应收，冻结不重复计入；应收不可支付|
|J14|partial|实际B控制HTTP、A闭环和转账/卡片/订阅/NOVA查询回归通过；全DAG浏览器未跑|原有业务导航；任务详情/接管/交还|复用旧引擎/授权；Action不冒充raw_text，无跨业务覆盖|
|J15|partial|A归属/版本、B真实JWT/跨context拒绝、原目录hash保护；另一用户完整UI未跑|另一演示账号不可读取旧Plan/订单；切context|服务器身份/范围隔离，失败不改账务，原库不清空|
|J16|partial|严格Action、通用筛选/赎回/分页测试通过；正式推荐正向无材料|任意合法类别/主题参数及数量，不局限四按钮|unknown键422，受控对象/单位/schema；不靠关键词假成功|
|J17|partial|正式handler/DTO/组件/资料/错误索引已交付；J尚未闭合|审阅两工程文档和03索引|不开始新LLM/NLP，不把本轮交付误称封板|
|J18|passed（SQLite/PG API；SQLite单账户UI）|未请求理财前两页T与A同scope/snapshot20,000；多账户/context API通过|登录概览→理财，尚未请求看总资产；交易后重新读|全授权同口径同值；多账户API证据不冒充多账户浏览器|
|J19|partial|实际银行账务/唯一到账/撤单和收益测试通过；正式A交易后的UI链blocked|有交易后查询订单/资金流水|结算才有investment、冻结非已完成扣款、撤单无假退款|
|J20|passed（隔离API/统计）|test_bills理财排除、精度/异常/分类/明细/文字与普通消费通过；页面空库仅加载|有混合流水时账单查询本期/对比期→异常/明细|理财现金流从统计/文字排除；普通消费保持准确|
|J21|partial|双表/收益入口前端状态和B实际收益HTTP通过；非空完整UI未跑|查询订单/持仓→分别详情/收益→整体收益|两表独立，订单不能当可赎份额；费用/版本/范围一致|
|J22|passed（SQLite/PG服务/API）|实际结算F20-F23、本金/应收到账/费用只算一次、全赎贡献及快赎时点通过|选择日/月/年/累计与产品、全赎后查询|银行真实模拟持仓收益正确；不等于A历史金融研究通过|
|J23|partial|实际缺NAV/历史成本/未发布NAV为unknown及银行收益禁止模型测试通过；所有页面粒度点击未遍历|切日期/粒度/产品，查看coverage与缺失|不填未来/假0，准确模拟标识；无NLP主线|


## 3. 最终负向及修复复验规则

R2→R4手动选择、0、极小权重、C最终prepare保持SUITABILITY_MISMATCH，无订单/占款/扣款/流水；A选择失败原Plan不变。当前与过期评估/画像版本、源资料/规则/预览、scope/会话closed/stop/HUMAN/PAUSED重验；用户强验证不能提高承受等级。既有高风险持仓仅保持/减持，不能自动强卖。HTTP恶意入参不能标人工点击。

失败按 request/action/Plan版本、具体SKU/披露/规则版本、原操作号、数据库、现金/占款/份额前后及来源日期记录，保留旧证据；先修对应实现再运行受影响用例。未知提交使用原号核对，不能新号自动重发；已受理订单不因清聊天或改草稿回滚。

## 4. 人工与最终联合流程的前置

当前**不开放正向人工验收**，不要求用户绑定MFA来掩盖零候选。批准新设计后，先由Codex实际闭合真实七家族链及完整必要J。之后才更新仓库外同名人工指南为实际已通过版本，包括合法账号位置、URL、本人问卷、普通/债券/主题、换批多选/有效A-C/详情、修订、prepare/整份MFA/七子单、撤单/两种赎回/非空双表/收益日历/操作员交还及实际等待/受控测试时钟。不能要求人工编请求包。

当前3013/8013/8014为本轮专用验证服务，最终核对归属后停止；原3010/8010/5432保持。全数据/账务保留。新模拟风险和交易规则尚未实施，七S产品目标方案不采用。A历史金融研究移出运行门禁但明确未完成；B实际收益及J23仍必须闭合。
