## v1.2兼容扩展（优先于下文v1固定8条/有限理解描述）

GET products返回当前资金上下文，legacy仍8条、新上下文60条。POST research接受dataset_key/query/category/max_risk/horizon_days/page/page_size(≤60)/product_ids(1–3)/period(1m/3m/1y/since)；未知字段拒绝，未知风险不能冒充已评级。返回items/metrics/history/analysis/as_of/datasets/macros，真实产品仅研究规则缺项明示。

GET contexts仅列本人上下文；POST contexts/select需要context_id/version。切换清候选/草稿和旧授权，不复制资金；已受理订单保留原账户。GET assets新增concentration/historical_hypothesis，缺完整现金流时不提供实际回报率。

POST messages新增client_message_id（≤64字符串）以及session_id/version，返回受控产品/对比/方案/command/knowledge卡。原消息重试返回相同响应、原操作号；同ID异内容409，多标签过旧版本STALE_PLAN。PROCESSING并发请求拒绝，超时可恢复；model失败显示MODEL_UNAVAILABLE，不假称成交。

新增错误：DATASET_MISMATCH、DATA_STALE、RULE_VERSION_CHANGED、RULES_INCOMPLETE、IDEMPOTENCY_CONFLICT、STALE_PLAN、ACCOUNT_CONTEXT_UNSUPPORTED。内部research/catalog/contexts接口仍要求X-Internal-Token和可信subject；公开层主体只由JWT确定。

渠道接入：APP/IM可发送同一messages请求，保存session/version/client_message_id；展示cards，确认/模拟授权回跳带JWT的网页，不把聊天“好的”映射execute。原订单号查询结果直接来自银行。当前未完成独立APP/IM端到端验收，不能将网页当作渠道证明。


# 当前授权策略修订

2026-10-01：理财默认点击模拟授权，普通演示库可用；本文旧TOTP/独立库描述仅作兼容历史。路由和状态名不变，现行语义与后续3号接口对接以[模拟授权说明](simulated_authorization.md)为准。

# 理财计算、状态与API契约 v1

状态：已实现，供1/2/3/4/5号联调。仅比赛模拟环境、虚构CNY产品和资金。基线main `bc10571`，任务正文v1.1。

## 角色与认证

前端及助手使用 `/api/v1/wealth`，所有入口要求 Bearer JWT，主体由JWT获取，不接受客户端自行指定用户。5号通过 `HttpWealthBankApi` 调用 `/api/v1/internal/bank/wealth`；内部请求使用 `X-Internal-Token`，不可发送到浏览器或模型。只有4号银行包访问账户、流水、产品、份额与占款ORM。3号保留原风险等级接口，新增独立理财评估和授权；`User.risk_level` 不代替本人评估。

## 公开接口

| 方法及相对路径 | 行为／主要请求 |
|---|---|
| GET products | 8产品，规则/披露版本、产品风险、净值及日期；无评估仍可读 |
| GET compare?product_ids=MF001,MW001 | 2—3个不同产品客观对比 |
| GET questionnaire | 9题、选项、问卷版本、画像版本 |
| POST assessments | questionnaire_version/profile_version、adult/full_capacity严格bool、answers九个0—3整数；单日2次、滚动365天8次 |
| GET assessments/current | 最新本人结果，确认状态、承受能力、到期日，不返回原始答案 |
| POST assessments/{id}/confirm | 确认本人最新结果；不可确认他人或旧结果 |
| GET assets | 现金/占款/可用、批次/锁定/占用/成本/盈亏、订单、应收、估值完整性、业务时间 |
| POST previews | 下文完整命令；银行预检，不占款，返回估算、规则、披露、匹配结果、digest、authorization_id、expires_at |
| GET previews | 本人未消费的原预览，供刷新恢复；过期并不变成有效 |
| POST previews/{id}/confirm | digest/disclosure_version/accepted:true，初始不勾选；再次预检产品与当前资格 |
| POST previews/{id}/verify | proof:六位字符串，仅可信验证器入口；复用现有TOTP防重放 |
| POST previews/{id}/execute | 原号查询后受理，未授权拒绝；同号同参恢复 |
| GET orders/{operation_id} | 本人银行事实，越权或尚未查到404；404不证明永未受理 |
| POST orders/{operation_id}/cancel | digest/disclosure_version/accepted:true；银行核验撤单窗口 |
| GET executions | 本人SUBMITTING/UNKNOWN等应用提交观察，独立于银行事实 |
| GET/POST plans | 列表／kind、account_id、budget_fen、reserve_fen、horizon_days、excluded_products；默认version1 |
| PUT plans/{id} | 当前version及完整表单，新版本新操作号，旧确认失效；在途/未知未核对不能覆盖 |
| POST plans/{id}/adopt | version、items:[operation_id,amount_fen或units]；只可删除或减少当前未提交子单 |
| POST plans/{id}/execute | version；每笔当前强授权后受理，纯购买原子占款；禁止通过单笔执行入口拆开购买组合 |
| GET plans/{id}/progress | 查询每笔原银行号；保留成功，失败暂停后续，未知继续核对 |
| GET/PUT profile | version、monthly_income_fen/monthly_expenses_fen/liabilities_fen、external_assets；改变须重评；外部资产CNY且不能交易 |
| POST goals | 可选goal_id、version、title、amount_fen、due_date、liquidity、priority、allocations；同资产跨目标不能超额，修改产生新版本 |
| POST messages / GET messages/history | 有限离线意图／本人最近会话，session_id/version；只查询或生成卡片，不授权或执行 |

