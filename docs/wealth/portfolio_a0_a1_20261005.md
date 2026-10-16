# Peter整体理财：A0/A1阶段交付

## 1. 范围、基线与完成状态

完整阅读00共用契约、01 Peter任务包v0.3；来源指纹SHA256分别为`7bae04e67cfc4c01408c8eafa1ded3e0400595689a77ce6cde74784a330b771d`、`dc56489f8a0e9b094f860b380bab44df41f5d02a4f559a5fdcbd6c3519c07e3b`。

开发基线main `1ce96d83d892e9af6c55a28234f628e1ca3b9ce3`，开始时working tree干净。依用户最新指令留在main，不创建/切换分支，不执行fetch/pull/merge/rebase/push。仅本地提交。本轮没有接DeepSeek、调整算法、训练模型、改页面、改银行写入或中央接线。

| 要求 | 本轮状态 | 证据/边界 |
| --- | --- | --- |
| A0共用schema、字段适配 | 完成 | 新增独立DTO、JSON schema导出；78条当前银行产品实际适配成功 |
| F01—F24共用fixture | 完成文件及阶段适用校验 | 可重现、fixture_only；不是后续24个交易/算法行为全部通过 |
| 独立运行与测试入口 | 完成 | CLI及独立ASGI挂载，不依赖B页面、数据库或模型 |
| A1账户/持仓/评估/市场数据盘点 | 完成当前可读范围 | HTTP、只读SQLite聚合审计、五个实时AKShare探测；空持仓/缺失评级等如实记录 |
| A1全量AssetSnapshot | 同协议替身完成并验收 | F08/F10/F18；B正式全范围读取与生产路由注册尚未提供，本轮不接管 |
| A2—A6、正式推荐/配置模型 | 未实施，按要求停止 | 等待数据与模型方法讨论 |
| B交易、两页头部、F13/F15等联合验收 | 不在本轮范围 | 不能用本轮59项测试代替B交易验收 |

## 2. 实际数据可用性

实际探测区间UTC 2026-10-05 23:28—23:34（北京时间2026-10-06 07:28—07:34），本机日期America/Toronto为10月5日。完整脱敏证据：[数据探测JSON](../validation/wealth_portfolio_a0_a1_data_20261005.json)。只保存字段、计数、日期、来源；未保存登录令牌、密钥、账户标识或完整净值序列。

### 银行事实与个人输入

| 数据 | 来源、获取方式 | 实测与日期 | 频率/缺失及后续可能用途 |
| --- | --- | --- | --- |
| 银行账户 | JWT本人`GET /api/v1/accounts/{user_id}/accounts` | HTTP200，2条；account_id/type/balance/held_fen/available_fen等字段 | 账务事件更新；balance为元浮点，只作旧字段盘点；不能把此列表与context现金直接相加或当全量授权范围 |
| context现金/占用 | JWT本人`GET /api/v1/wealth/assets` | HTTP200，1个现金账户；as_of实际读取UTC，business_at为2026-10-04T01:15:18.816037Z | 交易/结算更新，业务时钟与实时时钟不同；银行内部已有整数分balance_fen/held_fen/available_fen，来源范围是selected_context_only |
| 当前持仓/订单 | 同一assets返回lots/orders | 当前本人context：0个lot、12个订单，不能用空lot证明所有关联账户没有持仓。只读库审计共3个lot、17个订单，不作为本人全量明细 | 现有lot代码支持units/redeemable/held/locked、market_value/nav/date和成本；本次实际HTTP未返回非空lot，非空估值为fixture验证，不声称实账验证 |
| 全量AssetSummary | 新协议代码检索＋预期读取路径探测 | 未找到正式实现；`/api/v1/wealth/asset-summary`HTTP404 | 本轮明确使用00同协议替身；正式银行读取、完整授权关联范围及两页同值待B接线 |
| 正式风险评估 | JWT本人`GET /api/v1/wealth/assessments/current` | HTTP200，capacity_level=5、confirmed=true、purchase_allowed=true；2026-10-01创建，2027-10-01到期 | 问卷确认/有效期驱动；使用正式评估，不用用户旧risk_level替代，不下调为固定R4 |
| 收入/负债/目标/期限 | JWT本人`GET /api/v1/wealth/profile` | HTTP200，data为空；未填写收入、负债、目标或期限 | 用户填写才可用；缺失省略/null，不补零、不设置必填期限 |
| 历史真实投资收益 | 旧assets标明actual_return_unavailable_reason | 有现有订单/结算结构，但未实测获得完整授权scope的历史现金流及逐日估值 | 不将历史假设、今天持仓反推历史或目录净值涨跌当本人已实现收益 |

