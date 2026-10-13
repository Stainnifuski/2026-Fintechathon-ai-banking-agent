# 理财01／02：当前本地集成工程交接

更新：2026-10-13 America/Toronto。验证代码：`52f150d22e7ebde2f4ef397fffdc5579c08d50eb`（本地 integration/wealth-01-02）。

**当前：真实基金资料＋比赛模拟交易的本轮 Codex 联合检查通过，人工待验；未封板、未发布、未合原 main。** 旧07初版/零候选/等待政策批准状态已被本文件替换。01前11步、02手动流程及已有有效修复保留。不恢复全量采集，不接新LLM/NLP，不重新选模型或改数值参数。

## 1. 相对00的最终修订和责任

|类型|当前要求|
|---|---|
|新增|真实公开资料与项目模拟交易覆盖层、政策指纹、公开NAV冻结模拟锚、可复核的日频版本与离线包；canonical AdvisedPosition可选disclosure_version；B多账户时显式payment_account_id|
|调整|真实等级证据要求改为经用户批准的独立项目分类等级；七个合格具体代码为联合样本，不宣称七个独立风险来源；普通A05不依赖Plan/金额/期限；整体A01仍必须有完整全授权资产快照|
|移除|A/C切换、默认A、仅C例外、强制家族登记前置；00 J03/J05/J08同步删去切份额通过条件；生产推荐不设七代码白名单|
|保持|已确认DPP、原配置区间U、ETA/DELTA/投影/金融参数；可靠同基金去重；未知关系不编造；正式本人问卷与最新画像、风险/身份/控制权、统一MFA、参数/期限/幂等/审计、受理事实不回滚|
|研究范围|A6含分红历史总回报、固定组合、真正样本外金融效果评价未完成，按本轮决定移出运行封板门槛；不更名冒充通过。B实际账户收益/J18—J23仍须检查|

00需要改写：真实发行人等级唯一前置、强制家族登记、默认A/仅C例外、有效A/C切换、七独立家族数及A6研究封板文字；对应工程契约见portfolio_contract_v1.md最新修订及下表J矩阵。未放宽风险比较或最终入口校验。未知家族使用exact-code操作键，旧pipeline中family_groups等计数表示兼容操作组，不可解读为38个独立风险来源。

01负责事实、资格、特征/DPP、配置/修订和A接口；02负责手动UI、差额、交易、双表/收益与控制接线；04银行持久化账务与规则；03后续接入自然语言时只用受控接口，不自行计算资格/成交。当前修复属于本地集成，不回写02源分支。

## 2. 资料链及真实/模拟分界

分类核实后项目等级为货币R1、债券R2、混合R3、股票R4，独立于发行人评级。可靠公开更高评级或分类冲突拒绝复核，不降级。政策按具体代码、资料、报价和不可变版本登记；不新增30天失效、不新增单笔2万元上限，保留现有银行金额与现金约束。申购/赎回模拟费用为0、无额外销售服务费；现实完整费用未知仍未知。公开最后已取得NAV是冻结模拟成交基准，source_nav_date与simulated_pricing_date分开；北京15:00截止、中国业务日历、申购T+1、普通赎回T+1确认/T+2到账、持有期0、计价前按既有规则撤单。

政策：`real-public-paper-risk-20261013-v1`；数据集：`real-public-paper-v1:d72fa8d6346fcf8a74b9bfd6`。

|环节|当前实际事实|未知/拒绝行为|
|---|---|---|
|原A1响应|fund_purchase_em 27,695行，混合值列27,362：26,395非货币NAV＋967货币收益值；早期22,770为历史记录，不冒充本次统计|货币收益不转单位净值；MM-DD年按观察时点之前最近日期解释，原值/解释分别留存|
|09保护缓存|000015/000016真实公开脚本；代码/URL/文件SHA绑定、仅解析数据不执行JS；000015完整3282点已入包|以实际获取时刻作保守available_at，不假造历史发布时间；不提前使用未来点|
|正文|旧/新同一路径并集都是83，old_only/new_only均空；旧84统计错误已纠正|不是接口新失败或正文丢失；费用/绩效缺值不补0|
|A全目录|27,998真实基金；scope_verified19,265；非选定七名单扫描|当前没有公开主体/正文/可用NAV/有效销售及映射的产品按原因排除|
|比赛银行映射|38具体代码合格：债券9、混合24、股票5；材料操作组36|当前货币基金无完整映射，返回unavailable而非假造NAV/快赎；旧S/B目录/账务仍保留|
|七样本|见下表；已知同基金份额不凑数，未知关系不宣称独立|joint_samples仅验收元数据，不用于生产筛选|

|代码|公开名称|类别/项目等级|公开NAV日期|实际保存历史点|
|---|---|---|---|---|
|000015|华夏纯债债券A|bond／项目R2|2026-10-09|3282|
|001286|易方达新鑫混合E|mixed／项目R3|2026-09-30|1|
|005242|中欧时代智慧混合C|mixed／项目R3|2026-09-30|1|
|006253|永赢消费主题C|mixed／项目R3|2026-09-30|1|
|007331|国泰惠融纯债债券|bond／项目R2|2026-09-30|1|
|016173|汇添富优势企业精选混合C|mixed／项目R3|2026-09-30|1|
|020458|平安医药精选股票A|equity／项目R4|2026-09-30|1|

