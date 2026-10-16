# 账单分析：实现与接口契约

2026-10-14增强已加入消费追溯、订阅核对和现金流情景；扩展契约见[账单洞察与现金流 v2](账单洞察与现金流_API_v2.md)。

## 范围与入口

登录后首页的“账单分析”提供月报、年报、分类占比、消费趋势、上期对比、三类待核对提示和分页流水。点击分类或异常“查看依据”下钻。页面内有独立账单问答入口，未指定的条件沿用筛选器。银行操作成功后重新读取分析数据。

接口需 Bearer 登录态，从认证上下文取得 user_id，仅访问所属账户。其他人的账户返回 404。AI 通过银行服务查询，不直接读写银行 ORM。账单查询不创建转账任务、授权或扣款，无需二次确认。

## 统计口径

- 仅 completed 流水计入统计和分析明细；pending/failed/cancelled 不计入。
- 金额字段为整数分，CNY，不使用浮点金额求和；展示时转换为元。
- 报告流入：salary/transfer_in/refund；报告流出：purchase/subscription/transfer_out。
- 理财申购／赎回（investment_purchase/investment_redemption）在银行账单公共查询入口排除：不参与本期或对比期的金额、净额、笔数、未知笔数、分类、趋势、异常、分析明细及报告文本。原资金流水history／单笔读取保留真实记录。以银行业务类型识别，不按备注关键词或把所有purchase消费一并排除。
- 消费原额：purchase + subscription；净消费：消费原额减本期退款，允许为负。退款无原交易关联，不抵扣分类或历史消费；分类占比分母为消费原额。
- 订阅交易归入订阅类别，未知消费类别归入其他。未知交易类型只计笔数，不猜测收支方向，并提示限制。
- 全账户汇总排除能由对手账户 ID 确认的本人互转；单账户保留。历史对手仅为姓名时不推断归属。
- 分类筛选只选择该类消费，流入与退款为零；页面明确展示“某类消费”。
- 存储 UTC；遗留无时区时间按 UTC 解读。历史种子时间若实际为本地时间，需另行审计修复，分析模块不猜测修改。按 Asia/Shanghai 划分左闭右开账期。
- 完整月对比完整上月，完整年对比完整上年。未结束账期截至生成时刻，与上期同月日／同日时刻比较；超出上期月末或闰日时夹取最后有效日期并保留时刻。页面展示实际对比区间。
- 基期消费为零时 change_percent=null，仅展示金额变化。
- 数据库按类型、类别、日／月聚合，不受明细分页或原 history 接口的 500 笔上限影响。

## API

### GET /api/v1/bills/analysis

参数：period=month|year（默认 month）、date=YYYY-MM|YYYY（默认当前月／年）、account_id（可选）、category（可选 dining/shopping/transport/entertainment/subscription/other）。年份从 2000 起，不允许未来账期。

返回 query、excluded_transaction_types、start/end_exclusive、previous_start/previous_end_exclusive、as_of、partial、timezone/currency、totals/previous_totals、change_fen/change_percent、categories、trend、anomalies、report、notices。excluded_transaction_types当前为investment_purchase和investment_redemption，指整个报告的排除范围。

totals 含 inflow_fen、outflow_fen、net_inflow_fen、consumption_fen、refund_fen、net_consumption_fen、transfer_in_fen、transfer_out_fen、salary_fen、count、consumption_count、unknown_count。分类含金额、笔数、占比、上期金额、差额、增长率。趋势对区间内无消费的日／月补零。

为旧客户端保留investment_purchase_fen／investment_redemption_fen，schema限定为0，明确不参与账单报告；这不表示原资金流水不存在或已删除。前端以“报告流入／报告流出／报告净流入”“报告内已完成明细”区分全资金流水，并提供原流水入口。

### GET /api/v1/bills/transactions

相同筛选参数，另加 page>=1（默认 1）、page_size=1..100（默认 20）、tx_ids（可选，逗号分隔的异常证据 ID）、as_of（可选，带时区且不晚于现在）。前端传分析结果的 as_of，使明细使用相同时间上限。

