> 当前正式接线（2026-10-13）：main已经注册canonical A提供器；Action与direct计划读写同一store。selected_positions四字段引用不含金额/账户，advised_positions提供target_fen/account_ids与可选disclosure_version。跨批已选卡保存在Plan供接管只读；不重做算法。B多账户须显式payment_account_id，未提交旧A修订使旧B preview/confirm/verify/execute拒绝；受理后仍原号恢复，不撤销账务。真实正向证据和当前边界以portfolio_handoff.md为准。

# 整体配置：方案生命周期与独立组件

## 2026-10-10 第11步当前交付

准确启动及手测控件见[独立前端](portfolio_frontend.md)，本轮实际结果和必要闭合条件见唯一[交接](portfolio_handoff.md)第9节。A独立页面以中文区分已读取资产、评估/产品证据不足与创建写控制，原始状态/快照ID和全部原因在详情可查；没有修改本节下述生命周期、公共DTO或07四项校验。比赛正向仍blocked，历史评价与02联合接线分开判定。下方06—10阶段数字与命令属于对应历史记录。

## 2026-10-09 第10步当前独立前端

当前运行及控件说明以[portfolio_frontend.md](portfolio_frontend.md)为准，以下旧开发入口/14项结果为历史记录。默认Vite启动为比赛事实3022→8052→8010；显式`--mode fixture`才为隔离3021→8051。正常登录沿用原认证链，令牌仅内存。两模式复用同一A视图/client，加载不自动创建方案，真实缺事实不切fixture。

本轮补齐结构化筛选、候选跨批多选/超过5项、合法具体份额切换、携带candidate_binding创建/读回、滑块及明确偏好、逐项百分比/现金草稿。金额汇总来自服务端同快照，不以前端账户求和当总资产；百分数用十进制字符串移动小数点，分金额用BigInt。来源/权限/失效状态显示实际原因；刷新和context切换清除旧选择/cursor/Plan，迟到响应不覆盖。

组件原props保持，新增可选`assetTotals:{available_cash_fen:number|null,held_fen:number|null}|null`；`onAdjust`和`onPreferenceChange`仍传原PortfolioSelection。独立包装`/dev/context`返回snapshot绑定及实际编辑判定；`/dev/candidates/select|switch`核对原访问绑定后复用CandidateService。仅两个独立dev app注册，不新增00公共API，不要求02另建权限/会话体系。

公共schema/router外形、算法/模型参数、资格/画像/写控制门禁未改变。本页不含交易/授权/撤单/赎回。隔离功能通过不能解除07/08当前画像、产品正文和真实写控制缺口；09总回报/OOS仍blocked。实际第10步验收数字及HTTP见唯一handoff第8节和仓库外第10步回传。

## 2026-10-09 第07步契约对齐：当前状态

四项A侧修复已实施，公共DTO及路由外形不变：

1. `_access`允许可信`context_epoch=null`，另绑定真实`selection_version`；不填1、不改名。正整数epoch保留。本人/context/scope/账户、epoch/切换版本变化仍使旧Plan或cursor失效，切回原context也不复活旧状态。
2. 独立读取与推荐无需旧task/session。复用已有服务端候选会话及1800秒MemoryPortfolioStore；关联原task的关闭、版本和写锁仍校验。
3. `resolve(write=False)`不要求`can_edit`或遍历历史写锁；已知关闭及数据隔离仍拒绝。创建、修订是写操作，必须有可信编辑依据；未知停止/关闭不能当false。HUMAN、PAUSED、关闭、停止提交继续禁止写入。访问绑定与权限判断分离，读取时缺编辑字段不使合法Plan失效。
4. 当前模拟银行来源可映射内部`local-competition-bank`渠道。`CurrentSimulationEvidence`是A内部事实，不是公共新字段：同产品/披露版本、当前采集时点、模拟`business_at`、销售/暂停/open/stale、募集截止、规则日历、净值日期及可见时点逐项核对。无原生永久有效区间不单独拒绝；实际存在的区间仍验证，未来数据/过期/错源/错版本拒绝。真实基金评级、渠道、时效及未知风险要求不变；持有/赎回许可不默认允许。

快照双读排除任务`nodes`运行日志，保留任务版本、身份、关联和控制字段；实际首测只因四个旧DAG节点attempts变化409，有限诊断证实后修复，不放宽资产/规则版本变化检测。

当前适用验证：Windows Python3.12.14与现有后端容器Python3.11.17各运行同一来源/资格/生命周期/API/新增对齐集合，结果见[交接](portfolio_handoff.md)。集合完全重叠，不相加。受控fixture只证明边界；真实接口另外记录。

