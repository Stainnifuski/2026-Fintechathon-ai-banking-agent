> 理财本地集中收尾：13,578个普通真实基金＋既有模拟银行产品；真实货币永久退出。尚待人工验收，不发布main。

# AI Banking Agent

面向银行业务的 AI Agent 比赛演示项目：通过自然语言理解意图、规划任务，在风险分级和用户授权后调用模拟银行，展示可核对的执行结果。后端 FastAPI，前端 React/TypeScript，数据库支持 SQLite 与 PostgreSQL。

> 账户、资金、费用与交易均为比赛模拟；真实公开资料分别标源。理财使用统一高风险TOTP／短信验证，默认短信为本地模拟发送，不连接运营商或真实银行。

## 场景与当前能力

| 场景 | 已实现 | 当前边界 |
|---|---|---|
| NOVA前端工作台 | 登录后统一侧栏、财务概览、业务分区、北京时间时钟、“问NOVA”对话面板及验证器绑定入口 | 统一助手已接通时间、本人账户、账单、日历及店铺只读工具与页面上下文；交易仍在各工作台完成 |
| 智能转账 | 姓名/手机号转账、备注、多轮修改、定时转账、AA收款及原号恢复 | 聊天处理即时人民币转账；定时/AA走表单；可配置外部模型 |
| 账单分析 | 月度/年度报告、分类趋势、异常提示、流水下钻及只读问答 | 默认离线，可配置模型理解 |
| 理财操作 | 独立普通/类别推荐、整体配置与整数调整、跨批多选和详情比较、整份确认/MFA、申赎撤单、双表及收益日历；按代码NAV进入估值 | 13,578普通真实基金静态交集，实时资格另核；真实货币永久退出；不接新LLM/NLP，不宣称金融效果或人工验收通过 |
| 订阅管理 | 已有订阅记录及相关操作 | NOVA支持本人订阅查询及预计月费汇总；取消/模拟扣费在工作台完成，不等同完成全部自动识别能力 |
| 卡片管理 | 卡片查询、申请虚拟卡、设置交易限额（黄色确认）；调整信用额度、解锁、挂失（红色：确认＋TOTP或短信验证码） | NOVA支持本人卡片状态/限额查询；申请、修改与验证仍由网页可信面板完成 |
| 日历 | 月视图、时间点／时间段事件、ICS 上传导入、全天与重复日程、用户隔离和事件删除；NOVA日期/活动查询 | 本地日历，不含提醒推送或外部日历同步 |
| 花店／蛋糕店场景联动 | 第三方服务页、服务端商品与订单、独立商户工作台、消费流水、银行确认/TOTP、整单原路退款及订单对账；NOVA商品/报价及本人订单查询 | 模拟花店与蛋糕店 MVP；不含库存、配送、部分退款或真实商户网关 |
| 其他跨场景联动 | 后续范围 | 不声明已完整交付 |

比赛要求至少覆盖3个核心场景，并重视任务完成、安全和交互体验。当前功能入口、限制和证据按上表及下列文档核对；网页演示不自动等同已完成原生APP或IM渠道验收。

## 01／02本地集成：真实基金＋比赛模拟交易

本地静态质量交集13,578个普通真实基金（债券4,093、混合8,444、股票1,041）及19个既有模拟银行定义。真实货币基金永久退出，专用收益/固定1元功能及可用包条目删除；不保留其交易兼容。项目风险分级独立发行人，可靠公开更高评级或冲突拒绝，运行资格/报价/窗口仍须当前核验，不承诺全部始终可买。

本人问卷→独立普通/类别推荐→换批多选/可选比较→整体配置与整数百分比→差额→整份本人确认及统一高风险MFA→订单/持仓、申赎撤单和收益日历。动态按代码NAV进入估值和新Plan；参考价与订单日最终价分开，未公布等待，不改现金/份额/成本/历史成交。模拟快赎保留原额度和窗口。主题、高股息、A/C、客服领取交还、旧研究/对话工具已范围调整取消；新LLM/NLP和金融效果研究不在本轮。

当前状态、完整DAG及J01—J23证据见[工程交接](docs/wealth/portfolio_handoff.md)、[联合验收](docs/wealth/wealth_joint_acceptance.md)。本轮只本地验证，人工未验，不宣称封板或main已发布。

## 当前前端与入口

