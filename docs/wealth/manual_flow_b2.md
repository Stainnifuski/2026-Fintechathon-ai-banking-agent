# B2：整体目标核对与现有执行计划适配

2026-10-09；分支 `codex/wealth-manual-flow`。依据02 v0.3第5.1／B2、00第6.5、6.6、6.9及F11。B消费整体目标，不实现A配置算法，不调用LLM。

## 已实现与运行边界

- `AllocationReview`展示整体基准、具体产品/份额与版本、最终整体百分比、已校验目标、当前持仓、三个板块、保留现金与应收。编辑后调用A revision回调；立即清除旧差额并禁止prepare，收到匹配结果才可继续。操作页切换保留比例草稿，scope切换清空。
- DEV入口仍为 `http://127.0.0.1:3000/?wealth-dev=1`。点“体验七产品整体核对”→“校验所选产品与比例”→“核对差额与准备预览”。F11录制七家族目标及明确修订：青禾S1 15%、清岚S2 5%，其他不变。其他比例明确提示未录制，不冒充A校验成功。样例不发送银行请求，不伪造execution_plan_id或授权成功。
- `POST /api/v1/wealth/portfolio/plans/{id}/prepare`严格请求`{expected_version,snapshot_id}`。从A服务读取本人已校验目标，重读快照并与银行HTTP现金/占款/批次/估值/可赎/应收核对，计算目标减当前持仓。不调用旧预算推荐，不自动转账、卖出、冻结或再买完整目标。
- `PlanExecution`复用原`/plans/{id}/preview/confirm/verify/execute/progress`、公共SecondFactor及原披露展示。整份集合一次本人确认、一次TOTP或短信；原号恢复。确认默认不勾选；验证码只进入验证接口，不进入聊天。
- A尚未合入，正式prepare返回503 `PORTFOLIO_PROVIDER_UNAVAILABLE`。真实模拟银行七笔授权/冻结在隔离HTTP测试验收；浏览器当前仅录制核对，不宣称真实A+B provider端到端完成。原手选银行产品入口继续可用。

## A接入边界：合并01时共同核对

不维护第二套PortfolioPlan schema。前端暂从共享F11 JSON推导类型，A canonical类型合入后替换这一导入。B-owned `schemas/wealth_selection.py`只定义prepare请求。

服务端只读`PortfolioSource`签名：

```python
async def owned_plan(db, user_id, portfolio_id) -> dict: ...
async def snapshot(db, user_id, context_id, bank: HttpWealthBankApi) -> dict: ...
```

A服务验证归属/当前版本/风险/区间，返回canonical PortfolioPlan；snapshot返回重新核对银行事实的canonical AssetSnapshot。相同事实snapshot_id须稳定。集中接线`app.state.wealth_portfolio_source = adapter`，不接受客户端provider或fixture开关。测试只在独立ASGI app注入RecordedSource，生产不注册它。

`selected_positions[]`执行投影需`account_id,family_id,product_id,product_version,disclosure_version,share_class,bucket,target_ratio_total,target_fen`；显示可另有name/current_fen。字段补在共享F11，不增加同义DTO。付款账户必须来自授权借记范围；PortfolioSelection仍按00采集product_id/version/share_class/target_ratio_total，付款分配来自A适配后的服务端目标。无法确定分配或现金不足时拒绝，通过现有手动入口核对，不因读取多卡就自动划款。

prepare保留00响应`portfolio_id,portfolio_version,execution_plan_id,execution_plan_version,actions[]`，附stage/execution_plan复用可信操作组件。两个版本独立，测试整体v9映射执行v1。动作携带当前/目标/差额、账户和具体银行引用；无动作不需要MFA。

## 账务、依赖和容量

- 未提到的旧仓保留；目标＋保留仓＋现金＋应收必须等于总资产。缺资料、超区间、重复家族、现金不足或锁定份额不足明确拒绝，不自动归一化、补零或缩单。
- 减持按可信净值向下取四位份额，正式command amount_fen=0；全退出用原全部份额，不把锁定/占用部分当可赎。
- 有减持只建立SELL阶段可提交项，买入标WAITING_SETTLEMENT_AND_REREAD_CASH。复用SETTLEMENT→REREAD_CASH→BUY_PLAN；到账后A按新快照修订，再prepare新BUY执行版本，保留卖出历史且重新确认/验证码。
- 相同版本prepare返回相同执行ID/操作号；未提交修订使旧预览失效。拒绝旧PUT /plans或/adopt覆盖整体目标。
- `MAX_EXECUTION_ITEMS=160`覆盖当前78条目及卖/买两阶段。AdoptPlan/PlanConfirmation、内部Batch/BatchVerification、verify_authorizations、accept_many与旧历史容量统一，无暗拆五笔、多次验证码或漏项。
- 映射、旧授权失效与DAG登记同事务。银行远程预检前释放SQLite写锁，保存前重读计划/快照/映射，防并发覆盖。HUMAN/HANDOFF_PENDING/暂停仍受控。
- 修复register_plan把已取消历史SETTLEMENT也纳入到账门的问题，只核对有效依赖；未到账仍不能买入。

## 验证（2026-10-09）

```powershell
# frontend目录
npm run test:wealth-picker
npm run test:wealth-allocation
npm run build
# 仓库根目录，使用隔离库
.\.venv\Scripts\python.exe -m pytest -q --tb=short -o cache_dir=.tmp/pytest-cache tests/integration/test_wealth_portfolio_prepare.py tests/unit/test_wealth_portfolio_differences.py
```

新增后端18通过、6个PostgreSQL跳过；连同已有统一授权和B0消费回归，最终25通过、13跳过。前端产品选择12＋整体核对5通过，TypeScript及Vite生产构建通过；生产产物不包含F11/开发预览。覆盖七笔统一确认/一次TOTP/冻结/原号重试/七笔撤单恢复、拒绝仅确认六笔、旧仓差额、修订旧版本失效、预检期间修订拒绝保存旧执行记录、scope/归属/未知字段、HUMAN、SELL到账门与新BUY重新授权、缺估值/现金占用/保留仓/锁定份额。

扩展回归有50通过、47跳过、3个旧基线失败：`test_real_jwt_ownership_and_injected_description_cannot_authorize`（refusal/clarification）、`test_cancel_submitted_task_keeps_settlement_observation`（PARTIAL_COMPLETED/COMPLETED）、`test_cancelled_sell_blocks_buy_dependency_and_human_direct_cancel`（缺REREAD_CASH）。三项2026-10-08已记录，不宣称全仓通过。

浏览器：七项完整显示；v2校验/差额；编辑立即禁用prepare并清除旧差额；切订单页返回保留15/5草稿；v3目标1500/500元正确；14%未录制明确拒绝；百分比无浮点长尾。截图`D:/Project/Fintechathon-2026/.tmp/wealth-b2-review-20261009.jpg`。未代用户回答问卷、提交交易或输入验证码。

B3a—B3d/B4/B5/B6仍待实现；本阶段不宣称双表、实际收益、结构化总入口或完整DAG已完成。下一步B3a独立订单/持仓表。
