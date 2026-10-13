# 理财01／02：本地集成工程交接

更新：2026-10-12 America/Toronto。替代旧01单独 wealth 分支交付说明。共同依据仍为00 v0.3，模型和参数以 portfolio_model.md 为准，未重新选型。

**状态：正式代码接线完成，联合验收未全部通过、未可封板，人工待验；没有远端写入。** 真实银行来源可创建全现金配置，但合格候选为0，不能以此声称投资正向主线通过。阶段4未执行。

## 1. 来源与工作区保护

- 本地01原 main：`5e36e4c48c82c24a088c9f271df7774727d7435f`，完整未提交01成果保留原目录。
- 本轮只读 fetch：main `853569b1b06b3f7fb261e4e62cdf053eb382d47d`；wealth `ec8d143c0242a4f0acbeb1a4fb5d95fbb0c5d020`；02 `8b611dd10a6b876c8a3baf50ec1b5d94247dc6b4`。
- 集成底座为02（已含核对时 main），本地 `integration/wealth-01-02`，独立检出；未切换原脏目录，未修改02源分支。没有执行阶段4整合 main。
- 导入清单181份A源码/测试/配置/文档及7份重叠fixture语义合并。大小写冲突保留B录制字段与A input/expected，生成器不覆盖B扩展，跨平台加载兼容大小写。
- 原旧交付检出保留；旧批处理脚本已备份并改名 `.disabled`，不会重跑。浏览器脚本的环境变量可移植性修正保留。私有附件/回传、运行库、原料/验证缓存、密钥未纳入提交。

## 2. 相对00的新增／调整／移除／保持

|类别|本轮实际内容|
|---|---|
|新增|`wealth_portfolio_joint.py` 薄服务适配、正式注册与本轮隔离银行/浏览器检查；不增加第二套算法|
|调整|服务从B全授权 AssetSummary+完整cash/lots/orders读取；显式lot历史版本；真实profile/assessment版本与相关会话/任务控制；共享金额/比例/份额/epoch等类型；A读取过程中变版拒绝|
|移除|同方法同路径的B重复snapshot注册、由录制fixture推导的生产类型、正式页面重复板块编辑器；不删除B测试录制数据|
|保持|00必要资格、本人确认、统一强验证、版本/参数绑定、有效期、幂等/审计、赎回退出与受理事实、B交易引擎和双表；DPP、区间、权重投影方法和参数；其他main业务|

00必须改写／移除的必要条件：**无**。空授权账户仅在余额均为0或未知时允许只读汇总，不能据此创建配置。未知估值保留null，不补0。必要输入缺失仍关闭。测试临时日期推进仅存在独立测试server，不是正式API。

## 3. 实际接法与唯一DTO

`backend/app/main.py` 安装 `JointPortfolio`：

```python
app.state.wealth_action_handlers = {
    'plan_portfolio': provider.plan,
    'recommend_products': provider.recommend,
    'revise_portfolio': provider.revise,
}
app.state.wealth_portfolio_source = provider
# async handler(*, db, user_id, context_id, bank, params) -> canonical DTO
# snapshot(db, user_id, context_id, bank); owned_plan(db,user_id,portfolio_id)
```

|接口|实现／约束|
|---|---|
|GET `/api/v1/wealth/portfolio/snapshot`|唯一A规范路由；B全scope/snapshot，不用旧单run资产替身|
|POST `/api/v1/wealth/portfolio/plans`|A PortfolioLifecycle.create，不问本次预算或期限|
|GET／PUT `/api/v1/wealth/portfolio/plans/{id}`|同一生命周期/store；GET带context_id；PUT带expected_version/snapshot_id/context_id/items及revision_kind|
|POST `/api/v1/wealth/portfolio/recommendations`|独立A05；无Plan、不读取A01完整估值；仍核实本人、产品、资料/风险|
|POST `/api/v1/wealth/actions`|原B ActionV1；同A handler和store；参数严格，身份来自JWT，不接受浏览器Plan当权威方案|
|POST `/api/v1/wealth/portfolio/plans/{id}/prepare`|保留B差额准备；从正式owned_plan重验，不占款，最终具体SKU再查适当性|

canonical Python：`schemas/wealth_portfolio.py`、`wealth_portfolio_candidates.py`、`wealth_portfolio_lifecycle.py`；B `wealth_assets.py` re-export同AssetSummary；Action导入同Selection/Filter。前端 `types/wealthPortfolio.ts` 为共享类型，生产代码不导入fixture类型。

整数分金额；0—1十进制字符串比例；四位字符串份额；share_class A/C/null；legacy context_epoch=null不造1。产品版本、披露版本、资料data/daily/offers版本、Plan version、会话version和执行版本分别核对，不能互换。

正式客户页 `StructuredActionPanel -> AllocationReview -> PortfolioAllocation`，A滑块回调实际调用修订、接受后同步B Plan；B保留产品选择/百分比、详情/比较/整体确认、执行/订单/持仓/收益。隐藏A独立产品编辑器防重复。操作员页面复用A图为只读，交易继续依赖本人授权。独立dev入口仅测试工具，不能冒充正式购买入口。

