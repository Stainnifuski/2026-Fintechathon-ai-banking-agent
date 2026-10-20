# NOVA 转账任务接入验证（2026-10-20）

实现范围：NOVA 单窗口多笔即时人民币转账草稿、本人联系人/历史线索、新收款人姓名加完整号码、缺失信息追问、候选选择、现有银行计划接线、前端任务卡与会话恢复。资金确认与执行继续由转账工作台处理。

执行记录：

```bash
env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p pytest_asyncio.plugin tests/integration/test_nova_transfer_manager.py tests/integration/test_ai_conversation.py tests/integration/test_nova_reads.py -q -x
# 40 passed

cd frontend && npm run build
# TypeScript 与 Vite 构建通过
```

新增测试覆盖：新收款人补号码后才建计划、已保存联系人缩减输入、多笔独立草稿和幂等重放、完整卡号和修改计划、多账户候选选择、HTTP 与流式入口及恢复、仅已知卡号时不显示未知手机号、转账记录只读查询的意图边界、仅说“转钱”时生成草稿并明确缺失字段。后续复验以当次命令结果为准。

限制：当前对明确的多笔中文句式做确定性拆分；更复杂的省略和指代会要求用户逐笔明确。NOVA 不直接确认或执行转账，用户需在转账页核对任务卡。聊天任务可恢复，但普通只读聊天文本仍按原 NOVA 机制保存在前端内存中。默认短信仍是本地模拟发送，所有账务为模拟银行数据。
