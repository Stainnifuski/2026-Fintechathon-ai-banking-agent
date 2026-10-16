> 当前集中收尾：普通真实基金13,578（债券4,093、混合8,444、股票1,041），真实货币永久退出；主题、A/C、客服领取交还范围调整取消。保留原U和模型参数；本文件下方旧范围只作历史，现行接线与证据以portfolio_handoff.md、wealth_joint_acceptance.md为准。尚待人工验收，不发布main。

> 当前共同口径（2026-10-13）：按核实具体代码推荐/下单，生产switch接口410；A/C切换、默认A、仅C例外及强制家族登记前置移除。可靠同基金关系仍去重；未知使用exact-code操作键，不声称独立风险来源。真实基金项目分类等级独立发行人，可靠更高公开评级/分类冲突拒绝。下文相关F09/旧切换行为仅作隔离历史回归；当前接法及实证以portfolio_handoff.md、wealth_joint_acceptance.md为准。

# 01 共用新购资格与候选状态

## 第07步契约对齐记录（历史；当前证据状态见文末）

四项A侧修复已实施，公共DTO及路由外形不变：

1. `_access`允许可信`context_epoch=null`，另绑定真实`selection_version`；不填1、不改名。正整数epoch保留。本人/context/scope/账户、epoch/切换版本变化仍使旧Plan或cursor失效，切回原context也不复活旧状态。
2. 独立读取与推荐无需旧task/session。复用已有服务端候选会话及1800秒MemoryPortfolioStore；关联原task的关闭、版本和写锁仍校验。
3. `resolve(write=False)`不要求`can_edit`或遍历历史写锁；已知关闭及数据隔离仍拒绝。创建、修订是写操作，必须有可信编辑依据；未知停止/关闭不能当false。HUMAN、PAUSED、关闭、停止提交继续禁止写入。访问绑定与权限判断分离，读取时缺编辑字段不使合法Plan失效。
4. 当前模拟银行来源可映射内部`local-competition-bank`渠道。`CurrentSimulationEvidence`是A内部事实，不是公共新字段：同产品/披露版本、当前采集时点、模拟`business_at`、销售/暂停/open/stale、募集截止、规则日历、净值日期及可见时点逐项核对。无原生永久有效区间不单独拒绝；实际存在的区间仍验证，未来数据/过期/错源/错版本拒绝。真实基金评级、渠道、时效及未知风险要求不变；持有/赎回许可不默认允许。

快照双读排除任务`nodes`运行日志，保留任务版本、身份、关联和控制字段；实际首测只因四个旧DAG节点attempts变化409，有限诊断证实后修复，不放宽资产/规则版本变化检测。

当前适用验证：Windows Python3.12.14与现有后端容器Python3.11.17各运行同一来源/资格/生命周期/API/新增对齐集合，结果见[交接](portfolio_handoff.md)。集合完全重叠，不相加。受控fixture只证明边界；真实接口另外记录。

实际独立8052路由：GET snapshot **200**，as_of=`2026-10-09T20:50:20.637377Z`，T=2010000分、可支付310000分、冻结1700000分，1账户、0持仓。POST recommendations **200 / unavailable**，仍含`ASSESSMENT_CURRENT_VERSION_UNPROVEN`和产品身份/版本/销售等真实原因，非成功建议。POST plans **503 / PORTFOLIO_WRITE_CONTROL_UNPROVEN**，当前相关任务未公开session_closed/stop_new_submissions。未创建Plan，不用get/revise404冒充通过。本次启动的8052进程已停止，既有3010/8010保留。

可信Plan仍受当前画像核验、真实写控制、银行v4/研究v1及policy_mismatch等必要事实阻塞；普通/归档范围、历史lot版本、真实风险/家族/主题、历史评价及B中央接线仍待完成。以下旧检查数字/响应只作历史记录，不能替代当前结论。


本模块提供独立、离线可测试的候选基础；不接中央应用、交易或配置优化，不包含正式 DPP。资料快照沿用 `wealth_portfolio_data`。客户端只提供筛选条件、份额引用和预期版本，不能提供风险评估、产品风险、购买许可或 fixture 标记。

## 入口与可信事实

