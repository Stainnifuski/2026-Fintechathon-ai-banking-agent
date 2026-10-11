# 统一 LLM 服务验证记录

日期：2026-10-06。基线提交：`1ce96d8`。本次仅修改源码、示例及文档，未编辑真实 `.env`、数据库或前端，未进行付费真实模型请求。

## 实现范围

- 根目录 `.env` 与进程环境统一加载，进程环境优先；Docker 沿用 `env_file`。
- 单一 `LLM_*` 配置、auto/model/offline 全局模式；有效密钥自动启用转账、账单、理财。
- `llm.generate_text()` 和 `llm.generate_structured()` 统一通信、JSON/schema 校验、连接池、并发、连接/总超时、错误和使用记录。
- 默认沿用 DeepSeek deepseek-flash；其他兼容地址不携带其 thinking 参数。未实现其他原生协议或语音/图像/向量能力。
- 保留业务提示词和金额/原文/产品/来源检查，保留交易确认和验证流程；推理不自动重试。
- 旧模型配置集中按组过渡兼容，全局有效密钥优先，旧业务 NLU 开关不再生效；真实凭据不进入测试日志。

## 自检修复

- 修复离线启动受错误模型地址影响的问题，注入 NLU 模型也只依赖统一超时；强制模型模式缺密钥在初始化数据库前失败。
- 旧密钥组缺省地址仅继承显式全局地址，没有地址时报配置缺失；避免将未知旧服务商密钥发送到默认服务商。模型只在共享地址一致时继承，保留各旧组隔离。
- URL 解析错误、非法端口、误填完整 completions 地址和非法密钥字符统一为不含原文的配置错误。
- 非法 max_tokens、temperature、消息及 schema 在网络请求前拒绝；服务商的可选 usage 字段异常不影响有效回答。
- 连接池参数改变时明确拒绝替换，不关闭在途连接；关闭等待在途请求并拒绝新请求。请求取消、并发关闭及关闭操作被取消后重试均已测试。
- 启动失败、请求异常、后台任务异常及 LLM 关闭异常时，仍清理注册任务并释放数据库资源；后台异常记录任务名和异常类型。
- pytest 在 `.env` 加载前将真实模型联调开关默认为关闭，只接受进程环境中的显式 opt-in。

新增边界用例在修复前复现了 15 个失败；修复后统一服务专项 **62 passed**。相关回归及完整回归结果见下文。扫描后仅统一客户端保留模型 HTTP 请求入口，业务模块没有残留密钥读取或独立模型 HTTP 请求。

## 验证结果

本地 Python 3.14.3，项目 `.venv`。补齐已有 `backend/requirements.txt` 声明而虚拟环境缺失的 icalendar、recurring-ical-events 依赖；未修改依赖清单。

相关回归命令：

```bash
WEALTH_LIVE_TEST=false WEALTH_SEMANTIC_LIVE=0 WEALTH_WORKSPACE_LIVE=0 \
.venv/bin/python -m pytest tests/unit/test_llm_service.py tests/unit/test_ai_understanding.py \
  tests/integration/test_ai_conversation.py tests/integration/test_bills.py \
  tests/integration/test_wealth_workspace_mainline.py tests/integration/test_wealth_workspace_lifecycle.py -q --tb=short
```

结果：**149 passed, 6 skipped**。跳过项为未配置的 PostgreSQL 分支。

覆盖一行有效密钥启用模型、占位密钥离线、全局 offline、配置优先级/原子旧配置继承、非法配置、不泄露凭据、普通文本/结构化协议、三个业务模块与知识解释共享同一密钥/地址/模型/连接、服务商专用参数隔离、错误响应/非法 JSON/越权字段/类型错误/截断、超时（排队及网络）、并发和连接释放，以及原有转账会话、账单只读、理财工作台与授权流程。

自检后的完整后端回归（普通 pytest，无额外关闭开关）：

```bash
.venv/bin/python -m pytest tests -q --tb=short
```

结果：**517 passed, 118 skipped, 10 failed**。跳过包括 PostgreSQL 分支和显式 opt-in 的真实模型测试。以下十个失败均已在 `/private/tmp/fintech-llm-baseline-20261006` 中，通过 `git archive HEAD` 提取的未修改基线使用同一虚拟环境复现；错误位置与断言一致，属于已有失败：

| 测试文件 / 用例（均为 sqlite） | 基线与本次相同失败 |
|---|---|
| test_wealth_bank / real_jwt_ownership_and_injected_description_cannot_authorize | 旧离线分支返回 clarification，断言要求 refusal |
| test_wealth_fund_directory / mixed_pool_immutable_import_default_and_source_disclosure | 临时样本缺少 data/manifest.json |
| test_wealth_research / immutable_import_cash_context_and_future_visibility | 返回缺少 duplicate 字段 |
| test_wealth_research / multiturn_preview_replay_knowledge_and_context_invalidation | 返回 plan，旧断言要求追问保留金额 |
| test_wealth_research / admin_scenarios_have_actual_orders_lots_holds_and_bank_failures | 样本产品 S007 不存在 |
| test_wealth_research / read_only_archives_are_owner_scoped_and_never_resume_commands | 归档列表为空，断言期望一项 |
| test_wealth_semantic_constraints / unknown_goal_is_conditional_no_loop_and_keeps_explicit_horizon | 金额优先方案与旧增长目标追问断言不同 |
| test_wealth_semantic_dialogue / pending_purchase_does_not_become_planning_dependency | 金额优先方案不包含旧波动追问文案 |
| test_wealth_task_controls / cancel_submitted_task_keeps_settlement_observation | 返回 COMPLETED，断言期望 PARTIAL_COMPLETED |
| test_wealth_task_controls / cancelled_sell_blocks_buy_dependency_and_human_direct_cancel | DAG 中不存在旧断言要求的 REREAD_CASH 节点 |

基线仅执行上述失败集合：**10 failed, 10 skipped, 81 deselected**。没有为通过完整测试而改写这些业务行为或放宽断言。

`git diff --check` 通过。没有验证 Docker 实际构建/容器重建、PostgreSQL 或真实服务商调用；MockTransport 协议验证不能替代真实模型联调。
