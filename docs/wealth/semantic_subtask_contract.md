# 理财语义与子任务接口（本轮实施中）

## 需求与澄清

DeepSeek输出严格`wealth-semantics-v1`，从双向近期对话、最后未回答问题、已核对需求、候选快照及银行摘要理解当前轮次。表达示例不是关键词全集。用户资产陈述不成为投入预算，模型不计算比例金额、改写银行现金、代评估或授权。

每字段保留状态、用户原文、来源消息及版本。补丁`set/clear/mark_unknown/skip`明确区分设置、未提及、未知和跳过；无值不代表清除。比例保存ratio/base，以银行核对的基数Decimal向下取整到整数分；半年以北京业务日加六个日历月。

具体支出用途（学费、房租、旅游）可缺失。投资目标、可投资期限、临时用钱到账要求分别记录且可同时成立；目标/期限缺失影响下一步时按上下文引导，模型可选择真正需要澄清的字段，无固定问序。已经提供的信息不重问；不确定、请助手建议、暂不提供保留真实状态，返回注明条件的候选，不默认填值、不循环追问。产品查询/比较无上述前置问题。

“保值”是偏好，不表示保本或正式风险评估。少波动/保值、兼顾、增长及到账约束进入provider输入、服务端候选过滤及方案证据。个性化建议仍检查有效本人评估，可通过原九题可信表单后继续需求。

## 持久化

复用`wealth_tasks`/`wealth_task_nodes`，澄清阶段真实保存READ_FACTS、REQUIRE_INPUT、PLAN/RESEARCH节点及需求版本；修订新增无环节点，保留已完成事实。新规划和旧待确认申购分别保留，主任务选择不撤销旧订单，也不授予交易权限。订单图使用既有确认/模拟授权/提交/结算保护。

## 2号接口（已实现部分）

认证主体来自JWT；context_id必须匹配银行当前本人选择。`POST /api/v1/wealth/subtasks`复用同一对话服务，可传raw_text让模型理解，或已通过严格schema的parsed_intent避免重复付费调用。两者均不能替代交易确认授权。

```json
{
  "schema_version":"wealth-subtask-v1",
  "request_id":"parent-create-0001",
  "parent_task_id":"parent-task-2",
  "context_id":"当前本人范围",
  "raw_text":"投入可用现金的一半，半年后用，希望兼顾波动和增长"
}
```

typed方式在同一请求增加：

```json
{
  "intent":"allocate",
  "semantic":{
    "schema_version":"wealth-semantics-v1",
    "intents":[{"action":"plan","relation":"independent"}],
    "patches":[
      {"field":"investment_budget","op":"set","quote":"投入可用现金的一半","budget":{"mode":"proportion","ratio":"0.5","base":"available_cash"}},
      {"field":"horizon","op":"set","quote":"半年后用","horizon":{"unit":"months","count":6,"text":"半年后用"}},
      {"field":"investment_goal","op":"set","quote":"希望兼顾波动和增长","goal":"balanced"}
    ]
  }
}
```

返回统一envelope：schema_version、child_task_id、parent_task_id、session_id、request_id、version、requirement_version、status、waiting_reason、cards、result、error/code。

- `GET /subtasks/{id}`：读取本人状态/需求/节点与已保存卡片。
- `PATCH /subtasks/{id}`：expected_version、request_id、raw_text、可选parsed_intent，复用原会话修订。
- 控制沿用`POST /tasks/{id}/{pause|resume|rollback|cancel|handoff}`；`POST /tasks/{id}/select-main`只改变会话主任务，返回更新后session_version。
- create请求按完整参数幂等：换parent/context/typed内容重放拒绝；跨用户不能读任务。

外部依赖完成接口、复合动作自动分解和端到端局部修订仍待本轮完成；不得把上层“已付房租”当现金到账或本人授权。最终联调示例与验收尚需补齐，当前文档不表示I01/D01已完整通过。
