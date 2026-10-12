# 主线同步回归｜2026-10-12

目标分支：`codex/wealth-manual-flow`。合并前功能分支 `cfaacff`；远端 main 最新 `853569b`（添加流式输出），相比共同基线有 7 个主线提交。远端通过明确 main/wealth ref 抓取重新检查，不依赖仅抓功能分支的默认 fetch 配置。

本次是 main → 功能分支，使用普通合并保留双方历史；不合入共享 wealth 或 main，也不推送它们。双表收尾暂停，等待本次同步完成后继续。未跟踪的 `docs/wealth/02_conformance_audit_20261010.md` 保持原文件，不纳入本次合并提交。

## 冲突处理

- App.tsx：保留理财资产汇总、DEV 专用 PortfolioConsumerPreview、未提交草稿清理及原工作台；同时保留 NOVA 并排面板、bankShellRef、页面上下文、卡片核对入口和流式客户端。
- BillAnalysis/index.tsx：组件同时接收 onViewCashflows 和 onContextChange；保留报告排除理财说明与原流水入口，以及队友已应用筛选/选中流水上下文。
- 移除主线首页旧现金卡被总资产卡替换后遗留的未使用变量；未改变队友其他页面行为。

## NOVA 口径修正

主线 get_financial_overview 原先只汇总普通 savings 现金，描述为“与首页一致”；这不能与我们的现金＋持仓＋赎回应收总资产等同。

保留旧 totals 现金小计兼容字段，另返回共享 asset_summary。后者使用与页面相同的银行资产构建逻辑、当前授权借记账户范围及快照；查询在专门只读一致性事务中进行，不重置 NOVA 外层事务。普通账户现金小计与选中模拟上下文资产可不同，明确返回说明。模型工具说明和离线回答均区分现金、可支付现金及总资产；缺估值显示不能完整计算，冻结不重复加。

新增回归覆盖多账户/checking、信用排除、冻结、赎回应收、缺估值、选中模拟上下文不混入普通账户，以及离线总资产回答。隔离数据库，不修改用户演示账户，不调用真实模型。

## 本轮验证

- 前端 `npm ci` 完成（使用合并后的锁文件）。`npm run build` 通过，包含 TypeScript 检查及 Vite 生产构建。
- 前端全部 wealth 状态测试与 AssetSummary 测试：63 通过、0 失败。
- 后端下列范围：66 通过、9 跳过、0 失败；10.77 秒。跳过项是未配置独立 PostgreSQL 验收库的分支，不代表 PostgreSQL 已通过。

```text
.venv/Scripts/python.exe -m pytest
  tests/integration/test_nova_wealth_summary.py
  tests/integration/test_nova_reads.py
  tests/unit/test_nova.py
  tests/unit/test_nova_stream.py
  tests/integration/test_wealth_asset_summary.py
  tests/integration/test_wealth_bill_boundary.py
  tests/integration/test_cards_controls.py
  tests/integration/test_nova_cards_subscriptions.py
  -q -p no:cacheprovider
```

这是合并受影响范围回归，不是全仓所有用例或 B6 联合验收。本轮未做真实模型联网请求，也未进行新一轮登录浏览器验收。检查时本机 3000/8000 没有监听，旧启动记录不能证明服务在运行；试用需重新启动。依赖安装报告现有 4 项审计问题，本次没有进行可能改变主线依赖行为的自动升级。

后续仍按功能分支 → wealth 联合验收 → main 推进；继续完整双表复用及 B6，真实 A 接入按队友交付进行。
