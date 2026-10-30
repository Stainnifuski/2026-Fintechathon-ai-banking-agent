# 开发者自动化测试与证据索引

本文件是源代码包内的开发者测试导航，不是新增封板检查报告。评委逐步输入、点击与预期结果见[操作用例](test_cases.md)。以下代码及命令供开发者复现，不能把“存在用例”当作“当前已全部通过”。历史报告按各自日期、源码及环境解读。

## 环境与执行边界

后端安装 `backend/requirements.txt`，从仓库根目录使用项目Python环境运行pytest；多数单元／集成用例自行创建隔离SQLite。PostgreSQL专用用例须按各文件夹具配置独立测试库，未配置时跳过，绝不指向当前演示账本。默认模型协议测试使用MockTransport，无真实DeepSeek请求。真实模型联调需单独明确配置，不能用mock证明真实语义效果。

前端在 `frontend` 执行 `npm ci` 和 `npm run build`。Node需支持 `--experimental-strip-types` 才能直接运行仓库的TypeScript测试（本地验证使用Node 24）；生产镜像的构建由Dockerfile完成。

```bash
# 仓库根目录：相关知识库和任务路由离线回归
python -m pytest tests/unit/test_planning_knowledge.py tests/unit/test_nova_runtime.py -q
# 前端纯逻辑测试（从仓库根执行）
node --experimental-strip-types --test frontend/tests/*.test.ts
```

`python -m pytest tests -q`为扩大范围的命令，可能涉及额外依赖、专用数据库及历史兼容用例，不作为一条无需准备即可全通过的保证。本轮未另外执行全量封板验收。

## 用例、人工步骤与预期

人工体验涉及业务写入的用例应在隔离演示账户／测试库进行。下表是可复现步骤与预期，不替用户执行交易。

| 编号 | 步骤 | 预期／对应代码 |
|---|---|---|
| T01 登录与动效 | 错误密码登录，再用有效密码登录；观察球体及页面 | 失败恢复表单；成功球体约0.5秒移动，再约0.5秒淡入，淡入期间不可交互；`tests/browser/login_orb_transition.mjs`，全拦截API |
| T02 操作阶段 | 查看准备方案、确认、MFA及成交各阶段 | “待本人确认”“待验证码验证”“待提交”“已成交”区分，未知状态提示核对；`frontend/tests/requestFeedback.test.ts` |
| T03 安全拒绝 | 在隔离用例中模拟验证码错误、权限拒绝、会话过期 | 不把MFA错误误判为登录过期，不跳过授权，不展示内部技术码；`frontend/tests/sessionAuthenticationSecurity.test.ts`、`requestFeedback.test.ts` |
| T04 中断提示 | 在模拟网络中返回超时／500响应 | 提示结果尚未核实及核对原任务，不声称未执行；不改变请求编号和重试逻辑；`requestFeedback.test.ts`、`wealthFeedback.test.ts` |
| T05 跨场景示例 | 更多示例→账单与订阅一起看／日程与扣费一起看 | 发送示例原文给模型，查询本人资料；操作不因示例点击而获得授权。`tests/browser/nova_feedback_examples.mjs`只验证界面和请求，不等同真实模型端到端验收 |
| T06 规划知识问答 | 首次询问、追问原理、确认进入及问库外事实 | 简短概述准确归属理论和诺奖身份；确认后才创建规划；未覆盖如实说明；`tests/unit/test_planning_knowledge.py` |
| T07 产品检索与申购准备 | 类别／名称搜索、本人选品及逐只填金额 | 候选不等于已选；金额随产品身份恢复；准备后进入核对而非显示已成交；`tests/integration/test_nova_product_name_search.py`、`frontend/tests/wealthTargetFlow.test.ts` |
| T08 卡片状态刷新 | 修改未提交的日程、收款或银行卡表单，再刷新相同服务端资料 | 无关刷新不覆盖输入，真实字段更新仍应用；`tests/browser/card_refresh_regression.mjs`，全模拟API |
| T09 交易授权与幂等 | 复用银行授权相关隔离测试中的确认、验证码、同操作号重放 | 未授权不能执行，同操作号不重复记账；按 `tests/security/` 及 `tests/integration/` 中相应夹具执行，不从当前演示库扣款 |

## Agent能力与既有测试索引

原则：每项对外声明的能力都应有正常路径、拒绝／缺失信息路径、状态或副作用断言。写操作还应覆盖归属、版本、本人确认、MFA和幂等；模型语义、银行业务、界面与真实模型联调分开。下表列出已存在的代表性文件，并非逐工具完整覆盖认证；未逐项审计的边界、环境及真实模型表现仍待核对，本轮不因此执行额外全量验收。

