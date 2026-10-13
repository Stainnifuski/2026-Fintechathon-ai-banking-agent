# 理财01／02：当前本地集成工程交接

更新：2026-10-13 America/Toronto。代码验收版本 `813894c6a6e670229ad8381a3c6367f3998f5b18`，分支 `integration/wealth-01-02`。

**状态：独立可修项已实施，真实基金交易映射及评级政策需要业务决定；完整联合投资正向未通过，未可封板，不安排用户重复验空页面。** 未远端写入，未合原main，未接新LLM/NLP，未恢复采集。

## 1. 保留成果与范围变更

- 保留01前11步、02手动流程、0abee50精确版本材料映射、PostgreSQL修复和原工作区；旧七家族S产品投资目标补全方案没有批准、本轮不实施，不作为正式来源。
- 共用00 v0.3和模型已确认参数保持；旧“模型待定”文字不覆盖 `portfolio_model.md` 的冻结DPP、区间与投影。
- 新增只读批量目录读取、不可变资料索引指纹、正常启动预加载、不可修改的相同未知字段复用、请求内事实复用及离线包准备命令；不缓存用户资格、资金、控制权或购买许可。可变注入源不复用字段；未知值仍未知，数据指纹不变。
- 调整：候选费用按当前具体份额版本映射，销售服务费未知仍null，不将年度综合费误称销售服务费；移除临时120秒Action超时，回到公共客户端超时，无自动交易重试。内部BankPurchaseFact增加可选fee_rules，外部Fees DTO不变。
- **移出运行功能封板门槛**：原01 A6的含分红历史总回报、固定组合和真正样本外金融效果评价。不继续补证，保留原NAV描述及未完成状态，不宣称金融有效性。原00中的相关研究交接改为历史未完成、非本轮运行门禁；不删除资格/账务/J条件。
- 保持：未知风险不放行、具体份额与当前版本、本人确认、统一MFA、参数/有效期/幂等/审计、超风险原持仓仅保持/减持、受理事实不回滚、B真实收益和J18—J23要求。B收益没有随A研究项排除。

## 2. 当前双池实测及责任

|链路|实际结果|状态|
|---|---|---|
|A冻结全目录|28,076条：27,998真实基金＋78原S/B模拟产品|全目录索引，不是固定44/60/七产品白名单|
|真实范围|19,265 verified；7,243 pending；1,490 excluded|不能将所有目录条目计为可推荐|
|真实已保存/解析|全部有目录引用；目标40、策略40、基准80、跟踪7；至少一正文/基准字段84条|目录引用不等于84条正文，更不等于合格产品|
|真实注册证据|87条注册记录：风险5、家族3组/6份额、主体4、目标/策略/基准各25；87公开文本文件|风险5条均没有明确公布和适用起止日期；与模拟销售渠道也不匹配|
|真实银行映射|当前SKU0、产品/披露版本0、当前资格0、DPP0、B prepare0|研究导入只标research-only；需要决定真实资料＋模拟交易的正式政策|
|现有S60+B18|78→46范围内→34合格份额/18家族→0充分材料→0DPP|只用于隔离回归，不能替代真实基金正向|

实际真实排除：19,265条来源模式不符、产品/披露版本未知、当前银行产品缺失；19,263份额未核实；19,260无评级，5条渠道不符。pipeline逐条保留结果，未按产品名称模糊匹配。MF/MW前轮遗漏已关闭，不重复诊断。

已有 `wealth_research.import_dataset` 支持完整模拟overlay入银行，但没有任何已批准真实基金overlay；`wealth_preload.compose` 将真实产品写R前缀、risk=null、rules_complete=false。只改默认目录或取消门禁不能闭合。

**必要决定**：是否允许为真实基金的比赛模拟交易采用明确标注的项目风险分级及模拟计价/费率/销售条件，改变原01“必须可信当前真实渠道评级”的前置要求。具体七家族及原值/拟值方案在同一仓库外联合回传，尚未实施。公开资料、项目分级和交易条件必须分别绑定、分别标源；不能将模拟分级登记为verified public evidence。

