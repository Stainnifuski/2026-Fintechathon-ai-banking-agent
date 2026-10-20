# AI Banking Agent

面向银行业务的 AI Agent 比赛演示项目：通过自然语言理解意图、规划任务，在风险分级和用户授权后调用模拟银行，展示可核对的执行结果。后端 FastAPI，前端 React/TypeScript，数据库支持 SQLite 与 PostgreSQL。

> 账户、资金、费用与交易均为比赛模拟；真实公开资料分别标源。理财使用统一高风险TOTP／短信验证，默认短信为本地模拟发送，不连接运营商或真实银行。

## 场景与当前能力

| 场景 | 已实现 | 当前边界 |
|---|---|---|
| NOVA前端工作台 | 登录后统一侧栏、财务概览、业务分区、北京时间时钟、“问NOVA”对话面板及验证器绑定入口 | 统一助手已接通只读查询与页面上下文；转账可在聊天中生成多笔待确认计划，实际确认和执行仍在转账工作台完成 |
| 智能转账 | 姓名/手机号转账、备注、多轮修改、定时转账、AA收款及原号恢复 | 聊天处理即时人民币转账；定时/AA走表单；可配置外部模型 |
| 账单分析 | 月度/年度报告、分类趋势、变化追溯、订阅核对、现金流情景；商户标识、退款／互转关联、用户标签和持久化周期收支 | 默认离线，可配置模型理解；新增分类纠错、预算预警和异常核对，辅助数据不改变原始账本 |
| 理财操作 | 理财规划（整体配置与整数调整）、目标产品推荐（类别/名称/代码匹配，逐产品指定金额）、跨批多选和详情比较、确认/MFA、模拟即时申赎、持仓及收益日历；按代码NAV进入估值 | 806只普通真实基金固定池，实时资格另核；目标推荐不调用资产配置模型；统一自然语言理财执行接线待完成 |
| 订阅管理 | 已有订阅记录及相关操作 | NOVA支持本人订阅查询及预计月费汇总；取消/模拟扣费在工作台完成，不等同完成全部自动识别能力 |
| 卡片管理 | 卡片查询、申请虚拟卡、设置交易限额（黄色确认）；调整信用额度、解锁、挂失（红色：确认＋TOTP或短信验证码） | NOVA支持本人卡片状态/限额查询；申请、修改与验证仍由网页可信面板完成 |
| 日历 | 月视图、时间点／时间段事件、ICS 上传导入、全天与重复日程、用户隔离和事件删除；NOVA日期/活动查询 | 本地日历，不含提醒推送或外部日历同步 |
| 花店／蛋糕店场景联动 | 第三方服务页、服务端商品与订单、独立商户工作台、消费流水、银行确认/TOTP、整单原路退款及订单对账；NOVA商品/报价及本人订单查询 | 模拟花店与蛋糕店 MVP；不含库存、配送、部分退款或真实商户网关 |
| 其他跨场景联动 | 后续范围 | 不声明已完整交付 |

比赛要求至少覆盖3个核心场景，并重视任务完成、安全和交互体验。当前功能入口、限制和证据按上表及下列文档核对；网页演示不自动等同已完成原生APP或IM渠道验收。

## 理财场景与数据口径

固定资料包含806只普通真实基金（债券242、混合501、权益63）及19个模拟银行定义，从原900只固定池按2026-10-17实测保留有数值夏普的806只；不按夏普高低排名。清单与选择方法见`config/wealth/compact_pool.json`。不含真实货币基金。项目风险分级独立于发行人评级，可靠公开更高评级或分类冲突会拒绝申购；资格、报价、窗口、额度和现金仍动态核验，不代表全部产品始终可买。活钱板块保留账户现金及符合条件的R1模拟快赎，本金可能亏损。