实际独立8052路由：GET snapshot **200**，as_of=`2026-10-09T20:50:20.637377Z`，T=2010000分、可支付310000分、冻结1700000分，1账户、0持仓。POST recommendations **200 / unavailable**，仍含`ASSESSMENT_CURRENT_VERSION_UNPROVEN`和产品身份/版本/销售等真实原因，非成功建议。POST plans **503 / PORTFOLIO_WRITE_CONTROL_UNPROVEN**，当前相关任务未公开session_closed/stop_new_submissions。未创建Plan，不用get/revise404冒充通过。本次启动的8052进程已停止，既有3010/8010保留。

可信Plan仍受当前画像核验、真实写控制、银行v4/研究v1及policy_mismatch等必要事实阻塞；普通/归档范围、历史lot版本、真实风险/家族/主题、历史评价及B中央接线仍待完成。以下旧检查数字/响应只作历史记录，不能替代当前结论。


新增[受保护比赛银行只读独立接线](portfolio_bank_facts.md)。原银行正常登录及认证后四组现有字段已读取；该入口不回退fixture，全授权、评估适用性及会话控制证据仍有缺口，完整配置关闭。

## 范围与来源

仅计算整体配置目标，不产生购买授权、冻结、订单或银行写入。正式中央应用未注册此独立router；正式全授权AssetSummary、当前正式评估、银行可售规则与context/scope resolver尚未接通。默认依赖返回503，客户端不能开启fixture。真实池的风险、可靠家族、主题证据缺口仍存在，不声明正式推荐或金融效果通过。

独立开发app使用明确标注fixture_only的9家族、虚构身份、隔离内存store和注入时钟，计算使用实际DPP、三板块投影、产品权重投影及共用资格函数。它不读取生产银行数据库或.env，也不调用LLM或上游数据服务。

## handler与依赖

模块：`backend/app/services/orchestration/wealth_portfolio_lifecycle.py`。

```python
PortfolioLifecycle(provider: PortfolioModelProvider,
                   store: PortfolioStore,
                   access: PortfolioAccessSource | None)
await service.create(user_id: str, request: PortfolioCreate) -> PortfolioPlan
await service.get(user_id: str, portfolio_id: str, context_id: str) -> PortfolioPlan
await service.revise(user_id: str, portfolio_id: str,
                     request: PortfolioSelection) -> PortfolioPlan
await service.recommend(user_id: str, request: PortfolioRecommendation) -> CandidateGroup
```

`provider`沿用[计算方法](portfolio_model.md)；`PortfolioStore`有insert/read/replace三个async方法，replace接受expected_version。`MemoryPortfolioStore(clock, idle_seconds=1800)`只用于单进程短期状态，async锁保护副本和CAS。首次创建使用随机生命周期ID/version1，合法修订恰好+1；进程重启或30分钟空闲后404。读取活动不能延长风险评估或银行规则有效期。失效GET返回409，草稿与版本留在服务端，不自动重建。

`PortfolioAccessSource.resolve(user_id, context_id, *, write, portfolio_id=None)`由可信服务端注入，返回本人、context_epoch（允许可信null）、全授权scope/account_ids、can_read；session_ref可选，null路径另需可信selection_version；写入另需真实can_edit。不能从客户端取得这些事实。`ExistingPortfolioAccess(db, scope_resolver)`是只读接线适配，复用现有`require_plan_edit`并检查同context全部非终态任务的人工接管/暂停/关闭锁；无commit或银行写入。全授权scope必须由B提供，不能用旧单context资产接口替代。操作员不能凭UI草稿或readOnly属性取得本人修改权限。默认依赖保持关闭，尚未完成公共接线。

计算前与最终CAS前分别重读context、资产、评估与产品事实。资产内容、scope、epoch/session、风险版本/许可/资料变动或证据到期要求重新创建；单纯读取时间变化不算资产变更，未来时间仍拒绝，业务日按北京时间参与指纹。服务端保存原b/h/eta/x*/P/U/delta、舍入结果、validation_contract、建议目标、明确选择及原持仓账户来源。外部只得到PortfolioPlan和小型只读视图，不能回传可信计算契约。

## HTTP契约

独立router：`backend/app/routers/wealth_portfolio.py`，前缀`/api/v1/wealth/portfolio`。使用现有真实JWT认证依赖。

| 方法、路径 | 输入 | 输出 |
|---|---|---|
| GET /snapshot | 认证；可选context_id，指定时核对快照context | AssetSnapshot，旧无query调用兼容 |
| POST /plans | PortfolioCreate | PortfolioPlan v1 |
| GET /plans/{portfolio_id} | context_id、认证 | 当前有效PortfolioPlan |
| PUT /plans/{portfolio_id} | PortfolioSelection | PortfolioPlan v+1 |
| POST /recommendations | PortfolioRecommendation | CandidateGroup |