当前采用队友已合入main的 **NOVA AI BANKING** 前端。登录后通过左侧“概览／转账与收款／账单洞察／卡片／订阅／理财／生活服务”切换工作台；顶部“问NOVA”打开右侧银行与生活服务对话面板，验证器图标用于本人绑定。登录页仍显示“AI模拟银行”，不能仅凭登录页标题判断版本。

NOVA 已接通只读 Agent Loop：模型选择工具，服务端校验参数并注入本人身份，执行查询后回传工具结果，由模型生成回答。当前覆盖现实时间、账户概览、月度/年度账单和流水、本人卡片/订阅、本人日历，以及模拟鲜花店/蛋糕店商品、报价和本人订单付款状态。使用方式与当前边界见[NOVA使用指南](docs/setup/nova_usage.md)。

账单、卡片、订阅、日历和店铺页面会传递已应用筛选或选中对象，可问“解释当前账单”“这一天有什么规划”“这个商品两件多少钱”“这个订单付款了吗”“这张卡的限额是多少”“这个订阅什么时候扣费”。桌面端对话面板与业务页面并排显示，窄屏采用上下分区；回答支持Markdown标题、列表和表格。收起保留聊天，刷新或退出登录清空。无模型时明确标记有限离线规则，复杂表达和多轮查询需要模型。

转账执行、卡片变更、订阅取消/扣费和理财操作仍在各业务工作台完成，NOVA没有资金执行、确认或授权工具，也不代表跨场景总DAG已完成。日历是本人保存数据，账户及商店交易是模拟数据；店铺查询不下单、扣款或退款。理财保留唯一当前方案、产品走势图及统一确认验证入口。

本轮包含后端工具和前端交互，标准Docker部署更新后执行 `docker compose up -d --build backend frontend`；仅修改前端时可单独重建frontend。源码开发重启后端并在frontend运行 `npm run dev`。已有独立3010／8010演示环境使用单独构建和数据库，端口不是版本号；不要混用账户数据，也不要删除数据库卷来更新页面。详细步骤见[运行指南](运行指南.md)。


## 快速启动

已有 `.env` 时保留原文件；首次运行复制 `.env.example`，设置独立的 `JWT_SECRET` 与 `INTERNAL_BANK_TOKEN`，不要提交密钥。无模型密钥时使用有限离线规则；默认 DeepSeek 服务只需填写一次 `LLM_API_KEY`，NOVA、转账、账单与理财共用。需要全局离线时设置 `LLM_MODE=offline`，详见[统一模型配置](docs/setup/model_configuration.md)。

```bash
cp .env.example .env
docker compose up --build
```

前端：<http://localhost:3010> <http://localhost:3000>；健康检查：<http://localhost:8000/health>；API：<http://localhost:8000/docs>。本地SQLite、Windows启动及故障排查见[运行指南](运行指南.md)。当前无需Redis。

演示用户：张三 `13800000001`，密码 `password123`；仅用于虚构演示。升级已有数据库先备份并遵循[理财迁移交接](docs/wealth/handoff.md)，不要删除数据卷或混跑旧扣款代码。

**理财授权配置**：本人确认后选择验证器TOTP或短信验证码；沿用公共 `MFA_SMS_ENABLED`、`SMS_PROVIDER` 等配置。旧 `WEALTH_DEMO_VERIFICATION_ENABLED` 已移除，不再生效。默认短信 `mock` 为模拟发送，通过本人模拟收件箱读取验证码，仍必须校验。升级前备份数据库，启动自动迁移验证方法约束，保留旧证据和已提交交易。

离线回归初始化合成60份额：`docker compose exec backend python -m app.services.bank.wealth_data_cli init --user-id U001`，在理财“数据集、产品研究与宏观背景”选择新资金上下文。原legacy账本和旧订单保留。模型配置与独立日更命令见[数据说明](docs/wealth/data_sources.md)。

## 体验与风险授权

