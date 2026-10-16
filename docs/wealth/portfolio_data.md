> 当前事实接入（2026-10-13）：已修复A1混合值列，26,395非货币NAV与967货币收益分开；09保护000015/000016历史进入版本/日频/包，0000153282点。旧正文84纠正为同一路径83，并无丢文。真实公开事实与项目模拟等级/费用/成交分源，政策版本/报价SHA绑定，不新增30天期限/2万元上限。当前数据b06…、日频273…及离线包完整SHA/复现命令见portfolio_handoff.md；下文早期未接线状态为历史，不代表本轮仍阻塞。

# 整体理财只读资料接入

> 2026-10-12联合状态：本文件保留01原实现/独立验证说明。当前正式主应用已注册A提供器与规范路由并消费B全授权事实；正式接线、已关闭旧缺口和剩余产品/研究阻塞以 [portfolio_handoff.md](portfolio_handoff.md) 为准。独立入口不替代正式联合验收。

此入口消费已经保存的原料，构建模型事实，不提供推荐、配置、购买资格或交易。产品研究记录始终 `approved_trade_product=false`。没有预算或必填期限输入。当前受保护同run资产已接通；普通/归档全授权范围仍缺证明，缺来源不自动接fixture。

## 入口

从仓库根目录使用现有Python依赖：

```powershell
$env:PYTHONPATH=(Resolve-Path .deps).Path
$pythonExe='C:/Users/m1353/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
& $pythonExe -B -m scripts.wealth_portfolio_data build --as-of 2026-10-09T05:45:00Z
& $pythonExe -B -m scripts.wealth_portfolio_data coverage --version <build返回的64位版本>
& $pythonExe -B -m scripts.wealth_portfolio_data read --version <build返回的64位版本>
& $pythonExe -B -m scripts.wealth_portfolio_data validate-evidence --input <公开证据登记JSON>
```

`build`读取进度库的完整 `universe` 及允许的成功原料。禁止队列运行、探测、mirror或刷新。CLI禁止socket创建；底层适配器不导入requests/AKShare、不发起HTTP。只读SQLite使用 `mode=ro`。不能把中断的running标记解释为活跃采集。

完整规范资料保存于忽略目录 `.deps/wealth_portfolio_data/snapshots/<version>.json`，包含分开的 `products` 固定资料层和 `daily` 日频观察层及各自版本。索引包含完整内容SHA256；构建/缓存读取时间单独写 `last_build.json`，不改变规范内容。相同原料、登记、适配器代码、as_of得到相同版本和内容；源哈希、登记或代码改变会改变版本。旧版本不覆盖。缺文件、路径越界、指纹/版本矛盾明确报错，不改用另一来源。

## 事实字段与单位

定义：`backend/app/schemas/wealth_portfolio_data.py`。

- 身份含产品代码/份额、产品/披露版本、来源real/simulated/fixture；未保存身份信息为null及字段原因，不补1。
- 每个 `FieldFact` 含 `value/status/reason/observations`。观察含原值、字段、函数、raw引用/哈希、抓取UTC时刻、正式资料日期和单位。未知/冲突没有合成值，原观察保留。
- 主体原名仅来自基金管理人/基金公司。基金经理姓名不当主体；简称与全称差异保留冲突，经正式证据核实的规范主体另存。
- 目标、策略、基准、跟踪标的分别存储；无跟踪标的等占位不是有效文本，不用产品名字补文本。
- 保存目录的 `money_market` 归活钱；债券归稳健；混合/股票归进取。指数/ETF联接/QDII/LOF仅是形态；实质未知保留pending。已排除的ETF本体/FOF保留排除依据。
- 金额为明确来源的非负整数分；未知单位的公开购买金额只保存原值，`source_unit_unverified`，不转分、不当银行规则。模拟规则显式fen可消费。NAV使用十进制字符串及元/份单位；累计净值、每万份收益、七日年化分别记录。批量表混合的“最新净值/万份收益”不当单位净值。
- 正式风险按具体份额/渠道/公开日期/适用时间存入独立证据数组，不取跨渠道最低值。同渠道等级或时点未对齐记冲突。星级不转R等级。没有适用时间时保留原因，不规定永久有效或自行设评级到期时间。
- 日频保存原抓取与业务日期，不因重建更新。`source_expires_at`缺失显式保留；24小时龄期指标只是本地老旧提示，并非发行方有效期或可交易证明。正常命令不启用日更。
- 模拟价格仅取as_of前已公布且已可用的观察；不提前使用2027价格。模拟证据不能核实真实产品。

## 公开证据登记

`config/wealth/portfolio_evidence_manifest.json`存已核验登记和旧声明的检查摘要。旧17份额/8组声明的检查摘要属于历史记录。当前定向登记已证明其中3组/6份额的明确关系；其余保持未核实，不因页面200或相关/关注列表就验证家族。

导入对象必须符合 `EvidenceRecord`：

```json
{
  "evidence_id":"公开来源稳定标识",
  "source_kind":"real",
  "kind":"risk",
  "source_role":"seller",
  "url":"https://正式来源/原文",
  "codes":["具体基金代码"],
  "share_classes":{"具体基金代码":"A"},
  "channel":"明确适用销售渠道",
  "published_on":"YYYY-MM-DD",
  "applicable_from":null,
  "applicable_to":null,
  "applicability_reason":"原文未说明适用时间",
  "observed_at":"带时区UTC读取时间",
  "reference":{"path":".deps/wealth_portfolio_data/public/本地原文","sha256":"原文件64位SHA256","function":"public_document","kwargs":{"url":"与url一致"}},
  "excerpt":"包含公开日期、具体代码、份额与正式风险等级R文字的真实原文短摘录",
  "risk_level":3
}
```

