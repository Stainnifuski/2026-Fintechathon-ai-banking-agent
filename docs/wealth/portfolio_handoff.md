# 第2轮联合实施回传（2026-10-14）

## 当前结论
本轮主要实现和投资、撤单、普通赎回、快赎、T+1/T+2账务主线已实际运行。**尚不能宣布完整联合封板**：当前正式浏览器中人工接管→操作员领取→交还的完整往返还未重新完成；该能力当前双库回归通过；当前UI暂停/继续v1→v2→v3已通过，完整操作员往返仍待补。收益页保留真实缺资料状态，不能据此声称金融历史评价完成。人工验收未通过前不发布。

## 实现与已关闭问题
- 静态资料完成加载后才报告portfolio_ready。目录及明确事实只读路径30秒、详情15秒；前端推荐/Plan60秒、代理65秒。交易预检与提交时限不全局扩大、不自动重试写入。只读失败返回可恢复503。
- 固定活钱、原U、ETA=.25、DELTA=.01及原产品投影保持。服务端计算可执行离散区间和步长；百分数仅转换一次、金额为整数分，调整同步完整产品目标及版本，旧预览移除。
- prepare结束旧SQLite读快照后获取原有写锁；远端预检后仍锁定重读Plan、资产、版本及映射。最初并行实测出现database locked/500，修复后的正式算例与双库prepare复验通过，不把首次失败隐藏。
- 删除旧研究工具、基金目录壳及独立理财对话入口；保留正式按钮入口、本人问卷、context选择、现有MFA、任务/操作员及未来03接口。目录标签只在同登录人/context内复用显示；账户/持仓/控制及执行许可仍实时校验。交易后不重复读取完整目录。
- 清除原始费用JSON、长政策串、空指标及单点曲线。具体代码详情按需历史查询，弹窗切窗口复用本次结果，不全池采集、不新增永久历史缓存。现金管理银行理财的模拟NAV行情与货币收益分开。
- 不展示保本或保本未知标签；所有产品提示本金可能亏损。活钱银行准入为核实R1现金管理及完整快赎规则；风险、窗口和每日额度保留。

## 产品与来源
真实基金13,856：货币278、债券4,093、混合8,444、股票1,041；原始目录27,998，不缩池。另复用18个B001—B018及原MW002，共19个冻结模拟银行定义。当前R4/20,000元/2026-10-13业务日实测可选B017、B018、MW002；其他12个不符合已确认银行两类政策，B013/14非开放日，B015/16已过申购期。19不等于19个当前可购。
模拟SKU按冻结配置中的具体身份选择，不额外要求家族登记；已知基金关系继续去重，未知关系不编造或称独立风险来源。银行源配置：config/wealth/portfolio_bank_supplement.json；绑定政策bank-cash-risk-quick-no-guarantee-v2；附加dataset joint-bank-existing-v2:66cbd10c27efcbc79716821d。原风险、净值及快赎合同保留；未增加保本属性。
全量真实政策仍real-public-paper-core-risk-20261013-v2。货币为项目模拟固定1元/份、真实NAV未知，不把万份收益/七日年化转NAV，亦不自动快赎。真实基金公开更高风险或分类冲突仍拒绝。

## 固定J02正式证据
R4由专用用户ROUND2在正式浏览器九题问卷提交、本人确认得到；账户初始借记现金20,000元、持仓/占款/应收0。不传p，选择000003 v2及671030 v2，规则允许5,760—7,040元。

|阶段|版本|活钱元|稳健元|进取元|
|---|---:|---:|---:|---:|
|初始50%|1|7200|6400|6400|
|滑块45%|2|7200|7040|5760|
|滑块55%|3|7200|5760|7040|
|手动50%|4|7200|6400|6400|
|离散下限40.2%|5|7200|7654.40|5145.60|
|离散上限59.8%|6|7200|5145.60|7654.40|

正式API、浏览器均验证上述序列。范围[.402,.598]、步长.001；前端90%拒绝，API .9返回422、版本/目标/订单/占款不变。API初始Plan19.915秒；45% prepare1.388秒，55% prepare1.272秒；二者正确金额、无订单占款。浏览器45/55旧预览移除并重新prepare成功。原U保持有生命周期单测及固定边界证据；不把理论算式当实测。

## 实际交易与账务
另选B017/000003/671030的同context三产品方案：1600/6387.20/6412.80元。一次本人确认及现行统一SMS，三笔受理、冻结14400元，无可赎持仓。计价前取消000003释放6387.20元，不生退款；T+1另两笔结算，现金11987.20元，两只可赎持仓。
普通赎回671030的10份及市值12.34元（2.7893份）分别本人确认和统一验证；受理时占用份额。B017快赎100份即SETTLED，到账105.16元，现金12092.36元，剩1421.4499份，日限额10000元及00:05—23:55窗口绑定。T+1两笔普通赎回应收56.58元，现金不提前增加；T+2各自到账，现金12148.94元、应收0、无占款。全部成交与时钟均在独立测试库，不代表真实交易。
日/月/年/累计及月日历实际页面已打开；缺有效NAV的期间显示partial/null与原因。公开冻结NAV日期不改成业务日，不能把模拟测试时钟推进日期当真实未来资料。研究历史总回报/样本外金融效果仍未完成。

