# 账单三项增强自检与修复 · 2026-10-14

## 交付

1. 消费差额按商户和类别追溯，分解交易次数、平均单笔金额及本期出现／未出现影响，返回两期流水证据。
2. 订阅扣费增加、疑似重复、计划差异及关系歧义核对，跳转现有服务端签名预览及本人确认取消流程。
3. 现实时间起算的储蓄现金流，计划／历史估计分开，消费比例调整和排除订阅的只读情景，新增 NOVA 工具。

契约：[账单洞察与现金流 API v2](../contracts/账单洞察与现金流_API_v2.md)。没有数据库迁移。

## 自动验证

- 新增 `test_bill_enhancements.py`：16项全部通过。覆盖精确分解及证据上限、501笔统计、本人权限、订阅变化和关联歧义、确认前不取消、确认后移除计划、信用额度排除、理财占款只扣一次、工资样本不足、重复工资不估计、情景组合、百分比校验、重复参数、单账户互转、草稿／取消／旧计划排除、完整时间窗口与首尾折算、月末锚点、负余额、HTTP鉴权及NOVA只读调用。
- 账单、NOVA及订阅相关回归命令见下文；最终执行：83 passed。既有账单回归还覆盖601笔流水的完整统计。
- 全仓最终：610 passed、118 skipped、10 failed。已用 `git archive HEAD` 生成独立未修改基线，在相同虚拟环境运行：594 passed、118 skipped、10 failed。失败项完全一致，均属于理财模块，不是账单增强回归。跳过包括需独立PostgreSQL环境及显式启用真实模型的项目。
- 前端 `npm run build` 类型检查和生产构建通过，保留仓库既有的大分块提示。
- `git diff --check` 通过。

基线失败文件：test_wealth_bank.py、test_wealth_fund_directory.py、test_wealth_research.py（4项）、test_wealth_semantic_constraints.py、test_wealth_semantic_dialogue.py、test_wealth_task_controls.py（2项）。本次未扩大改动范围到理财模块，不将全仓结果表述为全部通过。

```bash
.venv/bin/python -m pytest tests/integration/test_bill_enhancements.py tests/integration/test_bills.py \
  tests/integration/test_nova_reads.py tests/integration/test_nova_cards_subscriptions.py \
  tests/unit/test_nova.py tests/unit/test_subscriptions.py -q
.venv/bin/python -m pytest tests -q --tb=short
cd frontend && npm run build
```

## 浏览器检查

使用本机 Chromium headless / Playwright CLI、独立合成数据库、LLM_MODE=offline。

- 2026-10报告显示消费2240元，与2026-09同期差额1975元；主要驱动为新商城1800元、餐饮150元、音乐订阅25元。次数贡献-35元、平均金额贡献210元、新出现贡献1800元，与总差额精确对账。
- 展开新商城依据能看到 DEMO-LAPTOP；两期证据可选中供NOVA解释。
- 音乐扣费展示由15元增至20元，以及同金额两笔相隔30秒的核对提示。
- 餐饮减少50%并假设排除音乐订阅，得到正的联合改善金额；请求及已应用情景保持一致。预测end_exclusive-as_of精确为30天。
- 点击订阅取消详情后，通过接口确认仍为ACTIVE；本人点击确认后变CANCELLED；返回账单，预测不再包含SUB001，保留腾讯25元计划。
- 390像素手机宽度下修复现金流画布不缩放问题；账单内容不横向溢出。明细表在自身滚动区域展示。

本地图片：output/playwright/cashflow-desktop-20261014.png、cashflow-mobile-20261014.png、subscription-preview-20261014.png（浏览器临时文件不提交）。

## 自检修复

- 消费分解舍入余数归入平均金额贡献，确保总额能对账。
- 预测排除信用额度及研究账户，起点已扣占款，避免再次将理财占款当未来支出。
- 定时转账限定已确认等待执行的当前版本READY操作；汇总现金范围内部转账不重复计算。
- 月末计划始终从原始锚点推演，二月夹取不会将三月31日变成28日。
- 预测窗口从当前时刻覆盖完整天数，首尾日消费按时长分配，累计金额在分上舍入，避免每天舍入的漂移。
- 假设排除的已取消／他人／范围外订阅拒绝；重复参数及非整数比例拒绝。
- 同商户多条关系只提示歧义，取消后扣费仅在唯一关系可确定时提示。
- 用户修改假设期间不把上次结果误称为新结果；明确显示需要重新计算。
- 历史证据选择增加选中状态反馈，并支持取消选择。
- 手机现金流图增加容器ResizeObserver、窗口重绘和溢出边界，修复宽画布残留；关闭预测折线入场动画，避免截图只呈现部分曲线。

## 限制

未使用真实外部模型进行本次增强联调，也未实际运行PostgreSQL分支。工资和消费估计是有门槛的规则基线；历史高低线不是置信区间。没有自动预算执行、未完成付款/投资收益预测或跨商户名称智能合并。新报告仍按查询时点读取，不是不可变账本快照。
