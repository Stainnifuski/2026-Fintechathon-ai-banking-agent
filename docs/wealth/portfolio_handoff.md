# 理财01／02集中收尾交接（2026-10-15北京时间）

## 当前状态与范围
当前交付到Draft PR #12，不合并main、不接新LLM/NLP、不恢复采集。最终人工验收仍由用户完成；不能将本轮技术检查称为整项目封板、生产认证或金融有效性验证。

真实货币基金按最新指令**永久退出**，覆盖此前“保留历史兼容”的要求。删除固定1元计价、收益率专用适配、专用类别接口、清单及样本；不保留其交易兼容分支。真实基金静态质量交集为13,578个具体代码：债券4,093、混合8,444、股票1,041；不是13,578个始终可买的承诺。身份、申赎/报价、风险与交易控制仍逐当前业务条件核对。模拟银行B001—B018及MW002共19个定义保留；活钱为账户现金及符合政策的既有R1模拟快赎，不给普通基金自动赋快赎。本金可能亏损。

主题/高股息、A/C切换、强制家族证据、文本DPP/MMR、旧对话/研究工具、客服领取交还及另建模式切换均为**范围调整取消**，不写测试通过。已知同基金关系继续去重，未知不编造。保留原宏观模型、产品投影、原U、ETA=.25、DELTA=.01；本人确认、统一高风险MFA、幂等、审计、暂停及跨场景共用权限未删除。

## 相对00的契约差异
- 新增：按代码NAV观测和正常银行估值消费；运行计价政策published-nav-by-order-date-20261015-v1；前端整数操作的原U只读校验契约adjustment_contract；运行范围清单competition_runtime_scope.json。
- 调整：A07手动进取/产品比例仅整数百分比，服务端界限向内取整；自动模型结果和金额保持原精度。示例连续约40.2%—59.8%对应整数41%—59%。校验失败保留旧Plan，合法修改升版本，未提交预览重做，已受理订单不变。
- 移除：真实货币及上述已取消范围；00中的相应必过项据此取消，不是暂待实现。
- 保持：全部授权借记账面现金＋持仓市值＋赎回应收；冻结是现金子集，不重复计资。总资产和可支付现金分开。风险上限、期限、窗口、额度、份额及参数绑定不放宽。

## 正常接线与动态报价
A源backend/app/services/orchestration/wealth_portfolio_joint.py，生命周期wealth_portfolio_lifecycle.py，银行wealth_service.py与wealth_quotes.py。正式入口仍是Wealth/index → StructuredActionPanel → ProductPicker / StructuredPortfolioReview / AllocationReview / PortfolioAllocation；交易复用PlanExecution、SingleTradePreview和统一第二因素。

GET /api/v1/wealth/portfolio/snapshot；GET /portfolio/products/{code}?context_id=；POST /portfolio/recommendations；POST /portfolio/plans；PUT /portfolio/plans/{id}；POST /portfolio/plans/{id}/prepare。这些路径均相对/api/v1/wealth。B的原plans预览/确认/验证/执行、订单/持仓/赎回/撤单接口保留。货币类别请求不再是合法契约。

静态索引和政策在启动校验加载；银行完整资料在第一次需要全量资格的受保护读取时装载一次，按数据库、固定资料范围缓存。后续不再次从银行传输/解析全目录；账户、正式评估、context、任务控制动态读取。当前展示产品再按具体代码核对报价及资格，动态停售不能被固定模板覆盖。prepare只读取所选及执行相关代码。首次银行资料读取仍有成本，未伪称所有材料在首个用户请求前均已传输完毕。

按需范围为当前展示、所选待买、全部实际持仓和处理中的订单代码；去重、单代码请求合并，最多3个并行，观测缓存60秒且最多128代码。使用既有AKShare1.19.1 fund_open_fund_info_em(symbol=具体代码, indicator=单位净值走势)，无全市场接口调用。该上游返回单代码历史；正常估值只持久化最新有效日期及未计价订单所需日期，图表仍点开读取，不全池下载历史。源日期与观测时间分开，未知发布时间保守采用实际观测瞬时作为可用时点，不能把获取时间当NAV日期或倒灌历史。

受保护银行/assets及/asset-summary先按实际范围更新观测，再建立一致读快照；持仓、市值、浮盈、总资产、表格、头部与新Plan使用同一NAV表。不改份额、成本、现金和已结算价格。来源失败保留最近可靠值、日期和失败提示；无可靠估值时总资产未知，禁止依赖完整估值的方案确认，但不阻断独立合法推荐。参考NAV不等于最终成交价；真实基金按订单计价日的已公布NAV，未公布保持PENDING_PRICING。模拟银行产品继续用明确标源的原模拟价格/收益/到账规则。

