# 现有定时转账与订阅：接入说明

更新：2026-10-06，基于main 07287a6的保护修复，按功能分支提交main PR交接。只维护已有功能，不新增订阅自动识别、续费提醒或自动到期扣费，也未开始统一Agent联调。

## 一次性定时转账

1. 主Agent澄清未来绝对时间和时区，查询本人账户与唯一收款账户。现有即时转账messages入口仍不支持定时请求。
2. POST `/api/v1/tasks/transfers`，传request_id、operation_id、from_account、to_account、amount（元字符串）、scheduled_at（带时区ISO8601）及可选备注。只创建计划，不扣款。
3. 可信页面展示confirmation_card；本人确认调用confirm，红色任务再走TOTP/短信verify。每一步使用返回的最新state_version/plan_version。
4. WAITING_EXECUTION表示预约已确认，不表示转账成功。到期由现有进程内扫描器执行；提前调用accept返回409 SCHEDULE_NOT_DUE。关闭后端时不会执行，恢复后补扫到期等待任务。
5. 按task_id查询状态；UNKNOWN用原operation_id核对，不换号重转。取消/修改仍沿用现有接口，修改需重新确认；当前modify不支持改时间。

## 订阅查询与取消

所有接口使用本人Bearer JWT，主Agent不访问银行ORM。

- GET `/api/v1/subscriptions`：列表事实；ACTIVE项目可解释下次扣费日期。
- POST `/api/v1/subscriptions/{id}/cancel-preview`：无请求体，返回最新subscription、confirmation_token、expires_at、message。不会修改订阅和余额。
- 可信确认页面显示商户、账户、金额、下次扣费与取消影响。本人点击“确认取消”后，POST `/api/v1/subscriptions/{id}/cancel`，传 `{"state_version":当前版本,"confirmation_token":"预览返回的凭证"}`。
- 凭证5分钟有效，绑定登录主体、订阅、版本、扣费账户、商户、金额、币种、周期和下次日期。缺字段返回422；无效/过期/绑定不匹配返回409 CONFIRMATION_INVALID；服务层缺凭证返回CONFIRMATION_REQUIRED。
- 已取消且同一有效凭证重试返回当前状态，不重复取消或删除历史流水。超时先查列表；凭证过期或订阅更新时重新打开预览并确认。
- 凭证使用独立签名域，不可作登录JWT。它是版本绑定的操作凭证，不是MFA或“真人证明”；主Agent只获取事实/展示卡，凭证和执行动作保留在可信确认控制器，不能同时开放预览签发与取消执行给模型自由调用。

## 模拟扣费

保留原simulate-charge入口、确认页面与幂等键重试，供手动测试。它真实扣减模拟余额、写流水、把下次日期推进一个月，不检查是否到期；不映射为主Agent自动到期扣费工具。本轮不增加订阅识别、提醒、周期调度或改变其演示语义。

## 证据与运行

[验证记录](../validation/scheduled_subscription_guards_20261005.md)。本地实际运行前端 http://127.0.0.1:3000，后端/Swagger http://127.0.0.1:8010/docs；8000在此机被拒绝绑定。Vite代理支持BANK_API_BASE_URL，未设置时仍默认8000。前后端必须同时更新，旧取消客户端需补confirmation_token。
