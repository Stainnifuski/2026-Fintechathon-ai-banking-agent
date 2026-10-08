# 理财人工操作：B0 独立开发与验证

开发基线：`ec8d143`，本地分支 `codex/wealth-manual-flow`。需求依据为用户指定的最新版 `02_协作同学_交易与人工操作任务包_v0.3.md`，字段按最新版 `00_理财场景_共用接口与联合验收_v0.3.md`。本记录不修改团队契约，也不宣称 B1—B6 已完成。

## 入口与隔离

沿用现有后端与前端启动方式。开发预览地址为 `http://127.0.0.1:3000/?wealth-dev=1`，登录现有模拟客户。仅 Vite development 可用，生产构建剔除预览入口与样例，不新增公共路由或正式客户导航。

本机演示后端使用 `.demo-runtime/wealth-demo-desktop.db`，与原 `banking_agent.db` 分开。自动化银行测试沿用 `test_wealth_bank.store`：每例在 pytest 临时目录建独立 SQLite；PostgreSQL 仅在已配置独立 `bank_test_*` 测试库时运行，不接触客户演示库。

预览内有两种明确区分的内容：

| 内容 | 数据与效果 |
|---|---|
| 配置与资产样例 | F01／F18／F10 全部是虚构 JSON，不读取登录账户资金，不发送交易、评估或模型请求 |
| 手选模拟产品 | 读取当前登录账户的银行产品、资产和已确认本人评估；沿用原 preview → confirm → TOTP/短信 verify → execute，不使用样例 ID，不调用 LLM |

手选入口是 B0 的单产品银行联调，不是最终整体配置确认：目前金额由用户输入，B2 才消费 A 校验后的整体目标并计算实际差额。申购风险、版本、账户所有权、可用资金及授权仍由现有服务端校验。当前账户有未完成 HUMAN 任务时，入口提示限制并禁用手选预览；旧任务续接、控制、领取与交还保留在原工作台。

浏览器验收仅生成未确认的订单预览，未替用户确认、验证、提交或撤单。全链交易验收在隔离测试库进行。

## 共享样例与 provider

目录 `tests/fixtures/wealth_portfolio_contract_v1`：

- F01：整体资产 ¥10,000，三板块 20%／30%／50%，进取占扣活钱后部分 62.5%。全部带 `fixture_only/fixture` 标识，assessment 为 null；不提供伪造候选或执行命令。
- F18：两账户现金 ¥6,000／¥2,000，冻结 ¥1,000，持仓 ¥1,500，应收 ¥500；总资产仍为 ¥10,000，冻结不重复加。
- F10：同类资产缺少持仓估值，市值、总资产及规划基准为 null；界面明确“不完整”，不显示 0，也不套用 F01 方案。

`provider.py` 仅供测试注入：`snapshot(case='F01')`、`plan(case='F01')`、`recommend(case='F01')`、`revise(selection, *, case='F01')`。返回独立副本，F01 recommend 如实返回空候选；revise 只回放已记录的无变更输入。未记录的比例修订、版本或不同快照方案均拒绝，不能冒充 A 的真实校验。B1/B2 随实现补充候选和修订录制样例，之后替换为 A 的正式 provider。

B 不新增第二套生产 portfolio schema；前端直接消费共享 JSON。单测核对现有样例字段版本、身份绑定、比例分母、金额与 null 语义。A 的正式 schema 合并后仍须对真实输出做契约验证；当前样例通过不代表 A 模型已通过。

## DAG 与页面步骤

页面右侧六步是用户操作指引，不能作为完整 DAG 的替代。完整交付仍保留 02 第7节 N01—N05、A01—A07、B01—B22 的映射。

本次复用现有银行预览／确认／验证／提交，并展示即时占款和在途事实；未改节点引擎、任务控制或公共认证。整体方案适配和 SETTLEMENT／REREAD_CASH 依赖在 B2 实现，结构化 action 分派及聊天／人工接管复用在 B5 实现。B3a—B3d 及 B4 不因简化页面步骤而删减。

## 验证记录（2026-10-08）

命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q -o cache_dir=.tmp/pytest-cache tests/unit/test_wealth_portfolio_consumer_fixtures.py tests/integration/test_wealth_portfolio_consumer_baseline.py tests/integration/test_wealth_bank.py::test_public_personal_confirmation_totp_and_lost_response tests/integration/test_wealth_cancellation_contracts.py
```

结果：**10 passed，6 skipped**。6 项均是未配置独立 PostgreSQL 测试库；SQLite 已运行。

覆盖：F01 比例和快照语义、F18 全量汇总与冻结去重、F10 缺估值、provider 副本和未记录修订拒绝、F13 即时冻结与撤单释放。HTTP 手动链路读取真实 catalog；跨账户隐藏为不存在，未确认／未验证 execute 被拒绝，完成本人确认＋TOTP 才受理；原号重试不重复下单，资格校验后的撤单恢复可用且不伪造退款。该测试将统一 LLMService 的生成入口设为立即失败，证明链路无需模型。另回归原响应丢失和撤单合同测试。

前端 `npm run build` 通过（TypeScript＋Vite）。原项目的较大打包体积提示仍存在，未在本阶段处理。生产 JS 不含 B0 入口、样例标识或手选联调文案。

浏览器实际读取到 8 只模拟产品、本人 R3 评估、可投资账户可用 ¥50,000；手选 MF002、¥1,000 后展示原本人确认弹窗、披露、估算费用 ¥3／份额 963.1919 和结算日期。预览后现金与占款未变。F18/F10 页面切换及入口切换正常。未进行手机浏览器验收；仅检查当前桌面窗口。

本次不是全仓无失败声明。此前基线的 10 个失败及缺少研究数据问题见本机 `测试/台式机环境与理财基线验证_20261008.md`，未在 B0 顺带修复。

下一阶段 B1：按家族的 ProductPicker、多选、换批保留、A/C 有效份额切换、详情／比较及行情口径；A 的比例图／滑块接口由 A 主写，B 消费。随后按 02 继续 B2—B6，不缩减总资产、流水边界、双表、实际收益或两种赎回输入。