## 当前证据（不累计有重叠的测试数量）
- 68通过：受影响SQLite/PostgreSQL报价、全池银行映射、prepare、两表、实际收益、账单排除、快赎；affected-final.log。
- 6通过：真实响应消费、独立测试报价变化/新Plan、MW00230天合法退出，双库；new-plan-quotes.log。另2通过为追加动态停售反例；live-stop.log。测试报价明确标ISOLATED_TEST_QUOTE_NOT_REAL，不能冒充真实行情。
- 57通过：生命周期与活钱准入；unit-final.log。目录6通过；directory-v2.log。最初目录旧数量和缺失fixture失败已保留，修正后复验，不用失败前结果算通过。
- 前端34通过：整数输入（含57%/58%浮点边缘）、选择、表格、资产摘要；frontend-fixed34.log。正式类型/构建结果见build-final.log（最终类型检查及构建，9.31秒）；大chunk提示不等于构建失败。
- 真实联网取得000003：2026-10-14 NAV=0.947，观测2026-10-15T02:26:48Z；真实响应保留于私有quote_observation.json，服务消费在隔离双库验证。实际671030成功观测入库审计为2026-10-14 NAV=4.0211，正常HTTP资产读取对应市值5777.48元、份额1436.7913、成本6356.24元；当次再请求上游失败，正确显示failed_using_last_reliable。现金12148.94元、份额/成本/历史成交不变，详见real-http-valuation.json；不把该次失败写成当次更新成功。
- API固定R4/T20,000空持仓/000003＋671030：初始7200/6400/6400；45%=7200/7040/5760；55%=7200/5760/7040。边界41/59、步长1%，90%拒绝，旧版本和零订单/占款不变；golden-api.json。R2在A手选、0/极小权重及最终银行0/1/100000分均拒绝；guards-api.json。另R1全0答案产生禁购是正确适当性结果，不冒充R1可买正向测试。
- 当前浏览器证据和最终补验结果另记同一联合回传。此前正式九题/本人确认、整份SMS、申购占款、撤单、普通赎回/快赎、收益日历未受影响的有效记录复用.deps/round2的state-1/2/3、state-quick及截图；state-ordinary-accepted.json明确无效，不引用。

## 性能实测
最终静态启动25.016秒；以下最终HTTP实测，不声称满足旧5秒目标。后续批次没有全目录调用；首次正式显示包含一次银行资料初始化，随后推荐复用同一材料。写请求不自动重试，保留加载/防重复和明确失败反馈。

|请求|耗时|银行全目录调用|银行按代码调用|
|---|---:|---:|---:|
|initial_formal_display|4.128秒|1|0|
|repeat_formal_display_fixed_material|1.066秒|0|0|
|initial_material_and_exact_000003|5.573秒|0|1|
|initial_material_and_exact_671030|5.504秒|0|1|
|recommend_no_prior_plan|12.982秒|0|1|
|next_batch|12.035秒|0|1|
|Plan|7.477秒|0|0|
|prepare|1.819秒|0|5|

推荐/换批各展示3个代码；prepare核对2个执行产品，内部多个受保护检查复用同一代码观测，不是反复全市场请求。计数来自实际服务日志，不按逻辑任务数估算。首次普通推荐没有旧Plan；正式页面资料与A共用该静态目录。

## 永久退出清理、数据版本与准备
先备份当前独立库，实查真实货币订单0、持仓0、关联账务0，未伪称删除不存在的流水。删除278个产品、278个版本、278条NAV、278个研究条目；移除快照947个真实货币身份；全部账户现金及其他订单逐项不变，PRAGMA foreign_key_check无孤立记录。只操作集成目录的专用库，原工作区/原业务库不动。恢复备份仅作为私有回退材料，不是运行兼容入口，也不打入新包。旧含货币包已移出可用目录。

为了保留非货币产品和既有订单引用，其规则/产品版本及dataset key未改；本次授权范围清理以独立scope_policy_version记录，并定点勘正本库导入manifest指纹。这不是允许任意覆盖既有不可变版本的通用接口。