- schema：`backend/app/schemas/wealth_portfolio_candidates.py`。
- 唯一资格函数：`qualify_purchase(product: ProductFact | None, facts: CurrentFacts, user_id: str, now: datetime, requested: ShareReference | None = None, context: VersionContext | None = None) -> QualificationDecision`。
- 纯流水线：`pipeline(index, filter, facts, user_id, now)`，完整匹配 → 每份额资格 → 可靠家族 → 排序资料充分性。
- `CandidateFactsSource.read_current(user_id: str, at: datetime) -> CurrentFacts` 异步协议必须由服务器可信适配器实现；本步没有以旧 profile 或旧单 context 资产替代正式来源。
- `CurrentFacts` 包含本人最新正式评估、渠道、资料/日频/当前可售版本，以及逐份额银行购买规则。独立候选不依赖完整资产估值或整体计划。
- 资料索引 `SnapshotIndexCache.local(root)` 读取本地完整快照，最多缓存两个版本；每次动作仍重读当前事实并复验资格。当前事实指纹包含许可、风险、规则和有效期，不包含单纯读取时点，避免正常重读使所有游标过期。

`CandidateService(source, indexes, selector=None, *, enable_fixture=False, clock=None)` 的异步方法：

| 方法 | 入参（除本人 user_id） | 用途 |
|---|---|---|
| `candidates` | `CandidateFilter` | 建立完整家族池及候选会话 |
| `validate_selection` | `SelectionRequest` | 独立手动选择资格入口，供后续计划步骤复用 |
| `select` | `SessionSelectionRequest` | 验证整份选择，任何非法项都不写入新状态 |
| `switch_share` | `SwitchRequest` | 已证实同一家族 A/C 切换；失败保持原选择 |
| `next_batch` | `NextBatchRequest` | 排除已展示和已选家族，每批最多三个 |
| `refresh` | `SessionAction` | 更新事实绑定，保留并标记失效选择 |

独立路由 `backend/app/routers/wealth_portfolio_candidates.py`：POST `/api/v1/wealth/portfolio/candidates`，以及 `/validate-selection`、`/select`、`/switch`、`/next`、`/refresh`。仅独立 ASGI 测试应用挂载；未修改主应用注册。通过既有 JWT 取得身份，服务器依赖注入提供可信 service。默认来源未接通返回 503。

## 资格、原因与状态

新购/增购要求本人当前有效正式评估，confirmed、purchase_allowed、合法 R1—R5、最新 ID/版本已证明；到期恰等于当前时点也拒绝。产品逐份额核对范围、风险、来源、渠道、版本、当前可售、规则、以分为单位的起点及限额证明。真实风险证据必须匹配代码/份额/渠道且其明确适用区间覆盖北京时间业务日；缺适用区间不默认永久有效。模拟风险仅在明确模拟来源下消费。未知、冲突或过期事实均不能开放购买。

R2 → R4 返回 `RISK_EXCEEDS_CAPACITY`；零权重和极小权重仍检查。评估失败时不宣称已完成等级比较。购买、继续持有、赎回分别输出；后两者只有可信当前绑定来源时才非 null。购买拒绝不处置已有持仓。研究资料 `approved_trade_product=False` 不改变；独立银行事实才能证明银行可购。金额尚未给定时 `amount_check_status=not_checked`、`amount_executable=null`，本入口不证明金额成交能力。

| 对外状态 | 条件/代表原因 |
|---|---|
| `ok` | 至少一个资格、家族、资料都完整的候选；其他排除不制造整组风险警告 |
| `no_theme_match` / `no_category_match` | 完整范围中该筛选无匹配 |
| `unavailable` | `ASSESSMENT_REQUIRED`、评估未确认/过期/旧版本等；或 `SELECTOR_UNAVAILABLE` |
| `risk_mismatch` | 全部匹配风险已知且均超过有效用户等级；保留其余并存原因和等级 |
| `no_eligible_products` | 未知风险/停售/规则/家族/资料不足等组合，或 `POOL_EXHAUSTED` |

全部每产品 reason_codes 和阶段计数保留。常见原因：`PRODUCT_RISK_UNKNOWN/CONFLICT/TIME_UNPROVEN`、`CURRENT_BANK_PRODUCT_UNAVAILABLE`、`PURCHASE_STOPPED`、`CURRENT_RULES_INCOMPLETE`、版本缺失/冲突、`FAMILY_UNVERIFIED`、排序资料不足；准确原因以 domain 输出为准。

上下文/会话/游标/产品旧版本按 409 处理；跨本人及正常服务混入 fixture 来源按 403；来源未接通按 503；客户端非法字段按 422。HTTP 200 的资格结果可为 accepted=false，不能当作购买成功。

## 标签、主体、家族及选择器

