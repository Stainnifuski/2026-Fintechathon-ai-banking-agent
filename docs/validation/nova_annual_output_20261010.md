# NOVA 年度账单输出预算修复（2026-10-10）

用户问题：“请帮我查看今年的账单情况”返回 MODEL_OUTPUT_INCOMPLETE。

检查发现 NOVA 未指定预算，继承 llm.generate_with_tools 的800-token默认值；当前全局 LLM_MAX_TOKENS 为4096，却被局部800上限限制。年度报告的分类、趋势和异常等信息容易超过该输出预算。统一客户端对未正常结束的响应报 MODEL_OUTPUT_INCOMPLETE。这个代码同时覆盖 length、content_filter、insufficient_system_resource 等原因，原请求没有保存具体finish_reason，因此不能断言用户当次一定是长度截断。

DeepSeek 官方定义 length 为输出上限或上下文长度超限，其他中断亦有独立停止原因：[Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)。

修复：

- NOVA 显式申请4096 token；依然遵守全局上限，未修改密钥或环境配置，也未修改其他业务预算。
- 默认报告优先摘要、概括年度趋势、最多举3条待核对提示；需要更多内容时可继续查询。
- 不完整响应携带白名单finish_reason，日志记录停止原因、实际预算、整数completion_tokens；不记录原始输出或输入。
- NOVA HTTP错误消息分别提示长度限制和其他生成中断，稳定code仍保留。
- 不自动重试，不把截断结果当作成功，不执行截断的工具参数。

验证：

- 单元回归（test_nova、test_llm_service、test_ai_understanding）：99 passed。新增验证4096预算、全局低上限、截断工具参数不执行、安全日志、明确HTTP错误提示。
- NOVA与账单集成回归（test_nova_reads、test_bills）：36 passed，隔离内存SQLite，沙箱外运行。
- 真实模型：隔离合成年度账单56笔、6个类别、54条大额提示，问同一句话，正确调用现实时间和年度账单工具，年度消费54370.26元，报告完整返回并将异常示例控制为3条。
- 修复前同一句话在合成数据下也曾成功：未稳定复现用户原始失败，不能以合成验证替代对原请求停止原因的确认。
- git diff --check通过。

未读取运行账户数据，未修改运行数据库，未重新运行前端构建；本次仅后端变化。运行中的后端需要重启或 docker compose up -d --build backend 后生效。未进行全仓库回归。
