# 理财01／02联合检查与人工验收

更新：2026-10-12。**本地集成、未可封板、人工待验**。表中partial/passed限定明确证据，未验银行或浏览器部分不能补勾。真实合格池为0，J08未通过；没有发布到wealth/main/02或创建PR。

## 1. 当前实际运行入口和账户

正式联合URL：`http://127.0.0.1:3013/`；对应隔离银行/应用：`http://127.0.0.1:8013/health`，OpenAPI：`/docs`。这是正常App正式理财页面，不是portfolio-dev或录制Plan。页面显示明确本轮隔离测试标识。原3010/8010保持。

合法演示账号：测试server公开构造的张三/U1 `13800000001`，操作员李四/U2 `13800000002`；密码获取位置为 `tests/browser/wealth_joint_server.py` 的seed hash_password输入或项目既有演示账号说明。不要把密码、JWT或MFA因子放在截图、回传或Git日志。本人统一验证沿用原组件；此测试seed没有给用户绑定验证器，人工购买须按现行三号流程登记合法测试因子，不能恢复点击模拟授权。当前J08已被产品材料阻塞，不要求用户先绑定来掩盖此问题。

人工审阅前默认关闭本轮服务；启动步骤如下，保留每轮数据库，不覆盖旧文件：

```powershell
# 从本地 integration/wealth-01-02 检出根目录，使用已装requirements的环境
# 不使用含真实运行数据库/密钥的.env启动隔离写验收
$env:WEALTH_JOINT_DATABASE="$(Get-Location)/.deps/joint_validation/$(Get-Date -Format yyyyMMdd-HHmmss)/joint_bank.db"
$env:LLM_MODE='offline'
python -B -m tests.browser.wealth_joint_server
# 第二个终端，在同一检出的frontend目录（npm ci已完成）
$env:BANK_API_BASE_URL='http://127.0.0.1:8013'
$env:VITE_WEALTH_JOINT_TEST='true'
node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 3013 --strictPort
```

测试server拒绝已有DB文件，独立进程生成内部凭证；正式服务不能使用该server的测试seed/日期工具。数据包按portfolio_handoff.md准备，缺包503不切fixture。结束在对应两个终端Ctrl+C，仅关闭本轮服务。

## 最终正式浏览器证据（2026-10-12）

`browser-finalproof/browser_results.json`：20子检查，pageerror=0、requestfailed=0，未调用messages或新模型。实际九题+本人确认R4；总资产2,000,000分；A与B同scope/snapshot；正式A Plan v1（0产品、全现金）显示唯一配置图；Direct GET读取同Plan，PUT修订至v2；实际HTTP关闭该Action会话后GET旧Plan=409 PORTFOLIO_SESSION_CLOSED。修改前浏览器错误和首次测试日志保留，不用旧图证明最终代码。

**边界：** GET/PUT和关闭相关Action会话是受控HTTP步骤；不能说这些都是人工点击。普通导航关闭的是聊天窗口，不等于必然关闭手动Action会话。最终harness明确关闭plan响应的session_id，不误把其他聊天会话当该Plan会话。转账/账单/卡片/订阅/生活服务实际页面加载；空库页面未证明所有非空行操作。Action返回候选业务unavailable、count=0和真实原因；整个七产品购买链仍blocked。

## 2. 实际检查结果（各集合分开，不累加重叠）