基础DATA=d59ecfcc2ecb09e6573278d5b5f38524407f2ac72ac0051ebf7e7b63f53ff3e1，DAILY=5a1f10fea14d1691fc2341df93536d4550d03e2214f1ed390ddd7ec89dc4b5f8；增补后DATA=e08200a8427a1a80f635bcc993d563c9660dacb4cb590f8c67153ff919ab6e83，DAILY=d351efa228b2b9b0d42f77fbded651cd6eaf68463ef5fb1953ff34a85bbc91f9。可用数据包.deps/final_round/wealth-final-offline-20261015-disclosure-v2.zip，SHA256=63094dcbefbc4e92745859db095458fa6e29a10019844b17fe4bc41bc8655184；禁网空目录安装通过，包含13,578普通真实基金＋19银行定义，无env/密钥/运行库。原始缓存不发布；所有专属货币功能和可用包条目已退出。

从仓库根目录安装依赖backend/requirements.txt、requirements-market.txt，前端npm ci。解压交付包后使用python scripts/wealth_portfolio_package.py install --manifest <解压目录/package/d59ecfcc2ecb09e6573278d5b5f38524407f2ac72ac0051ebf7e7b63f53ff3e1.package.json>安装资料包，保持公开配置指纹一致。设置自己隔离DATABASE_URL后：

```shell
python -m backend.app.services.bank.wealth_data_cli import --import-root . --file config/wealth/paper_products.json
python -m backend.app.services.bank.wealth_data_cli init --user-id <本人演示ID> --dataset-key real-public-paper-v1:b498da40b510a409211716f0
python -m backend.app.services.bank.wealth_bank_supplement_cli --user-id <本人ID> --context-id <已授权模拟context>
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port <端口>
```

BANK_API_BASE_URL指向对应银行服务；统一MFA和内部令牌沿用现有受控配置，不复制本机.env/数据库/凭据。正式frontend npm run dev，BANK_API_BASE_URL指向上述后端。当前3014/8014为独立受控人工环境，3010/8010/5432保留。正常backend.app.main使用独立副本/8015烟测，临时端口结束后关闭，不替代完整业务验收。

## 分工、限制及闭合条件
01负责配置模型/材料与推荐，02负责共享银行事实、正式交易接线和页面消费，统一LLM/总DAG由后续03/团队接入。本轮只完成既定手动子场景，不开展02新业务或新模型。主要剩余依赖是用户人工验收；上游可能失败，真实基金NAV未公布时必须等待。历史总回报、真正样本外效果、2027预测日历的现实准确性及生产认证均不在本轮通过声明内。客服领取/交还不再是阻塞项，已范围调整取消。

所有原始验证文件在集成目录.deps/final_round和未受影响历史.deps/round2；正式文档不包含密钥、数据库或私有任务书。最终本地SHA与浏览器实测追加在联合回传。没有任何远端写入。用户人工确认后另行授权发布，当前停止。


## 本轮最终页面与检查补记
- 正式浏览器：000003（R2）与671030（R4）比较字段正确；关闭比较仍为同一Plan v1并保留两产品。浏览器初始7200/6400/6400；45%提交v2为7200/7040/5760；55%提交v3为7200/5760/7040。45.5和90按钮禁用。截图见.deps/final_round/browser-compare.jpg、browser-integer.jpg；之后补充明确整数/范围提示，最终构建再通过（build-shared.log，6.86秒（最终代码））。
- 最终报价补验quote-final-patch.log：SQLite/PostgreSQL共2通过（63.01秒）；契约与字段适配contract-final.log：68通过（3.23秒）。这些与前述集合存在交集，不加总成独立用例数。
- 真实行情证据：671030的2026-10-14 NAV=4.0211，实际观察时间2026-10-15T02:57:47Z，已入正常NAV表并由实际HTTP估值使用。本次随后HTTP刷新上游失败，正确显示failed_using_last_reliable；不把失败请求冒充成功。000003实际按代码响应成功另见quote_observation.json，双库消费该真实捕获响应属于重放；隔离报价变化明确为测试报价，不冒充真实行情。
- 最终保全：原有账户行、全部原订单与lot完全保留（新增隔离测试账号单列）；外键无孤立。13800000125现金20,000元、零订单/持仓、未代填问卷；原577文件、.env和HEAD哈希一致。
- 独立3014/8014正常运行，原3010/8010/5432保留，正常启动烟测8015已关闭。用户人工验收尚未完成；本轮无远端写入。
- 已闭合首次UI重复资料读取：正式/products与A使用同一个按资料版本及授权scope核定的静态源，并发初读合并；前端等待context并复用同scope初始promise。static-catalogue.log共7通过；benchmark-shared.json真实HTTP首次1次全目录，重复显示及推荐/换批/Plan/prepare全为0，权限/资产/选中产品控制仍动态核验。