- NOVA：打开顶部“问NOVA”，查询“可用资金有多少”“请帮我查看今年的账单情况”“明天有什么规划”“鲜花店100元以内有什么花”。业务页选中对象后可追问，详细示例见[NOVA使用指南](docs/setup/nova_usage.md)。
- 转账：输入“给手机号13800000002转300元，备注房租”，核对收款人、金额和风险等级，再操作任务卡。
- 账单：进入账单分析查看报告、异常和流水，使用独立只读问答入口。
- 卡片：在“卡片管理”选择操作→核对银行返回的变更明细→确认；信用卡调额、解锁、挂失还需输入验证器动态码（先在右上角绑定验证器）。演示信用卡初始为锁定，可先解锁再调额。
- 理财：查看产品→本人评估→生成订单或配置方案→确认披露→选择TOTP或短信验证码并验证提交→在持仓表查看可赎、冻结及在途状态。刷新、退出重进或新对话从空白开始；银行已受理订单保留。通过自然语言修改投资金额并替换当前未提交方案；点击“一键申购全部”，一次本人确认及整份方案验证码验证，无旧方案列表和逐笔采用表单。详细操作见[理财演示指南](docs/wealth/demo.md)。
- 花店／蛋糕店：点击左侧“生活服务”→选择花店或蛋糕店→选择商品并提交订单→返回银行接受收款申请→再次核对并确认付款→回店铺查看“交易完成”。提交订单本身不授权扣款。

产品风险R1—R5、用户风险承受能力和操作权限等级是三个不同概念。绿色查询自动执行，黄色需要本人明确确认，红色需要本人确认及强验证。理财申购、普通赎回、快赎均复用三号的 `verify_second_factor()`，TOTP／短信二选一；整份方案一次验证为每笔订单写入绑定证据，改金额或版本后旧短信挑战不能用于新方案。

短信通过 `POST /api/v1/auth/sms/send` 获取，绑定本人及具体授权或整份方案授权集合；默认5分钟有效、单次使用、错误5次锁定。`SMS_PROVIDER=mock` 不真实发送，演示通过 `GET /api/v1/auth/sms/dev-inbox` 查看本人模拟短信。旧点击授权入口已删除，未提交的旧点击证据不能继续成交；已受理订单仍按原规则结算和幂等查询。所有交易为模拟，不声称生产级认证或真实银行接入。详细字段、迁移及限制见[统一理财授权说明](docs/wealth/simulated_authorization.md)。

## 文档导航

