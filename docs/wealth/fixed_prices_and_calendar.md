# 固定模拟价格与中国日历

## 数据边界

`config/wealth/data/manifest.json`记录78条现有模拟产品（S001–S060、B001–B018）的数据/生成/日历版本、seed、成立日、首末日、行数、家族、单一说明书模板及历史指纹。最终8位字符串净值已固化，银行产品年度费率调整也已包含；不是在每次启动重新随机生成。

范围为2021-10-01至2027-12-31；晚成立条目从其模拟成立日开始。静态未来模拟价格仅保存于后端配置，不能提前通过API、候选、图表或计价使用。每条价格在相应净值日北京时间18:00可用，保存/传输为UTC。年度费用已含NAV，申赎费按原账务单独算；不重复扣年度费用。模拟因子反映类别差异，不拟合真实产品、不是预测。

运行时`synthetic_payload()`和`bank_products()`验证固定文件SHA-256并读取，缺失或内容变化失败，不调用生成器补造。已有批次按原import_dataset指纹规则重复导入写入0，同标识异内容拒绝。旧账务引用的旧产品版本不覆盖；新S规则使用version2，新混合池按现行compose规则创建银行版本。

显式开发生成命令（不要放启动脚本）：

```bash
python -m scripts.generate_wealth_calendar
python -m scripts.generate_wealth_fixed_prices
```

更改生成规则/来源必须创建新数据与日历版本，不能用相同版本覆盖已发布历史。旧legacy普通账户保持原规则和旧流水；本轮新静态集合为任务书现有S/B集合。

## 日历依据

`calendar_sources.json`保存来源和逐年休市区间；`data/calendar.json`每日一条，共2283天。2021–2026按中国大陆交易所公告，周六日始终休市，调休上班周末不作为交易日。

- [上交所2021](https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20201224_5286951.shtml)
- [上交所2022](https://www.sse.com.cn/disclosure/announcement/general/c/c_20211220_5662606.shtml)
- [上交所2023](https://www.sse.com.cn/disclosure/announcement/general/c/c_20221227_5714458.shtml)
- [上交所2024](https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20231226_5733941.shtml)
- [上交所2025](https://www.sse.com.cn/disclosure/dealinstruc/closed/c/c_20241223_10767110.shtml)
- [上交所2026](https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml)，[深交所2026五一交叉核对](https://www.szse.cn/disclosure/notice/general/t20260423_620142.html)

截至本轮查询，2027尚未作为官方市场日历使用。农历/节气日期依据[香港天文台2027对照](https://www.hko.gov.hk/en/gts/time/calendar/text/files/T2027e.txt)，项目预测休市：春节除夕起9日、五一5日、国庆7日、其余节日3日；各日标记`predicted_simulation`，不是官方安排。官方公布后创建新版本，不覆盖既有订单绑定日历。

新模拟产品普通申赎统一中国交易日/T+n作为模拟规则，非真实发行人承诺；B017自然日快赎、B018交易日快赎沿用原窗口/端点、自然日额度及原号保护。日历覆盖外明确失败，不回退到工作日猜测。

## 当前验证与限制

固定文件与日历已实现并验证；运行库备份、本人旧轮次清理和20,000元空持仓新基线尚待单独执行。不会因启动自动清库或替换他人数据。
