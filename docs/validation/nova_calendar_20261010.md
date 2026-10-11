# NOVA 日历查询验收（2026-10-10）

本轮新增三个只读工具：list_calendar_events、search_calendar_events、get_calendar_event。生活服务日历新增日期及活动发生实例上下文、活动选择按钮和NOVA快捷查询入口。复用现有日历读取/重复展开服务，无数据库迁移，没有增加日历写入或提醒工具。

## 自动验证

- `python -m pytest tests/unit/test_nova.py tests/unit/test_llm_service.py tests/unit/test_ai_understanding.py -q`：99 passed。
- `pytest tests/integration/test_nova_calendar.py tests/unit/test_calendar.py tests/integration/test_nova_reads.py -q`：26 passed，隔离内存SQLite，沙箱外执行。
- 最后调整离线礼貌前缀和多匹配分页提示后，`pytest tests/integration/test_nova_calendar.py -q`：6 passed。
- frontend 下 `npm run build`：TypeScript/Vite通过，仍有既有大包提示。
- git diff --check通过。

覆盖内容：

- 按日查询与现有日历服务返回的事件一致，时间段重叠、跨日、结束/开始恰好边界、全天原始日期、重复改期实例均正确。
- 列表分页保留总数/has_more，日历结果为user_calendar，不带模拟银行金额标记。
- 全部保存标题/地点搜索，包括历史单次事件和ICS例外标题；重复原始开始时间和未来实际发生次数区分。
- 指定/页面选中发生实例匹配，EXDATE和旧改期时间拒绝；长备注按offset读取，列表返回摘要。
- UTC晚间在北京时间次日的事件详情不漏查；纽约全天日期不移位，夏令时跨日使用正确的-04/-05偏移。
- 默认未来30天没有发生不等于事件不存在，指定远期窗口可取得实际发生。
- 其他用户/不存在活动统一404语义，工具不接受user_id，非法时区、日期、分页、范围和空名称拒绝。
- 常用离线日期、名称、选中日期查询复用同一工具；同名多个活动要求选择，写请求不伪装成功。
- 登录HTTP请求经模型原生工具调用/结果回传生成回答，页面协议验证，服务端身份不进模型输入；读取没有改变事件数。

## 真实模型联调

使用项目现有模型配置和独立内存合成日程完成五个场景；没有读取运行用户日程，也未修改运行数据库。

| 问题 | 实际工具 | 结果 |
|---|---|---|
| 明天有什么规划？ | get_current_time、list_calendar_events | 北京时间今天10月11日，明天10月12日，项目答辩09:00–10:00 |
| 项目答辩什么时候举行？ | search_calendar_events | 唯一非重复匹配，返回10月12日09:00–10:00、合成一号会议室 |
| 下次团队例会什么时候举行？ | search_calendar_events、get_current_time、get_calendar_event | 下一次10月13日17:00–18:00（北京时间），后续每周实例正确 |
| 这个活动什么时候举行，在哪里？ | get_calendar_event | 使用页面选中答辩，时间和地点正确 |
| 这一天有什么规划？ | get_current_time、list_calendar_events | 沿用选中10月12日，返回答辩 |

五个场景均为model模式，日历结果没有被称为“模拟银行数据”。初轮联调验证脚本要求单次名称搜索后必须再取详情，模型已从唯一单次搜索结果正确回答，脚本却判失败；随后改为验证准确的搜索结果，重复活动仍要求实际发生详情，并明确提示词和协议中的这一语义，重跑五项通过。

## 交付边界

未进行浏览器自动化或截图验收，前端验证为构建；未运行全仓库回归。未重启现有服务或重建Docker。前后端更新运行方式见 [日历工具契约](../contracts/NOVA日历查询_API_v0.1.md)。

搜索为字面名称/地点匹配，默认详情重复未来30天，单次范围最多62天；查询无结果明确说明范围，不宣称用户一定空闲。备注可能需要分页读取。工具记录随响应返回，不持久化。
