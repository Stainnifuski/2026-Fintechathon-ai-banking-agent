# Codex 工作约定

本仓库的 `backend` 和 `frontend` Docker 服务从源码构建镜像；运行中的容器不会自动加载后来修改的应用代码。

完成会影响运行中前后端行为的修改并通过相关验证后，如果本仓库的 Docker Compose 服务正在运行，从仓库根目录执行：

```bash
docker compose up -d --build --wait --wait-timeout 300 backend frontend
```

等待命令结束，确认 `backend` 和 `frontend` 健康后再报告修改已在运行服务中生效。若构建或健康检查失败，报告实际状态和错误。仅修改文档或其他不影响容器运行内容时，无需重建。用户明确要求暂不重启时，遵从该要求。

## NOVA 语义抽取原则

修改 NOVA Agent 理解用户输入的代码（意图分流、字段抽取、追问续接、相关提示词与测试）前，先阅读 `.agents/skills/nova-semantic-extraction/SKILL.md`（Claude Code 同文副本位于 `.claude/skills/nova-semantic-extraction/SKILL.md`）。意图和业务字段只能由模型做结构化语义理解，禁止用正则、关键词或句式模板抽取信息或覆盖模型结果；服务端只做引用核验、候选核对、格式解析和业务规则校验。
