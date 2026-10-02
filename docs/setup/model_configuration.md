## 理财 v1.2

WEALTH_NLU_MODE=offline默认有限规则；model使用严格结构化意图与受限知识解释。优先WEALTH_MODEL_API_KEY/BASE_URL/NAME，再TRANSFER_MODEL_*，再LLM_*；团队DeepSeek已联调deepseek-flash，base_url=https://api.deepseek.com，关闭thinking。密钥写忽略.env，不发到前端/日志/仓库。35秒总读超时、连接10秒、共享低并发连接池、不会自动重试扣款。

Docker compose的env_file会加载配置；改文件后`docker compose up -d --force-recreate backend`才更新进程环境，仅restart不重新加载env_file。外部同步独立worker默认关闭；详见wealth/data_sources.md。真实模型验收需显式WEALTH_LIVE_TEST=true，在隔离库运行tests/integration/test_wealth_live_model.py；失败不以offline/mock结果代替。


# 模型配置

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


理财当前仍为有限离线规则，转账模型配置不等于理财已经接入大模型。真实模型测试产生费用，须按团队授权执行。
