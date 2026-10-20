# 账单洞察白屏修复

> 发布更新：此文是故障修复时的记录；后续已整合最新main，当前发布范围见[本次同步说明](release_20261020.md)。旧文“不推送／不合并”及运行PID仅描述当时状态。

日期：2026-10-20。工作树：D:\Project\Fintechathon-2026-frontend，分支codex/frontend-20261020。基线769a09e及上一轮尚未提交的NOVA精简／文案改动均保留。本次没有拉取、推送、创建PR或合并main。

## 故障复现与证据

在独立浏览器登录3100，从首页点击“账单洞察”，整页停止渲染，地址不变。已实际复现，不是仅依据用户截图判断。

- 3100进程2844启动命令为node_modules/vite/bin/vite.js --host 127.0.0.1 --port 3100 --strictPort；运行目录是本前端工作树。代理指向独立8110后端。
- 同时段账单analysis、foundations、recurring、transactions、budgets、subscription-insights均返回200。
- 控制台先出现“[ECharts] Component grid is used but not imported”，随后抛出“xAxis \"0\" not found”。React组件栈为Chart → BillAnalysis → Suspense → App，导致整应用白屏。
- [原始异常](../../output/playwright/nova20-bills-error-before.txt)、[修复前白屏](../../output/playwright/nova20-bills-white-before.png)已保存。

## 根因

上一轮为取得同数据的改前截图，我建立了临时改前预览，并通过目录链接复用了改版的node_modules。两份Vite预览默认也因此共享了node_modules/.vite的依赖预构建缓存，缓存被不同项目根目录的优化结果交替使用；懒加载账单模块时又分批发现ECharts模块，使浏览器同时加载了不同预构建版本的内部模块，图表注册与实际执行未处于一致模块图。

现场证据：Chart/index使用echarts_core.js?v=fad1a189，charts入口为v=f146ca2e，components入口为v=bb410172，renderers入口为v=1cea2a06；旧缓存_metadata.json中的ECharts源码相对路径指向改前副本根目录。此时业务源码已显式注册GridComponent，却仍报告未注册。故障属于本地预览缓存污染，不能归因于空账单或接口失败。

此前31项界面检查没有覆盖实际打开账单图表，未发现这个问题；本轮补上入口、图表和失败路径回归。构建通过本身不能证明开发预览的懒加载模块正常。

## 修复

仅新增修改frontend/vite.config.ts中的6行配置：

- cacheDir指定为配置所在前端根目录对应的../.deps/vite-cache。工作树和改前副本即使复用node_modules，也分别维护自己的优化缓存；不再通过依赖目录共享优化状态。
- optimizeDeps.include显式包含echarts/core、echarts/charts、echarts/components、echarts/renderers，使注册和初始化依赖在同一预构建阶段处理。

没有添加错误边界来隐藏异常，没有改图表数据、账单接口、资金、授权或用户业务记录；原NOVA布局和文案修改保留。旧共享缓存没有被删除，新缓存位于本工作树.deps/vite-cache，源码路径正确指向本工作树frontend/node_modules。

3100在18:23:10检测配置变更并自动重启，修复后的页面已实际加载并验证。用户已打开的白屏标签页需要刷新，使浏览器放弃旧模块图。临时改前3101预览此前已关闭，不是正式体验入口。

## 回归结果

- npm run build通过（TypeScript＋Vite生产构建），保留既有大分块提示。
- git diff --check、回归脚本node --check通过。
- tests/browser/bills_entry_regression.js的14项定向断言通过；修复后捕获的未处理运行时异常为0，资金交易／卡片变更／订阅取消请求为0。

| 路径 | 结果 |
|---|---|
| 实际空账单 | 账单外壳、空消费提示、趋势图与现金流图正常显示 |
| 首页／账单往返3次 | 均正常 |
| 刷新后重新进入 | 正常 |
| NOVA展开／收起 | 账单图表保留，当前账单上下文正确 |
| 非空图表 | 使用明确标注的UI隔离响应，在真实报告结构上设置非零消费分类与趋势；饼图、趋势、现金流图均正常，不写数据库 |
| 接口失败 | 使用明确标注的503响应；保留账单外壳、错误提示和侧栏导航，没有白屏 |
| 失败后重试 | 移除拦截，再点“查看账单”，恢复真实空报告 |
| 390×844 | 账单及助手无文档横向溢出；输入与关闭入口可达，收起后账单保留 |

回归脚本通过Playwright CLI run-code --filename执行，前置为已登录3100独立演示库。finally移除所有响应拦截并恢复专用会话尺寸。没有操作用户已有浏览器的视口，测试结束停留在真实账单报告。非空与503是UI隔离验证，不是实际非空账单业务或真实模型验收。

## 截图和当前服务

- [修复后真实账单](../../output/playwright/nova20-bills-fixed-final-desktop.png)
- [真实空账单图表初始化](../../output/playwright/nova20-bills-fixed-empty-desktop.png)
- [UI隔离非空图表](../../output/playwright/nova20-bills-ui-fixture-nonempty-desktop.png)
- [UI隔离非空账单与手机NOVA](../../output/playwright/nova20-bills-ui-fixture-mobile-nova.png)
- [UI隔离失败提示](../../output/playwright/nova20-bills-ui-fixture-failure-mobile.png)

当前体验地址仍为http://127.0.0.1:3100/，8110仍使用本工作树新建SQLite演示库。原demo保持main且没有本轮受跟踪修改，原配置与账本未动。

剩余范围：未运行真实模型、生产部署、PostgreSQL、Docker或全仓测试；没有在实体手机上验证。修复针对已复现的开发预览缓存／图表模块故障，不宣称所有未知接口结构或其他页面异常都已覆盖。