最终资料包按当前配置重新生成并禁网空目录安装通过（54.08秒）；文件指纹与当前源码配置匹配。功能本地提交e27d27f0f2b31863c7c6d0c62b123aab5dc56b19、a0d329c（目录共用补修）。上述是历史阶段记录；当前披露绑定与包版本以下述v2记录为准。


## 正式理财环境入口修正（2026-10-15）
“原银行账本（兼容8产品）”实际是legacy-v1哨兵：selected()返回None，资产读取本人不属于模拟context的原账户，产品读取无rules.dataset_id的当前版本。当前独立集成库实查该集合7个产品（MF001—MF005、MW001、MW003），并非固定8个；人工账号原账户ROUND2H-LEGACY余额0，当前比赛账户为SAba7edaba6791dcf182160535d43e，现金20,000元。
正式页面仅展示本人已有、未归档的real-public-paper-v1比赛context，优先保留当前比赛选择；当前选择为旧环境时经原授权context选择接口切换到已有比赛环境，不创建账户或复制现金。无可用比赛context则明确提示初始化，不回退旧环境。共用银行模块/API、原账户、历史订单/持仓/流水均保留；未新增旧环境兼容能力。
核验：只读数据库审计.deps/final_round/legacy-entry-audit.json；环境筛选/默认/缺失3个独立检查通过；最终类型及构建见legacy-entry-build.log。正式浏览器核对结果另附当前回传。


正式页面精简补记：整个比赛环境下拉框及环境说明已移除；后台仍自动核定并选择本人当前比赛环境，保留context/账户范围校验。无需手动选择环境，不修改共用账务或历史记录。


风险评估提示修正：正式结果与方案错误统一显示中文及下一步。未评估时先展开“本人风险评估”→“填写／重新评估”，本人回答9题并提交，再点击本人确认结果，之后重新点击推荐或规划。未确认、过期、来源绑定不明和禁购分别说明，不以重复填写或跳过评估作为通用解决办法。内部码保留在网络/审计证据，不直接展示给用户。前端针对性检查4通过；最终构建见chinese-feedback-build.log。


## 最新main整合与PR交付
已按用户新授权整合aa436b0；4处冲突逐项保留双方功能，96项相关后端、41项前端及构建通过，证据与复现范围见../validation/wealth_main_integration_20261015.md。当前进入PR审阅，未合并main；此前“本轮不远端写入”是原阶段边界，由本次明确PR授权覆盖。人工验收和资料包依赖仍保留。


PR补充检查：正常时钟恢复、原配置保全及旧货币专属资料清理的既有证据保留；披露指纹问题已修复，使用下述v2快照和新离线包。

## PR #12 披露指纹闭合（2026-10-15）