| 能力 | 既有测试文件（仓库相对路径） | 主要验证层／限制 |
|---|---|---|
| 能力目录、路由、模型协议 | `tests/unit/test_nova_capabilities.py`、`test_nova_runtime.py`、`test_llm_service.py` | 单元与模拟模型协议；不代表真实DeepSeek准确率 |
| 多目标规划及顺序执行 | `tests/unit/test_nova_task_graph.py`、`tests/integration/test_nova_execution_queue.py` | 依赖、状态和执行队列；不保证任意复合自然语言均可办理 |
| 时间、账户、基础查询 | `tests/integration/test_nova_reads.py` | 工具与隔离数据读路径 |
| 联系人、转账、收款与收款申请 | `tests/integration/test_nova_contacts.py`、`test_nova_transfer_manager.py`、`test_task_transfer_flow.py`、`test_nova_collection.py`；`tests/unit/test_nova_payment_requests.py` | 隔离服务／语义结果，实际覆盖边界以各用例断言为准 |
| 账单、整理确认及结果卡 | `tests/integration/test_nova_bill_insights.py`、`test_nova_bill_results.py`、`test_bill_decision_api.py` | 统计、受控修改与展示数据，不以模型生成数字代替账本 |
| 卡片与订阅 | `tests/integration/test_nova_cards_subscriptions.py`、`test_cards_controls.py`；`tests/unit/test_subscriptions.py` | 查询、任务与银行校验；模拟扣费／恢复不是真实运营商服务 |
| 日程 | `tests/integration/test_nova_calendar.py`、`tests/unit/test_calendar.py` | 日程查询／变更及归属等用例；无外部日历同步 |
| 模拟商户联动 | `tests/integration/test_nova_shops.py`、`test_flower_shop_payment_flow.py`、`test_cake_shop_payment_flow.py` | 商品、订单与模拟银行付款链，不是现实支付网关 |
| 定时任务 | `tests/integration/test_scheduled_tasks.py`、`frontend/tests/scheduledTaskTime.test.ts` | 调度服务与时间转换；不能据此声明完整周期转账产品已实现 |
| 风险评估、推荐、持仓、收益及申赎任务 | `tests/integration/test_nova_wealth.py`、`test_nova_wealth_steps.py`、`test_nova_holdings_continuation.py`、`test_nova_product_name_search.py` | 任务及语义结果、候选引用与状态；不代表所有行情实时可用 |
| 配置计算与金额约束 | `tests/unit/test_wealth_portfolio_model.py`、`test_wealth_portfolio_differences.py`、`test_wealth_portfolio_acceptance.py` | 算法／约束测试及历史对照；不是金融收益／风险优势证明 |
| 本地知识问答与启动确认 | `tests/unit/test_planning_knowledge.py` | HTTP协议、知识原文、证据引用及阶段，无网络默认回归 |
| 人工接管、会话恢复与清理 | `tests/integration/test_nova_presentation.py`、`test_nova_conversation_history.py`、`test_nova_conversation_removal.py` | 本人归属、任务／输入与历史保存；不等于银行客服接管产品 |
| 安全与审计 | `tests/security/`、`tests/integration/test_operation_audit.py`、`frontend/tests/sessionAuthenticationSecurity.test.ts` | 隔离安全用例、模拟短信及记录；不宣称抵御所有攻击 |

表内同一单元格省略目录前缀的文件，沿用该单元格最近一个完整路径的目录。未来增加新能力时应同步补充用例和此索引；文件存在只能说明有测试资产，完整覆盖及当前通过状态必须依据具体断言和执行记录。

## 浏览器用例

浏览器脚本使用Playwright和独立浏览器实例。`PORTFOLIO_PLAYWRIGHT_MODULE`指向已安装Playwright的入口模块文件，`PORTFOLIO_BROWSER_EXE`指向Chrome／Edge／Chromium可执行文件；仅为测试依赖，不要求放入源代码包。上述隔离脚本从 `http://127.0.0.1:3000/` 读取前端静态资源，拦截API并返回测试响应，不使用真实Key或账本。不要把其他历史浏览器脚本未经检查地套在运行服务上。

```bash
node tests/browser/nova_feedback_examples.mjs
node tests/browser/login_orb_transition.mjs
node tests/browser/card_refresh_regression.mjs
```

前两项使用3000端口的前端静态资源。`card_refresh_regression.mjs`使用3019组件测试服务器，需另在 `frontend` 执行 `npm run dev -- --host 127.0.0.1 --port 3019`；这不是正式用户入口。证据截图写入被忽略的 `.deps/`，不附带真实业务数据。

## 验证记录的解释

本轮只做与状态、提示及示例修改直接相关的前端测试、生产构建和隔离浏览器验证。真实模型、真实银行服务、SQLite、PostgreSQL、人工体验是不同验证层；README及说明文件不将其中一种替代另一种，也不把历史通过数量或缺依赖跳过写成当前全部通过。模型金融效果未完成评价，不从测试通过数量推导收益或风险优势。

2026-10-30本轮记录：前端Node测试128项通过；Docker内TypeScript及Vite生产构建通过；`nova_feedback_examples.mjs`通过（两条示例原文发送、未知结果提示、安全拒绝提示及重试请求编号保持）；前后端健康。浏览器API均为拦截模拟，不发生真实银行业务或DeepSeek请求。本记录不包含额外封板检查、全量后端回归或新的真实模型语义验收。
