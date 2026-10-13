# 理财01／02联合验收：当前版本与人工步骤

更新：2026-10-13 America/Toronto。验证代码：`52f150d22e7ebde2f4ef397fffdc5579c08d50eb`（本地 integration/wealth-01-02）。

**Codex本轮范围通过，人工待验，未发布。** 本文覆盖00旧J矩阵的冲突要求；取消A/C操作及七独立家族计数，保留具体代码风险和账务条件。不同环境/集合不累加；历史记录只用于未改变路径，新数据方案不靠旧零候选结果证明。

## 1. 前置与证据范围

分类核实后项目等级为货币R1、债券R2、混合R3、股票R4，独立于发行人评级。可靠公开更高评级或分类冲突拒绝复核，不降级。政策按具体代码、资料、报价和不可变版本登记；不新增30天失效、不新增单笔2万元上限，保留现有银行金额与现金约束。申购/赎回模拟费用为0、无额外销售服务费；现实完整费用未知仍未知。公开最后已取得NAV是冻结模拟成交基准，source_nav_date与simulated_pricing_date分开；北京15:00截止、中国业务日历、申购T+1、普通赎回T+1确认/T+2到账、持有期0、计价前按既有规则撤单。

真实来源是冻结27,998全目录＋38已核实paper SKU/36材料组，七具体代码为样本。浏览器SQLite；PG是独立bank_test_*实际约束/账务/API，不是浏览器。bank测试的authorize helper不代替浏览器统一MFA。

|本轮记录|实际结果与范围|
|---|---|
|model-unit-final.log|232通过：数据、资格、模型、生命周期；隔离数据例不称真实交易|
|pool-tests.log|95通过：完整池、换批、真实结构观察时点稳定及源版本变更失效|
|bank-controls-final.log|14通过：SQLite/PG真实保存基金7买入、申赎、最终风险拒绝、取消及控制|
|history-two-db.log|53通过：历史导入/实际收益/资产/统一验证两库；未改变部分复用|
|final-affected.log|93通过：两库受影响映射/账单排除/正式provider＋生命周期|
|lifecycle-final2.log|49通过：最终selected-card保存、区间与canonical桥|
|frontend-final.log / types-delivery.log|47前端状态通过；最终TypeScript通过|
|build-delivery.log / build-independent-*-final.log|正式及两个独立构建通过；正式chunk大小提示不当错误|
|browser-final|最终七真实具体代码UI12检查通过、runtime/network0|
|browser-exits2|实际两种赎回/应收/到账、所有收益粒度及刷新；12检查、runtime/network0；该路径未改，复用范围标明|
|browser-controls4 / browser-operator3|前者滑块与旧预览目标检查通过，但后续定位器失败不冒充整脚本通过；后者接管续验4检查通过；原失败记录保留|
|browser-cancel-final / browser-comparison|实际问卷＋取消4检查、已有比较2检查，均runtime/network0；取消买入前置用公共HTTP真实统一MFA，不能写全由UI买入|
|normal-actual-risk-and-filters.json|真实R2/0与微权重/final HTTP/他人404、普通分类主题10项；无订单占款，原Plan保留|
|normal-result.json / offline-result.json|正常main启动health/login/assets/snapshot200；空目录包实装/SHA/日期不续期/缺包篡改拒绝|
|shared-regression.log（历史有效）|共享转账/订阅/卡片/NOVA读/认证29通过，5PG未配置skip原样留存；本轮统一MFA两库另实测，不把旧skip改成通过|

早期browser到browser7、controls到controls3的真实失败/修复及略超3秒的performance记录留本地。最终正向性能连续三次2.959s, 2.434s, 2.950s，不以旧结果替代；初始化约22秒单列。

## 2. J01—J23逐项

