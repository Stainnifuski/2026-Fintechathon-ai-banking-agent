# 日历事件与 ICS 导入 API v0.1

所有接口要求用户 Bearer JWT，只操作当前登录用户的日历。NOVA 已接入本人日期查询、活动名称/地点搜索和发生实例详情，接入协议见 [NOVA日历查询](NOVA日历查询_API_v0.1.md)。

## 手动事件

`POST /api/v1/calendar/events`，成功返回 201 和事件。

```json
{
  "title": "取蛋糕",
  "starts_at": "2026-10-02T09:30:00+08:00",
  "ends_at": null,
  "location": "甜时光蛋糕店",
  "description": "带上订单号"
}
```

`ends_at=null` 表示时间点；时间段要求结束时间严格晚于开始时间，支持跨日。时间须带时区，服务端按 UTC 保存并返回。名称必填且最多 200 字符，地点最多 500 字符，备注最多 10000 字符。

## 查询与删除

- `GET /api/v1/calendar/events?start=<ISO8601>&end=<ISO8601>&time_zone=Asia/Shanghai`：按开始时间排序。查询窗口为 `[start,end)`，包含与窗口相交的时间段；时间点须落在窗口内。范围大于零且不超过 62 天。
- `DELETE /api/v1/calendar/events/{event_id}`：返回 204。删除重复日程时删除整个系列。其他用户的事件和不存在的事件均返回 404。

每项包含 `event_id`、名称、备注、地点、开始与结束时间、`all_day`、`recurring` 和 `source`（`manual/ics`）。重复日程的各次发生共享 `event_id`；前端用 `event_id + starts_at` 区分列表项。最多返回 3000 条，超限返回 422 并要求缩小窗口。

全天事件的 ISO 日期部分是原日历日期，前端不能按设备时区转换其日期；结束日期为不包含的边界。定时事件按用户时区显示。

## ICS 上传

`POST /api/v1/calendar/import`，multipart 字段：

- `file`：UTF-8（允许 BOM）`.ics` 文件，非空且最多 2 MB。
- `time_zone`：无时区时间的默认 IANA 时区，浏览器提供，缺省为 UTC。

支持 VEVENT、文本转义与折行、DTSTART／DTEND、DURATION、全天日期、UTC、TZID／VTIMEZONE、常见 RRULE、RDATE、EXDATE 和 RECURRENCE-ID 例外。有效 `X-WR-TIMEZONE` 优先用于文件内的无时区时间。重复日程保留原规则，在查询窗口内展开，不截断为固定年度的静态事件。

响应：`{"created": 3, "updated": 1, "skipped": 2, "total": 6}`，按独立事件／重复系列计数。同一用户以 UID 去重，相同内容跳过，修改内容更新原事件；无 UID 时用组件摘要去重。不同用户各自导入。文件内同 UID 的原事件与例外组成一个系列。

整份文件先解析和校验，再通过单事务写入；任一事件格式错误，整份文件返回 422，不部分导入。空文件或过大文件返回 413，并发导入冲突返回 409，可重试。单次最多 1000 个 VEVENT 组件。

支持每日、每周、每月、每年重复；不支持秒／分钟／小时重复，以及 BYHOUR／BYMINUTE／BYSECOND 各自设置多个时刻的规则。日历不自动发送提醒、不触发购买或银行操作。

解析采用 [icalendar](https://icalendar.readthedocs.io/en/stable/usage.html) 和 [recurring-ical-events](https://recurring-ical-events.readthedocs.io/en/stable/user-guide/examples.html)，日期边界遵循 [RFC 5545](https://www.rfc-editor.org/rfc/rfc5545.html)。
