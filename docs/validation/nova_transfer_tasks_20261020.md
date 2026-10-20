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

## 模型自主工具规划复验

同日后续改造移除独立的确定性意图规划器，所有模型模式消息统一进入 NOVA 原生工具循环。模型可连续调用查询工具和 `manage_transfer_request`；历史转账金额须绑定本人已完成转出流水 ID，付款账户须重新核验本人当前可转账账户。转账工具仍只创建草稿或待确认计划。

补充修复单笔多分句误拆：`按照最近一次转的钱数，从我第二张银行卡里，给赵六转钱` 保持为一笔；明确的 `给李四转100元，给王五转200元` 仍拆为两笔。

```bash
PYTHONDONTWRITEBYTECODE=1 env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m pytest -p pytest_asyncio.plugin \
  tests/integration/test_nova_transfer_manager.py \
  tests/integration/test_ai_conversation.py \
  tests/integration/test_nova_reads.py \
  -q -k 'not legacy_transfer_account_code'
# 54 passed, 1 deselected
```

被排除的旧展示测试仍以已移除的 `bill_service.transactions(..., now=...)` 参数调用当前服务，单独运行会触发 `TypeError`；它与本次 Agent 路由和转账工具改造无关，未把该旧测试计入通过结果。NOVA 工具循环单元回归另有 15 项通过，IDE 对本次修改文件未报告 lint 错误。

重建 Docker 后以正式页面实测原问题语句。模型依次调用时间查询、账户概览、流水列表、转账任务规划共 4 次工具；系统只按一笔请求处理。实测第二张卡为信用账户，不属于可转账现金账户，因此受控工具返回付款账户无效并未创建草稿，模型说明了最近一笔转出金额及阻断原因，没有把信用额度当作现金，也没有生成两张转账任务卡。

## 多收款人、备注与工具终止修复

后续将单轮实际工具调用总上限调整为10，并在转账工具成功后立即返回任务管理器结果，避免重复调用直到 `AGENT_STEP_LIMIT`。模型规划上下文新增当前任务摘要；修改或补充可提交经服务端归属校验的 `chat_task_id`，无明确对象时使用焦点任务。备注解析支持“备注为／改成／设置为”并支持清空。

多收款人拆分扩展为顿号、中文/英文逗号及“和／及／与”。正式页面输入“给赵六，李四，王五，周八各转账100元。”后生成4张独立任务卡：赵六、李四、王五各生成待确认计划，周八因没有本人已知完整号码保持独立待补充草稿；不再把四个姓名合并成一个收款人。

同一正式页面继续验证备注修改：四笔并存时，未指定对象的“修改备注为房租”会安全追问；明确输入“把周八这笔的备注改为房租”后只调用一次转账规划工具，周八任务卡备注更新为“房租”，其余三张卡备注保持不变。单任务及焦点任务的修改/清空另有自动化测试覆盖。

```bash
PYTHONDONTWRITEBYTECODE=1 env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m pytest -p pytest_asyncio.plugin \
  tests/integration/test_nova_transfer_manager.py \
  tests/integration/test_ai_conversation.py \
  tests/integration/test_nova_reads.py \
  -q -k 'not legacy_transfer_account_code'
# 56 passed, 1 deselected
```