请求模型拒绝未定义字段。现金均为**整数分**；份额及净值为十进制**字符串**，不是浮点数。日期ISO，时间必须带时区。

```json
{"operation_id":"WO-example-0001","direction":"purchase","product_id":"MF001","product_version":1,"disclosure_version":1,"account_id":"A001","amount_fen":100000,"units":"0.0000","currency":"CNY","plan_id":null,"plan_version":1}
```

赎回 direction=redeem、amount_fen=0、units为正且最多4位；正式命令不能用目标到账金额代替份额。账户须本人、active、非credit。强授权10分钟按真实UTC计时，绑定主体、原操作号、方向、产品、金额/份额、账户、币种、产品/披露/计划版本和规范化SHA256摘要；任意非空授权ID无效。受理前重新检查适当性、支付能力、暂停规则、可用现金/份额及真实授权。已受理结算不扩大原范围，不是新授权动作。

## 内部银行接口

GET products；POST assets/precheck/verify/orders/orders/batch；POST orders/{operation_id}/query或cancel。主体传subject_user_id，仅受信内部服务可用；batch含submissions，1—5笔纯申购且同主体，整个事务一起占款。银行不接受模型直接访问这些写入口。

管理员 POST admin/clock {business_at} 和 admin/settle/{operation_id}：同时要求内部凭据、`WEALTH_MOCK_ADMIN_ENABLED=true`、显式隔离库名称标记，普通运行库403；不在模型白名单。默认后台每30秒仅结算已受理订单，无需开启管理员。模拟时钟不改变JWT、问卷或TOTP有效期。

## 计算及估值

净值8位、份额4位，内部份额BIGINT万分之一，现金BIGINT分。申购净额=floor分(总支出/(1+费率))，费用=总支出−净额；份额向下取4位，未换份额的整分退还，低于分的尾差明确存入actual.tail。赎回按可赎回FIFO批次，用各批次规则版本及持有日数费率；份额×指定日净值按分四舍五入，费用按分四舍五入，净额=毛额−费用。成本比例向下分配，整批清空释放全部剩余成本。

按Asia/Shanghai 15:00截止排期，周末及固定模拟休市日2026-10-01至07顺延。计价用**固定计价日**NAV，缺少则等待，禁止未来价。种子2026—2027固定锯齿净值含下跌，不是预测。cash_date/confirm_date是营业日规则。预览不保证成交份额或到账；按金额取出只是估算转换具体份额，不自动追加赎回补差。

资产合计=非信用现金账面+持仓估值+待到账应收；占款是现金子集，不重复加总。账面−有效现金占款=可用额。投资申购/赎回是资金流，不能归入日常消费。外部资产只进全景诊断，缺估值显示不完整而非猜价。

## 状态与恢复

