# v1.2当前数据入口

最新成功混合快照是默认研究池。目标44真实基金＋16官方规则参考模拟理财，可调整；目录不限数量。当前检索、缓存、规则来源、初始化和新日更请使用[全量目录与预加载指南](preload_and_directory.md)。以下固定合成池及10基金/宏观适配继续保留用于离线回归、历史研究与独立宏观同步。来源统计的日期不同，不要求行数保持一致。

# 理财 v1.2 数据来源与同步

## 边界与版本

本项目用于学习研究与比赛演示。虚构产品、模拟行情与外部真实基金数据分开标识。全部申赎由模拟银行执行；点击授权不验证真实身份。项目不代表银行、管理人或数据提供方背书。

离线回归 `synthetic-v12:20260930-factor-v1`，seed=20261001，模拟基准日2026-09-30；60份额、30家族，旧8条在legacy上下文保留。共同权益/债券/信用/流动性因子；A/C共享家族扰动，年度费用已含净值。日历`synthetic-weekdays-v1`仅模拟周一至周五，不声称真实中国节假日。未来预生成净值按业务时间和18时首次可用规则释放。

货币类万份收益来自每日模拟收益；七日年化为最近7个模拟营业日观测的简单年化，前6条为空。计价采用净值等价模型，不实现真实货币基金份额结转。

## 固定适配与字段

可选依赖`backend/requirements-market.txt`固定AKShare 1.19.1。原始数据与索引留在忽略的`.deps`，不默认公开分发。代码许可证与上游数据权利不同。

| 接口 | 字段与用途 | 保守处理 |
|---|---|---|
| fund_name_em | 基金代码/基金简称/基金类型 | 代码为字符串，保留前导零 |
| fund_purchase_em | 当前状态完整快照 | 未知不当无限额；不回填历史状态 |
| fund_open_fund_info_em 单位净值走势 | 净值日期/单位净值 | 排序、正值、重复日期；首次获取时间不伪装发布日期 |
| macro_china_lpr | TRADE_DATE/LPR1Y/LPR5Y | 1年、5年分别入库 |
| macro_china_cpi / ppi | 月份/全国-同比增长 / 当月同比增长 | percent，缺值不补零 |
| macro_china_pmi | 月份/制造业-指数/非制造业-指数 | index，不称交易信号 |
| macro_china_supply_of_money | 统计时间/M1及M2同比增长 | 标记M1-definition-2025口径断点 |

来源：[AKShare基金文档](https://akshare.akfamily.xyz/data/fund/fund_public.html)、[宏观文档](https://akshare.akfamily.xyz/data/macro/macro.html)，源站东方财富。核验家族000003/000004中海可转债A/C；其余未经核验的家族为空。真实产品缺风险/全费用/持有/结算规则，当前仅研究展示，不生成订单。未核实分红时只称单位净值变动，不冒充总回报；000001累计净值（6017行）与分红详情（25行）已实测支持，但未用于总回报重建。

## 管理员导入与日更

在仓库根目录、已配置DATABASE_URL的环境运行。先停止写入、备份、迁移；管理员操作不能进入模型工具。

```bash
python -m backend.app.services.bank.wealth_migration
python -m backend.app.services.bank.wealth_data_cli init --user-id U001
python -m pip install -r backend/requirements-market.txt
python -m backend.app.services.bank.wealth_data_cli probe --live --timeout 60
python -m backend.app.services.bank.wealth_data_cli sync --live --timeout 60
# 环境设 WEALTH_MARKET_SYNC_ENABLED=true 后，独立前台进程长期运行：
python -m backend.app.services.bank.wealth_data_cli worker --live --timeout 60 --interval 3600
```

worker每小时检查，UTC每日一个job；数据库唯一主键和120分钟租约防重复，失败/过期租约重启可恢复，成功日不重复。默认关闭网络；同步不在聊天请求中运行。停止按Ctrl+C，重启相同命令。每接口硬截止、低并发1、最多3次指数退避、3次连续失败触发60秒熔断。失败保留旧快照，job记录FAILED及失败类型；没有新观测不补数据。

sync落库`live_sync`；probe只生成本地snapshot报告。每次版本不可变，指纹重复不重写；修订需要新版本。研究API返回数据集获取时间、来源、净值日期；过旧行情不能据此承诺最新价格。

真实研究沙箱可在已有数据集导入后显式`init --user-id U001 --dataset-key 数据集键`创建，缺完整审核规则仍不可交易。

CSV模板见`import_template.csv`。CSV仅研究，不能通过上传伪造审核规则。JSON结构见生成器/探测报告，schema_version=1，products包含身份、source和history(date/nav/published_at/available_at)。最大25MB，受控目录默认`.deps/wealth-import`，禁止目录外文件和任意URL。逐产品错误返回row/code；任何错误整批不写，dry-run不写。

```bash
python -m backend.app.services.bank.wealth_data_cli import --file example.csv --dry-run
python -m backend.app.services.bank.wealth_data_cli import --file example.csv
```

本轮实际成功和早期失败见[验收记录](../validation/wealth_v1_2_20261002.md)。