责任：A核实公开材料、家族、具体份额及特征；模拟银行维护版本化交易映射、计价、规则和最终风险拒绝；01／02共同完成真实候选→prepare→本人MFA→七子单→结算回读；用户决定新政策。03不负责替代上述运行缺口。

## 3. 按用途的字段门禁

|字段|消费者/代码|缺失处理|
|---|---|---|
|本人、正式评估、画像版本、当前scope/control|assessment_reasons、JointAccess、最终银行适当性/写锁|必要；当前重读，未知或过期拒绝；MFA不提升产品资格|
|具体代码/份额/可靠family、产品/披露/规则版本|qualify_purchase、validate_current、B prepare|必要；不从名字合并、不借A等级给C；0和极小权重仍检查|
|实质类别、主体原文/核实别名、正文|family_candidates、DPP|类别与主体必要；目标/策略/基准/跟踪至少一个有出处，不要求全部；空文本不能用默认向量或名称补齐|
|主题策略/跟踪证据|theme_tags|主题才需要；无主题证据不命中，不能把此门禁扩大到普通推荐|
|当前可售、窗口、NAV可见性、规则、限额|current_offer_reasons、银行precheck|购买所需；没有真实当前映射不能伪造可售。模拟条件需明示/版本化|
|费用|CandidateGroup.Fees、详情、银行实际交易|非DPP输入，不统一关闭推荐；公开未知为null。已存在模拟版本费率按版本返回，交易规则不完整则购买拒绝|
|近一年/日涨跌|Performance、详情|可缺，null和准确指标/日期，不填0，不把NAV涨幅叫总回报|
|全部授权资产估值/持仓/应收|A01 plan、B AssetSummary|配置需要完整分母；冻结不重复，应收不可支付；A05不要求先有Plan或A01成功|
|B真实持仓收益|returns服务、日/月/年/累计/日历|必验；本金/到账不是利润，费用不重复、全赎贡献保留，缺历史/估值partial/null|

## 4. 实际接口、组件和注册

正常 `backend.app.main:app` 安装唯一JointPortfolio，注册plan_portfolio/recommend_products/revise_portfolio，handler签名 `async(*, db,user_id,context_id,bank,params)`。Direct create/get/revise和Action共用PortfolioLifecycle/MemoryPortfolioStore。

|接口|接法|
|---|---|
|GET `/api/v1/wealth/portfolio/snapshot`|唯一A路由；消费B全授权AssetSummary|
|POST `/portfolio/plans`；GET/PUT `/portfolio/plans/{id}`|同store与事实/会话/版本校验，GET带context_id，PUT带expected_version/snapshot_id及完整选择|
|POST `/portfolio/recommendations`|独立A05；general/category/theme及版本化候选绑定；无Plan、无单次预算/期限|
|POST `/api/v1/wealth/actions`|B严格ActionV1分派到同一A/B服务，JWT主体不是前端事实|
|POST `/portfolio/plans/{id}/prepare`|B差额准备；具体SKU重读资格；不冻结。整份确认/统一MFA/受理/原号回读保持|

canonical schemas为wealth_portfolio*.py，B wealth_assets re-export同AssetSummary；前端types/wealthPortfolio.ts不导入fixture。金额整数分、比例十进制字符串、份额四位字符串；legacy epoch=null不造版本。正式组件 `StructuredActionPanel→AllocationReview→PortfolioAllocation`，B ProductPicker/PlanExecution/双表/收益保持；操作员图只读，本人授权不可代签。

## 5. 当前验证与性能边界

