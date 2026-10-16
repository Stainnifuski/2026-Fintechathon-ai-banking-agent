# 01 独立前端：当前交付（第11步）

2026-10-10：已读取的资产与评估/产品资格、写控制分别说明；普通界面显示中文状态和主题，原始状态、多原因、完整快照ID在可展开详情保留。页面未改变来源或业务门禁。完整M01—M14及比赛入口新会话复核以本轮回传和唯一handoff当前交付状态为准；隔离通过不能证明真实正向或金融评价。

同一`PortfolioAllocation`及`portfolioAdapter`提供两套**启动时固定**来源。只计算整体建议和修订，不调用prepare/confirm/verify/execute，不扣款、不赎回；没有注册或修改02正式页面。隔离功能成功不证明可信推荐或金融效果。

## 启动

仓库根，使用已有Python及项目依赖（使用各机器已有Python，见正式交接）：

```powershell
$env:PYTHONPATH="$PWD;$PWD/.deps"
$env:PYTHONDONTWRITEBYTECODE='1'
$pythonExe='python' # 激活已有项目虚拟环境或使用PATH中的Python
$env:PORTFOLIO_BANK_BASE_URL='http://127.0.0.1:8010'
$env:PORTFOLIO_DATA_VERSION='38005c79535cb83a2d5be663d8a7a1812ba21946fe8f9fa2ab21ca80aea338a9'
$env:PORTFOLIO_DAILY_VERSION='e5ce972368c9b735a5fe9ddd64fa0fd81a9194c419cb89ceda255ead71f460d0'
& $pythonExe -B -m scripts.wealth_portfolio_bank_dev
```

另开终端，在frontend目录：

```powershell
node node_modules/vite/bin/vite.js --config vite.portfolio.config.ts
```

正常入口：`http://127.0.0.1:3022/portfolio-dev.html` →8052→既有8010。页面输入原演示银行登录信息，调用原`POST /api/v1/auth/login`；Bearer仅React内存，退出/重载清除，没有localStorage、URL、开发身份签发。Vite仅代理固定本地认证及独立后端，不开放任意host。没有fixture开关或静默回退。

隔离后端与前端各开终端：

```powershell
# 仓库根，使用上述PYTHONPATH和已有Python
& $pythonExe -B -m scripts.wealth_portfolio_dev
# frontend目录
node node_modules/vite/bin/vite.js --config vite.portfolio.config.ts --mode fixture
```

隔离URL：`http://127.0.0.1:3021/portfolio-dev.html` →8051。醒目标记“隔离功能测试，非真实推荐”；独立身份、store、资料及固定时钟，不读银行业务库。R1/R2/R4/F18/仅C/单份额/仅持有/缺估值/缺来源可选；F01—F24及原固定产品未变。

终端Ctrl+C关闭；隐藏启动仅按记录PID与命令行确认后停止自己的进程。不能结束原3010/8010。

## 控件与接线

`frontend/src/dev/portfolioMain.tsx`负责内存认证、来源标记、当前context及事实/候选/Plan状态。加载不自动创建Plan。刷新事实清除旧Plan、选择和cursor；context切换使用generation丢弃迟到响应。当前方案读取失败标失效并禁止编辑，原版本仍可核对。人工只读、服务端控制未知/禁止均禁写，合法读取保留。

`PortfolioAllocation`已有props保持；新增可选`assetTotals`为独立后端同快照汇总。T从AssetSnapshot/Plan返回，不由前端汇总账户。活钱/稳健/进取显示当前、基准、已接受目标、原允许区间；无Plan不伪造目标。滑块以非活钱金额为分母，R1不渲染。`onAdjust`/`onPreferenceChange`传`PortfolioSelection`，成功才触发`onAccepted`；失败保留接受版本和可改草稿。产品百分数移动小数点转十进制字符串；分金额用BigInt校验/汇总，未整分不隐式舍入；原始非终止比例按服务端Decimal精度往返。

普通、六类别、登记主题与显式AND/OR筛选走原`PortfolioLifecycle.recommend`，未知键422，不回退general。每批至多3家族；session保留跨批选择，可超过5项。具体份额代码、风险、可售、来源日期、费用和准确业绩名称同时显示；当前费用/行情未知保持未知，不造收费或总回报。证据另行展开。无匹配类别/主题、全部超风险、资料或来源缺失分别显示。

候选→select/switch→create→get；创建携带具体selected及candidate_binding。切换只提供合法A/C，不把单份额或两个家族变成A/C；失败保留旧具体份额。已接受方案的逐项权重携带portfolio_id/expected_version/snapshot_id/context_id/具体版本，服务端原资格及P/U完整校验，非法一项不删掉再成功。

### 独立开发薄包装（不是00新公共API）

`scripts/wealth_portfolio_dev_routes.py::install(app)`仅由两个独立应用挂载：

- GET `/dev/context`：受保护本人读取source→builder，返回context、snapshot_id、原汇总、实际读/编辑判定；必要写控制未知为false及真实reason。前端与GET snapshot核对相同snapshot_id。
- POST `/dev/candidates/select`：`{context_id, action:{session_id,expected_version,items:ShareReference[]}}`。
- POST `/dev/candidates/switch`：`{context_id, action:{session_id,expected_version,family_id,target:ShareReference}}`。

包装核对原access_binding和候选context，复用CandidateService.select/switch_share；主体、版本、资格仍在服务器。拒绝额外字段。未新增B公共/actions或授权机制。

隔离专用bootstrap/reset/change/conflict仅在8051；8052不存在这些路由。测试变动只作用隔离对象。00公共DTO、中央依赖默认拒绝、07四项适配及既有算法/参数不变。

## 构建与验证

现有环境不安装依赖。frontend目录实际使用package build的等价命令：

```powershell
node node_modules/typescript/bin/tsc --noEmit
node node_modules/vite/bin/vite.js build
node node_modules/vite/bin/vite.js build --config vite.portfolio.config.ts
node node_modules/vite/bin/vite.js build --config vite.portfolio.config.ts --mode fixture
```

浏览器脚本`node scripts/wealth_portfolio_frontend_check.mjs`使用已有Playwright/Edge和上述真实本地HTTP。先比赛认证/读取/关闭，再隔离完整操作。非法手选/零或极小权重等以浏览器受控HTTP送入原A服务，不是纯点击；无响应mock。设`PORTFOLIO_EVIDENCE_DIR=.deps/wealth_portfolio_step11/final`保存本轮结果，其中matrix对应M01—M14。英文主题键保持原协议；日常中文下拉选择，展开“主题参数与组合”可输入已登记多主题及AND/OR，未知键仍拒绝。

前端类型、主/独立构建、后端受影响集合、控制台运行错误/预期HTTP失败、构建警告分别记录。真实当前推荐/Plan仍受画像/正文/写控制缺口阻塞；含分红总回报/固定组合/OOS保持09状态。PostgreSQL、02交易及联合验收未覆盖。

可移植检查：PORTFOLIO_PLAYWRIGHT_MODULE与PORTFOLIO_BROWSER_EXE由本机现有环境设置，脚本不内置开发者路径。旧scripts/wealth_portfolio_browser_check.mjs仅是早期生命周期UI检查，不纳入本次wealth交付；当前完整入口使用wealth_portfolio_frontend_check.mjs。