### 产品、目录、历史与宏观

| 数据 | 获取方式与实际状态 | 观测日期/覆盖 | 频率、缺口及可能用途 |
| --- | --- | --- | --- |
| 全量基金目录 | HTTP搜索200：缓存28,013条；实时fund_name_em成功：28,002条 | 缓存入库可用时间2026-10-02；实时获取时间见JSON | 目录保留曾有代码，新上游缺代码不等于基金终止，因此两个数量不同不自动覆盖。研究搜索不受预加载或研究样本数限制；名称不是主题/风险证据 |
| 申赎状态 | 实时fund_purchase_em成功27,695条；含申购/赎回状态、起点、日上限、手续费等 | `最新净值/万份收益-报告时间`缺年份，不能认定完整估值日期 | 状态缓存TTL6小时；本次未入库。可以支持可售/流动性研究，不能代替完整银行交易规则或正式评级 |
| 普通基金历史 | 实时000001单位净值接口成功6,017条 | 2001-12-18—2026-09-30 | 历史缓存TTL1日；单位净值变化不是含分红总回报。单样本可用不等于全目录都有历史 |
| 货币基金收益 | 实时000009成功40条，沿用有界Eastmoney分页适配 | 2026-08-27—2026-10-05；每万份收益、七日年化、申赎状态 | TTL1日；40条不是一年历史，七日年化不能当一年已实现回报或保证收益 |
| 池外详情缓存 | 000001 `fetch_missing=false` HTTP200，history/status/fees READY | 抓取2026-10-02；API未返回expires_at，原缓存表可审计 | 本次禁用fetch_missing，不触发刷新/写入；存在fees PARTIAL和history/status FAILED记录，不能把READY数量当全部完整 |
| 最新混合研究池 | HTTP`POST /wealth/research`只读查询200，total62、第一页60；只读全量表验证62=44 AKShare基金＋18合成银行理财 | manifest_as_of2026-10-03；有6只money_yield无普通NAV历史 | 44条真实基金risk_level及rules_complete均未补齐；27条缺明确family_id，只有13个非空显式family_id；不能据此推断13个独立风险来源。真实评级不得用合成等级补齐 |
| 当前交易context产品 | HTTP`GET /wealth/products`200：78条，全是semantic-fixed合成版本 | 可见NAV日期均2026-09-30，五类category；只读目录35个显式家族 | 所有78条旧接口顶层缺disclosure_version，但嵌套disclosure.version可用，字段适配78/78通过。这是固定模拟行情，不是今日真实78只基金 |
| 宏观LPR实时 | macro_china_lpr成功1,576条，TRADE_DATE/LPR1Y/LPR5Y等 | 本次探测未保存TRADE_DATE范围，不冒充已完成日期筛选 | 按发布频率更新，不是日收益信号；只能作后续背景特征研究 |
| 已留存宏观序列 | 只读审计最新混合池8类，来源akshare | LPR1Y1538/LPR5Y86，截止2026-09-20；CPI224/PPI248，截止2026-08；制造/非制造PMI各225，截止2026-09；M2 362/M1 571，截止2026-08 | 月频发布；LPR历史不规则。保留period/published_at/available_at/revision/definition_break/unit；没有可靠真实发布时间时不能做无前视偏差的历史回测 |