|编号|Codex实际结果（注明环境）|证据索引|人工点击|预期|
|---|---|---|---|---|
|J01|通过：正式A无预算/期限七具体代码Plan，T=20,000；三板块、原金融参数|browser-final＋model-unit-final|理财→直接操作→普通推荐，换批勾选七代码→继续核对|不问本次金额/期限；不是全现金假正向|
|J02|通过：实际UI滑块50%→50.1%，活钱7,200及原U不变；preference生命周期复验|browser-controls4成功检查＋lifecycle-final2|调整“待提交进取比例”→应用本页调整|目标守恒、可准备；不漂移原区间|
|J03|通过：真实R2→R4手选0/0.000001的A422，最终0/1/100000分HTTP409；两库最终accept亦拒绝|normal-actual-risk-and-filters＋bank-controls-final＋final-affected|用模拟R2账户只查看风险原因；恶意HTTP由Codex执行|无订单/占款/扣款；原Plan v1保留；无切A/C入口|
|J04|通过：普通真实正向3项，债券3项；高股息no_theme_match；货币无映射unavailable；主题AND/OR/风险分类边界复用单测|browser-final＋normal-actual-risk-and-filters＋pool-tests|推荐类型普通/类别债券/主题高股息→按条件推荐|无匹配、缺来源、越风险解释不同；主题证据不编造|
|J05|通过：七具体代码跨批保留；ACUI移除；实际两代码比较＋关闭不改账务|browser-final＋browser-comparison|换批多选→比较已选产品勾选2–4→关闭|具体代码不串；已知同基金去重，未知不称独立|
|J06|通过：真实公开NAV详情/走势图；一历史点不伪造曲线，未知指标null；0000153282点包保留|browser-final＋browser-comparison＋source-path-audit|已选产品详情→切观察窗口→关闭|正确代码/日期；项目等级与模拟费用明确；NAV变化非总回报|
|J07|通过：规范引用/整数分差额；滑块可prepare；旧A修订后preview/confirm/verify/execute均STALE_PORTFOLIO|browser-controls4成功检查＋canonical-unit＋preview-version-guard|校验比例→准备预览→回修改比例重新校验|旧预览不能沿用；失败保留原方案；prepare不占款|
|J08|通过：七真实具体产品一次本人确认＋现行统一SMS，7受理→7结算；双库7产品账务|browser-final＋final-affected|预览整份→勾本人确认→短信→获取/查看模拟短信→验证整份|不漏第6/7笔；不再有原点击授权|
|J09|通过：浏览器计价前撤单释放100元占款/无退款；预览无占款、受理占款、结算只扣一次|browser-cancel-final＋browser-final＋bank-controls-final|订单表未计价订单→取消→明确确认；另保留七产品用于结算|已结算不可撤，现金不虚增；已撤不成持仓|
|J10|通过：00001510份及00524212.34元两种输入真实UI，T+1应收/T+2到账；两库FIFO/精度|browser-exits2＋history-two-db＋final-affected|持仓表赎回→份额或持仓市值→换算→本人确认/短信|冻结与可赎分开，未到账不可买入；赎回不是新购资格|
|J11|通过：7非空持仓/订单分页每页5，跨页选中保持；本页全选/上下文清空前端检查|browser-final＋frontend-final|两表分别每页5→勾选→下一页→上一页→清选择|订单ID/持仓key不混用，scope改变清空|
|J12|通过：本人问卷/按钮/七购买/赎回/收益/接管均无新messages调用；LLM offline|各browser_results与normal-result|仅按按钮和可信表单操作|本轮新LLM/NLP调用0；不以mock模型代通过|
|J13|通过：应收2443分时现金720000、T=2000000，应收未可付；冻结不重复；缺估值两库保护|browser-exits2＋history-two-db＋asset-summary旧有效两库例|查询资产、在途与可付；缺数场景只核对null|不补零，不重复计入冻结/应收|
|J14|通过：实际客户暂停/继续/接管→操作员领取原7项/核对/建议/交还→客户重验；共享回归复用|browser-operator3＋bank-controls-final＋shared-regression历史限定|未完成任务申请接管→授权操作员登入/领取/读取/说明/交还|无代签、交还暂停；旧未提交授权失效，受理订单保留|
|J15|通过：其他合法用户原A Plan404、伪操作员403；刷新旧9订单/7持仓保留；scope/F18两库隔离|normal-actual-risk-and-filters＋browser-operator3＋browser-exits2＋final-affected|另一账号不能看到原客户Plan；刷新/退出重进|主体由JWT，客户端角色/金额不能越权；已提交账务不撤回|
|J16|通过：通用category/theme/general/数量/page和两赎回模式，严格unknown字段/单位测试|normal-actual-risk-and-filters＋pool-tests＋frontend-final|用不同合法类别/粒度/页数|同handler/schema；未知键拒绝，不限定示例关键词|
|J17|通过：正常main注册handler/canonical源/唯一snapshot；03索引/依赖/离线包/本文完整|normal-result＋portfolio_handoff＋handoff最新节|查接线文档与startup命令|不把本轮无NLP理财工作台当统一总Agent全部完成|
|J18|通过：未请求理财前概览T20k，与A同scope/snapshot；正常50k普通源同口径；多账户API|browser-final＋normal-result＋history-two-db|登录概览→理财查看总资产/可付/应收|账面借记现金＋市值＋应收，冻结不重复；多账户API非浏览器|
|J19|通过：最终7产品受理/结算，现金20k→7200、7lot，公开NAV日期≠模拟计价日；赎回应收唯一到账|browser-final＋browser-exits2＋final-affected|结算后刷新订单/持仓/资金流水|investment保留，申购冻结不是已完成支出，撤单不生假退款|
|J20|通过：本期/对比/总数/分类/异常/文字/明细投资排除及正常消费保持，SQLite/PG范围|final-affected test_wealth_bill_boundary＋历史test_bills|进入账单→本期/比较期/异常/明细，查投资现金流|消费统计排除investment；资金流水仍显示投资真实模拟流|
|J21|通过：非空独立双表、整体与000015单产品收益实际UI|browser-final＋browser-exits2|查询订单/持仓→详情/收益→整体/单产品|订单非可赎份额；费用/版本与资金scope一致|
|J22|通过：日/月/年/累计＋日历；本金/到账非盈利、费用一次、全赎贡献/FIFO两库既有实际例|browser-exits2＋history-two-db actual_returns|切日/月/年/累计及收益月份、已赎贡献|不将现金入账当收益；少历史/费用未知不算0|
|J23|通过：真实稀疏行情/缺估值/缺成本返回partial/null并列原因，月日历实际渲染，无模型估收益|browser-exits2＋final-affected＋model-unit-final|看日期/覆盖/缺失，点月日历日格|不填未来/不假0；未完成A金融研究明确保留|

