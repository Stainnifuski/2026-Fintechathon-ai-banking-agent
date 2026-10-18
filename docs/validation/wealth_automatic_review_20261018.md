# 申购自动核对修复（2026-10-18）

## 问题与修改

正式服务日志及只读草稿核查发现：第一只产品草稿绑定9月30日离线净值，后续核查已读到10月16日净值，资金环境并未变化。行情刷新在发起网络读取前固定了业务时点，读取完成后的新观察时间比该时点晚，导致首轮跳过、下一轮才写入。首次未展示草稿仍沿用旧绑定，重复prepare因此持续返回QUOTE_CHANGED。

- 刷新完成后重新读取该账户业务时间作为观察截止；历史固定业务钟仍不能消费未来观察。已有净值、现金、成本、份额和历史成交不改写。
- 尚未签发授权的内部草稿，在首次对外核对前重新绑定当前报价；已签发授权不覆盖摘要。并发签发变化仍拒绝。
- 准备接口自动返回交易明细。报价变化/授权过期时，未提交BUY可自动生成新版本明细，旧授权失效、逐笔操作号及短信集合参考更新，不继承本人勾选或MFA。
- 前端自动发起首次核对；确认遇到报价变化或过期时最多自动更新一次，无独立必点“生成预览”步骤。本人确认及验证码保留。未知提交结果不自动重建。
- 可用资金由服务端核对，不让陈旧页面余额提前误拦截。不足时说明申购总额、可用金额、差额及减额/补资办法。单笔金额限额显示具体上下限；净值及产品资格错误给出下一步。执行返回PAUSED时展示实际失败原因。

## 本次实际验证

容器内独立`bank_test_wealth_20260930`随机schema与临时SQLite，运行：

```
tests/integration/test_wealth_review_refresh.py
tests/integration/test_wealth_target_purchase.py
tests/integration/test_wealth_redemption_batch.py
tests/integration/test_wealth_instant.py
```

32 passed，61.42秒。覆盖首次草稿期间报价变化、确认后报价变化及新版本确认/MFA/即时成交、旧授权失效、同请求过期自动恢复、余额不足差额及无订单占款、历史钟拒绝未来观察、整批申赎与即时成交回归。行情改变仅使用明确标记的虚构夹具，不访问真实行情。

前端`wealthFeedback.test.ts`：9 passed；TypeScript及Vite生产构建通过。未运行全部历史测试矩阵，未用本人账户做验证性申购或赎回。

已重建并部署到本地3000，`/health`为`ok`、`portfolio_ready=true`、`source_revision=a305091-dirty`。实际连接仍为`bank_local_wealth_20261017`，`LLM_MODE=model`保持启用。重启前后accounts、transactions、wealth_lots、wealth_orders、wealth_cash_holds、wealth_unit_holds的行数及完整行SHA256一致；等待订单0、HELD现金占款0。未对本人账户执行测试交易。本节记录发布前的本地验收状态，后续发布以Git记录为准。
