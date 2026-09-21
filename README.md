# 2026-Fintechathon-ai-banking-agent
面向银行业务场景的安全 AI 智能体，支持自然语言任务理解、规划执行、权限分级与风险控制。

## 第一轮最小闭环

已实现：用户注册和登录、JWT 鉴权、数据库账户与流水、选择其它用户的内部转账，以及不调用模型的聊天入口。

1. 复制环境变量：`cp .env.example .env`，并设置一个随机的 `JWT_SECRET`。
2. 启动完整环境：`docker compose up --build`。第一轮不依赖 Redis。
3. 打开 `http://localhost:3000`；首次启动会创建张三至周八共 6 个演示用户，手机号为 `13800000001` 至 `13800000006`，密码均为 `password123`。

登录成功后页面会直接切换到账户面板，地址栏不会变化。若显示“登录失败”，请先确认后端健康检查 `http://localhost:8000/health` 返回 `status: ok`。

如果 Docker Desktop 无法拉取镜像，可先走本地 SQLite 路径，无需 Docker：

```bash
python -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
DATABASE_URL=sqlite+aiosqlite:///./banking_agent.db .venv/bin/uvicorn app.main:app --reload --app-dir backend
```

另开一个终端启动前端：

```bash
cd frontend
npm install
npm run dev
```

表单和聊天入口都先创建持久化转账任务，再经过确认、应用受理、模拟银行执行和结果合并。
用户操作状态与银行账务状态分别保存，超时后按原 `operation_id` 核对，不会重复扣款。
聊天当前仅识别“转给 李四 100元”，不会调用真实模型。完整接口与实现范围见
[转账任务状态机 API](docs/contracts/转账任务状态机_API_v0.1.md)。
