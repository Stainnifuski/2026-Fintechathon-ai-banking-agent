# 2号即时转账 AI：使用与交付记录

日期：2026-09-22。基于内层应用仓库 e09fe24 开发；开始时工作区干净，无 AGENTS.md。初始交付未推送、未合并；后续用户授权的远端整合见文末。实现以当前HTTP任务编排为准，没有另建DAG执行器。

## 使用

从应用仓库根目录执行原有启动命令。首次启动的 `init_db/create_all` 会自动添加 `transfer_conversations` 和 `transfer_chat_receipts` 两张应用表，不修改银行表结构。生产迁移需要团队将相同表定义纳入自己的迁移流程；回滚代码前可保留新表，恢复本版本仍可继续会话。若确需删除这两张表，只在备份并确认不再需要对话恢复后执行；任务/银行账本无需回滚或重置。

默认 `TRANSFER_NLU_MODE=offline`：有限中文规则，完全离线可运行。支持示例：

- `给李四转300元`、`转三百块给李四，备注聚餐`、`转给 李四 300元`。
- `给李四转钱` → `300元`；`转300元` → `李四`。
- `从A001账户给李四转300元`；付款账户无法唯一解析时选择服务器候选。
- 在当前会话输入 `改成500元` 或 `换另一个李四`，必要时选择候选后生成新版本。
- `结果如何` 只解释任务快照；`好的`、`确认`、`取消`只引导点击任务按钮。

同名时不猜测；候选显示姓名、手机号和账户尾号。备注可选，不强制追问。明确拒绝未来、周期、AA、非人民币及数额歧义；中文数词是有限语法，不支持所有口语表达。模型模式也不把超范围请求改成即时转账。

一个会话绑定一笔任务，新转账点击“新建转账会话”。服务器存储槽位、候选、版本和任务引用；浏览器按用户保存会话ID及待重试请求。刷新恢复最后会话/快照，不恢复整段聊天文本。多个会话可以并行，恢复其他会话可使用API中的session_id。相同消息原样重试；新消息必须新编号。模型调用或消息响应失败时不会执行转账。

## 模型配置

显式设置 `TRANSFER_NLU_MODE=model`。配置：

| 变量 | 说明 |
|---|---|
| TRANSFER_MODEL_API_KEY | 模型密钥；未设置时兼容 LLM_API_KEY |
| TRANSFER_MODEL_NAME | 模型名；未设置时兼容 LLM_MODEL |
| TRANSFER_MODEL_BASE_URL | 兼容API根地址；未设置时兼容 LLM_BASE_URL，再默认 https://api.openai.com/v1 |
| TRANSFER_MODEL_TIMEOUT | 请求与整体理解超时秒数，默认15 |

适配器使用现有 httpx 依赖，调用 `/chat/completions` 的 JSON 对象输出模式；不依赖新增Agent框架、不开放任何模型工具调用。兼容服务需要支持JSON输出。Pydantic额外字段禁止、严格类型、金额原文核对及受控目录解析仍在本地执行。模型仅收到当前消息和“是否缺收款人”布尔值，不收到JWT、内部银行令牌、账户余额或完整对话历史。禁止从模型输出风险、授权、执行状态或任意工具动作。

Docker Compose 已通过 env_file 载入 `.env`；本地可先设置环境变量，或：

```bash
python -m uvicorn backend.app.main:app --env-file .env --host 127.0.0.1 --port 8000
```

未配置密钥/模型或超时、无效JSON、越权字段时返回明确错误，不静默降级为“模型成功”。测试可通过 `NLUEngine(model=...)` 注入异步 `extract(message, context)` 适配器，HTTP层通过 FastAPI `get_nlu` 依赖覆盖。

初始开发时没有可用模型密钥，使用MockTransport和注入模型验证；用户随后配置DeepSeek并明确授权发送虚构测试文本，现已完成真实模型联调，结果见文末。凭据没有输出或写入测试报告。

## 接口和模块

- `backend/app/services/ai/nlu.py`：有限离线解析、中文金额、模型适配和结构验证。
- `backend/app/services/ai/conversation.py`：持久化追问/建议桥接；不导入银行ORM、不签发授权、不调用银行写操作。
- `backend/app/models/conversation.py`：会话CAS版本与消息回执；与任务创建/修改在同一事务落盘。
- `backend/app/routers/tasks.py`、`schemas/tasks.py`：消息改为ChatReply信封、新增会话恢复GET。
- `backend/app/services/bank/transfer_service.py`、`routers/bank_operations.py`、`integrations/bank_api.py`：4号内部目录查询和5号HTTP适配器。
- `backend/app/services/orchestration/task_service.py`：创建/修改增加commit=False事务组合方式（默认行为不变）；版本锁防并发修改与旧确认/受理交错。
- `backend/app/domain/state_machine.py`：Decimal严格转换整数分；不再使用float乘100的容差作为权威校验。
- `frontend/src/App.tsx`、`services/bank.ts`：实际界面接入追问、候选、版本、刷新恢复、原请求重试和新会话。
- `agent/core/`：旧假成功骨架替换为实际入口兼容导出；旧TaskPlanner/DialogManager类不再提供，仓库实际入口没有依赖它们。

契约详见 [转账任务API增补](contracts/转账任务状态机_API_v0.1.md)。1号需适配ChatReply信封；4号新增只读目录接口；5号复用事务组合参数；3号强验证边界未改变。

## 验证

