# B3c：理财资金流水与账单统计边界

日期：2026-10-09。基准：最新02 v0.3 B3c，00第6.10节、F19、J19/J20。B分支独立实现和验证，A+B联合验收及02全任务仍未完成。

## 已实现

`bill_service.scope`是分析与报告明细共用的银行查询入口。按银行实际业务类型`investment_purchase/investment_redemption`排除理财；不按备注、客户文字、交易ID前缀或宽泛的purchase消费类型删除记录。当前／对比期聚合、净额、笔数、未知计数、分类／趋势、消费异常及其基线、分页明细、按tx_ids取证、事实报告和账单问答输出均使用该过滤。

普通消费、订阅、工资、退款、转入／转出、本人互转、未知类型、北京时间账期和账户权限沿用原规则。普通purchase即使备注写“理财”或category为investment，仍按普通消费规则处理；有真实银行理财业务类型的记录即使分类写错，也不能进入报告。海量聚合仍先按全账期计算，不取明细第一页或最近流水代替。

`BillAnalysis.excluded_transaction_types`明确排除范围。旧`investment_purchase_fen/investment_redemption_fen`兼容字段保留，schema限定为0，不代表原现金流水被删除。API、前端类型及[账单契约](../contracts/账单分析_API_v1.md)同步。

账单页面说明理财金额／笔数／明细不纳入报告，指标改“报告流入／报告流出／报告净流入”，明细改“报告内已完成明细”。“查看资金流水”跳到原转账页的最近流水列表。问答报告文字也注明不含理财；模型仍只负责查询参数理解，不生成统计数字。

## 银行流水核对

银行成交代码保持原实现，不为此阶段重写账本或补造理财事件：

| 实际阶段 | 已核对的流水行为 |
|---|---|
| 预览、受理冻结 | 不生成已完成现金扣款；受理有订单与现金／份额占用 |
| 撤单 | 释放原占用，无已完成扣款也不造退款 |
| 申购结算 | 一笔completed investment_purchase，金额为actual.gross_fen减actual.refund_fen，复用原operation_id唯一性 |
| 普通赎回定价 | 订单PENDING_CASH、持仓转应收；到账前不造现金入账 |
| 普通赎回到账 | 一笔completed investment_redemption，金额为actual.cash_fen；重复核对不再入账 |
| 原快速赎回 | 原同事务即刻到账及唯一流水保留，相关回归通过 |

`/api/v1/transactions/history`及本人单笔流水读取未应用账单过滤。权限和原普通／沙箱范围保持原状。当前银行真实生成的理财现金流水只有上述申购／赎回类型；没有新增分红／费用产品业务。未来实际新增类型时同步排除集合，而非按备注猜测来源。

## F19与验证结果

新增共享`tests/fixtures/wealth_portfolio_contract_v1/f19.json`：同账户已完成消费20000分、申购100000分、赎回到账110000分。数据是独立测试用虚构记录，不写入运行中的演示库，也不进入前端生产构建。

真实JWT／HTTP测试结果：资金流水仍有三笔，本人两笔理财单笔查询可读取；账单只有一笔消费、消费／流出20000分、流入0、净流入-20000分、未知笔数0，两个旧investment字段为0。给报告明细指定理财tx_id仍返回空；另一用户流水404；账单问答同口径，无理财本金金额。

分别对月／年、全部／餐饮范围，先读普通交易报告，再向当前／对比期增加大额理财交易；两份报告及明细完全一致，包括消费异常、类别变化、历史中位数提示、文本与笔数。原消费备注含理财词汇及原错误category不会误删。真实银行状态链验证冻结／撤单无流水、申购结算和普通赎回到账各一笔，原号重试仍一笔；费用与尾差按原实际成交金额。

2026-10-09命令（全部独立数据库）：

```powershell
.venv\Scripts\python.exe -m pytest tests/integration/test_wealth_bill_boundary.py tests/integration/test_bills.py tests/integration/test_wealth_quick.py -q --disable-warnings
cd frontend
node --experimental-strip-types --test tests/assetSummary.test.ts tests/wealthTables.test.ts tests/wealthProductSelection.test.ts tests/wealthAllocationReview.test.ts
npm run build
```

后端44通过／7跳过；PostgreSQL库未配置，7项跳过，不宣称双库已验。原账单已有601笔／分页、部分账期、普通收支、时区／闰年、异常、澄清／模型回退、权限及只读验证全部通过。前端31项通过，TypeScript／Vite生产构建通过；已有大chunk提示保留。不是全仓测试或旧基线失败的修复声明。

浏览器使用原张三演示库，重新登录并只读查询：2026-10普通消费5笔／¥533，指标与报告说明正确；点击“查看资金流水”能看到原消费、订阅、工资、转账记录。未向演示库添加F19理财记录、重置资金或提交交易；F19理财排除由隔离HTTP／银行测试核对。未通过页面问答向外部LLM发送数据。

截图：`D:/Project/Fintechathon-2026/.tmp/wealth-b3c-bills-20261009.jpg`。接口和页面本地重启可用，阶段版本只做本地提交。

## 待完成

B3d：实际收益服务和整体／单产品四粒度页面；B4：市值／比例赎回；B5：结构化动作及人工视图；B6：完整独立及A+B联合验收。赎回到账本金不计入账单，不等于已经实现实际收益统计；不能用投资净现金流冒充利润。