- 原失败测试保持原样，现已通过。同版本风险、费用、期限、计价、边界及公开策略正文变更可识别；缺失/不一致绑定继续拒绝。effective披露重复规范化不会掩盖正文篡改。
- 000003原v1快照、精简、完整指纹均为`f3b4c804ffacdedf2fbd62a51223f5bbd66e9c9470e43e55394cfa8cc0320f7d`，原因是根本没有绑定正文。旧精简捕获只有13个披露字段；完整读取另含dataset/source/project_policy/public_fields等。运行时pricing由“使用本版本冻结的最后公开NAV…”改为“最终按订单计价日净值…”，limitations由“价格冻结…”改为“比赛模拟资金、费用和交易…”，并增加runtime_pricing_policy_version。直接加入整个disclosure同时引入这些差异而误拒。精确值、前后SHA见[字段证据](../validation/wealth_disclosure_fingerprints_20261015.json)。
- 统一模块`backend/app/domain/wealth_product_content.py`，契约`wealth-static-disclosure-v2`。必要披露正文、原始及effective计价/边界、公开目标/策略/基准语义、项目政策版本、规则均绑定。精简/完整保持相同JSON类型和缺失/null语义，不把嵌套对象转字符串。公开材料原始来源仍由material/classification指纹独立核验。
- NAV、报价日期、可用/观察时间和execution_policy.quote不进静态内容指纹；行情时效、报价与政策注册校验仍单独保留。没有放宽风险、确认、MFA、控制或现金检查。
- 同版本原始资料不再因重建库漂移：新导入保留原始冻结计价/边界文本，当前公开NAV计价由独立版本化effective政策表达；没有修改历史数据库记录或成交事实。
- 已离线逐项验证13,578个旧绑定，生成新不可变资料版本及捕获引用；旧快照保留为历史证据，不是可接受新运行绑定的兼容分支。新DATA=`d59ecfcc2ecb09e6573278d5b5f38524407f2ac72ac0051ebf7e7b63f53ff3e1`，DAILY=`5a1f10fea14d1691fc2341df93536d4550d03e2214f1ed390ddd7ec89dc4b5f8`；补充后DATA=`e08200a8427a1a80f635bcc993d563c9660dacb4cb590f8c67153ff919ab6e83`、DAILY=`d351efa228b2b9b0d42f77fbded651cd6eaf68463ef5fb1953ff34a85bbc91f9`。模拟银行19个定义继续使用独立补充资料版本，不混入普通基金基准。
- B017、000003、671030实际HTTP选品成功，推荐/换批/Plan/prepare通过；prepare后订单0、持仓0、占款0。推荐/换批/Plan/prepare耗时见字段证据，首次1次全目录，后续全目录0次；业务动态核验继续进行。
- 最新离线包`.deps/final_round/wealth-final-offline-20261015-disclosure-v2.zip`，SHA256=`63094dcbefbc4e92745859db095458fa6e29a10019844b17fe4bc41bc8655184`；禁网空目录安装通过（56.67秒），普通13578＋银行19，目录27070。公开版本清单`config/wealth/portfolio_content_binding_v2.json`随包校验。原始缓存、数据库、密钥不打包。
- 验证记录见`docs/validation/wealth_disclosure_fix_20261015.md`。本轮不重新采集、不改算法参数；未受影响的前端41项及正式构建证据复用。人工验收与独立离线资料安装仍为交付条件；保持Draft PR，不合并main。


## 2026-10-16 共用银行账户修正（覆盖旧沙箱入口说明）
- 正式理财与其他场景共用本人银行账户；用 wealth_data_cli bind-account 绑定本人现有有效借记账户及已审核比赛资料，不发行、复制或合并余额。历史沙箱仍隔离，不能借此导出测试资金。
- account_scope=shared_bank 为服务端管理员绑定标记；通用账户、转账、AA、账单/订阅及NOVA查询只排除历史隔离账户，不排除共用理财账户。理财占款继续由 available_funds 与转账共享；申购不能重复花费，赎回回到原account_id。
- 共用账户不允许测试工具重置、调资或删除，正常业务使用当前时间；已受理订单和历史成交不改写。旧“空通用账户404转空列表”前端处理已撤回。
- 初始化：先import既有paper_products.json，然后执行 `python -m backend.app.services.bank.wealth_data_cli bind-account --user-id <本人ID> --account-id <本人已有借记账户ID> --dataset-key real-public-paper-v1:b498da40b510a409211716f0`，再按原银行补充CLI关联产品。不执行旧init来创建另一份理财资金。
- 联合核对：登录后通用accounts与理财asset-summary包含同一account_id；对应余额/占款/可用现金相等；同账户理财申购占款后转账受限，合法转账后理财可用现金同步减少。历史隔离资金仍拒绝转账；测试工具拒绝共用账户。


### 2026-10-16 差额准备修复
动作处理不再持有SQLite写锁等待远端银行按代码NAV更新；最终仍在用户锁下复核会话版本/范围与请求幂等。准备失败显示实际中文原因。未采用建议产品不能准备交易：先在推荐结果勾选产品，点击“校验所选产品与比例”，再准备预览，不自动采用。prepare实际成功19.198秒，8个待确认动作，同号结果一致且未下单/占款；详见docs/validation/wealth_prepare_quote_lock_20261016.md。后端重启后内存中的未提交A方案需重新生成，已提交银行事实保留。


### 本人风险评估测试限次
专用wealth_joint_server可用WEALTH_TEST_DISABLE_ASSESSMENT_DAILY_LIMIT及WEALTH_TEST_DISABLE_ASSESSMENT_YEAR_LIMIT分别临时关闭单日/年度次数限制（值true）。正常服务不读取测试开关，默认2/8；关闭对应开关并重启测试服务恢复。问卷接口以null表示该项暂不限次数，审计记录有效值。仅变更次数，不代填、改等级、删记录或跳过最终风险/MFA校验。验证详见上述20261016差额准备记录的限次附录。