## 4. 事实与旧缺口闭合

|项目|本轮证据／状态|
|---|---|
|全授权资产|B公开汇总与A精确相同scope/snapshot；多账户/F18、冻结/应收/缺估值与旧lot版本隔离检查通过，不再写“B没有AssetSummary”|
|当前评估与profile|实际九题答题+本人确认；服务读取最新assessment及真实profile，版本不匹配拒绝，不把purchase_allowed当画像证明|
|相关控制|Action实际会话绑定；新Plan入store前重核当前会话、旧Plan核其绑定会话；真实closed/stop/HUMAN/PAUSED写拒绝，查询读写分离|
|正式A计算|正式来源全现金20,000元基准，A计算并在B正式UI展示；不是fixture Plan，但0产品不能证明申购正向|
|A05|无需先有Plan；缺估值依赖专项通过。当前仍返回unavailable+精确产品原因，不能称推荐成功|
|最终风险|实际隔离SQLite最终prepare比较R2/R4；手选、0、极小权重和同家族C份额均SUITABILITY_MISMATCH；无订单/占款/现金扣减；银行执行原风险/授权检查保留|

## 5. 数据、依赖、运行与不能伪造的输入

依赖：Python环境安装仓库 requirements；frontend按package-lock `npm ci`；新增main依赖icalendar/recurring-ical-events已声明，集成验证本地补齐。Playwright及浏览器由运行环境指定，工程不写个人绝对路径。

正式资料冻结 data `38005c79535cb83a2d5be663d8a7a1812ba21946fe8f9fa2ab21ca80aea338a9`，daily `e5ce972368c9b735a5fe9ddd64fa0fd81a9194c419cb89ceda255ead71f460d0`。最小运行包是 `.deps/wealth_portfolio_data/snapshots/<data>.json` 与 `<data>.index.json`，索引校核内容SHA，配置和公开证据随代码。来源从既有01缓存只读复用；没恢复采集、没联网补数。资料包不在Git，其他机器须由资料维护方交付同SHA的受控离线包到相对位置。完整审计/重建还需原料及manifest引用，不可仅靠两文件声称可重建。缺包返回PORTFOLIO_DATA_UNAVAILABLE，不自动fixture后备。

当前隔离银行原生MF/MW八产品，与A冻结资料池中的具体产品身份/渠道/份额/产品和披露版本及正文证据不闭合，候选0。现有preload.json及wealth_preload将真实基金标research-only，不能把目录或研究数据当可购买产品；也不能仅改名字/risk或嫁接正文凑7只。

最小解除条件：资料维护方与A/B共同确认至少7个原设计范围的可售具体SKU，逐条提供精确产品/披露/渠道/份额/风险及时点、family与正文（目标/策略/基准/类别）和规则版本；银行当前目录与冻结研究事实可验证一一映射。若需要新的模拟产品定义或设计变更，须由用户明确决定后再版本化，不假称公开金融事实。A核验材料映射与推荐；B维护当前规则与执行。不能删除这些门禁以封板。

## 6. 尚未完成及责任

- J08正式A生成>5合格产品整份确认/统一MFA/受理/结算：**blocked**（上述SKU材料）；由此J09/J10/J12/J19等完整浏览器正向未闭合。B隔离银行单项交易/撤单/赎回/收益通过与此分开。
- 操作员完整正式浏览器接管/交还/核对及交易后正式A重读仍未完整验收；已有实际HTTP控制检查和前端状态证据不算完整浏览器通过。
- PostgreSQL：本轮未提供 `BANK_TEST_DATABASE_URL`，相关skip；仅声明SQLite/隔离HTTP，不称双库通过。
- 01研究：09既有10具体份额、各243个2025 NAV描述记录可复用，非总回报。合格财富指数0；完整分红/拆分/折算/再投资方法与历史时点身份/风险/可售/正文不足，含分红总回报、固定组合和真正样本外仍blocked；责任为A及资料/方法提供方，不由B模拟持仓收益替代。
- 人工验收待用户实际执行；本轮不代签、不发布、不开展新NLP。03接入门槛仍为必要联合J项闭合，参见 handoff.md 的本轮索引。

实际命令、J逐项状态、失败修复复验和人工步骤见 [wealth_joint_acceptance.md](wealth_joint_acceptance.md)。原工作区、旧检出、验证数据库、失败截图/日志和断点全部保留本地；旧大规模采集已停，不续跑。

## 本地保存与服务状态

本轮代码提交 `82681abc598ca90b45265c6101573b1090908675`。文档在下一本地提交保存；最终SHA见仓库外回传。3013/8013专用服务已核对并关闭，原3010/8010保留；旧3021/3022/8051/8052无监听，旧交付smoke PID已不存在。数据库与失败证据保留。未远端写入，人工待验，禁止据此进入阶段4。