## 当前验证记录（集合有重叠，不累计）
- 119通过：生命周期、全池银行、prepare、快赎、赎回输入、联合提供器；SQLite及独立bank_test_* PostgreSQL。
- 16通过：锁修复后prepare与structured_portfolio双库复验。先前未配置PG的10通过/8跳过记录不算PG通过。
- 97通过：候选及R1现金管理/无保证政策身份回归。
- 56通过：双库任务控制、订单/持仓、账单边界及实际收益；12通过为现金管理表格口径修复后的重叠复验。
- 3通过：最终只读超时/不重试写入及银行准入单元测试；33通过为既有前端选品、配置、表格、资产摘要回归。
- 类型与正式生产构建通过；存在既有大chunk提示，未另开打包优化。
- 实际API：普通换批11.665秒、债券9.594秒、快赎7.608秒、银行理财8.031秒，HTTP200。R2手选R4及0/极小权重A422；最终银行0分schema422，1/100000分风险409；订单与占款0、原Plan v1保持。R1全活钱滑块null、其他用户Plan404。
- 正常backend.app.main生命周期在独立副本实际启动、portfolio_ready=true，冻结资料27.467秒。首次副本U1/U2与正常U001/U002种子手机号冲突而拒绝；仅在第二独立副本分离测试手机号后重验，无改原账户/种子策略。
- 离线空目录安装、增补28017目录/13856真实池/19银行定义通过，禁网检查外部请求0。包sha256 9e28f1ca8b5db9d9e49e0488a278c7376b88a85f5580c1020441c42fa526b228，.deps/round2/wealth-round2-offline-20261014.zip，不含env/密钥/数据库/私有原始缓存。
- 原工作区577文件、.env与HEAD哈希核对无改动；3010/8010/5432保留。本轮8015烟测已关闭，3014/8014保留。

## 剩余与停止边界
必须补当前正式UI的完整人工接管、操作员领取/交还往返；双库通过不替代该浏览器证据。普通基金详情的最新上游历史可能失败，正确隐藏不足图表；不能保证上游始终可得。所有必要当前功能未完成前不宣称封板，等待人工审阅及未闭合项复验。不恢复采集、不接新LLM、不远端写入。

## 本地提交与原始证据
08aa1bbdd678a9ec524fbc928c172e0f7c1c1fd6 refactor(wealth): simplify formal workspace and synchronize previews
f22656925560582897c6a4c1ca52c1b3f08bf9e3 fix(wealth): align executable slider and existing bank scope safely
最终工程文档提交见git log。私有原始日志/截图/运行库仅存集成目录.deps/round2及.deps/joint_validation；不提交。golden-api.json、catalog-api.json、guards-api.json、state-1/2/3.json、state-quick.json、各测试log及normal-compatible.err为实际证据；首次失败日志保留。state-ordinary-accepted.json是失败采集后误复制旧文件，**不是有效赎回证据**；应使用state-ordinary-current.json。

## 工程接法与准备

A源为backend/app/services/orchestration/wealth_portfolio_joint.py；正式GET /api/v1/wealth/portfolio/snapshot、GET /products/{code}?context_id=、POST /recommendations、POST /plans、PUT /plans/{id}；B的POST /portfolio/plans/{id}/prepare与现有plans preview/confirm/verify/execute衔接。前端唯一正式Wealth/index挂StructuredActionPanel、ProductPicker、StructuredPortfolioReview、AllocationReview/PortfolioAllocation及原PlanExecution/SingleTradePreview。旧主题410、A/C切换退出。没有新总Agent或LLM路由。

准备方式（仓库相对路径）：按scripts/wealth_portfolio_package.py install导入已交付离线包；确认config/wealth/paper_products.json、portfolio_bank_supplement.json及内容指纹一致。设置自己的隔离DATABASE_URL，使用python -m backend.app.services.bank.wealth_data_cli import --file config/wealth/paper_products.json，随后init --user-id <本人合法演示ID> --dataset-key real-public-paper-v1:b498da40b510a409211716f0；再python -m backend.app.services.bank.wealth_bank_supplement_cli --user-id <ID> --context-id <本人已授权模拟context>。附加银行数据只能由服务端受控命令绑定，客户端不能扩大scope；CLI不更改余额/MFA许可。普通启动python -m uvicorn backend.app.main:app --host 127.0.0.1 --port <本地端口>，内部BANK_API_BASE_URL配置对应本地银行服务，原MFA/SMS及数据库依赖沿用工程环境。不得提交.env、OTP、运行库或阶段回传。

相对00：增加服务端可执行slider_bounds/slider_step/slider_reason及精确代码资格读取、冻结银行补充和受控context绑定；调整活钱R1现金管理+完整快赎规则准入、去保本显示、当前模型默认具体代码排序；移除旧研究/对话壳及主题/A-C入口；保持全授权资产公式、原U/ETA/DELTA、投影、风险、确认/MFA、有效期、幂等、审计和账务规则。冻结现金不重复计入T，未知估值不补0。01负责配置与材料适配，02继续负责交易/事实与正式接线，03/统一LLM在后续任务；本轮不改02源分支、不发布main。

闭合条件：补当前正式UI人工接管往返、核对最终入口稳定性、用户人工主线通过；仅在后续授权发布任务再整合main及处理冲突。研究历史金融效果仍独立记录未完成，不把当前模拟交易通过当金融评价通过。