规则版本 `portfolio-themes-v1`。high_dividend、bond_income 只接受有来源日期的目标/策略直接肯定说明；名称、分红次数、旁引、否定不能赋标签。broad_market 等固定标的映射必须有明确基准/跟踪事实和经核实映射；当前真实映射为空，不能凭相似名称推断。每个标签保存规则版本、份额、来源、短摘录、日期和指纹。确定性规则采取保守句段判断，不能解释任意复杂自然语言。多主题显式 AND/OR，未知键、空值或不支持条件拒绝。

家族只来自可靠来源证据，不按名称后缀或 product_id 合并。有来源/日期且无冲突的单一主体原文可用；将不同名字合并才需别名证据。共同类别、主体或文本冲突保留并阻止进入排序。排序材料要求可靠家族、合格份额、实质类别、主体和至少一项有效投资文本，不按收益/费用/规模加分。

默认合格 A，A 不合格则合格 C；已核实其他份额保持原标签，稳定代码选择，不冒充 A/C。每批最多三个家族，已选无三只或旧五笔上限。池耗尽明确返回，不重播。当前评估/资料/规则变化使旧游标失效；停售、超风险或证据失效的旧选择保留并标 invalid，不自动替换。

`FamilySelector.select(pool: list, excluded: set[str], limit: int) -> list[str]` 协议，另有 `version` 与 `fixture_only`。正式选择器未配置时返回 `SELECTOR_UNAVAILABLE`。测试按家族 ID 排序的 selector 显式 fixture_only，不是 DPP。会话在本进程内保存，重启不恢复；本步没有改数据库或承诺跨进程持久化。

## 独立运行和验证

在仓库根目录执行（Python 环境须包含项目现有依赖）：

```powershell
python -m pytest tests/unit/test_wealth_portfolio_candidates.py tests/integration/test_wealth_portfolio_candidates_api.py -q -p no:cacheprovider
python -B -m scripts.wealth_portfolio_candidates_audit --version <本地资料快照版本>
```

审计脚本阻止外网 socket，读取完整本地池；输出保存在忽略目录 `.deps/wealth_portfolio_candidates/`。正式 source 未接时记录 503，再以单列显式 fixture 用户上下文核验纯规则，不冒充实际用户正式风险评估。无网络刷新或银行交易。

2026-10-09：本步 98 项（unit 92 + 独立 API 6）及第02步适用 98 项回归，合计 **196 passed / 0 failed / 0 skipped**。完整资料池 28,076 条；真实匹配 19,265、模拟匹配 44，当前可购均为 0。真实风险/可靠家族及有效主题证据仍缺；四个银行 R2 归类不等于可购，12 个政策不匹配及 B017/B018 pending 保持。首次完整索引加载约 13.906 秒，复用索引的全池 handler 两次约 0.515/0.516 秒，索引只加载一次。上述 handler 明确使用 fixture 评估、无银行 offer，并非实际正式推荐验收。

剩余：正式评估与当前银行事实适配、真实风险/家族/标签证据、正式选择器及后续 DPP 接入。本步不包含前端、交易闭环或金融效果验收。


## 当前证据消费与保护

完整产品索引从新的A派生快照读取，公开登记实际进入FieldFact、份额/家族及风险数组。已证明的3组真实家族与当前模拟SKU身份分开；没有证据的其他代码仍在全目录和排除台账。产品category原文冲突使其pending，不覆盖旧观察或ETF/FOF排除；bank_wealth的S系列不能混入公募bond_fund。

当前模拟SKU可凭受保护当前自身身份建立`bank-sku:<product_id>`独立候选实体，member_codes只含自身，不据同公司/模板/SF编号合并。同版本号下规则/披露内容变化，也由当前内容SHA256与派生绑定核对，不把v4降成旧v1。内容不符/未知返回BANK_PRODUCT_CONTENT_MISMATCH；错源、错份额、渠道、风险、版本与业务时间仍各自拒绝。资料/当前内容摘要参与现有上下文指纹及游标/选择失效机制，没有HTTP绕过参数。

固定流水线仍是完整范围匹配→逐份额资格→可靠家族→主体/文本充分→DPP。q=1、算法/参数不变，新增证据不自动批准研究基金交易。实际新银行SKU没有自身投资正文，不能移植旧文本进入DPP。真实公开R观察虽已5份额，未证明评级适用时点，当前银行也未提供这些真实份额；不能标为真实可交易。

真实普通/债券/高股息请求和当前产品＋fixture用户审计分列，详见唯一portfolio_handoff.md。fixture只用于风险矩阵、A/C、超过5项选择等边界和资料层诊断，不证明本人真实画像或真实Plan。正常来源仍因当前画像匹配缺证返回unavailable；高股息无肯定策略匹配时no_theme_match，并保留画像缺口。