```bash
python -m pytest tests -q
cd frontend
# 使用正常 npm 环境：npm install && npm run build
# 本次环境使用已安装的等价命令：
node_modules/.bin/tsc && node_modules/.bin/vite build
```

后端覆盖：中文/小数金额、零负数/精度/歧义拒绝；缺参数；同名选择、伪造/过期候选；修改与旧卡失效；跨用户/版本隔离；并发同消息去重；跨数据库会话恢复；提交异常事务回滚；模型超时/结构攻击/金额幻觉/HTTP协议；恶意备注不执行；黄色真实模拟记账和双边流水；红色保持强验证阻断；UNKNOWN可信解释；既有状态机和银行所有权边界。

浏览器实测（独立临时数据库）：登录→“给李四转钱”→刷新→“三百元”→确认卡300→“改成500元”→确认卡500→确认提交。页面最终显示SUCCEEDED、银行流水号、余额50,000→49,500，和500元转出流水。全过程通过生产HTTP银行适配器；项目原banking_agent.db未修改。

限制：真实模型已通过本文所列虚构测试场景，但不代表覆盖所有自然语言表达；未运行PostgreSQL/Docker部署或跨进程PostgreSQL压力测试（并发和恢复使用文件SQLite）；强验证仍返回501。离线表达覆盖有限，模型服务也可能拒绝或误解表达，所以最终始终展示可信账户和精确金额由用户确认。不会扩展到定时、AA或其它业务。待消歧修改由会话持久化，前端禁用旧卡片，服务端也阻止旧卡确认和受理；取消仍可通过现有任务接口进行。已提交的完整修改会失效旧计划和授权。

最终验证结果：`python -m pytest tests -q` 为 **60 passed**；TypeScript检查通过，Vite生产构建通过（89 modules）；`git diff --check` 无问题。测试依赖位于临时虚拟环境 `/private/tmp/fintech-ai-venv`，前端依赖仅安装到被忽略的node_modules；没有提交生成的锁文件或修改实际凭据。

## 真实接口连通性补验

用户配置DeepSeek后，已通过现有ModelAdapter实际发送“你好”，成功获得符合Extraction schema的 `intent=unknown` 和空槽位，证明当前密钥、网络接口及JSON输出路径可用。此项仅为连通性/结构验证，不代表真实模型的转账理解、多轮和修改案例已通过。首次包含收款人、金额和备注的转账样例被自动审批阻止；用户随后明确授权仅发送虚构测试文本，已完成下述联调。没有发送实际银行数据。密钥未输出或写入文档。


## DeepSeek真实模型联调完成（2026-09-22）

在用户明确授权后，使用当前 `.env` 配置的DeepSeek接口运行 `tests/live/deepseek_transfer_smoke.py`。所有姓名、账户描述、金额及备注来自脚本中的虚构TEST样例；使用独立内存SQLite，没有读取或修改项目原银行数据库。每次模型调用仍只发送当前虚构消息和是否缺收款人的布尔值。

最终结果：**11个场景全部通过，12次真实模型调用，总耗时21.21秒**。其中7个场景调用模型；4个非法金额/明确日期场景在本地阻断，没有调用模型。不是模型基准评测，也不保证每次调用的延迟或表现完全一致。

覆盖完整口语表达与确认前不扣款；缺金额补充、刷新恢复、修改金额、旧卡拒绝、黄色模拟记账及可信结果查询；缺收款人后仅回复姓名；同名消歧；指定付款账户和精确小数；恶意备注不执行；负金额、超精度、多金额和未来转账拒绝。明确“明天”由本地阻断，“等我下班以后再……”由真实模型识别为不支持。

实际发现并修复：

1. 模型把单独回复的姓名/虚构联系人称呼当作无法理解：补充intent定义、追问语义，收款人是否存在与唯一性仍由银行目录判断。
2. 模型保留付款账户的“账户”后缀，导致目录精确匹配失败：服务端支持原称呼及移除明确“账户”后缀的匹配，且只能在银行返回的当前用户付款账户集合内匹配。

修复后 `python -m pytest tests -q`：**62 passed**；`git diff --check` 通过。本轮没有修改前端文件，沿用上一轮已通过的前端构建与浏览器验证。

复现命令（会调用外部模型并产生API费用，默认pytest不会执行）：

```bash
python -m tests.live.deepseek_transfer_smoke --run-live --output /tmp/deepseek-transfer-smoke.json
```

结构化报告：[deepseek_transfer_smoke_20260922.json](validation/deepseek_transfer_smoke_20260922.json)。报告仅包含虚构输入、提取结果、耗时和断言结果，无密钥、真实余额或真实流水。

剩余既有边界：3号MFA强验证仍为501；PostgreSQL/Docker及多进程压力验证尚未进行。本次没有绕过这些边界，没有推送或合并代码。


## 远端更新整合（2026-09-22）

按用户要求获取GitHub最新状态，发现远端提交 `8e5d3df` 新增AA收款。将该提交与本地即时转账AI实现整合，保留远端收款申请、付款任务取消后的释放逻辑、6个演示用户和前端AA面板；同时保留多轮对话、候选目录和持久化恢复。解决内部银行路由与前端App两处内容冲突，并修正AA页面“接受后立即扣款”的过时提示，使其符合实际先展示任务再确认的流程。

整合后默认测试套件：**68 passed**；前端TypeScript检查与Vite构建通过。README已统一更新为当前功能、启动方式、DeepSeek配置、验证结果与限制；新增本地数据库忽略规则。此次AI聊天没有扩展到AA理解，AA功能来自保留的远端实现。