- 受影响147通过：PG29＋SQLite29＋其他89；涵盖目录发布时点/停售变化、银行收益/汇总/控制、请求内资产变化及A模型/生命周期。费用补验216通过，含两库JointFacts和统一MFA；最后现金双读一致性116通过，候选序列化42通过，未知字段不可变复用181通过。集合重叠不相加。前一轮257通过为中间修复证据，早期1失败已修复并保留日志。
- 正常main生命周期在新隔离SQLite实际启动，加载真实JointPortfolio/全28,076索引；health/login/assets/snapshot=200，全scope/snapshot/T一致（正常种子总资产50,000元），不注册录制source。正常启动与隔离测试seed分开。
- 正式浏览器SQLite3项阻塞页面检查通过、runtime/network错误0、新NLP0；仅证明状态诚实及读UI，**没有七真实家族投资正向/MFA整链**。类型、正式与两独立构建、前端66状态检查通过。既有大chunk提示保留。
- 首轮带profile配置API 65.443/40.979/37.709秒；观察到逐SKU NAV/material请求及多次全目录序列化。改为当前版本批量查询、按日期/可用瞬时SQL取最新价、启动索引、不可变指纹、同次计算复用事实与资格结果，最终仍重读时钟/用户/画像/银行/控制；不跨用户缓存。
- 当前冻结包配置连续响应：3.170s / 2.576s / 2.604s；普通推荐：1.245s / 1.227s / 1.958s。目录28,076/真实范围19,265/模拟合格34/材料0。首次≤10秒、重复≤3秒已在此受限路径核验，**不能代签未来真实正向性能**。启动建索引25.407秒单列，复用364,029个相同未知字段；离线安装完整校验20.333秒单列。保留之前重复请求超过3秒的日志，没有用120秒超时当验收，已删除临时超时。

## 6. 可复现离线源准备与缺包

源码依赖按requirements，frontend `npm ci`（锁文件）；不传.env、账务库或全量缓存。冻结data `c1e4e964d09cf088e558be8f18c53e84fda75bdf32fcaf0ec1e1d6f3fcbcafa7`，daily `3aad3d4a16ea0cba70e516b4a6d79c317f358870329975f8fa42c857bcffdc65`。只需受控包的manifest＋gzip和本提交config公开证据即可复现既有运行索引，**不能重建全量采集，也不续期评级/公开状态**。

包gzip 2053835字节，SHA `c097062ef73d1b48608bf817a2cf6a11f89939085d9adf572042865f1d7cfc2c`；snapshot SHA `0830f7fdb330dd99e8ac7254c3ff80e294dcb66572f3a8c00bec7fb3093b87c8`。实际在新空相对目录导入28,076，88个公开文件（manifest＋87文本）校验，篡改/缺包拒绝。包留本地ignored目录、未入Git；其他机器需取得同SHA受控包，不再要求本机绝对原料路径。

```powershell
# 集成检出根，导出需已有该冻结快照；没有快照不自动采集
python -B -m scripts.wealth_portfolio_package export --version c1e4e964d09cf088e558be8f18c53e84fda75bdf32fcaf0ec1e1d6f3fcbcafa7
# 其他机器收到manifest+gzip后，同版本源码根目录
python -B -m scripts.wealth_portfolio_package install --manifest path/to/c1e4e964d09cf088e558be8f18c53e84fda75bdf32fcaf0ec1e1d6f3fcbcafa7.package.json
$env:PORTFOLIO_DATA_VERSION='c1e4e964d09cf088e558be8f18c53e84fda75bdf32fcaf0ec1e1d6f3fcbcafa7'
$env:PORTFOLIO_DAILY_VERSION='3aad3d4a16ea0cba70e516b4a6d79c317f358870329975f8fa42c857bcffdc65'
```

正常应用启动预加载；缺/坏包保留其他业务启动能力，A请求PORTFOLIO_DATA_UNAVAILABLE，不静默切fixture。任意注入的可变内存source不缓存指纹，变资料会使旧草稿失效。生产加载的版本文件只校验一次，不承诺抵御管理员篡改运行内存；新资料必须新版本、重启/重载。

## 7. 闭合条件和移交

真实基金映射及评级政策批准→按明确公开材料定点补证/注册，缺资料继续排除→至少七不同真实家族A普通/债券正向＋B多选prepare→完整J01—J23和必要负向／两库七产品账务／正式浏览器流程→用户人工验收。A历史金融效果不再是封板前置，B收益仍是。

当前J08等真实投资正向blocked；操作员整链/非空浏览器、真实七SKU双库仍待前置，不能把录制七笔或全现金算通过。专用服务默认停止，账务及失败证据保留，原3010/8010/5432保留。只有人工反馈及必要联合条件满足，才另轮整合最新main并按规则发布；本轮无远端写入。
