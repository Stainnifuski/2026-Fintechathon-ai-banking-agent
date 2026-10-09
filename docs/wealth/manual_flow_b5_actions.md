# B5 第一阶段：结构化动作接口与手动面板（2026-10-09）

基准：02任务包B5、00第6.8节。此阶段不是B5/B6最终完成：A配置/推荐服务未合并；原产品选择、比例修订、prepare及所有聊天/人工接管草稿控件还需统一接入并完成整体验收。原金融保护和原任务控制保留。

## 试用入口

- `/?wealth-dev=1` → 统一手动操作。原F11配置/候选样例、F23双表和本人银行入口保留。
- 原理财工作台聊天侧栏 → 直接选择操作（无需聊天）。使用相同组件及结构化结果，不调用自然语言解析器。
- 四个快捷按钮：理财规划、产品推荐、推荐债券基金、想买高股息产品。另可选择六类目录参数或高股息主题。A未安装时返回blocked及原因，不以固定产品充当模型输出。
- 查询我的订单/持仓、页大小和翻页；选择按表类型保存，跨页保留，切换表或用户/资金范围清空。收益日/月/年/累计摘要；产品资料（风险、最新净值、行情覆盖点）。原入口仍提供完整收益日历及产品详情图。
- 赎回按份额、市值、持仓比例填入；一个明确选中持仓可直接生成可信预览，无对象或多只只填一个金额返回clarify。多仓补选一只后重新核对，不自动均摊。
- 订单资格只读显示资格、原因及后果。实际撤单仍从原订单表本人确认；confirm/verify/execute/cancel没有混入actions。

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
