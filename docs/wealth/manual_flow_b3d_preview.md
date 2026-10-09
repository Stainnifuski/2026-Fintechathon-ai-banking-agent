# B3d第一阶段：收益视图与显式录制

日期：2026-10-09。依据02 v0.3 §5.2、B3d及00 v0.3 §6.11。用户报告每5秒资产卡片跳动，并要求解释各轮功能及核对账单导航；本轮修复刷新体验，继续完成收益页面的契约消费部分。**B3d尚未完工：没有真实银行收益计算服务，也未注册GET /returns或show_wealth_returns。**

## 用户可试用的变化

- 自动回读总资产继续每5秒执行，但已有资产时不临时插入一行加载文字；主动刷新使用原按钮提示。首次加载、失败及授权范围失效的提示保留。
- 独立开发预览→查看持仓→查看F23双表样例→查看整体收益，打开收益专门视图。持仓行的浮动收益按钮可打开该具体份额的入口，再切换整体／具体产品、日／月／年／累计及查询日期。
- 月视图有可点击的每日收益日历；点击日期回到该日。年视图为逐月序列，累计从录制覆盖起点作逐年汇总；累计禁用任意起点。
- 汇总显示所选期间利润、累计利润、累计已实现／未实现和费用明细。显示全量贡献，含已赎回产品；不把当前浮动收益或产品日涨跌当作此结果。
- 录制仅覆盖2026-10-07—09。覆盖外或缺成本／估值显示null／待资料；月年会因历史不足显示部分／无法完整计算，不把三天录制冒充整月或全年。
- 返回持仓表保留原页码和勾选；资产／主体范围变化不沿用旧收益视图。没有新增正式导航或银行交易。

本轮录制完全为虚构测试预期，与F23双表金额不联动，与登录账户银行记录无关。页面始终显示此说明。真实账户入口保留未接入说明，禁止显示样例收益替代本人结果。

## 账单导航核对

账单分析模块由此前`bc10571 feat(bills): add monthly and annual billing analysis`引入；左侧“账单洞察”入口已存在于`a2cf126 feat(frontend): prototype unified banking workspace`。B3c提交`472bf4d`只在原报告过滤理财业务流水、同步统计范围说明并连接原资金流水入口，没有新建账单模块。

02 §4明确“不新增独立服务、独立数据库产品或正式客户新导航”；B3c要求在bill_service公共查询口径排除理财，B3d要求收益从持仓整体／产品入口打开。因此保留团队原账单导航，收益进入理财内部专门视图。

## 交付协议与录制边界

新增B-owned `backend/app/schemas/wealth_returns.py`及JSON Schema `docs/wealth/wealth_returns_v1.schema.json`，前端协议为`frontend/src/services/wealthReturns.ts`。字段与00 §6.11一致；补充产品名称、贡献原因仅为展示。scope=product必须带具体product_id，all不得附带，A/C不可改为家族ID；未知字段拒绝，累计不接受任意anchor_date。

`ReturnsView`消费统一WealthReturns DTO，`ReturnsPreviewSource`当前只为开发录制。真实银行接入时补通用加载来源及可信权限读取，不能将本录制provider装入正式接口。该轮不改银行结算、LLM、权限或本人授权。

`returns_preview.json`与`FixtureReturnsProvider.ts`在DEV入口消费，生产构建不包含录制。这里的日利润／累计已实现／未实现／费用是明确录制的预期，日月年聚合只用于界面核对，**不是实际订单／现金流／净值重建算法**。

- F20录制本金增加不盈利，利润0。
- F21定价期盈利10000分，到账日利润0、累计10000分。
- F22净利润800分，费用300分仅作明细，不从800分再次扣除。
- F23七只产品及已赎回产品各自贡献；整体累计1111分等于全量产品之和，具体份额独立。
- F24缺成本／估值产品贡献null，整体null；已知产品可单独查询123分。

## 本轮验证（不沿用历史结果）

```powershell
# frontend目录
node --experimental-strip-types --test tests/assetSummary.test.ts tests/wealthReturns.test.ts tests/wealthTables.test.ts tests/wealthProductSelection.test.ts tests/wealthAllocationReview.test.ts
npm run build
# 仓库根目录
.venv\Scripts\python.exe -m pytest tests/unit/test_wealth_returns_contract.py -q --disable-warnings
```

前端39项通过，Python协议3项通过（1项既有警告）；Python跨语言校验234个录制响应，验证四粒度、单产品／整体、未来／覆盖外，均可由同一后端schema读取。TypeScript与Vite构建通过；既有大chunk提示保留。Windows沙箱Vite realpath受限时按已有授权在沙箱外执行。仅测试本轮受影响路径，不宣称真实银行收益或全仓库回归通过。

浏览器：整体／具体C份额切换、日/月/年/累计、31格月日历点击、原生日期键盘切换、F21到账日0及累计100、F24缺值均已检查。收益返回后持仓第2/2页、已选1行保持。资产45次DOM采样跨两个自动更新周期高度均293.5px，主动刷新成功。日期fill在当前IAB自动化未改变控件值，原生日历点击／键盘操作正常；没有用脚本写DOM强行通过。

截图：`D:/Project/Fintechathon-2026/.tmp/wealth-b3d-preview-20261009.png`。

## 下一阶段：B3d真实账务部分

2026-10-09补充：本人模拟银行实际收益已接入，详见[本阶段接入记录](manual_flow_b3d_actual.md)。本文件以下是录制阶段的历史边界说明，录制provider仍与本人银行来源分开；后续能力状态以接入记录为准。

实现银行历史收益重建及只读HTTP边界，按当前授权asset_scope_id覆盖所有相关账户、实际批次、关闭持仓、成交现金流、赎回应收、可信历史估值及费用；注册GET /returns，后续B5的show_wealth_returns复用同一handler。历史不足保留null，累计覆盖起点须由可核实事实确定，不用今天份额回填历史。

单独验证F20—F24的真实银行行为与J21—J23，再替换页面来源。当前协议／录制通过不能代替该验收。B4市值赎回、B5结构化动作与人工接管复用、B6及A+B联合验收仍待完成。