上表等级不是发行人评级，价格不是未来真实成交价。000015以外六样本在当前包仅有一个已保存净值点，曲线不连成虚假历史线，涨跌/一年指标可能null。使用真实NAV描述统计不等于含分红总回报或金融效果验证。

数据版本：`b06b8fd23996785385ba61fc1c6f311dc0531227cc32c294da10f62434f77fdd`；日频：`2732304411e02771e334239139049d5b496b491e1106331b32cccd24db3bf938`。原始缓存→混合列解析/09脚本→ProductFact/资料版本→日频快照→银行政策版本→A当前适配→离线包都已核对；没有恢复全池联网，本轮新增外部资料HTTP为0。

### 字段必要性与消费者

|字段|消费者|规则|
|---|---|---|
|本人最新风险、代码/产品/披露/规则/政策版本|A资格、B预检与最终受理|必要；未知/冲突/越级不放行，手选/零/极小权重也校验|
|核实分类及可溯源至少一项目标/策略/基准/跟踪正文|特征/DPP、类别、理由|按已有冻结方法；空材料不伪造向量；不要求每项齐全|
|主题正文证据|主题过滤|无匹配与无资格分别解释，不能靠名称/分红次数编造高股息|
|全部授权借记账面现金＋持仓市值＋赎回应收|A01 T与配置|未知估值不得补0；冻结不重复；available为付款口径，应收未到账不能买入|
|可支付现金、起购、冻结、可赎份额、当前报价|B prepare/购买/赎回|当前重新读取；订单不是可赎份额；申购未结算不提前造持仓|
|公开费用、行情窗口/日期、历史成本|客观资料/收益|非必要缺值不阻断全部推荐；未知null，公开与模拟费用分开标记|

## 3. 实际接线与canonical DTO

|接口/组件|接法及边界|
|---|---|
|GET /api/v1/wealth/asset-summary /portfolio/snapshot|统一全授权scope/snapshot/T；正常main已安装A source，无隐式fixture回退|
|POST /portfolio/plans、GET/PUT /portfolio/plans/{id}|同一A MemoryPortfolioStore及生命周期，旧版/变更事实/控制状态拒绝；重启丢失未提交纯配置需重读，银行已提交记录不丢|
|POST /portfolio/recommendations|独立A05 general/category/theme、cursor和selection binding；不要求Plan/预算/期限|
|POST /wealth/actions|schema wealth-action-v1；plan_portfolio / recommend_products / revise_portfolio / prepare_purchase / list_orders / list_positions / show_product_detail / compare_products / show_wealth_returns / preview_order_cancellation；固定结构字段，不走新NLP|
|ProductPicker→StructuredPortfolioReview→AllocationReview→PortfolioAllocation|按核实具体代码多选/换批，详情比较可选；滑块adjustment不漂移原U，preference才重规划；已选卡完整保存供接管核对|
|POST /portfolio/plans/{id}/prepare|B读取原A Plan＋对应快照＋当前银行事实；canonical selected_positions只有product_id/product_version/share_class/target_ratio_total四字段。金额/现持/bucket/account_ids/disclosure来自advised_positions，不能向四字段引用补假账户|
|多账户|唯一借记账户可解析付款账户，多账户须明确payment_account_id；同一代码跨账户持有保留，跨账户减持分配不明确则ACCOUNT_ALLOCATION_REQUIRED，不自动卖出或自动跨卡付款|
|整份执行|B execution_plan_id/version→/plans/{id}/preview /confirm /verify /execute；一次本人确认和统一短信/TOTP完整集合绑定；未提交旧A修订使旧预览/确认/验证/受理均STALE_PORTFOLIO；已受理/未知结果保留原号恢复|
|TaskControlPanel / OperatorPanel|客户pause/resume/handoff；服务端允许名单角色领取→原事实/历史方案→说明→交还；比例建议不是交易授权，交还暂停，客户重验。客户伪装operator仍403|
|OrderTable / PositionTable / ReturnsView|独立分页、跨页选中、scope变化清空；份额/市值赎回、在途/应收/到账；真实模拟账户收益日/月/年/累计/日历与缺值|

## 4. 修复与实际证据

