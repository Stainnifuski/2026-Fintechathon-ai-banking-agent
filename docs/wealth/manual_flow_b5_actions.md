# B5 第一阶段：结构化动作接口与手动面板（2026-10-09）

基准：02任务包B5、00第6.8节。此阶段不是B5/B6最终完成：A配置/推荐服务未合并；原产品选择、比例修订、prepare及所有聊天/人工接管草稿控件还需统一接入并完成整体验收。原金融保护和原任务控制保留。

## 试用入口

- `/?wealth-dev=1` → 统一手动操作。原F11配置/候选样例、F23双表和本人银行入口保留。
- 原理财工作台聊天侧栏 → 直接选择操作（无需聊天）。使用相同组件及结构化结果，不调用自然语言解析器。
- 四个快捷按钮：理财规划、产品推荐、推荐债券基金、想买高股息产品。另可选择六类目录参数或高股息主题。A未安装时返回blocked及原因，不以固定产品充当模型输出。
- 查询我的订单/持仓、页大小和翻页；选择按表类型保存，跨页保留，切换表或用户/资金范围清空。收益日/月/年/累计摘要；产品资料（风险、最新净值、行情覆盖点）。原入口仍提供完整收益日历及产品详情图。
- 赎回按份额、市值、持仓比例填入；一个明确选中持仓可直接生成可信预览，无对象或多只只填一个金额返回clarify。多仓补选一只后重新核对，不自动均摊。
- 订单资格只读显示资格、原因及后果。实际撤单在原表格与统一入口共用的本人确认窗口执行；confirm/verify/execute/cancel没有混入actions。

## 契约

公共POST `/api/v1/wealth/actions`，严格`WealthActionV1`，字段及结果固定外形遵从00。各action的params以对应模型校验未知字段、枚举、金额精度、分页正整数等；不能透传任意JSON、用户身份、accepted或raw_text。session_id必须与expected_session_version同时提交。无会话时建立独立结构化会话，不调用lifecycle.start关闭其他工作窗口。

```
{"schema_version":"wealth-action-v1","request_id":"sample-orders-0001","context_id":"legacy-v1","action":"list_orders","params":{"page":1,"page_size":5}}
```

SelectionContext对象引用：产品用`product_id:version`，订单用原operation_id，持仓用`account_id:product_id:version`。先重读本人事实，再恢复对象；非本人、旧资金范围、已选对象与params不一致拒绝。订单或持仓详情可恢复精确产品/版本。订单与持仓选择不能混用。详情与比较不将表格序号作为产品引用。

列表data沿用object_type/scope_key/rows并添加page/page_size/pages/total；持仓由全量批次按账户/产品版本合并，整数万分份额、未知估值为null，与现有前端表格口径相同。分页不改变选择或本金。收益读取原实际收益handler并核对asset_scope_id。

request_id用于关联与重放：复用既有WealthChatRequest表的WA命名空间，不新增账本表。本人级锁序列化；同号异参409，同号同参重放原响应，不新增授权。非重放会话版本不匹配409。关闭/过期沿用原会话生命周期，已过期预览不能靠新会话执行。银行operation_id由本人及request_id的指纹生成；独立于界面文字。

赎回沿用B4银行转换、新建AWAITING_CONFIRMATION授权，并调用原wealth_task.create_command(holding_redemption=True)，任务仍为READ_FACTS→CONFIRM→VERIFY→SUBMIT→SETTLEMENT→RESULT。不新建节点引擎，不冻结、不成交。HUMAN/PAUSED账户禁止此新预览，执行仍由原服务端重验；实际确认、验证码和原号恢复使用SingleTradePreview与原受保护接口。

## A与2号的接入边界

A可由服务端设置`app.state.wealth_action_handlers`，键为`plan_portfolio/recommend_products/revise_portfolio`；异步handler签名：`handler(*,db,user_id,context_id,bank,params)`。身份和范围由B验证，params已按对应动作严格校验。返回A拥有的PortfolioPlan/CandidateGroup；本轮未安装真实A提供器。测试注入按filter参数返回结果的替身证明分派，不在演示或生产默认安装录制推荐。