| 需要了解 | 文档 |
|---|---|
| 启动、部署、环境 | [运行指南](运行指南.md)、[模型配置](docs/setup/model_configuration.md) |
| NOVA使用与接口 | [使用指南](docs/setup/nova_usage.md)、[工具与页面上下文](docs/contracts/NOVA页面上下文与只读工具_API_v0.1.md)、[日历查询](docs/contracts/NOVA日历查询_API_v0.1.md)、[店铺查询](docs/contracts/NOVA店铺查询_API_v0.1.md) |
| NOVA验证证据 | [账户与账单](docs/validation/nova_read_context_20261010.md)、[日历](docs/validation/nova_calendar_20261010.md)、[店铺](docs/validation/nova_shops_20261010.md)、[年度输出预算](docs/validation/nova_annual_output_20261010.md)、[Markdown](docs/validation/nova_markdown_20261010.md)、[并排面板](docs/validation/nova_docked_panel_20261010.md) |
| 定时转账与订阅接入 | [现有功能保护](docs/contracts/定时转账与订阅_Agent接入.md)、[本次验证](docs/validation/scheduled_subscription_guards_20261005.md) |
| 转账对话与接口 | [对话契约](docs/contracts/转账任务状态机_API_v0.1.md)、[AI使用与交付](docs/即时转账AI_使用与交付.md) |
| 4号模拟银行 | [手机号/备注边界](docs/bank/手机号转账与风险边界.md)、[银行接口交接](docs/bank/接口与数据库交接_v0.5.md) |
| 账单分析 | [接口与口径](docs/contracts/账单分析_API_v1.md) |
| 卡片管理 | [接口与权限](docs/contracts/卡片管理_API_v1.md)、[验收记录](docs/validation/cards_20261003.md) |
| 店铺场景联动 | [花店购买与付款联动契约](docs/contracts/花店购买与付款联动_API_v0.1.md)、[蛋糕店购买与付款联动契约](docs/contracts/蛋糕店购买与付款联动_API_v0.1.md)、[运行指南](运行指南.md#花店场景联动演示) |
| 理财数据与研究 | [数据与日更](docs/wealth/data_sources.md)、[分析口径](docs/wealth/analytics.md)、[知识库](docs/wealth/knowledge.md) |
| v1.2验收与比赛 | [当前混合池验收](docs/validation/wealth_v1_2_supplement_20261002.md)、[清理与干净复现](docs/validation/development_cleanup_20261002.md)、[比赛材料与差距](docs/competition/wealth_v1_2.md) |
| 理财开发与联调 | [接口契约](docs/wealth/contract.md)、[设计决策](docs/wealth/decisions.md)、[数据库交接](docs/wealth/handoff.md) |
| 理财对话与账务增量（开发中） | [任务/时间/测试轮次契约](docs/wealth/dialogue_tasks_and_time.md)、[当前进度](docs/wealth/dialogue_ledger_progress.md)、[E01–E40验收](docs/validation/dialogue_ledger_20261002.md) |
| 理财工作台与临时状态 | [当前交互及清理](docs/wealth/workspace_simplification.md)、[C01–C08验收](docs/validation/wealth_workspace_20261004.md) |
| 理财语义、推荐与子DAG | [子任务接口](docs/wealth/semantic_subtask_contract.md)、[推荐provider](docs/wealth/recommendation_provider.md)、[推荐与详情展示](docs/wealth/recommendation_display.md)、[固定24项验收](docs/validation/wealth_semantic_dag_20261003.md) |
| 固定行情与本地基线升级 | [价格和日历](docs/wealth/fixed_prices_and_calendar.md)、[备份与归属清理](docs/wealth/baseline_upgrade.md)、[本轮进度](docs/wealth/semantic_dag_progress.md) |
| 安全与授权限制 | [理财安全自评](docs/wealth/security.md)、[统一验证及历史证据](docs/wealth/simulated_authorization.md) |
| 验证证据 | [理财原始验收](docs/validation/wealth_20261001.md)、[统一授权接入验收](docs/validation/wealth_unified_authorization_20261005.md)、[历史点击授权验收](docs/validation/wealth_authorization_demo_20261001.md)、[账单验收](docs/validation/bills_20260930.md) |
| 开发历史 | [实施进度](implementation_progress.md)、[历史README](docs/archive/README_before_authorization_20261001.md) |

## 测试与比赛交付

```bash
python -m pytest tests -q
cd frontend
npm ci
npm run build
```

PostgreSQL并发验收需单独配置测试库，未配置时相应用例会跳过；具体环境、日期、通过/跳过数量以每份验证记录为准。历史报告不代表当前提交已在全部环境重新验证。真实模型测试不属于默认离线测试。

提交材料应包含作品简介、架构与算法说明、安全设计/自评、部署说明、测试用例和答辩材料；视频按最终赛事口径准备。安全自评必须披露默认短信为模拟发送、全部交易为模拟，不能把本地验证码验证写成生产认证或声称已获得赛事豁免。功能与数据来源、开源引用在页面、仓库及比赛材料中保持一致；v1.2数据、分析、知识与模型升级的实际验收及限制见下级文档。

### 全量基金与混合预加载池

目录搜索不限预加载数量；默认最新混合快照逐项标识真实基金或模拟银行理财。可调整清单、官方规则与模拟调整、按需开关、初始化及日更命令见[全量目录与预加载指南](docs/wealth/preload_and_directory.md)。真实基金缺审核规则仅研究，全部资金和成交仍为模拟，默认短信通道亦为模拟。

## 文档与AI协作规范

1. README只维护项目定位、当前能力、快速入口、安全边界和文档导航；配置细节、接口字段、数据库设计及逐次测试日志放入对应下级文档，禁止不断追加重复“本轮完成”段落。
2. 功能变更同时更新对应契约、运行说明及有日期的验证记录。区分已实现、模拟替身、计划中和未验证，不复制旧测试数字作为当前结论。
3. 开始修改前阅读README及相关模块文档；早期 `.cursorrules` 中的规划/示例与现有实现有差异时，核对代码和当前契约，不为匹配旧规划重写项目。
4. 保留他人改动和现有数据。推送、创建PR及最终合并前重新获取最新 `origin/main`，比较并整合变化，复验受影响流程；不得强推覆盖队友提交。远端发布按用户授权执行。
5. 不提交密钥、个人数据、运行数据库、备份或默认抓取的完整真实数据集；保留来源/许可证说明，禁止复刻第三方银行界面和声称第三方背书。
6. 更改3号公共授权应同步公共契约；理财已复用3号统一第二因素，接入与模拟短信边界见专门说明，也不让模型调用授权签发工具。
