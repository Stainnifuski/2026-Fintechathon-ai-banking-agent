# B6 第一轮：独立银行全链路与前端契约消费｜2026-10-12

依据当前 02 v0.3 的 B6、第4节及第5节。新增测试以同契约 A 固定提供者接线，经结构化动作和现有公开授权接口走真实模拟银行；不是生产推荐算法，也没有注册到日常演示服务。

本轮结论：后台完整链路及前端响应消费通过。**尚不宣称 B6 整体完成**：非空数据的完整浏览器操作链、人工控制切换的全链路复验及最终交接仍需完成；真实 A 联调另列。

## 一条连续链路已验证

1. 本人当前银行总资产与 A 替身快照使用同一授权 scope、账户集合与总资产基准。
2. 理财规划、普通推荐、债券类别推荐、高股息主题推荐均通过 ActionV1；一般筛选参数按严格协议标准化，不调用 NLP/模型。
3. 固定七产品选择与整体比例经 revision，prepare 产生七笔差额动作，尚无订单或资金冻结。
4. 整份 plan 一次本人确认及 TOTP 验证；不完整确认被拒绝，未授权不能提交；一次受理七笔，立即冻结全部 20,000 元。原号重试不多生成订单。
5. 结构化查询显示七笔订单，分页首屏五笔、全量表格七笔；核对一笔 3,000 元订单撤单资格并本人确认撤单，释放原冻结，账面现金不额外增加。
6. 推进隔离测试业务时钟，六笔剩余订单结算；查询六条持仓，总资产＝账面现金＋持仓＋赎回应收，冻结已消费。
7. 整体/单产品分别查询日、月、年、累计，共八个请求，来源为实际模拟成交，公式版本 actual-settlement-replay-v1，scope 始终一致。
8. 六笔申购现金流水保留；账单分析及报告明细不计入这些理财流水。
9. 分别输入 10 份和 10 元持仓市值赎回：银行按可信净值换算份额；命令 amount_fen=0；显示持仓占比及非保证到账提示。各自完成本人确认和绑定短信验证，受理占用对应份额，结算后到账。
10. 最终九笔订单（七笔申购＋两笔赎回），其中一笔撤单；两笔赎回现金流水各产生一次；在途应收归零。

测试账户均由临时 SQLite fixture 创建，业务时间由测试时钟推进，不能把测试中的后续日期当作现实未来收益可查询。没有向当前张三演示账户造单，没有读写用户 API 密钥。

整份 plan 已消费当期 TOTP 后，继续使用同一步动态码会被 TOTP_REPLAY 拒绝。测试保留此保护；后续赎回使用各自绑定到本笔授权的模拟短信挑战，不重置防重放字段、不代替验证码校验。

## 前端实际响应消费

新增 `frontend/tests/verifyB6Evidence.ts` 读取上述通过链路导出的临时 JSON：

- 使用现有 readPortfolioPlan/readReviewSnapshot/readCandidateGroup 和 planProductSelection 校验真实 API 响应的字段、scope 与版本，能选择七个具体产品。
- 使用实际九笔订单和六条持仓核对独立分页/跨页勾选：订单两页选择九笔，持仓首屏只选择五条，切换 scope 清空。
- 核对八种收益查询的公式/来源，冻结及撤单释放，到账后应收归零。

导出文件是已通过隔离测试的读取证据，不是接口成功替身；导出时去除授权引用、验证码、挑战、令牌等字段。该验证仍是前端数据/状态模块检查，不能替代组件渲染或浏览器点击验收。

## 本轮验证结果

- 后端 B6、structured_portfolio、actions、tables、redemption_input、asset_summary、bill_boundary、actual_returns 八文件：**36 通过、30 跳过、0 失败，44.84 秒**。
- 单独 B6 链路：**1 通过、1 PostgreSQL 跳过**。后续固定提供者状态字段改为银行目录实际 status，并重新执行链路和前端消费验证，通过；前端未把不可售状态强制视为可选。
- 既有 wealth 状态测试及 AssetSummary：**66 通过、0 失败**。
- PostgreSQL 未配置独立验收库，因此不能声称双数据库验收完成。本轮没有执行全仓所有用例，也没有改变业务实现或页面布局。

运行方式（仓库根目录；临时目录应为全新路径）：

```text
.venv/Scripts/python.exe -m pytest tests/integration/test_wealth_b6_manual_chain.py -q -p no:cacheprovider --basetemp <全新临时目录>
node --experimental-strip-types frontend/tests/verifyB6Evidence.ts <该目录下的b6-evidence.json>
```

范围回归另外包含 test_wealth_structured_portfolio.py、test_wealth_actions.py、test_wealth_tables.py、test_wealth_redemption_input.py、test_wealth_asset_summary.py、test_wealth_bill_boundary.py、test_wealth_actual_returns.py。

## 下轮与交接边界

下一轮需要独立、明确标注的浏览器验收环境，使用非空订单/持仓验证详情、翻页多选、确认/验证、撤单/赎回后刷新及收益粒度；不为了浏览器非空而污染普通演示账户，不把录制 UI 当成银行成交。

已有人工控制安全分项测试仍需纳入 B6 总表，记录 HUMAN/PAUSED 下的限制、交还后的新预览和已受理原号恢复。F18—F24、完整第8节清单尚不能仅凭本轮一条测试判为全部完成。

03 最终按双方约定联合修订；本轮 B 实际接口补充可先查看本报告、manual_flow_b5_actions.md 和 manual_flow_b5_shared_tables.md。不把当前旧 NLP 功能当作新任务包已对接；不启用新自然语言入口。

本轮用户试用入口保持 `http://127.0.0.1:3000/?wealth-dev=1` → 统一手动操作；没有新增按钮。当前演示账户空订单/空持仓继续保持正常事实。仅在功能分支保存，不推送或合入 wealth/main。
