# B4 持仓赎回输入与确认（2026-10-09）

基准：02任务包B4、00第6.7节。B5结构化动作、多仓含糊请求的clarify和B6联合验收仍待后续；不宣称全部02完成。

## 入口和行为

开发预览及原银行工作台共用“查看持仓 → 赎回/赎回已选持仓 → 填写 → 换算核对 → 本人确认 → TOTP/短信验证 → 提交”。按份额、持仓市值、持仓比例三种输入；多只产品逐只填写和预览，不均摊一个总金额。比例分母为该账户该产品版本的总持仓，可赎上限排除锁定及在途占用。换算结果同时展示份额、市值、比例、净值日期、费用及预计到账。

F23仅展示输入表单，不向银行发送虚构持仓或生成授权。当前登录账户没有理财持仓，不能在浏览器完成非零赎回；金融链路在隔离测试库验证。未添加演示订单、风险答案或改变演示账户余额。

## 接口与边界

- 公共POST `/api/v1/wealth/redemptions/preview`，内部POST `/api/v1/internal/bank/wealth/redemptions/preview`。公共身份来自JWT，内部沿用银行令牌；客户端不能指定用户或操作号。
- RedemptionRequest：`schema_version=wealth-redemption-request-v1`、`context_id`、`position_key`、`input_mode=units|market_value|holding_ratio`、`redemption_mode=standard|quick`。对应输入字段仅一项非null：`units`四位精确字符串、`market_value_fen`正整数分、`holding_ratio`0—1精确字符串（最多八位小数）。界面百分数最多六位小数，整数换算成比例字符串，不用浮点金额。
- 银行在同一读快照内取当前授权范围、精确账户/产品版本持仓、锁定与占用、已发布净值；按市值/比例向下换算到四位份额。换算为零、超过可赎、缺净值、跨用户/账户/资金环境拒绝，不自动缩减请求。每只对象必须明确。
- 返回保留原始input及position_key，包含holding_units/redeemable_units/resolved_units、estimated_market_value_fen、holding_ratio、nav/nav_date、estimated_fee_fen/estimated_net_cash_fen、notice和preview。普通金额是市值而非净到账；两处确认界面保留明确提示。
- 复用原InvestmentCommand和银行precheck，预览不占用份额；公共层仅生成AWAITING_CONFIRMATION授权。普通赎回沿用原版本，快赎沿用v2绑定报价、context epoch、窗口及每日额度校验，不静默降为普通。
- 原工作台将新预览通过已有preview-link链接至现有holding_redemption任务，再使用原confirm/verify/execute。保持原操作号恢复、HUMAN/PAUSED限制及服务端控制。无LLM调用，不改节点引擎。

示例请求：

```json
{"schema_version":"wealth-redemption-request-v1","context_id":"legacy-v1","position_key":"A1:MF001:1","input_mode":"market_value","market_value_fen":100000,"redemption_mode":"standard"}
```

隔离样例：持有1000份、净值2元，市值1000元/500份/50%均得到500.0000份、50%、市值100000分。费用扣除后净到账小于1000元。净值3元时市值1000元换算333.3333份，不超额赎回。

## 收益页面反馈

查询日期限制至银行业务日期；当前月份日历的未来日期禁用。日模式显示“日期 · 当日收益”，不重复起止同日；“时间序列”改为当天/每日/逐月/逐年收益明细，增加通俗解释。月/年查询仍保留缺历史、未结束时段的partial/null，不把没有资料当零。

## 验证

- `python -m pytest tests/integration/test_wealth_redemption_input.py tests/integration/test_wealth_actual_returns.py -q --disable-warnings --maxfail=1`：12项SQLite通过，12项PostgreSQL未配置跳过，1项既有警告。
- 补验preview-link后重跑赎回输入：4通过/4跳过。覆盖F06/F07换算、四位向下舍入、微小比例归零、锁定/在途、未知或异主持仓、跨context、缺净值、严格未知字段、快赎不支持拒绝；支持快赎从预览、本人确认、短信验证到SETTLED，报价绑定和原任务兼容。
- TypeScript无错误；收益/双表/总资产22项前端测试通过；最终Vite构建通过，保留原大chunk提示。
- 浏览器验证：本人账户日收益说明、日期max为2026-10-09、月历10月23日不可点击、F23市值/比例输入和不提交银行提示。非零金融行为仅在隔离库验证，未声称全仓库或PostgreSQL验收。

下一步B5：严格ActionV1/ActionResultV1分派、缺参clarify、四快捷入口及通用控件统一至现有DAG；不把按钮文字送模型解析。最终B6再做整体独立验收与03接口交接修订。
