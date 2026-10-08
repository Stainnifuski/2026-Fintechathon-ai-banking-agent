# B1 产品选择与可选详情／比较

基于 B0 `a0129a0`，仍在本地 `codex/wealth-manual-flow` 开发。需求按用户指定的最新 02 第5.1／B1 节及共用 00 第6.3／F09，不调用 LLM，不实现 A 的推荐与配置算法。

## 已实现

- `ProductPicker` 接收同契约的候选组，活钱按实际输出数量、稳健／进取每组按三家族展示。每家族一个卡片，可勾选多个。选中状态由外部控制，可供聊天／人工接管容器后续复用。
- 默认显示可买的有效 A 类；仅 C 类有效，或 A 类暂停时，直接显示 C。不提供无效切换按钮；非 A/C 产品用 null，不虚构份额。
- 切换 A/C 同步更新具体 product_id、product_version、disclosure_version；一个家族仍只占一席。取消勾选后保留份额偏好，重新勾选使用该偏好。
- 每组可换批；之前选中的家族仍在独立已选区，保留具体份额。切换页面不丢选择，切换资产 scope 清空选择、份额偏好、核对状态及旧详情／比较。
- 卡片展示实际提供的申购／销售服务／赎回费、起购、业绩口径及日期；A/C 说明全文保留在各卡片可展开说明中。缺历史或资料用原因，不补 0，不把七日年化／基准／净值变化改名为总回报。
- `ProductDetail` 复用原弹窗和三种观察范围，可注入开发录制数据，正式默认仍请求现有银行详情接口。补充显示已有 rules 中的费率；未知销售服务费不当成零。
- `ProductComparison` 默认复用现有 2—4 个唯一产品的 `/compare`；开发录制模式保持具体 A/C 代码与费率。选择超过四家族时，可在比较弹窗手选比较子集，比较限制不限制产品选择总数。
- “继续核对所选产品”不要求经过详情／比较或先切换份额。当前进入记录具体 ID／版本的核对区；此处没有伪造整体校验、银行预览或授权成功。

## provider 与后续接线

F09 使用 `tests/fixtures/wealth_portfolio_contract_v1/f09.json`；名称、产品、费率及行情都标注虚构样例，不发送这些 ID 到银行。只有 DEV 预览可访问录制 provider。

`ProductPicker` 的 `groups/selection/onSelectionChange/onRefreshGroup/onContinue` 为外部输入；换批回调携带原 CandidateGroup（含 filter/cursor）与 selected_family_ids。A 的 provider 合并后替换供数，组件不负责排序／适配算法。消费端类型直接从共享 JSON 推导，包含 null 资料及跨板块／风险说明；A 的正式类型可用后在服务边界改为导入同一 canonical 类型，不另维护同义 DTO。

未来 B2 从 `selectedReferences` 和 A revision 接入整体目标，不拿勾选金额再次买完整目标；未来 B5 才注册通用 ActionV1、四快捷入口及类别／主题参数控件。当前不宣称完整 DAG 或整体交易已完成。原银行单产品开发联调入口仍保留。

## 本阶段验证（2026-10-08）

```powershell
# frontend 目录，Node 24；内置测试，不安装额外测试框架
npm run test:wealth-picker
npm run build

# 仓库根目录
.\.venv\Scripts\python.exe -m pytest -q -o cache_dir=.tmp/pytest-cache tests/unit/test_wealth_portfolio_consumer_fixtures.py tests/integration/test_wealth_product_detail.py
```

Node：12 项通过。覆盖有效 A、仅 C、A 暂停、实际 ID／版本切换、家族唯一、多选与偏好、非 A/C、缺资料、scope 清空、换批保留、行情口径、状态原因、可选详情／比较及非法比较输入。

Python：7 项通过、2 项跳过（未配置独立 PostgreSQL 库）。核对共享样例及 F09 字段、家族／份额语义，并回归原详情发布时点、1m／3m／1y、非法范围与货币基金收益率不冒充净值。前端 TypeScript＋Vite 构建通过；原大包提示仍存在。生产包检查应不含 fixture-S1、F09 录制产品或开发入口。

桌面浏览器：有效 A 可直接继续，S1 可切 C 并与仅 C 的 S2 多选；换稳健批次后仍保留 S1-C／S2-C；比较显示两份具体 C 代码及费率，S2 仅展示不足一年；详情切近一月仅一个点，不连线或算涨跌；scope 切换清空选择。未提交模拟订单，没有替用户风险评估、确认或验证。

此次验收仅对应 B1。B0 银行冻结／授权／撤单记录见 `manual_flow_b0.md`；旧全仓基线失败未在 B1 顺带处理。
