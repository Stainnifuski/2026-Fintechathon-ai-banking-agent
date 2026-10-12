# NOVA 时间查询与最小 Agent Loop

本文记录时间工具基础契约。后续已扩展本人账户、账单、日历及店铺工具，当前页面协议、身份上下文及查询范围以 [NOVA只读工具契约](NOVA页面上下文与只读工具_API_v0.1.md) 为准。

已实现登录后的时间查询闭环：NOVA 对话面板 → POST /api/v1/nova/chat → 原生模型 tool_calls → 时间工具 → role=tool 回传 → 模型最终回答。

## 请求和响应

接口沿用 Bearer JWT 登录校验。请求示例：

```json
{
  "message": "现在北京时间几点？",
  "history": [
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "你好，可以查询当前时间。"}
  ]
}
```

message 去除首尾空白后不能为空，原始长度最大 2000 字符。history 可省略，最多 12 条，每条 content 长度 1–4000；role 仅接受 user/assistant，不接受客户端 system/tool 消息。前端只回传最近 12 条成功轮次的对话文本，不回传工具执行记录。

响应示例（时间仅作格式展示）：

```json
{
  "reply": "当前北京时间为2026年10月11日00:18:33，时区Asia/Shanghai。",
  "mode": "model",
  "tool_calls": [{
    "name": "get_current_time",
    "result": {
      "server_time_utc": "2026-10-10T16:18:33.121110Z",
      "timezone": "Asia/Shanghai",
      "local_time": "2026-10-11T00:18:33+08:00"
    }
  }]
}
```

mode 为 model 或 offline。tool_calls 为本次请求的执行摘要，工具结果含 error 时表示失败；一次请求可有多次执行。最终文本由模型生成，查询记录提供服务端事实供核对。模型原始响应及凭据不返回页面。

## 时间与工具执行

backend/app/services/ai/nova.py 的 TOOL_REGISTRY 集中定义 Schema、Pydantic 参数模型和执行函数，时间工具为 get_current_time，其他只读业务工具见扩展契约。timezone 可省略，默认 Asia/Shanghai；合法 IANA 时区由 ZoneInfo 校验。拒绝额外参数、无效时区、非字符串时区和非对象参数。

时间复用 backend/app/domain/time.py 的现实 UTC 时钟，与 /api/v1/system/time 同源；不受理财测试轮次或模拟推进影响，不读取用户电脑的本地时区。local_time 是带 UTC 偏移的查询瞬时，回答到达时可能已经过去数秒。工具为只读查询，不需要本人确认。

原生工具协议通过统一 llm.generate_with_tools 发送，复用 LLM_* 配置、HTTP 连接池和错误清洗。一次模型响应最多 4 个工具调用，一次 Agent 请求最多 4 轮模型调用，总截止时间 90 秒；各轮仍受 LLM_TIMEOUT 限制。不自动重试模型请求。

工具结果按 tool_call_id 回传，未知工具返回 UNKNOWN_TOOL，错误参数返回 INVALID_ARGUMENTS，执行异常返回 TOOL_EXECUTION_FAILED；模型可在后续轮次修正参数。循环耗尽返回 AGENT_STEP_LIMIT，总时限耗尽返回 AGENT_TIMEOUT。工具白名单不包含旧转账、余额等占位实现，账户查询使用已接通的只读服务，也没有确认、授权或银行执行入口。

## 模式、错误与会话

复用全局模型模式。有有效配置时使用模型原生工具调用；LLM_MODE=offline 或 auto 无有效密钥时，时间查询支持有限规则识别的默认/北京时间表达，仍调用同一个现实时间工具；其他已实现的有限离线查询见各业务契约，页面明确标记“离线规则”。其他地区时间需要模型模式。不在模型请求失败时自动伪装成离线成功。

未登录返回 401，非法请求返回 422，模型或循环失败返回 503：detail 包含稳定 code 和可展示的 message。前端保留失败输入以便重发，失败轮次不进入后续上下文。

会话只保存在前端内存，收起面板保留，刷新或退出登录清空；组件卸载取消浏览器请求，不保证已到达服务端的请求停止。工具执行不持久化。现有转账、账单、理财工作台独立保留。

## 运行和验证

源码开发重启后端并运行 frontend/npm run dev；Docker 更新执行：

```bash
docker compose up -d --build backend frontend
```

NOVA每轮输出预算4096 token，仍受LLM_MAX_TOKENS上限约束。不需要数据库迁移。前端请求超时 95 秒，Nginx API 代理读取超时 100 秒。

登录后打开“问 NOVA”，点击“现在北京时间几点？”，或输入“纽约现在几点？”。启用模型时应展示最终回答和可展开的查询记录。模型配置见 [统一模型配置](../setup/model_configuration.md)。测试和真实联调记录见 [验收记录](../validation/nova_time_20261010.md)。