依赖注入通过FastAPI `dependency_overrides[get_portfolio_lifecycle]`及`get_snapshot_source`，未来公共调用可直接复用同一handler，不另建ActionV1/授权会话。

### 最小schema增量

- PortfolioSelection原字段保留，`revision_kind`缺省adjustment，新增可选整数`cash_residual_fen`。未知字段422。
- PortfolioCreate：context_id、可选growth_ratio_ex_cash、selected具体product_id/product_version/A或C或null数组、candidate_binding。无投入金额、期限、风险或source字段。
- candidate_binding包含候选session_id、expected_version及filter，服务端另绑本人/实际context/epoch/scope/事实；引用版本或选择集合不符409。创建只读候选会话；最终候选版本检查与方案insert共同持有候选锁，不发生一半成功的双写。
- PortfolioRecommendation：context_id、filter、可选cursor/candidate_binding及selected引用。无需已有plan或完整资产；当前风险及购买事实仍必要。CandidateGroup新增可选session_binding作为只读选择版本引用；reason_codes默认空，非ok时保留实际不可用/排除原因，原字段与schema不改。
- PortfolioPlan新增可选model_view和默认空advised_positions。视图包括本版本初始解、固定活钱截面的slider_bounds、可支付现金/应收/冻结及快照。各目标区分advice、selected、holding；原持仓优先归holding，明确调整仍保留在selected_positions，不重复显示为新资产；保留真实内部份额标识original_share_class、账户来源和仅持有上限。外部A/C/null中的null不代表任意份额。
- 模型私有元数据、事实指纹与原U不接受客户端输入。未核实费用/行情标unknown。

### 调整与重新偏好

`adjustment`使用服务器保存的原U，不调用整体优化器，不移动原区间。手动完整显式items加上未重写的原持仓服务端目标，现金为T减合并目标或与显式cash_residual_fen核对。取消新购选择通过完整集合省略；明确原份额0目标是拟减持，不自动赎回。新购0权重仍购买资格验证，超风险原持仓仅在可信holding_allowed下维持/减持，不超过该原具体份额当前值；A/C或同家族不能规避。

滑块调整携带原selected_positions不变及growth_ratio_ex_cash、省略cash_residual_fen。固定当前活钱板块总额，用原P/U验证，产品使用既有按分投影。手动产品模式可带cash_residual_fen；growth_ratio_ex_cash若给出，必须与实际汇总一致。合法手动值原样保存，原U和计算台账不变。固定活钱截面可能变化时，从原有效集重新计算滑块端点，未重设U。

`preference`明确给growth_ratio_ex_cash，重新调用provider建立新b/x*/U及建议权重。items只承接身份，不把旧比例覆盖新计算；现有显式身份不隐式丢失，items为空时保留现有显式身份。想取消新购身份先使用adjustment。失败保留全部原版本。

比例占T，以Decimal字符串传输；显示乘100。不能表示为整数分的新输入明确拒绝PORTFOLIO_AMOUNT_PRECISION，不静默夹值。原服务端产生的非有限小数比例按完全相同原值往返，使用保存的整数分保持不变，不从显示的四舍五入比例改写目标。滑块允许区间不能由三个边际区间拼接。

### 最小调用例

```json
{"context_id":"fixture-context-R4","growth_ratio_ex_cash":".625"}
```

POST后GET `/plans/<服务端ID>?context_id=fixture-context-R4`。首次version1、占T目标20/30/50、非活钱进取比例62.5%；自动建议selected_positions为空。

```json
{"portfolio_id":"<服务端ID>","expected_version":1,"snapshot_id":"fixture-snapshot","context_id":"fixture-context-R4","items":[],"revision_kind":"adjustment","growth_ratio_ex_cash":".626"}
```

成功version2、金额200000/299200/500800分，原U保持。

```json
{"portfolio_id":"<服务端ID>","expected_version":2,"snapshot_id":"fixture-snapshot","context_id":"fixture-context-R4","items":[],"revision_kind":"preference","growth_ratio_ex_cash":".65"}
```

成功version3，重新建立偏好。旧expected_version重放409；R4的.8偏好422，不修改旧计划。

```json
{"context_id":"fixture-context-R4","filter":{"mode":"general"},"selected":[{"product_id":"Q00A","product_version":1,"share_class":"A"}]}
```

POST recommendations得到同协议CandidateGroup及候选版本。创建可带selected全部具体引用（支持超过5家族）及candidate_binding；不截为第一批DPP3个。无资产时推荐保留，创建503。测试覆盖6家族完整带入、换批保留、创建失败候选不变。

