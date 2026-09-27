# AI Banking Agent

银行智能体演示项目，使用 FastAPI、React 和持久化任务编排，支持即时转账理解、确认授权、模拟银行记账及结果核对。当前版本已整合 AA 收款申请和 DeepSeek 模型接入。

## 已实现功能

| 功能 | 当前行为 |
| --- | --- |
| 用户和账户 | 注册、登录、JWT 鉴权、账户余额与交易流水查询 |
| 即时转账对话 | 按姓名或完整中国大陆手机号定位收款人；支持多轮补充、同名/多账户消歧、参数修改和刷新恢复 |
| 转账备注 | 手动备注、明确用途的有限自动备注及默认“转账”；受理前可编辑或显式清空，修改后须重新确认 |
| 模型理解 | 默认有限表达的离线规则；可配置兼容 Chat Completions 的模型，已完成 DeepSeek Flash 真实联调 |
| 确认和执行 | 生成持久化确认卡，确认后由任务编排提交模拟银行；修改会失效旧计划与授权 |
| 一次性定时转账 | 表单设置未来执行时间，确认后等待到期自动执行；支持执行前取消，并复用即时转账的风险复查、幂等记账和结果展示 |
| 异常恢复 | 消息和业务操作去重，结果未知时按原 operation_id 核对，模型不能自行宣布成功 |
| AA 收款申请 | 表单多选付款人，按每人金额或总金额均分；支持申请列表、接受、拒绝、取消和取消付款后的申请释放 |

AI 聊天目前仅支持**即时人民币转账**，AA 和一次性定时转账使用独立表单。聊天中的定时、周期及外币请求不会被转换成立即执行的转账。文字“好的”或“确认”不会直接扣款，需要操作对应任务卡片。

## 快速启动

在仓库根目录执行；如果已有 `.env`，保留现有配置，不要覆盖密钥。

```bash
cp .env.example .env
```

在 `.env` 中设置自己的 `JWT_SECRET` 和 `INTERNAL_BANK_TOKEN`。默认离线模式无需模型密钥。

### Docker Compose

```bash
docker compose up --build
```

访问前端 <http://localhost:3000>，后端健康检查 <http://localhost:8000/health>，API 文档 <http://localhost:8000/docs>。当前不依赖 Redis。

### 本地 SQLite

无需 Docker；以下命令在仓库根目录启动后端，并读取 `.env`。使用独立本地数据库文件，避免修改仓库原有演示数据库。

```bash
python -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
DATABASE_URL=sqlite+aiosqlite:///./banking_agent.local.db \
  .venv/bin/python -m uvicorn backend.app.main:app \
  --env-file .env --host 127.0.0.1 --port 8000 --reload
```

另开终端启动前端：

```bash
cd frontend
npm install
npm run dev
```

首次启动会创建张三、李四、王五、赵六、孙七、周八共6个演示用户，手机号依次为 `13800000001` 至 `13800000006`，密码均为 `password123`。登录后页面直接切换到账户面板，地址栏不变。已有演示数据库的种子升级行为详见运行指南及种子服务实现。

## 体验流程

1. 用张三登录，在聊天中输入 `给手机号13800000002转300元，备注房租`；也可输入 `按手机号转账`，然后补全号码和金额。表单可选择“按手机号查询”并核对脱敏账户候选。
2. 有同名对象或一人多账户时选择脱敏候选，检查确认卡中的银行名称、手机号掩码、账户、金额、币种、备注及风险等级。
3. 输入 `改成500元`、`改成备注聚餐` 或 `清空备注` 会生成新计划，旧卡片不可再确认；刷新后仍可恢复当前会话。
4. 点击确认并提交。低风险授权后调用模拟银行，成功时显示银行流水号；结果未知时显示待核对。
5. 新转账点击“新建转账会话”。网络异常使用“重试原消息”，避免重复创建任务。
6. AA 收款使用“发起收款申请”表单；付款人先接受申请并查看转账任务，再确认付款。总金额必须能精确均分到分。
7. 表单选择“定时转账”并填写未来时间；确认后任务进入等待执行，可在到期前取消。演示时可选择一分钟后的时间观察余额与流水变化。

## DeepSeek 配置

在本地 `.env` 中设置（密钥不要写入代码或提交 Git）：

```dotenv
TRANSFER_NLU_MODE=model
TRANSFER_MODEL_BASE_URL=https://api.deepseek.com
TRANSFER_MODEL_NAME=deepseek-flash
TRANSFER_MODEL_API_KEY=替换为你的密钥
TRANSFER_MODEL_TIMEOUT=30
```

修改配置后重启后端。使用其他兼容服务时，按其文档设置接口根地址和模型名，服务需要支持 JSON 对象输出。也兼容 `LLM_API_KEY`、`LLM_MODEL`、`LLM_BASE_URL`，优先使用上述 `TRANSFER_MODEL_*` 配置。

设置 `TRANSFER_NLU_MODE=offline` 可关闭外部模型调用。模型模式仅发送本次消息和必要的追问标记，不附加认证凭据、账户余额或历史流水。输出必须通过本地 schema、金额原文及银行目录校验；模型异常不会触发银行执行，也不会伪装成模型成功。

## 验证与已知限制

```bash
# 仓库根目录：默认全部离线运行，不调用付费模型
.venv/bin/python -m pytest tests -q

# 前端类型检查及生产构建
cd frontend
npm run build
```

本次离线验证：**92 项测试通过，TypeScript 类型检查和 Vite 生产构建通过**（2026-09-27，隔离 SQLite）。此前 DeepSeek 联调覆盖11个虚构业务场景，最终轮12次真实调用全部通过；[结构化报告](docs/validation/deepseek_transfer_smoke_20260922.json)记录具体结果。这是有限场景验证，不代表所有自然语言都能正确理解。

如需主动重跑真实模型测试（会发送脚本内虚构文本并产生 API 费用）：

```bash
.venv/bin/python -m tests.live.deepseek_transfer_smoke \
  --run-live --output /tmp/deepseek-transfer-smoke.json
```

- 强身份验证仍未实现，接口返回 **501**；红色路径不能完成付款，模型不能绕过它。
- 默认离线规则只覆盖有限表达；聊天不处理 AA、定时、周期或其他业务。
- 定时转账使用 FastAPI 进程内轻量调度器，服务运行时每两秒扫描；服务重启后会补扫已到期任务，但当前不提供独立调度进程、周期转账或自然语言定时创建。
- 目前验证以文件 SQLite 和独立数据库连接为主；PostgreSQL／Docker 部署及跨进程压力测试尚未完成。手机号查询仅支持中国大陆号码，不验证号码所有权。
- 模拟银行内部令牌必须单独配置，默认演示令牌已禁用；手机号精确查询仍受演示环境已有的已认证接口边界约束，未配备生产级限流。
- 4号银行模块持有银行数据；AI不直接读写银行ORM、不决定风险或签发授权，5号通过受控银行接口执行与核对。

## 文档

- [运行指南](运行指南.md)
- [转账任务状态机 API 与多轮对话契约](docs/contracts/转账任务状态机_API_v0.1.md)
- [即时转账 AI 使用、交付与真实模型验证](docs/即时转账AI_使用与交付.md)
- [4号银行手机号、备注与风险边界](docs/bank/手机号转账与风险边界.md)
- [本次验证记录](docs/validation/phone_note_bank_20260927.md)