本人问卷后分两条流程：理财规划使用换批多选、整体配置与整数百分比，经差额、整份本人确认及统一高风险MFA执行；目标产品推荐按类别或产品名称/代码匹配，本人逐只指定金额，自动使用绑定借记账户，经全部已选统一确认及一次MFA执行，不进入资产配置。持仓与收益日历共用。新交易按最近已公布有效NAV，经本人确认及统一MFA后即时扣款/增加持仓或赎回到账；风险、持有期、窗口、额度和幂等保护保留，成功不可撤。订单查询和撤单页面已移除，后台成交记录与审计保留；旧未计价申购/赎回已取消并释放占用；已计价赎回应收一次到账，不再保留进行中等待订单。终态成交、幂等和审计记录保留。动态行情仅更新估值，不改现金、份额、成本或历史成交。当前不提供主题/高股息筛选、A/C切换或客服领取交还界面。

接口、工具边界及完整业务DAG见[NLP与执行Agent交接](docs/wealth/nlp_agent_handoff_20261015.md)；运行口径和验收证据见[工程交接](docs/wealth/portfolio_handoff.md)、[联合验收](docs/wealth/wealth_joint_acceptance.md)。

### 固定资料下载与一键安装

806只版本的完整固定资料随源码提供：[data/wealth/wealth-compact-806-v1.zip](data/wealth/wealth-compact-806-v1.zip)，约1MB。克隆或下载源码ZIP即可取得，无需Git LFS或另下旧Release。旧全池Release不适用于本版本。

Docker用户直接运行`start_bank.cmd`：构建当前镜像、在持久目录备份原数据库、校验并安装匹配资料、增量升级及绑定已有借记账户，再等待服务就绪。不复制测试库、不重置余额、不代填风险评估或MFA。

原生Python开发可先安装项目依赖，再运行`install_wealth_offline.cmd`；安装器只处理固定资料，不修改业务数据库或`.env`。SHA256、数据版本和公开配置必须匹配。旧活动快照移入本地升级回滚备份，不再参与推荐；备份不随源码分发。

旧PostgreSQL升级步骤、保护范围、例外和恢复方式见[固定产品与旧库升级](docs/setup/wealth_compact_upgrade.md)。

## 当前前端与入口

当前采用队友已合入main的 **NOVA AI BANKING** 前端。登录后通过左侧“概览／转账与收款／账单洞察／卡片／订阅／理财／生活服务”切换工作台；顶部“问NOVA”打开右侧银行与生活服务对话面板，验证器图标用于本人绑定。登录页采用白底“NOVA／你的银行智能体”与彩色模块装饰，保留原登录／注册流程及折叠演示账号帮助。

NOVA 已接通分层 Agent Loop：模型先输出严格结构化路由；单一目标直接进入 ReAct，多个独立目标（含同一业务的多个操作对象）或有条件依赖的请求同时生成 Goal Plan，再按依赖进入 ReAct。单笔转账路由后仅暴露受控转账工具并要求模型调用，避免在无关工具间循环；重复同参数调用会提前熔断。服务端校验参数并注入本人身份，执行后回传工具结果，由模型继续规划或生成回答。当前覆盖现实时间、账户概览、月度/年度账单和流水、本人卡片/订阅、本人日历，以及模拟鲜花店/蛋糕店商品、报价和本人订单付款状态。转账不再经过规则式意图路由；模型选择受控转账工具后，专用转账子模型读取原始消息及全部未终结任务摘要，自主判断本轮需新建或调整的任务数量，再由 Skill 与 Task Manager 校验和落库。离线模式仅保留有限规则，不提供上述模型编排。使用方式与当前边界见[NOVA使用指南](docs/setup/nova_usage.md)。

账单、卡片、订阅、日历和店铺页面会传递已应用筛选或选中对象，可问“解释当前账单”“这一天有什么规划”“这个商品两件多少钱”“这个订单付款了吗”“这张卡的限额是多少”“这个订阅什么时候扣费”。桌面端对话面板与业务页面并排显示，窄屏采用上下分区；回答支持Markdown标题、列表和表格。收起保留聊天，刷新或退出登录清空。无模型时明确标记有限离线规则，复杂表达和多轮查询需要模型。

