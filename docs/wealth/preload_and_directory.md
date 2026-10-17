> 当前部署口径（2026-10-17）：真实基金固定为900只（债券271、混合560、权益69），另保留19个模拟银行理财定义。旧全量目录、旧Release安装及手动dataset-key步骤已由随源码资料包和PostgreSQL增量升级替代，详见 `docs/setup/wealth_compact_upgrade.md`。下文旧阶段记录仅作历史证据，不代表当前产品数量或启动方法。理财订单/撤单页面已移除；后台成交及审计事实继续保留。

> 当前覆盖声明：真实货币基金已永久退出；本轮只使用13,578普通真实基金和原模拟银行产品，禁止按下方历史命令恢复全量采集/定时刷新。现行准备与数据包见portfolio_handoff.md。下方是历史研究工具记录，不代表当前比赛功能或可用数据范围。


# 全量基金目录与混合预加载池

2026-10-02，补充方案；数量以用户最新指令为准。44真实基金＋16模拟银行理财是可调整目标，不是搜索上限。配置见 `config/wealth/preload.json` 与 `bank_rules.json`。失效、身份变化、没有成功历史的基金不计入有效池。

## 身份、来源与交易边界

- 真实基金：AKShare 1.19.1／东方财富公开数据，代码、名称、基金类型来自全量目录。风险和完整规则未审核时仅研究；目录存在、申赎状态或历史净值均不能替代适当性或完整规则。
- 模拟银行理财：B001—B016，参考五份官方说明书规则；每个条目绑定单一模板。原始字段、未知值、文档指纹、历史到期日及模拟调整分别保存。虚构发行人、净值、账本、成交，不冒充原产品在售。
- 原合成60份额保留为离线回归和18种故障／业务场景，不混入真实基金池。旧订单与产品版本保留。新快照不是余额迁移。
- 关联份额按人工审核的目录份额名称记录家族证据；尚未核实的家族不宣称独立分散。银行模板家族也不作为16个独立风险源。

## 安装、获取和初始化

安装核心依赖及可选 `backend/requirements-market.txt`。Docker 可用 `docker compose build --build-arg INSTALL_MARKET_DATA=true backend`。以下从仓库根目录执行；部署镜像 `/app` 下用 `app.services` 替换 `backend.app.services`。数据库用本地有效 DATABASE_URL，密钥仅在忽略的环境文件。

```shell
python -m backend.app.services.bank.wealth_fund_cli directory --live
python -m backend.app.services.bank.wealth_fund_cli preload --live --rules
python -m backend.app.services.bank.wealth_fund_cli compose
python -m backend.app.services.bank.wealth_data_cli init --user-id U001 --dataset-key <compose返回的新版本>
```

本人在“资金上下文”选择新账户，研究页默认使用最新成功混合快照。切换只改变选择，旧预览失效；不复制余额或旧持仓。研究数据集和资金上下文可分别查看，交易须属于所选账本。修改配置后重新预取及compose；同版本不能覆盖不同内容。

## 检索、池外缓存和后台同步

JWT接口：POST `/api/v1/wealth/funds/search`（query、fund_type、page、page_size）；`/funds/detail`（六位code、fetch_missing）；`/funds/compare`（2—3个不同code、period）。代理只访问4号受保护服务。搜索代码、名称不限预加载池；池外自然语言查询与比较使用同一缓存事实服务，不能签发授权或执行交易。

显式 `WEALTH_FUND_ON_DEMAND_ENABLED=true` 后，首次详情在后台获取。历史／货币收益TTL一天、状态六小时、费用七天；每进程最多2个获取任务，同一代码和类型以数据库唯一领取及30分钟租约去重。最多2次上游尝试、每次进程硬截止90秒；失败退避5分钟并保留成功缓存。多实例部署需另外配置统一全局并发预算，当前上限为每进程。

日更：设置 `WEALTH_MARKET_SYNC_ENABLED=true`，运行：

```shell
python -m backend.app.services.bank.wealth_fund_cli worker --live --interval 3600
```

目录每ISO周领取一次；预加载池每日领取一次，费用仍遵守七天TTL。跨进程数据库作业租约防重复；`--once`用于单轮检查。原宏观worker继续负责八项宏观序列；不要把历史宏观成功记录当作本轮新增目录验收。

## 单位和适配限制

货币基金使用万份收益（元/万份）、七日年化（百分比），不伪造单位净值或多年历史。原AKShare货币接口遍历全部20行分页，实测超时；采用同一东方财富端点、两个20行分页，近期最多40点，适配版本单独标记。普通基金使用单位净值，未核实分红不宣称总回报。

最新费率页把“申购费率（前端）”标题改为“申购费率”；保留同源原始表格标题和字段，未知结构／缺表保持失败或PARTIAL，不补零。说明书未知浮动管理费、巨额赎回等不能静默拼接其他产品规则。模拟调整在页面展开区列示。

## 可复现证据

本轮真实目录28,013行，3条类型空值标为未知；数量随来源变化。最终产品、规则与验收结果见补充进度及验证记录。测试使用隔离SQLite及PostgreSQL schema，真实模型测试显式开关；旧323项成功仅证明旧开发阶段。