这是格式说明，不能作为真实证据登记。原文缓存目录不可包含凭据/cookie/个人数据。登记校验来源主机与角色白名单、URL绑定、文件边界/哈希、原文摘录、日期、代码和份额；代码须在实际完整目录。家族登记需多个明确关联代码及份额关系原文。等价五级体系尚无已核实登记，不能自行映射或由产品类别推出R。

`validate-evidence`只验证和输出，不自动写入正式manifest。`register-evidence`先验证整批再原子登记，随后必须显式离线构建派生版本；未知、非法、来源不明确和实体不匹配的记录拒绝成为已核实资料。定向证据获取与这些离线命令分离，获取失败不会触发模型请求现场补抓。

## 模拟银行政策

不修改原产品目录。明确快赎＋明确保本＋R1＋完整规则才归政策活钱；目前B017/B018只有快赎/R1/cash，保本证据缺失，pending。其他原R2且属性一致的模拟银行产品归政策稳健；原非快赎R1和R3记policy_mismatch，绝不降级，也不冒充超风险拒绝。具体模拟规则保留在原版本引用，不重定义银行交易规则。

## 全资产消费

`read_asset_model_facts(source,user_id,product_facts=None)`复用 `AssetSnapshotSource.read` 和原 `build_snapshot`。T=全部关联借记账面现金＋持仓市值＋赎回应收，冻结不重复计入；可支付现金另列。未知市值使T=null；分类不完整时已知金额仍留T，输出 `disable_eta` 与原因，不运行优化器。可注入规范产品事实检查产品版本/政策分类，缺资料不改变银行持仓。

正式评估仍来自同次source的 `assessment`，保留ID、等级、confirmed、purchase_allowed、expires_at。可另提供 `assessment_provenance={source,version,read_at,observed_at}`；read_at须匹配快照as_of，版本/读取来源缺失显式记录，不用客户端等级或旧评估补齐。不实现后续有效期资格门禁。fixture必须显式注入，范围和标签不变。F08/F18/F10不修改。

## 验证

```bash
python -m pytest tests/unit/test_wealth_portfolio_data.py tests/unit/test_wealth_portfolio_contract.py tests/unit/test_wealth_portfolio_snapshot.py tests/integration/test_wealth_portfolio_api.py 'tests/integration/test_wealth_collection_cache.py::test_existing_cache_orm_offline_binding_and_fingerprint[sqlite]' -q -p no:cacheprovider
```

原数据缓存保护被抽成共用函数，原ORM读取入口保持兼容。此检查仅隔离SQLite，没有测试生产数据库或交易。正式AssetSummary、可靠真实家族/风险、有效时点等仍为事实缺口；接入层通过不等于正式真实推荐已可运行。

## 当前产品证据与派生版本

`wealth_portfolio_evidence.register/derive`是A侧入口，银行产品配置和旧完整研究版本不变。完整目录仍为真实27,998＋模拟78，没有fixture或目标白名单。有限准备清单16个候选实体/25代码，34次直接HTTP取得34页；4次搜索/15查询单列，搜索结果不登记为原文。公开登记87项：75条目标/策略/基准、3条明确家族、4条主体、5条明确R观察；评级发布/适用时间未知仍null，不能成为当前银行购买资格。

发行方家族证据为华夏纯债000015/000016、华安沪深300增强000312/000313、华安医药ETF联接000373/000376。其他相关名单或同机构不合并。公开短摘录在`config/wealth/portfolio_public_evidence`，manifest含URL、摘录定位、公开时间（可null）、观察UTC、完整HTML指纹和摘录文件指纹；运行期校验摘录文件，完整原文另在本地。投资字段新旧冲突保留，未知日期不伪造；净值日期不当评级日期。

派生输入是旧内容寻址资料、已登记manifest、显式保护的本地银行观察JSON及SHA256、指定as_of。当前SKU独立记录实际产品/披露版本、原研究实体及版本、采用字段路径、内容摘要、采集UTC和模拟business_at、销售/规则结果及缺失/排除原因。S未证明与真实代码映射；也不从名称或SF编号合并A/C。当前模拟SKU自身可以成为独立实体，但不是跨SKU份额关系证明。当前正文缺失时不复制旧正文或虚构benchmark。

```text
python -B -m scripts.wealth_portfolio_data register-evidence --version <旧完整版本> --input <EvidenceRecord数组JSON>
python -B -m scripts.wealth_portfolio_data derive --version <旧完整版本> --as-of <带时区截止时点> --bank-capture .deps/wealth_portfolio_step08/<观察文件.json> --bank-sha256 <该文件SHA256>
python -B -m scripts.wealth_portfolio_data read --version <derive返回版本>
```

这些命令禁止socket，明确登记与构建，不发起公开获取/银行请求；同输入/as_of重复内容和版本相同，旧快照仍可读。缺目录或观察文件时报错，不静默切fixture或版本。观察缓存是本地相对路径，无私有绝对路径依赖；须由受保护只读准备取得，不能编辑产品事实来凑资格。独立银行入口使用新`PORTFOLIO_DATA_VERSION/PORTFOLIO_DAILY_VERSION`，版本具体值见内部交接。

B001–B012保持原12项policy_mismatch，B013–B016为R2稳健，B017/B018仍缺当前保本证明而pending。S045–S060实际kind为bank_wealth，按同一银行政策校验；指数包装S019–S028未证明实质类别而pending，不新增第五类。cash＋open_fund明确映射为money，原始cash字段仍保存。全78项当前投资正文不足，独立SKU身份不等于DPP资料充分。
