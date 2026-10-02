# 历史开发输出清理与干净复现（2026-10-02）

功能提交：`f178598`、`d448bfc`；文档提交：`fb0ede1`。清理为后续独立提交，不改写历史。本记录与 [本轮验收](wealth_v1_2_supplement_20261002.md)共同作为当前入口。

## 分类清单和实际处理

| 分类 | 处理 |
|---|---|
| 明确生成的测试输出 | 删除旧截图、重复模型JSON、迁移测试副本、重复目录JSON、过期探测JSON、Python字节码及pytest文件缓存 |
| 失败或误选来源 | 删除WAF挑战HTML、客户权益PDF、非说明书季度报告及派生txt；未把它们当有效规则 |
| 一次性探测/旧规则生成辅助 | 删除补充阶段临时助手脚本；最终配置、正式provider/CLI/生成器及测试保留 |
| 必要代码和最小样本 | 保留全部测试、18场景生成器、5条新规则评测、最小实际基金响应；迁移副本指纹脚本及摘要保留本地 |
| 有效来源 | 保留5份官方PDF/HTML及指纹配置、最新真实基金目录/缓存库、最终质量报告、8宏观来源库、最终成功同步报告 |
| 当前运行数据 | 保留.env、演示env、现用SQLite、PostgreSQL和wealth-v12-demo卷、当前frontend/dist和必要依赖；不重播种账本 |
| 用途不明/回退备份 | 保留bank-readiness的dump、local-update的env/dump/extra-old-backend.db、startup旧账本、demo-preserved.db及before-v12停止容器；待所有者明确用途再处理 |

脚本 `scripts/cleanup_development_outputs.ps1` 默认只列清单，`-Apply`仅删经绝对路径验证的明确普通文件；不递归清空.deps或删除数据库卷。两轮实际删除46＋174＝220个文件，共23,846,095字节。另删除before-supplement和failed-v12两个停止容器元数据，未使用-v或prune。当前运行容器和账务卷不受影响。

## 测试环境规范

所有账务测试使用临时SQLite或独立、明确命名的bank_test_ PostgreSQL库，各测试使用唯一schema并在finally清理；禁止连接运行账本。真实市场与付费模型仅opt-in，在专用研究/演示库使用虚构用户和资金；失败明确写入来源状态，不能用fake结果代替实际联网。

干净复现：新Docker容器，源码只读挂载，PYTHONPATH仅/workspace，关闭字节码及pytest缓存，不把历史.deps用于运行或导入路径，不挂载任何运行账务卷。数据库使用独立测试库；前端--no-cache构建从package-lock重新npm ci，不使用旧node_modules/dist。

```powershell
./scripts/cleanup_development_outputs.ps1       # 查看
./scripts/cleanup_development_outputs.ps1 -Apply
docker run --rm --network 2026-fintechathon-ai-banking-agent_banking-net `
  -v "${PWD}:/workspace:ro" -w /workspace -e PYTHONPATH=/workspace `
  -e PYTHONDONTWRITEBYTECODE=1 -e DATABASE_URL=sqlite+aiosqlite:////tmp/wealth_test.db `
  -e BANK_TEST_DATABASE_URL=postgresql+asyncpg://bankuser:bankpass@banking-postgres:5432/bank_test_wealth_20260930 `
  2026-fintechathon-ai-banking-agent-backend python -m pytest tests -q -p no:cacheprovider --tb=short
docker build --no-cache --target build -t wealth-v12-clean-frontend frontend
```

后端容器镜像含已安装的requirements.txt依赖，未声称本次再次从公网安装后端全部依赖；不依赖本地Python包缓存。源挂载中.deps仍为忽略目录，但不在Python导入路径，测试不读取其中业务状态。

## 历史摘要索引

以下日期/commit为记录所在阶段入口，具体命令、依赖、覆盖及跳过见各文件。旧摘要保留，不能证明当前版本通过。

| 入口 | 版本/阶段与commit | 状态 |
|---|---|---|
| deepseek_transfer_smoke_20260922.json | 转账模型探测，c6ceba8，2026-09-22 | 历史真实模型记录；不等于本轮理财模型 |
| phone_note_bank_20260927.md | 手机号/备注银行，9ca6305，2026-09-27 | 原业务保留，本轮全量回归覆盖 |
| bank_role4_readiness/completion_20260928.md | 4号准备度与整改，28afb34，2026-09-28 | 准备度缺项由completion替代；原断言保留 |
| bills_20260930.md | 账单，bc10571，2026-09-30 | 旧模块证据，本轮全量回归覆盖 |
| wealth_20261001.md | v1.1完整理财，fb1f9be，2026-10-01 | 原事务规则保留，当前功能扩展 |
| wealth_authorization_demo_20261001.md | 统一模拟授权，077ccd2，2026-10-01 | 仍保留本人确认及模拟授权边界 |
| wealth_v1_2_20261002.md | v1.2合成池阶段，82a9629；摘要fb0ede1 | 默认数据由混合池替代，100题仍为离线契约评测 |
| wealth_v1_2_supplement_20261002.md | v1.2真实数据补充，f178598/d448bfc | 当前功能入口；清理后最终结果补在本文 |

## 清理后验证

新增5条混合规则评测已经5 passed，3.31s。干净前端构建通过：新npm ci安装199包，TypeScript通过、693模块、Vite构建6.73s，镜像dbf96e4a5512。清理后新容器完整双库回归336 passed、6个显式联网测试 skipped，236.44s；不依赖旧临时数据库或模型JSON。新增5条规则评测纳入全量。


依赖审计另报3项既有问题：ECharts<6.1.0的XSS（moderate）、旧esbuild开发服务器（moderate）、Vite5的开发服务器路径/Windows问题（high）。audit建议跨主版本升级，未以自动audit fix --force覆盖团队依赖。本演示用nginx静态构建；这些审计项没有被本轮功能测试消除，应作为后续依赖升级项，不声称安全扫描全通过。