缓存实际分组：fees 44 READY＋5 PARTIAL，history 40 READY＋4 FAILED，money_yield 6 READY，status45 READY＋4 FAILED，共148条。这些是全库市场缓存统计，不等同本人资产。

只读研究目录存在历史6份数据集，不能合并为330个独立产品。当前混合池18/62项有exposure、benchmark、成立日期、manager等字段，均属合成原料；真实44项未提供这些字段，manager_change_date 0/62。已有数据结构不是实际因子/真实经理变更数据。历史合成存储可到2027-12-31；原catalog按available_at和业务日期过滤，审计原始最大日期不表示未来行情可用于推荐。A1快照还拒绝超过as_of/业务北京时间日期的NAV。本轮实时抓取仅记录摘要，不并入任何快照/模型。

### 状态区分

- **实际验证**：上述HTTP、缓存计数/字段/日期、五类真实上游响应、78条旧产品适配、F08/F10/F18快照公式和独立路由。
- **仅代码支持**：非空本人持仓的成本/冻结/可赎字段、历史披露和价格版本映射、其他宏观实时函数、定时同步TTL逻辑。未把这些标为本轮实账或新鲜上游验证。
- **暂不可得**：正式全量授权AssetSummary、完整真实评级/交易规则、27条真实基金家族关联、真实44产品exposure/benchmark等特征、未填写收入负债期限、完整本人投资收益序列。

## 3. 字段映射及快照口径

共用结构和约束详见[落地契约](portfolio_contract_v1.md)。

| 旧原料 | A字段/处理 |
| --- | --- |
| cash[].balance_fen/held_fen/available_fen | AccountFact；只允许借记savings/checking；逐项与B总额核对 |
| product.version / 研究product_version | product_version，必须显式且一致；不可猜默认1 |
| disclosure.version / 研究disclosure_version | disclosure_version；都缺时拒绝 |
| lots[].version_id | 仅目录精确product_id＋version_id查version；不解析字符串，无法映射拒绝 |
| lots[].units/redeemable_units | 四位份额；按账户/产品/版本分组求和，保留lot_ids；冻结/锁定不当可赎 |
| lots[].market_value_fen/nav/nav_date | 可信市值；缺失null；不以成本或零替代；同版本lot冲突拒绝 |
| category | 仅明确cash/money/bond/mixed/equity适配已有板块语义；index及不明确类型缺失标记，不猜指数资产类别 |
| 赎回PENDING_CASH.details.actual.cash_fen | 赎回应收，按唯一operation_id一次计入；缺值null；不计入available_cash |
| 正式assessment | assessment_id/capacity_level/purchase_allowed/expires_at/confirmed；保留真实等级，缺失显式标记 |
| fee规则 | purchase_fee_rate原样；redeem_fee_bands→赎回费规则；未知sales_service_fee_rate为null，不套用年度综合服务费 |
| 家族/份额 | 只用显式fund_family_id/share_class及证据；B/R等非A/C原料不强转A/C，本轮只盘点，范围决策留A2 |
| 当前context | 保留context_id，不能作为全量scope；scope/account_ids/snapshot/as_of须来自B全量汇总 |

全量总资产公式：借记卡账面现金＋关联持仓市值＋赎回应收；冻结不重复计入。F08：800000现金、100000冻结、200000持仓→总资产1000000，可支付700000。F18：两账户600000＋200000现金，100000冻结，150000持仓、50000应收→同样总资产1000000，可支付700000；两个lot聚为一个position，750.0000份。F10：未知持仓估值→总资产/basis=null，附缺失原因，不输出假总资产。