- 授权 AWAITING_CONFIRMATION → AWAITING_MFA → VALID → CONSUMED；过期服务端拒绝。
- 银行申购 ACCEPTED → PENDING_PRICING → SETTLED；赎回再经PENDING_CASH后SETTLED。窗口内CANCELLED释放占用，不伪撤销已成交。
- 5号 SUBMITTING/UNKNOWN/REJECTED 是提交观察；网络或5xx不能声称银行失败。保留原号同参查询/恢复，不更换号。银行相同号同摘要返回原结果，异参409。
- 方案 AWAITING_CONFIRMATION/EXECUTING/WAITING_SETTLEMENT/PAUSED/PARTIALLY_COMPLETED/COMPLETED。失败暂停未提交，已受理/未知仍查询；保留每笔成功及历史版本。卖出全部到账后回到待确认，新的购买版本必须新确认和验证。全方案包括历史成功步骤最多5笔。

## 错误及兼容

共享格式 `{detail:{code,message}}`，不回显凭据。401登录错误/TOTP_INVALID（前端验证码错误不清登录）；403权限/强验证缺失；404归属或对象不可见；409状态/版本/资金冲突；422精度、答案或范围错误。领域码包括 RISK_ASSESSMENT_REQUIRED/EXPIRED、SUITABILITY_MISMATCH、PAYMENT_CAPACITY_MISMATCH、ASSESSMENT_LIMIT_REACHED、PRODUCT_UNAVAILABLE、RULE_VERSION_CHANGED、INSUFFICIENT_AVAILABLE_FUNDS、INSUFFICIENT_REDEEMABLE_UNITS、STALE_PLAN、AUTHORIZATION_REQUIRED/EXPIRED、IDEMPOTENCY_CONFLICT、CANCELLATION_NOT_ALLOWED、BANK_RESPONSE_UNKNOWN。NAV_UNAVAILABLE是等待原因，不伪造终态失败。

原账户balance等元字段保留，新增可选available_fen/held_fen；共享扣款统一可用现金。原手机号/备注/定时/AA/订阅/账单契约不改变。新流水type最大32字符，投资类别明确列入资金流口径。

## 整份确认与演示验证增量API（2026-10-01）

- GET /verification-mode：mode=TOTP或SIMULATED_CLICK、simulated、replacement_required；服务器按显式开关决定，默认SIMULATED_CLICK，不限制库名；额外返回permission_level、authorization_method、provider_version和integration_target。
- POST /plans/{id}/preview {version}：当前采用后新版本全部待确认项，返回items（逐笔Preview）。没有交易项时无需确认。
- POST /plans/{id}/confirm {version,items:[{authorization_id,digest,disclosure_version,accepted:true}]}：必须精确包含整份方案的全部项，不允许遗漏、重复或异参。先银行预检，所有逐笔确认在同一事务提交；任一失败全部回滚。
- POST /plans/{id}/verify/demo {version}：只从可信按钮调用，银行逐笔写模拟证据。随后原execute接口原子批量申购或按既有卖出暂停规则执行。
- POST /previews/{id}/verify/demo {}：单笔演示；原verify {proof}继续为真实TOTP验证。
- 内部GET /verification-mode、POST /verify/demo {subject_user_id,authorization_ids}：仍须内部凭据。模拟开关默认开启，不检查数据库名；显式关闭后未受理模拟授权不能执行，返回SIMULATED_VERIFICATION_DENIED。模拟接口不进入AI工具白名单。

schema版本2新增wealth_verifications而不改旧账本。method/provider_version/simulated/replacement_required/verified_at记录验证提供者；orders.details.verification与audit标识来源。模拟不称为真实MFA成功。历史TOTP授权兼容保留，不能把模拟证据改为真实。后续统一高风险模拟授权衔接见simulated_authorization.md，不要求新增真实认证输入。


## 最新全量基金补充

默认混合研究池、全量目录、池外详情／比较、可调整清单、官方原始规则和模拟变更、TTL及日更见[补充入口](preload_and_directory.md)。真实基金缺审核规则仅研究，银行B产品交易使用独立模拟资金上下文；不复制旧余额或改变旧订单引用。数量不是固定上限。