|集合/实际命令|结果/证据|
|---|---|
|A portfolio单元、A API、B asset summary/structured prepare、joint provider、bank|495 passed, 48 skipped, 1 warning in 129.47s (0:02:09)；原日志 backend_verified.log|
|实际银行returns、B6 controls、Action、transfer/subscriptions/bills/cards/NOVA queries|79 passed, 14 skipped in 51.88s；shared_actual_final.log|
|joint provider写控制补验|13 passed, 12 skipped in 32.15s；controls_verified.log（与主集合重叠，不相加）|
|创建前会话复核修复后的lifecycle/API/joint复验|69 passed, 12 skipped, 1 warning in 33.88s；lifecycle_verified.log|
|全部frontend/tests/*.test.ts，Node strip-types|66通过/0失败；frontend_states_verified.log，录制F相关用例仅契约/状态|
|tsc --noEmit；vite build；独立bank/fixture build|通过；types_final/build_final/build_independent_final/build_fixture_final.log；正式包仍有既有>500kB chunk提示|

主pytest使用Python -B、隔离SQLite，明确未配置BANK_TEST_DATABASE_URL的PG例skipped。实际API非法/控制409/422及客户operator403为预期边界，不计网络失败。Starlette httpx弃用warning与功能失败分开。没有真实模型联调要求，本轮新LLM调用0，未调用外部模型。

完整命令：根目录 `python -B -m pytest` 加所有 `tests/unit/test_wealth_portfolio*.py`、`tests/integration/test_wealth_portfolio*.py` 及上表明确命名的测试。PowerShell须用Get-ChildItem先枚举文件再传数组，不能把未展开通配符当通过。前端根目录 `node --experimental-strip-types --test` 加tests各*.test.ts。日志在原检出忽略目录 `.deps/wealth_joint_20261012/`；不提交原始DB或缓存。现成受控浏览器：根目录 `node tests/browser/wealth_joint_check.mjs`，环境 `PORTFOLIO_PLAYWRIGHT_MODULE`、`PORTFOLIO_BROWSER_EXE`、`WEALTH_JOINT_EVIDENCE` 指定既有工具/输出，首次freshDB实际九题；同库重跑会正式触发assessment次数限制，应保留旧库另开freshDB，不能解除限制。

## 3. J01—J23逐项记录与人工操作

|编号|实际状态|Codex实际证据／缺口|人工点击说明|预期结果|
|---|---|---|---|---|
|J01|partial|正式无预算/期限Action与20,000全现金A图已验，非投资正向|点击理财→直接选择操作→理财规划|全scope三板块；不询问本次金额/期限；0候选不得显示购买成功|
|J02|partial|A模型/生命周期及组件状态通过；正式全现金无非活钱滑块，20/30/50联动仅隔离|看三板块与比例；有非活钱方案时调整滑块|T分母、守恒、普通调整原U不漂移；不能手造比例算正式通过|
|J03|partial|推荐资格与实际最终R2→R4零/微权重/C负向通过；正式合法候选仍0|不可购买产品保持禁用并解释；恶意参数由Codex测试|具体份额等级拒绝且无订单/占款/流水/扣款；已有超风险持仓允许减持|
|J04|partial|结构筛选资格/API/前端状态通过，正式unavailable原因已取证，合格正向缺材料|改推荐方式/类别/主题→按条件推荐|参数化筛选；无匹配/超风险/停售/来源缺失区分|
|J05|blocked|跨批/多选/A-C前端和A单元证据；正式池0不能实际换批>5|有合格池后勾选→换批→可选详情/比较→切份额|每批至多3家族、选择保留、失败不改旧选择、同具体份额身份/费用|
|J06|partial|未知字段与资格检查通过；没有正式合格产品指标浏览器证据|产品资料→选择原生MF/MW→详情|显示真实名称/具体份额/日期，null未知；NAV描述不是总回报|
|J07|partial|A修订和B前端比例/预览状态测试通过；真实投资比例与旧授权失效完整浏览器未闭合|有合格方案后调整比例→整体预览→再调整|原Plan失败保留、整数分守恒，旧预览/授权拒绝，受理订单不回滚|
|J08|blocked|实际正式A合格数0，未走7项购买；B录制7项测试不能计本项|材料闭合后选7项→整份确认→统一验证→提交|完整绑定七子单，单次确认，不漏项/重复扣款；当前禁止勾通过|
|J09|partial|B实际SQLite预留/撤单/结算不双扣及共享汇总通过；未串正式A正向浏览器|J08可运行后预览/提交/撤单各看余额及订单|prepare无占款；受理占用；撤单释放；结算不重复|
|J10|partial|B金额/份额输入和真实银行部分赎回/到账收益测试；联合持仓浏览器未跑|有合法持仓后选持仓→选择市值/份额/比例→核对→授权|四位份额、冻结/可赎分开、到账提示；退出不套买入等级|
|J11|partial|双表分页/独立选择前端66集合覆盖，通用Action查询HTTP通过；实际多页浏览器未造数|查询订单/持仓→各自翻页多选→切context|跨页保留、当前页全选、切context清空、不同ID不串|
|J12|partial|新按钮流程及A调用无messages/NLP请求；API禁止模型；整交易浏览器主线blocked|仅按钮与表单完成操作，勿输入聊天|无模型请求不等于主线交易已完成；查询不得提交交易|
|J13|passed（SQLite/API）|B缺NAV/冻结/应收原子汇总与A F18精确scope/lot版本通过|看资产总额/可付现金/冻结/应收；缺估值显示未知|T=账面现金+持仓市值+赎回应收，冻结不重复计入；应收不可支付|
|J14|partial|实际B控制HTTP、A闭环和转账/卡片/订阅/NOVA查询回归通过；全DAG浏览器未跑|原有业务导航；任务详情/接管/交还|复用旧引擎/授权；Action不冒充raw_text，无跨业务覆盖|
|J15|partial|A归属/版本、B真实JWT/跨context拒绝、原目录hash保护；另一用户完整UI未跑|另一演示账号不可读取旧Plan/订单；切context|服务器身份/范围隔离，失败不改账务，原库不清空|
|J16|partial|严格Action、通用筛选/赎回/分页测试通过；正式推荐正向无材料|任意合法类别/主题参数及数量，不局限四按钮|unknown键422，受控对象/单位/schema；不靠关键词假成功|
|J17|partial|正式handler/DTO/组件/资料/错误索引已交付；J尚未闭合|审阅两工程文档和03索引|不开始新LLM/NLP，不把本轮交付误称封板|
|J18|passed（SQLite/API及单账户UI）|未请求理财前两页T与A同scope/snapshot20,000；多账户/context API通过|登录概览→理财，尚未请求看总资产；交易后重新读|全授权同口径同值；多账户API证据不冒充多账户浏览器|
|J19|partial|实际银行账务/唯一到账/撤单和收益测试通过；正式A交易后的UI链blocked|有交易后查询订单/资金流水|结算才有investment、冻结非已完成扣款、撤单无假退款|
|J20|passed（隔离API/统计）|test_bills理财排除、精度/异常/分类/明细/文字与普通消费通过；页面空库仅加载|有混合流水时账单查询本期/对比期→异常/明细|理财现金流从统计/文字排除；普通消费保持准确|
|J21|partial|双表/收益入口前端状态和B实际收益HTTP通过；非空完整UI未跑|查询订单/持仓→分别详情/收益→整体收益|两表独立，订单不能当可赎份额；费用/版本/范围一致|
|J22|passed（SQLite服务/API）|实际结算F20-F23、本金/应收到账/费用只算一次、全赎贡献及快赎时点通过|选择日/月/年/累计与产品、全赎后查询|银行真实模拟持仓收益正确；不等于A历史金融研究通过|
|J23|partial|实际缺NAV/历史成本/未发布NAV为unknown及银行收益禁止模型测试通过；所有页面粒度点击未遍历|切日期/粒度/产品，查看coverage与缺失|不填未来/假0，准确模拟标识；无NLP主线|

passed范围限定在SQLite/服务或API，不代表未运行的对应完整用户点击。整条正式A投资正向链以及必要J仍有缺口，不能封板。R2/R4最终负向明确使用测试录制不合格target作为攻击输入，仅用于测试最终防线，不计算正式A正向推荐通过；实际比较SUITABILITY_MISMATCH且核对DB无副作用。A/C使用测试明确同家族A(R2)/C(R4)规则，非用错identity先被挡住。银行原执行授权/等级降级与版本/过期检查复验保留。

## 4. 失败、最小修复和复验索引

1. 重复snapshot/缺provider依赖提前构造bank、B录制snapshot缺全schema、null估值被误允许：唯一路由/DI顺序/fullcanonical fixture与完整性校验修复，backend_verified通过。
2. 前端共享DTO与旧录制差异、缺main依赖、Node类型导入扩展名：生产类型canonical，开发录制显式解析，锁依赖npm ci，66状态/类型/三构建通过；没删新main依赖。
3. 真实顺序读取后时间/assessment版本误冲突：按真实完成瞬时复核currentfacts，校验上下文未变，不回填虚假时间；模型改变上下文明确MODEL_FACTS_CHANGED，前述主集合通过。
4. 最终0/微权重R4被差额算法先忽略：先对所选实际版本逐项购买适当性，再生成差额。已有超风险持仓只保持/减持允许，增持拒绝。
5. 会话绑定后新Plan尚未入store误404：create最终复核当前Action会话，不查未入store新ID；get/revise仍查绑定实际会话。复验lifecycle_verified及最终browser取证；不能复用修复前截图证明最终版本。
6. 旧离线凭据拒绝kind误clarification：改refusal，不扩展解析/NLP；原JWT/注入保护测试通过。
7. 浏览器复跑assessment次数限制409，以及一次启动端口占用：保留旧库/日志，核实PID/命令行后只重启本轮，fresh库重新实际答题；不放宽银行次数/端口规则。异步请求等待真实结果，HTTP200不可冒充业务成功。

每次失败保留日志、实际版本/操作号、DB前后；按相同前置复验失败用例及受影响合法对照。持仓收益测试是真实隔离银行结算，不是response mock；模拟银行单项通过仍不替代A→B联合交易。

## 5. 当前人工可验步骤（无需手写JSON）

1. 启动后登录张三；概览先看20,000元，再点理财，核对相同资产总额与来源；页面须显示隔离联合标识。
2. 展开本人风险评估→填写九题→勾本人声明→提交→本人确认。浏览器自动检查选择各题第三项得到R4，是测试前置而非替用户答题；人工可真实回答，期望不同等级如实变化。
3. 展开“直接选择操作”，点“产品推荐”及类别/主题筛选；当前应显示0合格及来源/身份/风险等原因，不能假成功。首次加载冻结资料可能较慢，应等待状态结束，不反复点击创建。
4. 点“理财规划”。当前应由正式A创建20,000元全现金方案、唯一三板块图、非活钱比例不可用（不是NaN）。此步骤只证明读取/计算/展示，不能勾七产品交易通过。
5. 打开产品资料中原生模拟MF/MW资料或比较，检查名称/版本和模拟标签；不把其出现视为A合格候选。查询订单、持仓、收益，初始空表/历史覆盖按实际显示。
6. 切转账/账单/卡片/订阅/生活服务，观察正常加载；退出理财相关会话关闭。新请求重核当前事实；若相关Action会话明确关闭，旧Plan拒绝。普通导航关闭聊天窗口不等于手动Action会话一定关闭；已受理订单保持不变。
7. J08—J12、非空双表/收益全点击和操作员完整流程：**当前待条件闭合**。不用自行手写JSON绕产品校验，不把dev fixture当正式入口；补材料后按表中完整步骤验收并记录实际MFA/操作号及账务。

人工反馈写实际步骤、时间、截图和状态，勿附验证码/密码。没有人工结果不代签“通过”；阶段4发布须必要J与人工通过后再获取新main并整合/复验，当前不执行。

## 本地保存与服务状态

本轮代码提交 `82681abc598ca90b45265c6101573b1090908675`。文档在下一本地提交保存；最终SHA见仓库外回传。3013/8013专用服务已核对并关闭，原3010/8010保留；旧3021/3022/8051/8052无监听，旧交付smoke PID已不存在。数据库与失败证据保留。未远端写入，人工待验，禁止据此进入阶段4。

## 2026-10-13 零候选处理：当前工程状态

旧八MF/MW与冻结S/B目录错位、版本/材料读取路径遗漏已修复；默认联合测试server使用已有fixed_payload完整78，不调用旧轮次清理upgrade。完整目录78→46范围内→34合格份额/18家族→0材料家族→0DPP。产品目标/策略/基准正文原定义缺失，等待用户决定新增目标材料；七产品主线/J08和人工验收仍blocked，不能封板。原“PG不可得”只代表前轮：本轮PG51+SQLite51+其他2基线104通过，修复PG19+SQLite19+契约45共83通过；217 A相关回归/66前端状态/类型build通过，3个正式UI阻塞检查通过，不称投资正向。集合不能累加。

具体根因：MF八在原A集里不存在；S/B原不可变metadata有显式份额/family和基准名称，被bank目录读取路径遗漏；当前只读精确版本映射恢复。基准名称不是基准定义，保留name_only/unknown；两份额同家族。B013/B014非开放、B015/B016过期，S052/S056非开放；R5超R4和银行政策不放宽。

当前derived data `c1e4e964d09cf088e558be8f18c53e84fda75bdf32fcaf0ec1e1d6f3fcbcafa7` / daily `3aad3d4a16ea0cba70e516b4a6d79c317f358870329975f8fa42c857bcffdc65`，输入捕获 `23b28716bb7e47789c4aa194bc8495072af84353acc265bd1359929b0a358520`。新机器除原固定文件/冻结包外需受控交付该snapshot+index，或以新隔离库执行tests.browser.wealth_joint_diagnose_http取得新当前版本；诊断路由仅测试server注册，不进入正式main API。明确WEALTH_JOINT_CATALOG=fixed-full并指定PORTFOLIO_DATA_VERSION/PORTFOLIO_DAILY_VERSION；普通正式配置不可用fixture后备。

待确认的最小定义：SF001/SF004/SF006/SF007/SF018/SF019/SF020七家族14个A/C，只加investment_objective，三类同目标，不编七套不同文本抬DPP得分。批准后v2版本化，风险/费用/NAV/销售/渠道/family/份额不改；不补保本/快赎，不新建七产品白名单。未生成该新设计。批准后才做七产品整份确认/MFA/交易及完整J/人工。源修复代码 `0abee504fe01ea265ee0b7170d6a2a177f0bafec`，原工作区不变、未远端写入，专用服务已停止。