### 错误

外形`detail:{code,message,evidence?}`。401未认证；其他用户planID统一404 PORTFOLIO_NOT_FOUND；403 context身份权限拒绝；409旧版本/快照/context/scope/事实或证据到期；422未知字段/路径body不符/风险不合格/金额精度/超配/完整P/U失败；503正式source未接。GET失效不返回正常可用方案。模型偏好错误沿用PREFERENCE_OUTSIDE_POLICY、PREFERENCE_NOT_APPLICABLE_R1、AMOUNT_PRECISION_PREFERENCE_UNREPRESENTABLE等明确码。候选业务状态no_theme_match/no_category_match/risk_mismatch/unavailable沿CandidateGroup，不统一报500。

## 可复用组件

`frontend/src/components/Wealth/PortfolioAllocation.tsx`，类型在`types/wealthPortfolio.ts`，独立API适配在`services/wealthPortfolio.ts`，不依赖旧wealth.ts。

```ts
interface PortfolioAllocationProps {
  plan: PortfolioPlan | null;
  snapshot?: PortfolioSnapshot | null;
  modelView?: PortfolioModelView | null;
  loading?: boolean; error?: string | null; readOnly?: boolean;
  controlHint?: string; contextId: string;
  onAdjust: (selection: PortfolioSelection) => Promise<PortfolioPlan>;
  onPreferenceChange: (selection: PortfolioSelection) => Promise<PortfolioPlan>;
  onAccepted?: (plan: PortfolioPlan) => void;
}
```

三板块显示当前/本版本初始解/已接受目标/允许区间；总资产与可支付现金、应收、冻结分列。未知估值不作0。草稿局部联动预览，明确应用后才提交；成功替换已接受版本，失败留原值和可修正草稿。在途禁重复提交；context变化清除草稿/选择，忽略旧context迟到响应。readOnly禁用回调操作，服务端仍检查控制权。R1/全活钱比例不适用，不显示NaN。

## 独立运行、构建与检查

复用现有Python依赖及React/Vite，不装新框架。以下从仓库根目录运行，使用已有Python环境（Windows本地需包含.deps路径）：

```powershell
$env:PYTHONPATH="$((Get-Location).Path);$((Resolve-Path .deps).Path)"
python -B -m scripts.wealth_portfolio_dev
```

入口监听127.0.0.1:8051，启动时生成仅此进程使用的随机JWT密钥，不打印/保存，不读取或改.env。无需生产数据库。不要把bootstrap/冲突工具挂到公共应用。另开终端：

```powershell
cd frontend
node node_modules/vite/bin/vite.js --config vite.portfolio.config.ts
```

打开 `http://127.0.0.1:3021/portfolio-dev.html`。可切换R1/R2/R4/F18/缺估值/source缺失/版本冲突。简单按钮验证同板块100分、R2选R4零权重拒绝。F18严格显示T10000元、可支付7000、应收500、冻结1000。终端Ctrl+C关闭两进程；隐藏启动的PID存于本地忽略证据目录，停前核对命令行，不停止已有3010/8010服务。

正常环境执行`npm run build`，独立页额外`node node_modules/vite/bin/vite.js build --config vite.portfolio.config.ts`。本机npm不在PATH，实际运行build脚本同等的`node node_modules/typescript/bin/tsc`＋`node node_modules/vite/bin/vite.js build`，两者实测通过；不冒称npm命令已经执行成功。原公共构建保留既有500kB体积警告。

离线证据入口：`python -B -m scripts.wealth_portfolio_lifecycle_check --fixture`，生成忽略目录中的lifecycle_evidence.json。未显式指定fixture则拒绝启动。

后端复现：

```powershell
python -B -m pytest tests/unit/test_wealth_portfolio_lifecycle.py tests/integration/test_wealth_portfolio_lifecycle_api.py -q -p no:cacheprovider
```

使用已有Playwright和Edge的真实本地HTTP交互（不安装浏览器）：

```powershell
$env:PORTFOLIO_PLAYWRIGHT_MODULE='<已有playwright目录>/index.mjs'
$env:PORTFOLIO_BROWSER_EXE='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
node scripts/wealth_portfolio_browser_check.mjs
```

截图、响应摘要、保护哈希和运行日志在`.deps/wealth_portfolio_lifecycle/`，不跟踪真实缓存或身份密钥。此验收不覆盖公共人工接管容器/联合交易、PostgreSQL或金融样本外效果。

独立验收、解释与历史评价边界见[独立验收说明](portfolio_acceptance.md)。
