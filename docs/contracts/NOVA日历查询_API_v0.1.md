# NOVA 本人日历查询 API v0.1

在现有 NOVA Agent Loop 中增加日历范围查询、活动名称/地点搜索、活动及实际发生次数详情。仅查询本人已保存日历，不创建、修改、删除、导入、预约或发送提醒。

## 页面上下文

`POST /api/v1/nova/chat` 保持既有 message/history/Bearer JWT，page_context 在生活服务页面新增 calendar：

```json
{
  "message": "这个活动什么时候举行？",
  "page_context": {
    "version": 1,
    "page": "thirdParty",
    "calendar": {
      "visible_month": "2026-10",
      "selected_date": "2026-10-15",
      "timezone": "Asia/Shanghai",
      "selected_event": {
        "event_id": "CE123",
        "starts_at": "2026-10-15T11:00:00Z"
      }
    }
  }
}
```

calendar 仅允许在 thirdParty 页面传递。visible_month/selected_date 验证真实月份/日期；timezone 验证 IANA 时区；selected_event 可省略，event_id长度1–40，starts_at须带时区。拒绝 user_id、余额、角色等额外字段。

前端在“生活服务 → 日历”选择日期时更新上下文；活动行“选择这个活动”同时选择 event_id 和实际 starts_at，重复系列共享ID，不能只用ID区分各次发生。切换日期清空活动选择，切换花店/蛋糕店或退出登录清空当前日历上下文。刷新、导入或删除后，只保留仍存在且属于当前选中日期的发生实例。

“这天”沿用 selected_date；今天/明天/下周依据现实时间和查询时区计算，与页面选中日期区别处理。前端上下文只用于理解，不作为事实或授权；服务端独立注入 ToolContext 身份并校验归属。

## 工具

### list_calendar_events

参数：start_date/end_date（YYYY-MM-DD，可省略），timezone（可省略），page（默认1，1–10000），page_size（默认20，1–50）。

查询 `[start_date 00:00, end_date 00:00)`，结束日期不含当天，瞬时时长大于零且不超过62天。省略开始日期时取页面 selected_date，无日历上下文时取现实今天；省略结束日期时取开始日期的下一天。默认页面时区，否则北京时间。

复用现有 events.list_events，包含重叠窗口的时间段、窗口内时间点、全天与重复日程例外。最多展开3000项，超过时要求缩小窗口；工具分页不会悄悄把前20项当成完整总数。

### search_calendar_events

参数：keyword（去首尾空白后1–200字符），page（默认1），page_size（默认10，1–20）。

在本人全部保存记录中按名称或地点的字面包含搜索，不限日历当前月份，也不限未来30天。ICS 中改期实例的标题和地点也参与匹配。大小写通过casefold归一化，不使用模型构造事件或模糊向量检索。结果按原始开始时间和ID排序分页。

返回候选事件/系列，包含 recurring、time_meaning。重复系列时间标记 series_original_start，不能当成“下次”；普通活动标记 event_start。唯一非重复匹配的已保存时间可直接回答；重复日程的实际发生时间及长备注需 get_calendar_event。多个匹配先展示候选询问，不任意选一个。

### get_calendar_event

参数：event_id（可省略），occurrence_start（带时区实际开始时间，可省略），start_date/end_date/timezone，page/page_size，以及 description_offset（默认0，0–10000）/description_limit（默认2000，1–4000）。

- 不传 event_id 时使用页面 selected_event；未选中则返回 MISSING_SELECTION。
- 不传ID、不指定范围/发生时间时同时沿用页面选中的实际 starts_at。显式指定ID时不沿用其他活动选择。
- occurrence_start 指定某次实际发生时间，精确匹配；已取消或旧改期时间不能继续查询成功。该参数优先于日期范围。
- 无明确发生时间或范围：非重复事件查其原始**显示时区日期**，即使是历史或远期也能取得；全天事件用原日历日期。重复系列默认查询现实今天起未来30天，过滤已开始的定时事件，返回最早未来实际发生次数及后续实例。
- 默认未来窗口无发生时不宣称活动不存在；可指定其他不超过62天的窗口继续查。显式范围含全部与范围相交的实例，不自动过滤为未来。
- 全天开始/结束日期保留原日历日期，结束日期不包含；不用设备时区移动日期。跨日定时事件保留完整时间段。夏令时按IANA时区转换。
- 详情备注可分段读取，避免一次向模型发送10000字符。列表/搜索仅返回前400字符摘要并标记是否还有内容。

未知活动和其他用户活动统一返回 CALENDAR_EVENT_NOT_FOUND；时间范围等业务约束返回 CALENDAR_QUERY_INVALID；非法Schema参数返回 INVALID_ARGUMENTS。模型不能传user_id，缺少身份上下文不执行查询。

## 结果与来源

日历工具不使用模拟银行金额/数据标识，结果格式为：

```json
{
  "source": "calendar.events",
  "data_kind": "user_calendar",
  "timezone": "Asia/Shanghai",
  "as_of": "2026-10-10T18:15:00Z",
  "data": {
    "items": [],
    "total": 0,
    "page": 1,
    "page_size": 20,
    "has_more": false,
    "query": {
      "start": "2026-10-15T00:00:00+08:00",
      "end_exclusive": "2026-10-16T00:00:00+08:00"
    }
  }
}
```

其他source为 calendar.search/calendar.event。items 保留事件视图，增加 occurrence_key（ID+实际开始时间）、定时事件本地时间或全天日期、备注偏移/总长度/是否还有内容。搜索提供keyword及search_scope；详情提供系列名称、重复标识、实际查询范围和upcoming_only。

总数是本次窗口内实际发生次数（搜索则是匹配保存记录数），不是全部年份活动数；has_more描述当前分页，备注的description_has_more独立标记。不同工具按查询时读取，不保证与页面已加载内容为同一快照。

名称、地点、备注可能来自用户输入或ICS，作为数据处理，不能成为模型指令。不把“没有已保存日程”说成“用户确定空闲”。完整对话、预算、循环限制、错误HTTP行为沿用 [NOVA只读工具契约](NOVA页面上下文与只读工具_API_v0.1.md)。

## 离线与体验

有限离线规则支持：今天/明天/后天、指定日期、选中日期、本周/下周，活动名“什么时候举行”和选中活动详情。规则和模型均调用同一只读工具；同名多项提示选择。复杂日期表达、跨轮语义和更多筛选依赖模型，不声称离线支持全部语言。

打开 NOVA 可直接问“明天有什么规划？”“项目答辩什么时候举行？”“下次团队例会什么时候举行？”。在生活服务日历选择日期或活动，再问“这一天有什么规划？”“这个活动在哪里？”。NOVA保持并排面板，能继续操作日历。

没有数据库迁移。源码运行重启后端并运行前端开发服务；Docker更新：

```bash
docker compose up -d --build backend frontend
```

现有日历功能见 [日历与ICS契约](日历事件与ICS导入_API_v0.1.md)，验证见 [本轮验收](../validation/nova_calendar_20261010.md)。