prepare复用既有`app.state.wealth_portfolio_source`的owned_plan/snapshot及wealth_selection.prepare；Selection输入字段与现有F11客户端一致，最终A融合时替换为共同canonical类型，验证不能省略。现在不重做A区间、主题注册、资格或权重算法。

旧`/subtasks`继续严格wealth-subtask-v1且要求raw_text。actions与subtasks互塞422；新流程不调用wealth_dialogue.message、wealth_model.understand或explain。未来2号在/messages或总Agent解析后适配ActionV1，不能把新协议直接送给旧子任务入口。

本阶段result的A数据只接收服务端handler字典/数组；真实A DTO验证、候选换批/多选/比例revision结果的统一卡片和原页面迁移将在下一阶段完成。不得宣称F14—F17/F15整条链路已最终验收。

## 验证记录

- 隔离SQLite动作测试4通过，B4回归4通过，共8通过；8项未配置PostgreSQL跳过，1项既有警告。覆盖严格协议、无NLP调用、同号重放/异参冲突、会话过期版本、真实JWT、跨scope/异主引用、三个推荐filter/A替身参数变化、持仓详情解析、F12多仓单金额clarify、HUMAN限制、授权未确认不可执行、原任务挂接、不冻结、撤单资格和实际收益handler。
- TypeScript检查通过，现有收益/双表/总资产22项前端测试通过，Vite构建通过；保留原大chunk警告。
- 浏览器本人账户：四入口之一返回A未接入原因、持仓空表、缺对象/数量clarify、真实MF001产品资料和整体收益查询。没有造演示持仓/答案/订单。非零交易入口的金融边界由隔离银行测试验证，未在本人演示账户执行交易。

后续：更完整ActionResult卡片及原产品/比例/详情/表格控件迁移、对接A合同/替身验收、人工接管草稿复用，最后B6独立全链路和03交接。当前阶段只提交本地，不推送团队wealth/main。


## B5 第二阶段：就近结果卡片（2026-10-09）

按02 B5的ActionResult渲染要求继续推进，共用组件同时影响开发预览与原工作台。查询表、收益、产品资料各在自己的按钮后显示结果卡片；规划、赎回、撤单资格也就近显示完成、clarify、blocked、加载或失败信息。取消页面底部单一结果区。不同查询结果分别保留；请求中/失败时保留旧数据并明确提示上次结果，范围切换清除。订单和持仓共用一个列表区，切换类型仍清空原选择。

收益卡增加期间、累计、已实现、未实现四项金额和资料完整性，未知值仍显示无法完整计算。产品卡可展开原handler返回的起购、持有期、到账规则，未知规则不补零。修改所选对象、赎回模式或数量以及银行记录更新后清除旧预览及确认入口，须重新核对；原MFA/任务控制不变。

浏览器实际查询本人空持仓、整体收益和MF001，三个查询卡片距离按钮分别16、16、17像素；查产品后原持仓与收益结果仍保留。展开规则显示MF001起购100元、持有0天、普通到账1营业日，未生成交易。TypeScript、22项既有收益/双表/总资产测试与Vite构建通过（原大chunk警告）。本轮无后端/账务变更，未重跑银行集成套件。B5整体迁移和B6全链路尚未完成。


## B5 第三阶段：完整详情与产品比较（2026-10-10）

按02 B5继续迁移原查询控件。统一操作产品卡增加“打开完整产品详情”，复用ProductDetail；近一月/三月/一年、净值图、规则、缺指标原因通过show_product_detail动作及明确产品版本读取。增加2—4只产品比较，复用ProductComparison及原目录映射，compare_products传递明确产品id与SelectionContext产品版本引用。比较不作为继续交易的前置条件，不改变原配置/订单/持仓选择。原接口仍兼容。

所有统一入口与弹窗共用WealthActionSession，串行发送以继承服务端session_version。排队请求保留具体参数；资金范围切换使旧响应和旧排队操作失效，不恢复旧会话；失败不自动重试预览。组件按userId重建。缺少历史比较指标或销售服务费仍标未提供，不用净值或本人收益替代。