1. 原混合NAV被漏读及09脚本未进入构建：分离货币收益、严格解析保护缓存、版本及包保留历史；83正文统计复核。
2. 来源只落在旧78模拟目录：导入全质量交集38真实具体SKU，current protected source绑定对应policy，已核实类别/公开资料保持real，执行保持simulated。
3. 换批每次误409：嵌套读取时刻被误计成池修订；去掉观察时刻的假变化，当前事实/产品/价格/评估版本仍绑定。
4. 未映射同份额污染合格具体代码批次：公开操作仅输出选定具体代码；关联证据仍留审计/去重。
5. B把canonical四字段引用当enriched交易目标：改读advised_positions、披露和真实账户范围；不重分配A目标整数分。
6. 修订时React重挂载/滑块草稿误禁用prepare：稳定作用域身份，合法接受后同步草稿；金额仍是精确A target_fen，不以显示舍入替换。
7. A变化但旧B预览仍可请求：四个交易入口重新检查未提交A版本；受理后的恢复不依赖易失A草稿。
8. 接管跨批产品卡缺失：Plan保存已选代码卡而非短推荐批；同次资格复用、七卡取代无关批次卡，模型和参数不变。
9. 统一错误外形丢A风险原因：A message保留安全原因码，不输出账户凭据；真实R2→020458 R4的0/极小比例和最终HTTP均拒绝。
10. 比较界面明确标项目分级与模拟费用，不把0模拟费用当真实渠道费用。

实际日志在本地ignored `.deps/real_fund_close/`，精简当前结果见wealth_joint_validation_20261012.json；逐项环境和人工说明见wealth_joint_acceptance.md。不同测试集合重叠，不相加为总通过数。真实资料不是fixture；bank-layer authorize helper仅隔离账务测试，浏览器MFA单独实测。浏览器SQLite，PostgreSQL为真实独立库/API/账务测试，不冒称PG浏览器。

## 5. 可复现准备及启动

Python3.11+、Node依现有项目锁文件；`pip install -r backend/requirements.txt`，独立核验PG需asyncpg与bank_test_*测试库；`cd frontend; npm ci`。本轮运行不需要AKShare联网或模型密钥。原`.env`保留，本轮建议LLM_MODE=offline；JWT_SECRET/INTERNAL_BANK_TOKEN仍由操作者私有设置，不能提交。

离线包放仓库相对`.deps/wealth_portfolio_data/packages/`。Git含公共config及准备/校验脚本，不含原始采集缓存/数据库。先取得这次交付的package.json＋snapshot.json.gz，再执行：

```powershell
python -B -m scripts.wealth_portfolio_package install --manifest .deps/wealth_portfolio_data/packages/b06b8fd23996785385ba61fc1c6f311dc0531227cc32c294da10f62434f77fdd.package.json
# 先在私有配置中明确新隔离DATABASE_URL、JWT_SECRET、INTERNAL_BANK_TOKEN；不可指日常库。
$env:LLM_MODE='offline'
$env:BANK_API_BASE_URL='http://127.0.0.1:8013'
# 启动现有main迁移/建表/种子用户，等health=200；下列CLI另用同一DATABASE_URL终端。
python -B -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8013
# 以下两条在同一隔离库已有表/合法seed用户后执行；API进程保持运行。
python -B -m backend.app.services.bank.wealth_data_cli import --import-root config/wealth --file paper_products.json
python -B -m backend.app.services.bank.wealth_data_cli init --user-id U001 --dataset-key real-public-paper-v1:d72fa8d6346fcf8a74b9bfd6
# UI在产品资料与数据工具中选择本人新context；初始化不复制普通账户余额。
# 前端另终端：BANK_API_BASE_URL同样指8013，cd frontend后运行：
npm run dev -- --host 127.0.0.1 --port 3013
```

需先按现有迁移/启动准备表，再导入；当前数据工具默认不选择新context，在页面“产品资料与数据工具”选择本人的新context。正常main路径已实测；专用联合server另有明确初始化/lifespan，只替换测试时钟与数据库，不替换A算法/产品/交易响应。离线缺包/错SHA/错版本明确失败，不静默变fixture或网络抓取。

包：gzip 2504223字节，SHA `75b2bbe060aa90087242efa8d86aa5e61dc665f020436036aa8b13679d509a0a`；快照SHA `defc17a602a210e888bda70e10a5b967fc8d98549a790e599ab27cff7060edff`；89公开文件，空目录实装27,998条、38policy、000015历史3282点，日期不续期。缺包/篡改拒绝已验。索引首次启动约22秒，API正向三次为 2.959, 2.434, 2.950 秒；冷启动索引不混作API响应。

## 6. 剩余边界及闭合条件

- 人工验收尚未完成，原main没有发布；人工结果和必要缺陷复验通过后才进入最新main整合/发布阶段。
- 公开完整费用、未知同基金关系、多数产品长历史仍缺；如实null/unknown，不能宣称高股息有匹配、38独立家族或研究金融有效性。
- 多账户指定减持分配要求明确输入，当前不自动跨卡代做；缺失则受控拒绝，不伪造“执行成功”。
- 离线gzip是必要本地交付材料，未放Git；02需按manifest校验包。可由原有缓存确定性重建，但无完整原缓存的机器应使用交付包，不能依赖本机绝对路径。
- 03自然语言/统一总DAG不在本轮；接口索引在handoff.md最新节。LLM不得写确认、MFA或绕过当前资格。
- 原工作区577文件、HEAD与.env SHA复核未变，所有旧账务/原始缓存保留。旧采集没有恢复，无远端写入。
