# 全业务数据库统一到默认库（2026-10-25）

## 原因与最终状态

此前本机使用`docker-compose.yml`与私有`.deps/bank-runtime.override.yml`共同启动。后者选择`bank_local_wealth_20261017`，这是10月17日合并银行与理财数据后的整套应用账本，并非仅理财库。一次只使用基础Compose的重建误将后端切到`banking_agent`，导致多个场景显示的是旧默认库记录。该次操作没有删除原库持仓。

经用户要求，本次把完整原库直接更名为`banking_agent`，没有逐项拼接、重算余额或重写业务行。旧默认库先临时更名保留，待完整备份、全表比对与健康验证完成后移除。`bank_local_wealth_20261017`旧名称和本次临时恢复校验库均已不存在；其他历史测试库未操作。

本机现使用基础`docker-compose.yml`，不再依赖旧选库覆盖文件。旧覆盖配置已归档到本地备份目录；原JWT和内部银行凭据与不限问卷次数设置迁入不提交的`.env`。逐项核对原JWT_SECRET、INTERNAL_BANK_TOKEN与运行容器值一致。密码、MFA与原业务身份没有重设。

## 数据验证

- 停止前后端写入，分别保存原完整库与旧默认库的最终PostgreSQL自定义格式备份。
- 原完整库备份在临时数据库实际恢复成功。96张表的行数与全部行内容摘要一致，列结构一致，无缺失约束；两项CHECK约束经pg_dump恢复后的等价类型转换文本不同，已单独核对。
- 直接更名完成、服务启动之前，原库与默认库的96张表行数、全行摘要、列结构摘要和全部约束定义摘要完全一致。
- 服务启动后再比较：95张表完全一致，仅`audit_events`正常追加启动/健康审计，原业务表无变动。此比较在后续正常登录及只读接口验证之前完成。
- 全库保留全部用户、账户、流水、卡片、订阅、转账任务、NOVA历史及理财订单和持仓，没有重算余额或混入研究本金。逐表数量、客户标识和金额仅存本机私有核验文件，不纳入公开文档。
- 通过前端代理正常登录演示账号后，只读验证以下11个接口均200：账户、卡片、订阅、流水、收款、联系人、NOVA历史、账单基础资料、理财资产、资产汇总、组合资产快照。未进行验证性转账、申赎、问卷作答、订阅操作或工作区重置。
- `docker compose up -d --build --wait --wait-timeout 300 backend frontend`成功；前后端healthy，health=ok，portfolio_ready=true。运行引擎实际数据库为`banking_agent`。

## 队友的不限次数测试

按用户要求，只让现有`BANK_LOCAL_TEST_MODE`兼容默认`banking_agent`库名；保留SQLite及`bank_local_`库兼容，不增加新的本机开关。本人确认、MFA、风险、参数绑定、幂等和现金校验不变。

共享`docker-compose.yml`及`.env.example`已默认启用：

```dotenv
BANK_LOCAL_TEST_MODE=true
WEALTH_TEST_DISABLE_ASSESSMENT_DAILY_LIMIT=true
WEALTH_TEST_DISABLE_ASSESSMENT_YEAR_LIMIT=true
```

队友拉取源码后运行`start_bank.cmd`，或在仓库根目录按现有说明重建前后端，即可在自己的默认库重复做理财问卷测试，不需要本机私有覆盖文件或本机账本。若其现有`.env`明确设了上述任一选项为`false`，需要改为`true`；显式配置仍优先于Compose缺省值。设`BANK_LOCAL_TEST_MODE=false`可恢复原2次/日、滚动12个月8次限制。这是问卷重评次数设置，不是取消其他业务限额。

运行容器已核实daily_limit=0、rolling_year_limit=0。使用不带本机环境变量的Compose渲染验证上述三个共享缺省值均为true。隔离SQLite中的本机模式及订阅回归共16项通过。代码与共享配置尚未在此次操作中提交或推送。

## 私有备份与核验记录

全部保存在Git忽略目录`.deps/database_unify_20261025/`，不要提交其中的数据库或配置文件：

- `source-final.dump`：完整原账本最终备份；SHA256 `9dc35c2b2291ad037bd42991ccb7f207a7db03e08916b7df4aeb0a1ea1cec940`。
- `default-final.dump`：误连接的旧默认库最终备份；SHA256 `db671515d5afd595a98e574d44aea02584213fa4a1cf92b29a86daa8ce12e6a5`。
- `inventory-frozen.json`、`inventory-after-rename.json`：更名前后96表摘要及结构/约束摘要。
- `inventory-restored-backup.json`：备份实际恢复后的全表核验。
- `inventory-running.json`、`startup-table-differences.json`：启动后核验及审计追加差异。
- `api-smoke.json`、`runtime-secret-checks.json`：只读接口结果与凭据保持一致的布尔核验，不保存访问令牌。
- 本机环境与覆盖配置原件备份，用于必要时人工排查；不要恢复旧覆盖文件重新选择已退役库名。

备份包含切换时的历史快照；默认库产生新交易后，不得直接用旧备份覆盖，应先停写并核对新增账务。