验证：TypeScript通过；4项新会话测试与原22项前端回归共26通过；Vite构建通过，原大chunk警告保留。前后端恢复使用原演示数据库，3000/8000 HTTP均200。浏览器张三账户MF001完整详情可读，连续切换近三月/近一月后显示17个近一月净值点；比较明确产品，未提交交易。无后端/账务变更。

入口：/?wealth-dev=1 → 统一手动操作 → 产品资料 → 查看产品资料 → 打开完整产品详情；或“选择产品进行比较（2—4只）”并勾选产品。原聊天工作台复用同一组件，也取得新能力。A候选/配置返回值迁移、人工接管状态及整条B6联合验收尚未完成。

## B5 第四阶段：事实详情与本人撤单确认（2026-10-10）

按02 DAG的B10O/B17O、B10P/B17P和B14→B15→B16→B09推进。统一查询结果增加订单和持仓事实详情，复用原详情组件；持仓详情可查单产品实际收益。原表格和统一入口共用OrderCancellationDialog，核对资格后逐笔本人确认取消。actions只读资格；实际取消仍调用原受保护接口并提交原operation_id、订单digest及披露版本，ALLOWED、版本/摘要匹配、资料完整且无人工控制才允许。无自动重试；失败保留原号提示刷新核对。组件卸载后停止发起后续取消。

取消完成清除旧选择/资格并刷新资产、订单和任务状态。统一入口增加“刷新账户记录与操作状态”，显示非终态HUMAN/PAUSED账户提示，资料仍可查询；后端再次检查权限。未新增任务节点、账本或演示交易。

验证：TypeScript和Vite构建通过（原大chunk警告）；29项前端测试通过，包括3项撤单身份/摘要/版本/控制状态校验；隔离银行双表和actions集成10通过、10项未配置PostgreSQL跳过。浏览器本人订单查询正常空表、刷新入口可用；F23原表打开共用资格窗口显示UNKNOWN及不可取消，无交易按钮，原订单事实详情保留申请/状态/日期及未知值。本人账户未造订单，非零取消释放原占用和HUMAN拒绝由隔离测试验证。

入口：/?wealth-dev=1 → 统一手动操作 → 查询我的订单/持仓 → 每行事实详情；有允许撤销订单时勾选 → 核对已选订单撤单资格 → 继续核对并本人确认取消。无本人订单时可从“查看订单”→ F23双表样例勾选后核对共用窗口，仅展示不执行。A候选/配置统一控件、人工接管草稿复用及B6最终联合验收尚未完成。本阶段仅本地提交。

## B5 第五阶段：CandidateGroup消费接入（2026-10-10）

依据00第6.3/6.8节及02 B5，统一推荐动作的ok结果按CandidateGroup接入原ProductPicker，支持多选、有效A/C切换、换批cursor/filter/selected_family_ids、保留跨批选择。候选服务未安装仍返回blocked，不为展示默认安装固定推荐。尚未接入整体计划创建、比例修订及prepare，因此本轮统一候选区不提供假继续交易按钮；原F11路径保留。

消费边界校验只检查组件需要的DTO字段，不新增A算法或第二套PortfolioPlan。无效结构、未知业务状态、重复家族/产品、筛选条件不匹配及当前目录产品版本失配明确拒绝。unknown行情/费用仍为null；risk_mismatch/no_category_match等复用已有说明；风险组可保留被过滤项，但不显示可选产品。资金范围切换清空候选和选择。详情携带明确具体产品版本，修正原ProductPicker仅传ID的问题。

45项前端测试通过（含4项新候选协议/缺值/版本/换批选择检查和12项原F09交互测试），TypeScript及构建通过，原大chunk警告保留。本轮无后端及银行账务改动，未重复银行集成测试。浏览器只能核对真实服务未接入的blocked反馈及原候选组件回归；真实A返回产品卡的浏览器联调待合并，不能宣称B5/B6完成。

试用：统一手动操作→产品推荐/按条件推荐；目前应显示队友服务尚未接入。原“产品推荐 候选多选、A/C切换和比较”仍可使用录制样例核对交互。后续接PortfolioPlan、revision及prepare，再完成共享人工草稿和B6隔离全流程。
