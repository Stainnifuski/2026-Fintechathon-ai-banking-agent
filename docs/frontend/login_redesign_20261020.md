# 登录页改版实施与验证（2026-10-20）

> 发布更新：黄瑜已授权收尾提交、推送和合并本轮成果。下文“不推送／不合并／未提交”描述开发阶段；本次发布范围见[同步说明](release_20261020.md)。

## 依据与交付

黄瑜已批准“页面视觉设计（子）”交接中的NOVA登录样稿。已阅读D:\Project\Fintechathon-2026-demo\output\design\nova-login-v1\preview.html及《登录页视觉与动效设计说明.md》，并逐张查看references中的copilot-original-01.png、copilot-original-02.png、nova-workspace.png和nova-login-before.png。

工作树D:\Project\Fintechathon-2026-frontend，分支codex/frontend-20261020。此次不推送、不合并、不修改main目录或main原账本；此前NOVA历史折叠、按钮箭头、输入区简化等未提交修改均保留。本轮源码和测试保持未提交，便于一起审查。

体验入口：http://127.0.0.1:3100/ 。若已登录，退出后看登录页；也可使用单独浏览器会话。后端8110健康，portfolio_ready=true，沿用独立.deps/frontend-preview/bank.db、offline模式，未重启服务。演示帮助可填入13800000001 / password123。

## 实施内容

- 新增frontend/src/components/LoginPage.tsx、LoginPage.css，App.tsx仅替换未登录渲染并接回原authenticate/注册/令牌存储及跳转。无新依赖、无后端改动。
- 白底、中央两行“NOVA / 你的银行智能体”、原定副标题、七个彩色有景深的倾斜胶囊均映射自批准样稿。手机保留四个装饰，其他隐藏；装饰aria-hidden且不接收指针事件。原工作台时钟保留，登录页时钟移除。
- 外层负责向左右散开与倾角，内层8秒轻漂浮。悬停14px、聚焦38px、输入目标46px，输入停止360ms回聚焦；800ms过渡。手机聚焦12px、输入16px。减少动画偏好关闭漂浮与位移，保留输入焦点边框。标题和表单不参与位移。
- 生产输入使用username/current-password/new-password语义，密码显示切换有可访问标签。密码只用于已有受控表单和鉴权，不用于动画计算、日志或持久化。
- 姓名/手机号/密码字段就近校验，错误通过aria-describedby关联并聚焦首个无效字段；服务端反馈原样沿现有message解析显示，注册错误正确标记为注册失败。
- 登录/注册加载时禁用字段、模式切换和提交。App增加同步请求锁，避免React状态更新前的连续提交。成功直接进入现有工作台，无人为动画延时。
- 初始字段改为空以匹配样稿；原演示说明放在“演示账号帮助”折叠入口，可主动填入演示账号，无自动提交。
- 样稿的假提交处理器、评审按钮、参考图片区均未进入正式页面。

## 实际验证

使用独立Playwright CLI会话login20及真实HTTP预览。设计稿此前仅有静态检查，不把设计阶段的file协议尝试当成已完成浏览器验收；下面全部为实现后的结果。

- npm run build通过（TypeScript和Vite）；git diff --check通过。保留既有大分块提示。
- tests/browser/login_redesign_check.js通过：巨型标题/七模块、无评审区与登录时钟、autocomplete、悬停14px/聚焦38px实际位移、输入目标状态与回收、跨字段不闪回、标题与表单稳定、密码切换、减少动画无位移/漂浮、字段校验和原帮助。
- 1920×1080、1440×900、900×900、390×844、320×568、390×400、720×450下表单无横向溢出，提交和错误可滚动访问；手机四装饰和12px聚焦位移实际验证。
- 真实8110接口：错误密码返回401并保留输入；正确演示账号登录成功；注册成功返回201并进入现有工作台；相同手机号重复注册反馈准确错误。只新增一个测试用户“前端登录验收”（19911361157）及其系统自动生成的零余额模拟账户，不删除该记录，不执行转账、购买或其他金融操作。
- UI隔离响应：503长错误、网络失败和可控等待，验证重试、错误换行、加载禁用与连续提交仅一请求；不是实际后端故障。完成后移除所有拦截。
- tests/browser/login_keyboard_check.js的6项检查通过：Tab到密码与显示按钮/提交按钮，Space显示隐藏密码，焦点切换保持散开，手机注册空字段反馈。
- tests/browser/bills_entry_regression.js的14项通过：登录后真实账单图表/往返/刷新、NOVA上下文示例、手机输入/关闭可达、非空和503隔离响应、恢复真实报告。未捕获异常0、金融操作请求0。
- 已目视检查最终桌面、最终手机、手机注册错误和加载截图，保留批准样稿构图；底部帮助为正式接入补充。

测试迭代记录：第一次320px断言把按钮最小宽度固定为250px，但扣除两侧留白和Windows滚动条后实际为248.8px且未溢出，改为检查可用范围和220px可操作宽度。加载检查最初把自动滚动误判为位移，改用文档坐标并容许不足1px的浏览器布局舍入。最后两类检查均通过。测试脚本要求独立演示环境；主检查每次成功执行会新增一个唯一测试用户，不适合生产。

## 截图

同一HTTP环境、相同尺寸的前后对照：

- output/playwright/login-before-desktop.png、login-final-desktop.png（1440×900）
- output/playwright/login-before-mobile.png、login-final-mobile.png（390×844）
- output/playwright/login-after-wide.png（1920×1080）
- output/playwright/login-focus-desktop.png、login-focus-mobile.png
- output/playwright/login-reduced-motion.png
- output/playwright/login-register.png、login-register-mobile.png、login-register-errors-mobile.png
- output/playwright/login-real-error.png、login-loading.png、login-long-error-mobile.png
- output/playwright/login-reflow-720.png

## 验证边界

720×450验证的是1440×900在200%缩放时等效布局视口的响应式重排，未使用浏览器原生缩放或系统文字放大；不宣称原生200%文字缩放已验收。390×400模拟键盘占用空间，未验证实体手机和系统键盘。输入46px为CSS目标及typing状态验证，短暂输入状态不会等待完整800ms过渡后再回收。未运行全仓测试、真实模型或部署，未修改后端鉴权规则。