转账确认及执行、卡片变更、订阅取消/扣费和理财操作仍在各业务工作台完成，NOVA没有资金执行、确认或授权工具，也不代表跨场景总DAG已完成。日历是本人保存数据，账户及商店交易是模拟数据；店铺查询不下单、扣款或退款。理财保留唯一当前方案、产品走势图及统一确认验证入口。

正式前端只有一套：`frontend/index.html → src/main.tsx → App.tsx`，理财与其他场景共用。旧Docker镜像或本机`dist`不会因GitHub合并而更新；它们不是仓库中的第二套前端。历史01独立验收页只供隔离测试，不是用户入口。请按[统一启动步骤](运行指南.md#准备与启动)从同一份最新源码构建前后端。


## 快速启动

已有 `.env` 时保留原文件；首次运行复制 `.env.example`，设置独立的 `JWT_SECRET` 与 `INTERNAL_BANK_TOKEN`，不要提交密钥。无模型密钥时使用有限离线规则；默认 DeepSeek 服务只需填写一次 `LLM_API_KEY`，NOVA、转账、账单与理财共用。需要全局离线时设置 `LLM_MODE=offline`，详见[统一模型配置](docs/setup/model_configuration.md)。

**唯一正式入口：[http://localhost:3000/](http://localhost:3000/)**。所有场景共用同一NOVA应用、登录身份和银行账本；前端通过同源`/api/v1`调用最新后端。健康检查为[3000/health](http://localhost:3000/health)，API文档为[3000/docs](http://localhost:3000/docs)。后端8000是Docker内部端口，不作为另一个浏览器入口。

首次准备：保留并核对自己的`.env`；固定资料已经随源码提供；Windows在**当前这份源码根目录**双击`start_bank.cmd`，或执行`powershell -NoProfile -File scripts/start_bank.ps1`。脚本校验资料与配置，重新构建同份源码的前后端，等待`portfolio_ready=true`后才报告成功。更新代码后再次执行即可；不要仅重启旧镜像。Linux/macOS执行`docker compose up -d --build --wait --wait-timeout 300 backend frontend`，已有本地专用账本覆盖文件时按[运行指南](运行指南.md)使用相同参数。

全新数据库由正常启动创建演示客户/银行账户；已有账户保留。理财由启动升级流程导入匹配资料并绑定本人已有借记账户；不新增另一份理财资金、不代填评估。演示密码获取位置：正常初始化代码`backend/app/services/bank/seed_service.py`；已有自定义账本使用原有账号，不能假定它与全新库的演示ID相同。

旧3010/3014前端、旧镜像和仅挂载旧dist的启动方式不再作为正式入口。Vite开发也固定3000并开启`strictPort`，遇占用直接报错，不悄悄跳端口；与Docker互斥运行。`/api/v1`及银行内部接口保留，以保护转账、账单、卡片、理财与Agent接入；这里移除的是重复/过期服务入口，不批量删除共用API。

数据不会因重建镜像自动合并。默认Docker使用项目PostgreSQL卷；本机已按用户确认完成独立PostgreSQL副本迁移，旧银行账户本金为准，理财事实与占款映射到本人原账户，不叠加研究账户初始化资金。3000已使用PostgreSQL；原PostgreSQL库及SQLite备份保留。本机私有配置和运行库不提交GitHub，也不复制到队友机器。旧数据库不通过删卷更新。迁移证据见[本机迁移核验](docs/validation/postgres_cutover_20261017.md)，准备及启动见[运行指南](运行指南.md)。


## 体验与风险授权

- NOVA：打开顶部“问NOVA”，查询“可用资金有多少”“请帮我查看今年的账单情况”，或说“给李四转100元”建立转账草稿并按提示补齐资料。业务页选中对象后可追问，详细示例见[NOVA使用指南](docs/setup/nova_usage.md)。
- 转账：输入“给手机号13800000002转300元，备注房租”，核对收款人、金额和风险等级，再操作任务卡。
- 账单：进入“账单洞察”查看报告、异常和流水；明细可编辑标签、查看原单／互转对应流水；展开“商户与周期收支管理”维护别名、归并商户、刷新历史关联并设置周期预测。独立问答及NOVA查询保持只读。
- 卡片：在“卡片管理”选择操作→核对银行返回的变更明细→确认；信用卡调额、解锁、挂失还需输入验证器动态码（先在右上角绑定验证器）。演示信用卡初始为锁定，可先解锁再调额。
- 理财：本人评估→理财规划（选品及整体配置，整份确认）或目标产品推荐（类别/名称匹配及逐只金额，全部已选统一确认）→核对披露及TOTP/短信验证→查看即时成交结果、持仓及收益。在持仓表操作普通赎回或合格快赎；已成交记录不会因刷新或改方案改变。当前使用结构化工作台；自然语言团队通过[动作接口与DAG](docs/wealth/nlp_agent_handoff_20261015.md)接线，不能把旧聊天接口当成最新版配置入口。
- 花店／蛋糕店：点击左侧“生活服务”→选择花店或蛋糕店→选择商品并提交订单→返回银行接受收款申请→再次核对并确认付款→回店铺查看“交易完成”。提交订单本身不授权扣款。

产品风险R1—R5、用户风险承受能力和操作权限等级是三个不同概念。绿色查询自动执行，黄色需要本人明确确认，红色需要本人确认及强验证。理财申购、普通赎回、快赎均复用三号的 `verify_second_factor()`，TOTP／短信二选一；整份方案一次验证为每笔订单写入绑定证据，改金额或版本后旧短信挑战不能用于新方案。

短信通过 `POST /api/v1/auth/sms/send` 获取，绑定本人及具体授权或整份方案授权集合；默认5分钟有效、单次使用、错误5次锁定。`SMS_PROVIDER=mock` 不真实发送，演示通过 `GET /api/v1/auth/sms/dev-inbox` 查看本人模拟短信。旧点击授权入口已删除，未提交的旧点击证据不能继续成交；成交结果按原操作号幂等查询，不再进入等待结算。所有交易为模拟，不声称生产级认证或真实银行接入。详细字段、迁移及限制见[统一理财授权说明](docs/wealth/simulated_authorization.md)。

## 文档导航

- [10月20日前端版本同步：紧凑NOVA、账单预览修复与登录页](docs/frontend/release_20261020.md)

- [账单增强：消费追溯、订阅联动、现金流与情景模拟](docs/contracts/账单洞察与现金流_API_v2.md)
- [账单分类、预算、异常核对及原功能优化契约](docs/contracts/账单决策与优化_API_v4.md)与[本次验收记录](docs/validation/bill_decisions_20261019.md)
- [五项账单数据基础契约](docs/contracts/账单数据基础_API_v3.md)与[本次自检记录](docs/validation/bill_foundations_20261019.md)
- [三项账单增强自检记录](docs/validation/bill_enhancements_20261014.md)

| 需要了解 | 文档 |
|---|---|
| 启动、部署、环境 | [运行指南](运行指南.md)、[模型配置](docs/setup/model_configuration.md) |
| NOVA使用与接口 | [使用指南](docs/setup/nova_usage.md)、[转账聊天任务](docs/contracts/NOVA转账任务_API_v0.1.md)、[工具与页面上下文](docs/contracts/NOVA页面上下文与只读工具_API_v0.1.md)、[日历查询](docs/contracts/NOVA日历查询_API_v0.1.md)、[店铺查询](docs/contracts/NOVA店铺查询_API_v0.1.md) |
| NOVA验证证据 | [分层规划与转账任务编排](docs/validation/nova_goal_planning_20261020.md)、[账户与账单](docs/validation/nova_read_context_20261010.md)、[日历](docs/validation/nova_calendar_20261010.md)、[店铺](docs/validation/nova_shops_20261010.md)、[年度输出预算](docs/validation/nova_annual_output_20261010.md)、[Markdown](docs/validation/nova_markdown_20261010.md)、[并排面板](docs/validation/nova_docked_panel_20261010.md) |
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
| 固定行情与本地基线升级 | [价格和日历](docs/wealth/fixed_prices_and_calendar.md)、[备份与归属清理](docs/wealth/baseline_upgrade.md)、[历史开发记录](docs/wealth/semantic_dag_progress.md) |
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
