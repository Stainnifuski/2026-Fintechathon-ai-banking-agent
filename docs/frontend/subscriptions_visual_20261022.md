# 订阅管理视觉整理交付（2026-10-22）

## 状态与范围

工作树 D:/Project/Fintechathon-2026-frontend，分支 codex/frontend-20261020，基线 1738963240bd135885c28119f16eaf327daffafb。本轮实现保留为本地未提交；业务源码现已冻结，等待主 agent 独立复核。3100 原服务仍运行，未启动/重启服务，未启动 main 3000，未修改 main、PR19，未 commit/push/merge/部署。

业务文件仅两项：

- frontend/src/App.tsx：新增局部 CSS 导入、订阅页标题 class；选中按钮改“已选中 · 再点取消选择”。
- frontend/src/subscriptions.css：新增订阅页局部样式。单白卡、28px标题、16px商户、13px说明、轻边框、粉色选择按钮、近黑模拟扣费按钮、状态色块及页内暖色确认区。依卡片内容宽度720px切换操作区布局；按钮保持内容宽度，不拉成长条。

没有修改共享 styles.css、homeWorkspace.css、首页/导航/NOVA组件、服务请求或业务函数。所有 CSS 选择器限定订阅卡或订阅标题。

## 功能保留证据

将 App.tsx 的三处允许变化（导入、class、选中短文案）反向替换后，与 git show 1738963:frontend/src/App.tsx 完整文本严格相等，校验通过。因此原回调、条件字段、pageContext、aria-pressed、完整列表顺序、isActing、token/chargeId生命周期、跨页与离页状态及安全提示挂载均未改。

独立浏览器验证：全列表、选择/取消选择（无业务请求）、已取消项保留且只有选择按钮、条件来源说明、空列表；两组页内确认及完整影响文字；返回不提交；处理中入口及返回禁用、选择按钮仍可用；模拟扣费请求版本/幂等号、取消请求版本/预览token不变；合成409失败后保留确认并显示原错误。取消与扣费仍为页内二次确认，不新增弹窗或页面。

## 验证隔离与结果

使用独立 Playwright 会话 subscriptions_baseline22，未接管用户浏览器。首次导航前注册全API写入隔离：reset仅合成200/{}，其他变更和模型请求阻断；登录是唯一真实POST。所有订阅预览均为显式fixture，实际取消/扣费提交仅合成409，不调用后端。未执行旧交易/reset脚本，未修改真实账本。

- 最终 npm run build（tsc + vite build）通过；git diff --check通过。
- 1440×1000、1101×1000、390×1000 长名称/账户换行、已取消态、来源说明及扣费确认检查通过。卡片 scrollWidth/clientWidth 分别为706/706、409/409、360/360，整页无水平溢出。
- 390两组确认经滚动可见，确认按钮中心命中检查均通过；固定底部导航保持原样。长页全页截图中固定导航可能出现在中间，另有实际滚动视口截图证明确认区可达。
- 已亲自查看改前、最终1440/1920、选中、取消确认、1101长名称、390滚动确认及空态截图。

## 真实读取收尾与500限制

视觉验收中一次真实 GET /api/v1/payment-requests 观察到HTTP500，浏览器记录“Failed to load resource: ...500”，前端api.ts输出“服务器内部错误”，全局提示为“Request failed with status code 500”。当时未保存原始500正文，不能补造响应内容或推断根因。没有修复该接口。本轮旧日志不足以计算长期发生率，只能报告已观察到至少1次。

源码 load() 仍将 accounts/recipients/history/paymentRequests/listSubscriptions/transferRecipientHistory 放在同一 Promise.all：其中paymentRequests失败会使整组进入原错误分支，阻止该轮统一setSubscriptions等更新。首次加载可能保持空列表；若已有旧列表则可能继续显示旧数据。之前截图保留列表不代表500不阻断load。

收尾已移除所有GET数据替身（包括payment-requests空列表与订阅fixtures），保留纯业务写入隔离。随后3次完整真实只读页面重载：payment-requests均HTTP200、原始正文[]；subscriptions均HTTP200、真实2项（SUB001网易云音乐15.00、SUB002腾讯视频25.00），实际渲染2行、无全局错误。三次复核窗口500为0/3，不代表此前问题已修复或未来不会复现。三次reset均被隔离，仅合成空结果。最后浏览器停在1440×1000真实订阅列表，3100可供直接查看。

## 截图、数据与本轮差异

证据根目录：D:/Project/Fintechathon-2026-demo/output/playwright/subscriptions-20261022/。

- frontend-1738963-before-1440x1000.png：前端分支改前基线，含NOVA侧栏；不是main运行截图。
- frontend-1738963-after-1440x1000.png：同尺寸、相同两项只读订阅数据的改后图（视觉期间显式重放改前数据）。
- real-read-attempt-1/2/3-1440x1000.png：移除GET替身后的真实读取截图，最终第3张已人工查看。
- after-1920x1080.png：宽屏两行操作排列。
- selected-1440.png、charge-confirm-1440.png、cancel-confirm-1440.png、busy-charge-1440.png、busy-cancel-1440.png、empty-1440.png。
- long-cancelled-1440/1101/390.png、long-confirm-1440/1101/390.png：显式长名称/已取消fixture，非真实客户数据。
- mobile-scrolled-charge.png、mobile-scrolled-cancel.png：390滚动后真实视口，fixture。
- subscriptions-readonly-baseline.json：改前只读数据，不含凭据。
- this-round-source.diff：仅App本轮差异与新CSS内容；不包含此前已推送首页成果。
- source-fingerprints.json：本轮源码SHA256。
- verify.js、mobile-confirm.js、real-read.js：有界验收脚本；复用前必须保持首次导航前写入隔离。不得绕过隔离对真实接口执行取消/扣费。

## 未验范围

未执行真实取消、扣费、reset或模型调用；未运行真实成功交易回写/幂等重试/安全锁与陈旧版本等后端集成测试。跨账单直达、离页pending保留、登出清理通过严格源码未变核对，未在浏览器全链路重跑。无main运行对比。500正文和根因未取到，不声称修复。独立角色仍需按正式功能清单和体验流程复核。

## 源码指纹（SHA256）

- frontend/src/App.tsx：7C2DA8801676A807B93321D3878E0168232F006B912763F0830D25E1F2289A82
- frontend/src/subscriptions.css：6686D8CAAA48EAAFBA710BB859C575143ED3BC54E53541A53EC3B84912D5FFC0
- frontend/src/styles.css：D4FB72F7E205DE879386283BC565AF4E087D79FCCD91E09574774C7FB0C4C02B
- frontend/src/homeWorkspace.css：3D9EEF55EF6602A10F1FC3E7F191DCCB0FE9B64C74340B158697EFF3D5EA059E
- frontend/src/components/NovaChat.tsx：73EADAD5F82E3D764EB39DA19E39DEAADBA268BFAFDB1E40D714286483594F05

改前App指纹：D2768BE481F6C27F0E08D6526FF5012ED784AE4436D088F6A450D840D37BF01F。其余共享样式/NOVA无本轮差异。