返回 items/total/page/page_size。按 timestamp、tx_id 倒序稳定排列。tx_ids 仍受用户、账户、账期、业务类型排除和分类过滤，不会绕过权限或让理财记录回到报告明细。过滤在计数／分页之前完成。

### POST /api/v1/bills/messages

```json
{"message":"这个月为什么比上个月花得多","context":{"period":"month","date":"2026-09"}}
```

返回 kind=analysis|clarification、mode=offline|model|fallback、message 和可选 analysis。报告由后端统计事实生成，不让模型自由编造金额或因果关系。

支持“上个月餐饮花了多少”“2026年9月账单”“今年年度账单”“有没有疑似重复扣费”。缺失条件继承 context；多时间范围、多分类、账户描述不明确及写操作要求改写／使用筛选器。每次请求独立，暂不持久化账单对话。

## 异常规则

集中配置在 `services/bank/bill_service.py::AnomalyRules`，默认：

1. 大额：单笔 >= 1000 元；或上期至少 5 笔消费、本笔 >= 100 元且达到上期消费中位数 3 倍。返回流水 ID 和阈值依据。
2. 疑似重复：本期内同账户、同交易类型、同商户文本、同金额的相邻消费相隔 <= 300 秒。返回两笔 ID 和时间差。商户文本不同或跨账期重复不在当前覆盖范围。
3. 类别增加：上期该类非零、差额 >= 200 元且增长 >= 50%。返回当前和上期金额，可下钻本期分类。

这些是待核对提示，不是欺诈或重复扣款结论。历史不足不执行中位数规则。异常证据目前读取所选账期消费记录；特别大的数据量后续需索引、流式扫描及异常结果分页。

## 模型设置与降级

账单与转账、理财共用 `LLM_*` 和统一客户端。默认 `LLM_MODE=auto` 有有效密钥时启用模型，无密钥离线；`LLM_MODE=offline` 全局关闭外部模型。旧 `BILL_NLU_MODE` 不再生效，旧密钥组仅在全局密钥未配置时作为统一配置的过渡兼容。迁移和开发接口见[统一模型配置](../setup/model_configuration.md)。

模型仅收到本次问题和提取 schema，不收到登录凭据、余额、历史流水或报告。用户自行写在问题中的信息会随问题发送。输出必须逐字来自原文并通过本地规则校验；异常、超时、未配置时回退离线并标注。当前模型增强支持范围仍以月／年、已知消费分类为限。

## 独立演示数据

不自动扩充已有数据库。以下命令只允许新建不存在的 SQLite 文件：

```bash
.venv/bin/python scripts/create_bill_demo.py --output /tmp/bill-demo.db
DATABASE_URL=sqlite+aiosqlite:////tmp/bill-demo.db .venv/bin/python -m uvicorn backend.app.main:app --env-file .env --host 127.0.0.1 --port 8000
```

登录张三，选择 2026-09 或 2026 年。样例覆盖 2025-09、2025-12、2026-01、2026-08、2026-09，含分类增加、重复订阅、大额消费、退款、本人互转。样例均有虚构备注；演示初始余额不用于与合成历史对账。脚本打印完整 2026-09 的预期统计。

## 验证与限制

```bash
.venv/bin/python -m pytest tests/integration/test_bills.py -q
.venv/bin/python -m pytest tests -q
cd frontend && npm run build
```

覆盖超过 500 笔、整数分、金额分解、退款超过消费、本人互转、未知类型、非完成状态、用户隔离、北京时区边界、闰年、月末夹取、零基期、分类下钻、异常证据、模型输出校验与回退、接口鉴权和只读性。PostgreSQL 聚合分支需在可用 PostgreSQL 环境单独验证，不将 SQLite 测试视为 PostgreSQL 验证。

as_of 是交易时间上限，不是不可变账本快照；回填或更正数据会改变再次查询结果。暂不含导入、导出、持久化报告、实时欺诈检测。

2026-10-09理财B3c变更与验证见[阶段记录](../wealth/manual_flow_b3c.md)：F19资金流水保留三笔，账单只计一笔消费；同时核对受理／撤单无完成流水、真实结算／到账原号幂等。后续如银行实际新增分红／独立费用交易类型，需同步公共排除集合和契约；当前没有为统计补造此类业务。
