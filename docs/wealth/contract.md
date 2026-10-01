# 理财计算、状态与接口契约草案 v1

所有产品与资金为比赛模拟。以下冻结设计供实现与测试核对，接口尚未实现时不作为可调用能力。

## 计算

现金整数分，净值Decimal(8位)，份额Decimal(4位)。申购外扣费用模型：净申购额=总额/(1+费率)，先净额按分向下取整，费用为总额减净额；份额=净额/净值向下取4位，剩余未转换资金按分返还，低于分的尾差显式记入尾差账。赎回按批次计算份额×成交净值，毛额按分四舍五入、费用按分四舍五入、净到账=毛额−费用。成本按赎回份额比例分摊，最后一次全部赎回消除成本尾差。

例：10000元、费率1%、净值1.23456789；净额9900.99元、费用99.01元，份额向下取4位，展示预估与实际各自净值日期。结算必须使用指定计价日净值；缺失时等待，禁止借用未来净值。历史净值包含下跌，不保证收益。

## 状态

申购：RECEIVED → ACCEPTED（现金占用）→ PENDING_PRICING → SETTLED（扣除占款、入份额）。赎回：RECEIVED → ACCEPTED（份额占用）→ PENDING_PRICING → PENDING_CASH（出份额/形成应收）→ SETTLED（到账）。撤单仅在规则窗口内且尚未计价；事务锁保证撤单与结算只有一方发生。

应用SUBMITTING/UNKNOWN独立于银行事实。超时或5xx按原操作号核对，不更换编号；404不能证明永未受理。相同编号相同完整摘要返回原结果，异参409。计划DRAFT/AWAITING_CONFIRMATION/EXECUTING/WAITING_SETTLEMENT/PAUSED/PARTIALLY_COMPLETED/COMPLETED/CANCELLED，每子单记录依赖与确认。

## 接口清单与授权

拟公开前缀 `/api/v1/wealth`，必须Bearer，主体从认证上下文获取，越权404。

| 能力 | 输入与返回要点 | 安全等级 |
|---|---|---|
| 产品/详情/对比 | 产品ID、规则/披露版本、净值日期、费用/期限/暂停状态 | GREEN |
| 问卷/提交/结果确认 | 问卷/评分版本、本人9题答案、结果版本、确认时间 | 本人可信表单 |
| 资产/持仓/订单 | 整数分、份额字符串、批次可赎回/占用、应收、估值日期 | GREEN |
| 财务画像/外部模拟资产/目标 | 版本、币种、归属、期限、优先级、避免重复分配 | 本人可信表单 |
| 预览 | 方向、产品、金额或份额、账户、计划/产品规则版本 | 不扣款 |
| 确认/验证/受理 | 预览摘要、逐笔披露确认、TOTP、授权ID；10分钟有效 | RED |
| 查询/撤单 | 原操作号、当前版本；撤单显式确认 | GREEN/确认 |
| 计划创建/编辑/采用/执行/进度 | 快照与版本、现金保留、最多5子单、依赖、逐笔授权 | 写入RED |
| 消息 | 受控意图/解释；不接收验证代码，不直接交易 | GREEN/预览 |

内部银行HTTP前缀 `/api/v1/internal/bank/wealth`，内部凭据必需；管理员结算/业务时钟另设隔离权限，不进入模型工具白名单。银行再次核验可信授权的主体、方向、产品、金额/份额、账户、计划版本、披露版本、摘要、过期及强验证事实；任意非空授权ID不构成授权。

## 错误语义

错误沿用 `{detail:{code,message,issues?}}`，不回显敏感输入。400/409明确业务原因包括 RISK_ASSESSMENT_REQUIRED、RISK_ASSESSMENT_EXPIRED、SUITABILITY_MISMATCH、PRODUCT_UNAVAILABLE、RULE_VERSION_CHANGED、INSUFFICIENT_AVAILABLE_FUNDS、INSUFFICIENT_REDEEMABLE_UNITS、HOLDING_PERIOD_NOT_MET、NAV_UNAVAILABLE、STALE_PLAN、AUTHORIZATION_EXPIRED、IDEMPOTENCY_CONFLICT、CANCELLATION_NOT_ALLOWED。网络未知用RESULT_UNKNOWN，不伪造失败事实。
