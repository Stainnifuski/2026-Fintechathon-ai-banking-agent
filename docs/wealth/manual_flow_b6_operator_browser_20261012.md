# B6 第四轮：操作员领取、核对、交还与本人重准备｜2026-10-12

依据 02 §5.4、B5/B6、§8，以及共用 00 的本人权限和原 HUMAN 控制。原 3011 九笔订单/六条持仓及日常 3000 环境保持原样。本轮专用 3012/8012，全新临时 SQLite，虚构 U1 为客户、U2 为测试操作员；角色仅在明确启用的独立测试进程设置，不修改 `.env` 或日常角色。

## 浏览器验证

- 用公开登录进入 U2 原工作台，看到待接管任务，领取前没有原账户核对资料；领取后任务归属 HUMAN/U2。
- 读取原账户 A1 银行事实、七笔执行动作和准备时保存的完整整体快照。与 U2 自己的 A2 资产视图区分；核对视图清楚说明历史快照不能冒充最新估值。
- 用共享 AllocationReview 把 MF003 建议比例 20% 改成 21%、MF004 15% 改成 14%，整理为处理说明。原目标金额未重算，七笔命令未变，没有 revision/prepare/本人授权/下单入口。
- 保存处理说明后任务版本递增，旧核对表及输入清除；需要重新读取当前任务资料。再填写交还说明并交还，操作员列表为空，原资料消失。
- 退出 U2 后进入登录页；登录 U1 后没有操作员工作台和领取权限，自己的 A1 资料正常。交还任务为 AGENT/PAUSED，显示必须重读资料、重新核验；备注建议不自动应用到客户比例。
- 本人继续任务，重新读取整体方案，校验原七产品比例并准备新的执行版本；再“预览整份申购”，七筆预览展示，出现“本人已阅读全部订单与披露”勾选及“一次确认整份方案”。本轮未确认、未填写验证码、未提交。
- 最终订单查询 0 行，总资产/可用现金 20,000 元，冻结/持仓/应收均零。原 3011 非空验收环境未重置或造单。

截图保留于 `.tmp/b6-operator-review-20261012.png`（建议核对）及 `.tmp/b6-operator-customer-repreview-20261012.png`（交还后本人新预览）。截图不包含令牌、验证码或密钥。

## 本轮发现并修复的问题

交还后再次校验成功，但通过 /actions 重新 prepare 执行方案时返回 500 MissingGreenlet。原 prepare 为避免银行 HTTP 与 SQLite 写锁冲突，会 rollback；这会过期已加载的会话 ORM，或移除新建会话实例。分派器之后直接增加旧实例的 version，可能报异步加载错误，也可能返回一个未实际保存的版本，导致下次 STALE_SESSION。

修复在 /actions 的 prepare 分支：调用准备服务前保存必要会话标量；返回后重新取得用户锁并显式读取会话。验证主体、context、closed、版本；只有本次新建且被 rollback 移除的会话才重建。最后对重新读取的实例更新版本和请求结果，未取消版本或权限检查，未修改银行授权、金额或事务边界。

新回归 test_reprepare_revised_plan_preserves_structured_session 复现首次 prepare 后返回版本未保存的问题（修复前 STALE_SESSION），修复后验证两次 prepare、执行版本递增、同会话版本正确、同请求回放一致、无订单和冻结。浏览器中原有会话的二次准备 500 也用新库完整重跑领取→交还→本人修订→准备→整份预览，成功。

## 本轮验证

```text
.venv/Scripts/python.exe -m pytest tests/integration/test_wealth_structured_portfolio.py tests/integration/test_wealth_portfolio_prepare.py tests/integration/test_wealth_b6_manual_chain.py tests/integration/test_wealth_b6_controls.py -q -p no:cacheprovider --basetemp=.tmp/b6-reprepare-after-fix
```

10 通过、10 PostgreSQL 未配置跳过，18.19 秒。包含原七笔授权/银行链路、prepare 版本保护、新会话回归和真实 JWT 接管。修复未改前端，未重复上一轮已经通过的前端构建。不是全仓、PostgreSQL或正式 A 联合验收完成声明。

## 独立环境复现

tests/browser/wealth_b6_server.py 默认仍为 8011；新增仅允许 8011/8012 的端口选择。仅 `B6_BROWSER_OPERATOR_TEST=1` 且端口 8012 才配置虚构 U2 操作员。数据库必须是仓库 `.tmp` 下尚不存在的 `browser_bank.db`，不复用、不删除旧库。固定 A 提供者仍只属于测试入口。

```powershell
$env:B6_BROWSER_DATABASE='<仓库下全新 .tmp 子目录>/browser_bank.db'
$env:B6_BROWSER_PORT='8012'
$env:B6_BROWSER_OPERATOR_TEST='1'
.venv/Scripts/python.exe tests/browser/wealth_b6_server.py
```

另一个终端设置前端代理 BANK_API_BASE_URL=http://127.0.0.1:8012、VITE_B6_BROWSER_TEST=true，以 Vite --host 127.0.0.1 --port 3012 --strictPort 启动。后台运行使用隐藏窗口，进程和实际临时库路径记录在被忽略的 `.demo-runtime/b6-operator-session.json`。

新库启动后执行 `.venv/Scripts/python.exe tests/browser/wealth_b6_operator_prepare.py`。脚本只访问固定 loopback 8012，使用原登录和公开 /actions、/tasks 接口准备七笔并申请接管，断言无订单和冻结，不输出或保存令牌。测试账号 U1/U2 手机号分别为 13800000001/13800000002，均使用公开虚构夹具密码 password123。

试用操作员：3012 → 登录 U2 → 理财 → 返回原工作台 → 操作员处理工作台。已完成本轮交还后列表为空是正常状态，不为展示反复领取或修改角色。当前 3012 保留 U1 的新整份预览，仍需本人确认与验证码；普通用户继续试用 3011 即可。

## 收尾边界

客户/操作员的主要领取核对交还页面链路现已有浏览器证据；不再列为完全未验。但复杂竞态、context 切换浏览器矩阵及 PostgreSQL 仍不能因此全部打勾，既有接口/状态测试分别记录。

下一步整理交给 Peter 的接口、事件、执行映射及逐项验收证据；等待正式 A 提供者/图和联动滑块接入后做真实 A+B 联调，再按功能分支→wealth联合验收→main 推进。03 最终仍由双方按约定共同修订。本轮不向共享 wealth/main 合并或推送。