“通过”指本轮修订范围及对应测试层，不表示所有参数/所有数据库都做浏览器，也不表示人工已验或金融效果通过。高股息无匹配、货币映射缺失、稀疏行情null是正确边界，不编造正向。

## 3. 失败记录和修复复验

记录日期、入口/Action、用户与scope（不记密码/验证码）、HTTP业务码、A/B版本/快照、产品/披露/政策版本、原操作号，余额/占款/可赎/应收前后、截图/日志。交易结果未知只能原号查询，不能新号自动重发。若任一必须项实际失败，本轮状态改为未通过，定点修复后复验该路径与受影响约束，不能以模型文字代替银行结果。

最终入口恶意HTTP至少R2→R4手选、0/极小权重、公开更高评级/分类冲突、旧画像/评估/资料/授权、context错配、手工角色伪装；未知/已受理交易不因重新聊或关窗被撤销。A/C已删除，历史C绕行单测只保留资格回归而非操作验收。原模型、正常业务和任何日常库不清空。

## 4. 人工与发布

仓库外《理财场景_人工验收指南.md》是实际服务/账号/步骤索引。当前人工尚未验，等反馈后才考虑main整合、必要复验和发布。不会自动push/PR/合并。本地工程文档不包含私有任务书、阶段回传、密码、OTP、数据库和采集缓存。
