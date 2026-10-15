# PR #12 披露指纹修复验收

## 根因与边界

旧`product_content`只绑定`disclosure.version`，遗漏正文。同版本修改risk不能被识别。直接把完整disclosure加入哈希又会把旧精简捕获、完整资料及运行时覆盖混为一谈，导致000003被`BANK_PRODUCT_CONTENT_MISMATCH`拒绝。

000003旧精简捕获含version/identity/nature/manager/seller/risk/fees/liquidity/holding_period_days/pricing/settlement/cancellation/limitations。完整读取另外含dataset_id、dataset_version、data_source、rules_source、来源种类、source_product_code、project_policy_version、issuer_risk_rating、public_fields、public_sources；运行时增加runtime_pricing_policy_version并替换pricing、limitations。精简序列化还曾将嵌套JSON转成字符串。具体原值、实际值及前后指纹见[结构化证据](wealth_disclosure_fingerprints_20261015.json)。

统一静态契约为`wealth-static-disclosure-v2`：身份、规则、必要披露正文、原始及effective计价/边界、公开目标/策略/基准语义、政策版本绑定；缺失与null不互换，不做数值或文本宽松等价。材料/分类原始证据继续由原政策指纹独立核验。行情NAV、日期、可用/观察时间及政策内quote锚点不混入静态指纹；原行情与注册政策检查不删除。

运行时覆盖同时保留原始pricing/limitations。已经effective的披露重复进入规范化时保持原文，不覆盖掉其篡改。新建库恢复同产品版本的原始静态披露，当前计价规则仍由`published-nav-by-order-date-20261015-v1`明确表达；没有回退实际计价行为，也没有改写现有银行版本或历史成交。

## 资料与离线迁移

逐一核对13,578个旧绑定，再离线生成新快照，版本及捕获指纹见`config/wealth/portfolio_content_binding_v2.json`。原银行记录零改写；旧快照保留为证据，不将旧hash作为新绑定兼容放行。19个模拟银行定义仍为独立补充资料，不改普通池数量。

已有安装应使用新包，不能仅更新Python代码后继续指向旧快照。按交接文档安装新manifest；`PORTFOLIO_DATA_VERSION`及`PORTFOLIO_DAILY_VERSION`不设旧覆盖，或成对采用新版本。资料加载失败不得切fixture。

需要从受保护捕获重做迁移时：

```shell
python scripts/wealth_portfolio_rebind_disclosure.py --base-version <旧DATA> --capture <受保护银行捕获相对路径> --sha256 <捕获SHA256> --as-of <带时区时刻>
```

该命令不联网、不修改数据库、不覆盖旧快照；捕获来源、时间、身份、原绑定及政策继续验证。交付包可直接安装，不需要分发原始采集缓存。

## 最终实际验收

最终受影响集合：**156 passed，168.97秒，0 skipped**。原失败测试未改动。包括：

- 同版本风险、费用、期限、计价、限制、公开策略正文变更；effective正文篡改；未知/不一致绑定拒绝。
- SQLite/PostgreSQL精简/完整JSON、嵌套正文及新建银行库与安装快照一致。
- R2/R4手选、零/极小权重、份额版本绕行拒绝，最终银行/prepare前置防线。
- 生命周期及旧版本、资料来源/版本、动态估值、待计价、份额/现金/历史成交不漂移、普通模拟理财既有规则。
- 真实行情证据是此前已捕获的000003按代码响应，在独立双库重放；不冒充本轮新增联网取证。

复现入口（需已安装固定资料包、依赖，独立PostgreSQL测试URL及已有真实报价证据由本地环境提供）：

```shell
python -m pytest tests/unit/test_wealth_product_content.py tests/unit/test_wealth_portfolio_evidence.py tests/unit/test_wealth_portfolio_contract_alignment.py tests/unit/test_wealth_portfolio_lifecycle.py tests/integration/test_wealth_disclosure_binding.py tests/integration/test_wealth_joint_provider.py tests/integration/test_wealth_full_core_bank.py tests/integration/test_wealth_dynamic_quotes.py tests/integration/test_wealth_current_material.py -q
```

未提供独立PostgreSQL或真实捕获路径时，相关用例会明确skip，不能据此声称本机同样已完成全部验收。

实际8014 HTTP：B017/000003/671030选品200；推荐15.969秒、换批11.231秒、Plan7.175秒、prepare2.014秒。首次显示6.765秒且全目录1次，随后各流程全目录0次；初始化25.418秒。prepare后Codex专用账号订单0、持仓0、占款0。非写入重试，不执行申购。

禁网空目录安装及同版本重复导出逐字节一致通过。最终包SHA256、DATA/DAILY见交接和结构化证据；包外没有复制密钥或数据库。

原工作区577个文件、.env及HEAD哈希保持一致。原账户、全部历史订单、持仓记录逐行保留，外键检查无孤立。旧清理备份之后人工账号已存在3笔ACCEPTED订单（2026-10-15 04:14:23 UTC）；本轮不删除、不撤销，也不再把该账号宣称为空订单。Codex正向验收使用独立13800000126。

## 发布状态

更新同一PR #12分支，不合并main、不自动合并。已解决的披露遗留声明移除；用户人工验收、资料包分发及安装仍保留。前端没有本轮改动，复用此前41项前端及正式构建证据，不声称重跑全仓所有历史集合。3014/8014专用入口保持运行，原3010/8010/5432及原配置不改。

本地日志：`.deps/final_round/disclosure-release-tests.log`、`benchmark-disclosure.json`、`disclosure-after.json`、`disclosure-migration.json`、`offline-disclosure.json`、`disclosure-reproducible.log`、`disclosure-preservation.json`。这些日志/缓存不提交。