SnapshotSource是服务端可注入异步读取接口。明细必须覆盖全部scope并与同次summary一致；旧字段适配拒绝不同时点。FixtureSnapshotSource限定虚构用户并强制fixture_only，未被中央应用默认注册。A1消费协议而不替B创建ORM汇总，后续B完成source后可替换，不改变A快照schema。

## 4. 代码、运行及实际验收

新增schema、旧字段适配、snapshot服务/路由/CLI、两支生成与探测脚本、24份fixtures、两支unit测试和独立API测试；新增本MD、契约MD、脱敏JSON，并更新README导航/implementation_progress。没有改B负责的共享wealth.py/main.py/交易服务/前端。

在仓库根目录、已安装后端依赖的Python环境中：

```bash
# 独立无LLM、无数据库、无银行交易
python -m backend.app.services.orchestration.wealth_portfolio_cli --fixture F18
python -m backend.app.services.orchestration.wealth_portfolio_cli --fixture F10
python -m backend.app.services.orchestration.wealth_portfolio_cli --schema
python -m scripts.generate_wealth_portfolio_fixtures
python -m pytest tests/unit/test_wealth_portfolio_contract.py tests/unit/test_wealth_portfolio_snapshot.py tests/integration/test_wealth_portfolio_api.py -q -p no:cacheprovider

# 已有虚构演示服务的只读盘点（不注册新路由）
python -m scripts.probe_wealth_portfolio_data --base-url http://127.0.0.1:8010 --output probe.json
# 可选只读SQLite全库字段/市场缓存审计，不作资产来源
python -m scripts.probe_wealth_portfolio_data --base-url http://127.0.0.1:8000 --database /demo/wealth-demo.db --output /tmp/probe.json
# 已安装AKShare 1.19.1的现有环境；最多一次/方法，硬超时25秒
python -m scripts.probe_wealth_portfolio_data --live-only --output probe-live.json
```

实际使用现有wealth-demo-backend容器执行pytest；无SQL连接的DTO/builder/ASGI测试：最终**59 passed / 4.21s**。A0单元提交前**37 passed / 1.38s**。独立CLI F18、F10和JSON schema导出实际执行成功；非空总资产与不完整null分别符合合同。独立API：200同协议替身、401未登录、403其他用户、503未注册B来源、409上游冲突。真实上游五类每类一次全部成功；首次本机依赖目录读取被沙箱拒绝后用已有运行环境解决，未重新安装依赖、未将失败包装为成功。

没有本轮页面修改，因此未做浏览器/前端构建；没有改数据库结构或交易，因此没有声称双库交易/授权回归；没有运行旧E01—E40。实账本人当前非空lot估值、全量B接线与两页相等、其他实时宏观接口、年度货币收益全覆盖、正式配置模型均未验证/未实现。

本地提交：A0 `b8ad20e`（feat(wealth): define whole-portfolio contracts and fixtures）；A1后续同名阶段提交包含快照、实测证据和本文。通过`git log 1ce96d8..HEAD --oneline`查看本轮完整提交。未推送或创建PR。

## 5. 后续模型讨论需解决的问题（未选择算法）

1. B如何确定全部授权关联账户，并在一致版本下提供summary和明细、刷新和日期边界？A已具备同协议消费能力。
2. 真实基金评级/完整规则与family证据缺失时，研究和可执行候选的边界如何明确？没有证据不能补模拟风险等级或声称分散来源。
3. 日净值、货币每万份收益、宏观月频怎样对齐真实可用时点？分红总回报、费用、修订及口径变化证据未齐前，不能承诺无前视回测。
4. 对真实产品exposure/benchmark等缺失字段采用何种输入/样本条件？合成因子不可作为真实基金证据。研究样本的完整性筛选不能缩小全目录可见范围。
5. 收入/负债/期限未提供时如何维持可解释的整体配置，而非补造收入或引入单次投入预算？保留正式风险等级与账户事实。

本轮A0/A1到此停止；A2—A6等待讨论后另行授权推进。
