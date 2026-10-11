# 统一 LLM 配置与开发接口

NOVA工具调用、转账理解、账单问答、理财多轮理解及知识解释共用 `backend/app/services/ai/llm/`。业务模块不再自行读取密钥、设置服务地址或发模型 HTTP 请求。

## 用户配置

首次运行复制根目录 `.env.example` 为 `.env`。默认沿用团队的 DeepSeek 服务，用户只需替换一次：

```dotenv
LLM_API_KEY=替换为你的密钥
```

模板已提供 `LLM_BASE_URL=https://api.deepseek.com`、`LLM_MODEL=deepseek-flash` 和 `LLM_MODE=auto`；这三个字段也有代码默认值，因此只写一行有效密钥即可使用默认服务。DeepSeek 参数依据[官方 Chat Completions 文档](https://api-docs.deepseek.com/api/create-chat-completion/)；适配层对该服务关闭 thinking。

| 配置 | 默认值 | 行为 |
|---|---|---|
| LLM_MODE | auto | 有效密钥启用所有模型功能，无密钥或占位密钥使用有限离线规则 |
| LLM_API_KEY | 空 | 全局唯一模型密钥，空白及模板占位值不视为有效密钥 |
| LLM_BASE_URL | https://api.deepseek.com | 兼容 Chat Completions 的根地址，不含 `/chat/completions` |
| LLM_MODEL | deepseek-flash | 默认服务预设；其他服务地址必须明确指定模型 |
| LLM_PROVIDER | 自动识别地址 | `api.deepseek.com` 使用 deepseek，其他地址使用 compatible；可显式覆盖 |
| LLM_TIMEOUT | 35 | 单次调用总时限（秒），包括并发排队及网络等待 |
| LLM_CONNECT_TIMEOUT | 10 | 连接超时（秒） |
| LLM_MAX_CONNECTIONS | 4 | 共享连接池及最大并发请求数 |
| LLM_MAX_TOKENS | 4096 | 默认输出上限；业务可指定更低上限 |
| LLM_TEMPERATURE | 0 | 文本生成默认值；结构化提取默认固定为 0 |

`LLM_MODE=offline` 全局禁止外部模型调用，即使配置了真实密钥；离线启动和本地解析不检查未使用的服务商地址/模型。`LLM_MODE=model` 强制使用模型，缺少密钥时在启动阶段返回明确配置错误。auto 仅根据密钥是否存在/是否为占位值决定启用，密钥是否被服务商接受由实际请求判断。

其他兼容服务在同一处配置：

```dotenv
LLM_MODE=auto
LLM_API_KEY=替换为该服务的密钥
LLM_BASE_URL=https://your-provider.example/v1
LLM_MODEL=该服务支持的模型名
```

结构化理解需要服务支持 JSON 对象输出。兼容适配不携带 DeepSeek 的 thinking 参数；原生非兼容协议、语音、图像或向量模型需要后续增加能力适配，不代表当前已经接入。

## 加载与部署

源码运行在 `backend.app` 导入时自动加载项目根目录 `.env`，不依赖当前工作目录；已存在的进程环境变量优先。Docker 继续通过 compose 的 `env_file` 注入同一文件，容器不需要复制密钥文件。

修改配置后重启本地后端。Docker 需重新创建容器以更新 `env_file`：

```bash
docker compose up -d --build --force-recreate backend
```

共享 HTTP 客户端在应用退出时等待在途请求结束后关闭，关闭期间不接收新模型请求；启动失败或后台任务异常也执行连接清理。不热加载配置文件；运行期修改 `.env` 必须重启。连接池创建后，如进程中修改超时或连接数，返回 MODEL_CONFIGURATION_CHANGED，避免关闭其他请求使用中的连接；需重启应用，或由服务管理者在请求结束后显式关闭再重新创建连接池。密钥不进入前端、日志或版本库。

## 旧配置迁移

1. 将正在使用的密钥、服务根地址、模型名整体移到 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`。
2. 删除 `TRANSFER_MODEL_*`、`WEALTH_MODEL_*`、`BILL_MODEL_*`，以及各业务 `*_NLU_MODE`。全局超时改为 `LLM_TIMEOUT`。
3. 全局设置 `LLM_MODE=auto`（或需要离线时设为 offline），重启后端。

有效 `LLM_API_KEY` 始终优先。没有有效全局密钥时，为兼容现有开发配置，依次选择 TRANSFER_MODEL、WEALTH_MODEL、BILL_MODEL 中第一组有效密钥，所有业务共用这组配置，并输出不含凭据的弃用提示。优先使用该组地址，缺省时仅继承明确配置的 `LLM_BASE_URL`；没有地址时报告 MODEL_CONFIGURATION_MISSING，不猜测旧密钥所属服务商。模型优先使用该组名称，仅在使用全局地址或地址与全局一致时继承 `LLM_MODEL`；明确的 DeepSeek 地址可使用默认模型，其余地址必须指定模型。不会读取另一旧业务组的地址/模型。已有 `.env` 中的旧 `LLM_BASE_URL`/`LLM_MODEL` 如与实际服务不符，迁移时须一并替换。

旧 `TRANSFER_NLU_MODE`、`BILL_NLU_MODE`、`WEALTH_NLU_MODE` 和模块专用超时不再生效，避免旧模板的 offline 开关阻止全局自动启用。`agent/config/agent_config.yaml` 不再维护第二套模型配置。

## 新功能调用方式

```python
from pydantic import BaseModel, ConfigDict
from backend.app.services.ai.llm import llm, get_mode, LLMError

class Extraction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    intent: str

# 结构化理解：data 已通过指定的 Pydantic schema 校验。
result = await llm.generate_structured(
    messages=[{"role": "user", "content": "用户问题"}],
    schema=Extraction,
    max_tokens=600,
)
intent = result.data.intent

# 自由文本生成。
result = await llm.generate_text(
    messages=[{"role": "user", "content": "需要概括的内容"}],
)
text = result.data
```

上述示例位于异步函数内。业务模块通过 `get_mode()` 判断是否走自己的离线实现。`result.evidence` 仅包含 provider、model、白名单整数 token 统计和 live 标记，不保存输入、凭据或服务商原始响应。Agent 的源码导入入口沿用现有 backend 包；Docker 后端内使用 `app.services.ai.llm` 或相对导入，避免重复导入两套客户端。

统一接口负责通信、并发、总超时、JSON 解析和结构校验；业务仍负责提示词、最小必要上下文、金额原文、产品引用及事实一致性校验。转账不发送银行凭据、余额或历史流水；账单报告仍由服务端统计；理财保留原有最小上下文和来源校验。

## NOVA 原生工具调用

`llm.generate_with_tools(messages=messages, tools=tools)` 返回assistant消息字典（content、可选tool_calls），支持原生工具调用和role=tool回传，复用统一连接池与LLM配置，不改变generate_text/generate_structured契约。服务商须支持兼容Chat Completions的工具调用，否则返回模型错误。

NOVA当前注册16个只读工具，覆盖时间、账户/账单、卡片/订阅、日历及两家模拟店铺。每轮输出显式申请4096 token，受LLM_MAX_TOKENS上限约束；底层工具接口默认仍为800，其他业务预算不变。默认报告优先摘要，年度趋势概括主要变化，异常只举少量例子；详细内容可继续查询。

业务工具发送查询所需的本人事实：脱敏账户与账单、本人日历事件、模拟店铺商品和本人订单。金额为整数分，日历使用user_calendar，店铺使用simulated_merchant；登录凭据、服务端ToolContext和订单客户ID字段不发送给模型。无模型时NOVA使用明确标记的有限离线规则；模型启用后的调用失败不会伪装成离线成功。

停止原因length会记录实际预算及completion_tokens，并提示回答超出输出限制；其他中断保留白名单原因，不记录提示词、外部响应正文或密钥。截断工具参数不会执行。

操作示例和当前工具清单见 [NOVA使用指南](nova_usage.md)，接口分别见 [基础只读工具](../contracts/NOVA页面上下文与只读工具_API_v0.1.md)、[日历](../contracts/NOVA日历查询_API_v0.1.md)、[店铺](../contracts/NOVA店铺查询_API_v0.1.md)。基础循环限制见 [时间契约](../contracts/NOVA时间查询_API_v0.1.md)。

## 错误及验证

统一错误 `LLMError.code` 包括 MODEL_DISABLED、MODEL_CONFIGURATION_MISSING/INVALID/CHANGED、MODEL_CLOSING、MODEL_REQUEST_INVALID、MODEL_TIMEOUT、MODEL_NETWORK_ERROR、MODEL_HTTP_状态码、MODEL_RESPONSE_INVALID、MODEL_OUTPUT_INCOMPLETE、MODEL_SCHEMA_INVALID。非法消息、schema、token 上限或 temperature 在发请求前拒绝。异常只暴露稳定错误码，不输出外部错误正文或密钥，不自动重试请求。

转账失败返回理解错误，不生成或执行交易；理财失败返回 model_unavailable；账单保留有限离线回退并明确标记 fallback。不会把离线结果伪装成模型成功。模型不接收确认、授权或银行执行入口。

```bash
.venv/bin/python -m pytest tests/unit/test_llm_service.py tests/unit/test_ai_understanding.py tests/integration/test_ai_conversation.py tests/integration/test_bills.py -q
.venv/bin/python -m pytest tests -q
```

默认回归隔离真实模型凭据并关闭网络模型调用，协议验证使用 MockTransport。pytest 在读取 `.env` 前将真实联调开关默认为关闭，避免文件中的旧开关自动启用付费请求。真实模型验收需在运行 pytest 前通过进程环境显式开启原有 WEALTH_LIVE_TEST / WEALTH_SEMANTIC_LIVE / WEALTH_WORKSPACE_LIVE，在隔离数据库执行，不以离线或 mock 结果替代真实验收。
