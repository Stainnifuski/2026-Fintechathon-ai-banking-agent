# 转账与收款视觉优化：开发与冻结交付（2026-10-23）

状态：正式实施及有界自测完成，源码冻结。以下准备部分为历史基线，当前交付结论见文末。用户为黄瑜；这是转账与收款新任务，不延续历史卡片或首页修改。

## 授权与现场

已完整读取主仓前端/转账与收款视觉优化任务_20261023.md，亲看用户参考截图 codex-clipboard-7ae64791-0712-45d0-8593-ea08afd10131.png。

- 工作树 D:/Project/Fintechathon-2026-frontend；分支codex/frontend-20261020；HEAD f4711a4c59c8759e533500d0bd5243c847c5bea8。
- git status仅原有未跟踪.playwright-cli/；未清理、重置或覆盖其他成果。
- 原3100 PID49148：前端树vite --host127.0.0.1 --port3100 --strictPort；8110 PID43268：uvicorn backend.app.main:app。未启停服务。
- /health返回status=ok、source_revision=f4711a4-frontend-preview、local_test_mode=false。
- 首页、导航、NOVA、卡片、订阅保护；不改私有配置或账本，不启动3000，不重启后端，不播种，不提交推送合并部署。

## 源码基线

证据根：D:/Project/Fintechathon-2026-demo/output/playwright/transfers-20261023/。

source-baseline.json保存frontend/src全部127个文件的SHA256。source-before/保存11份原字节副本：App.tsx、main.tsx、styles.css、homeWorkspace.css、cards.css、subscriptions.css、TransferContacts.tsx、AddContactForm.tsx、SecondFactor/index.tsx、services/bank.ts、services/securityState.ts。

已分段读完整App结构、TransferContacts、AddContactForm、SecondFactor，核对services/bank.ts相关类型及接口。准备阶段没有业务源码、样式、函数或条件修改。

## 独立真实GET与截图

使用Playwright技能，独立会话transfers_prepare23，在about:blank阶段安装/api/v1守卫后首次导航。GET/HEAD/OPTIONS真实读取，仅精确/auth/login允许已有演示账号登录；workspace/reset合成200且不转发，其余全部业务和模型写入阻断409。包括收款人resolve、转账预览及任务操作、联系人、收款、短信及旧对话POST也不放行。没有接管、刷新黄瑜IAB或其草稿。

真实GET结果见real-before-results.json：accounts200两项，recipients200空、transfer-recipients/history200空、payment-requests200空、transactions/history200空、contacts200空，totp/status200未绑定。没有页面脚本错误。仅自动workspace/reset一项写入被拦截，真实转发0；演示登录独立精确放行。不使用fixture。本次200不代表既有payment-requests间歇500已修复。

- before-1440x1000-full.png：实际全页1440×1849，已亲看，含标题、我的账户、创建转账、发起收款、收款申请、即时转账对话及最近流水。
- before-1920x1080-full.png：实际全页1920×1833，同样包含所有下方分区。
- before-contacts-full.png、before-contact-settings-full.png：只打开本地联系人/设置入口，真实GET空态，未操作联系人写入。

浏览器未改CSS以获取全页：原bank-shell、bank-workspace和workspace-content均overflow visible，直接fullPage截图可包含下方。标题计算字号44px，这是本轮待优化旧样式。head正确加载homeWorkspace、cards、subscriptions、styles.css，区别于此前常驻旧App问题。准备浏览器现停1440转账页，联系人面板已关闭、写入守卫保持。

## 静态功能边界初稿（以接口角色最终清单为准）

1. 我的账户：账户类型、掩码卡号、完整模拟收款号、余额、可用、占款、今日已转/限额全部保留，信用账户不混称存款。
2. 创建转账：付款账户、姓名/备注名、银行卡/手机号模式和独立号码值、金额、可选备注、即时/定时、北京时间日期输入及两分钟快捷设置、生成确认信息。先resolve后建任务；付款账户与收款账户映射、note_source、幂等键及时区转换均不改。
3. 联系人与设置：已保存/历史人选择、保存历史人、备注名、手工添加、改备注、删除、清除历史及原原生确认；清历史不删流水的说明保留。resolveContact是POST，未来样例也必须隔离。
4. 发起收款：多选来源合并、收款账户、每人金额/AA总金额、均分开关、分精确性和总额提示、备注、禁用条件；添加联系人入口保留。
5. 收款申请：入/出向、时间、原状态、金额和备注、付款账户；PENDING/ACCEPTED接受或确认付款、拒绝、取消及跳转高亮。首次接受仅建安全任务，后续授权/强验证保持。
6. 转账任务：银行消息、付款尾号、收款人及手机号/卡尾号、金额、备注、风险、定时时间、最终备注；修改/清空备注、保存重新确认、返回；allowed_actions驱动取消/确认/验证/核对；FINISHED及轮询阶段保持。版本号、控制权、幂等、接受链及任务恢复不改。
7. 验证弹窗：TOTP/SMS方法、6位过滤/Enter、challenge绑定、倒计时、短信收件箱、安全锁和验证错误、未绑定跳转绑定保持；共享SecondFactor逻辑及原绑定弹窗不动。
8. 原即时转账对话：新建会话、消息记录、模式说明、候选选择、输入/Enter、发送、原消息重试；与NOVA并存，retryChat和clarification对任务动作限制保留，不删除此区。
9. 最近流水：最近8笔、类型映射、对手方、备注、北京时间、状态和方向金额；空态不伪造。全局反馈、忙碌状态、键盘和焦点可达保留。

## 建议的最小局部实施边界（尚未实施）

待交接后优先新增transfers.css，以转账独有父类（如transfers-page）和transfers-page-intro限定作用域，覆盖现有三组grid、账户卡、表单、联系人二级区、收款列表、任务及旧对话/流水；只增加必要样式类，不搬迁字段顺序或重写组件。窄内容宽度时原grid单列、按钮换行，不能新增更多菜单或折叠藏入口。

转账MFA位于页面分区外，若交接要求其视觉统一，仅给原弹窗添加明确transfers-mfa类以局部覆盖；不得使用全局.card/.confirm/SecondFactor选择器影响卡片、订阅或绑定验证器。TransferContacts/AddContactForm可通过父作用域直接样式化，优先不修改其文件。App只做样式import、转账容器/标题/分区及必要弹窗类标记；不改handler、条件、effect、请求、共享时间函数或NOVA上下文。

准备不提前创建业务CSS，不实施文案或布局变更，不执行金融长测。正式交接后再决定唯一方案及精确源码清单；完成后需冷加载/硬刷新与隔离有界自测，防止只在HMR状态偶然正确。


## 正式实施与冻结交付（2026-10-23）

状态：已收到主聊天正式实施授权，完整阅读视觉交接及功能保留清单，完成局部视觉实施、有界自测并冻结。文前只读准备部分为历史记录，不代表当前仍未实施。源码冻结后只补证据和本文，没有再次修改业务源码。

### 精确文件与实现

- frontend/src/App.tsx：新增transfers.css导入；原transfer Fragment改无业务语义div.transfers-page，增加标题、账户/创建/收款/任务/旧对话/流水局部类；原全局MFA遮罩增加transfers-mfa类，保持条件树外位置与生命周期。原文案全部保留。
- frontend/src/transfers.css：仅.transfers-page/.transfers-mfa作用域；28px标题、16px白卡、胶囊按钮；原三组grid在可用内容宽≥900px双列，否则单列；align-items:start、不拉等高、不重排；联系人、AA、状态、任务、原对话及MFA局部美化。
- docs/frontend/transfers_visual_20261023.md：本开发记录。

函数/state/effect/条件/API/props/时间/幂等/恢复原样，TransferContacts、AddContactForm、SecondFactor及绑定弹窗源码未改。父wrapper只包原转账分区，MFA没有搬入分区。

source-freeze.json：127个基线文件中126个字节不变；App反向撤销本轮样式标记/包装/导入后与改前全文完全一致。transfers-only.diff为本轮完整差异，包括新增CSS，不混历史卡片/订阅成果。

冻结SHA256：

- App.tsx：40A44D8F50274DC556AB783C5C2DC63A8B08671495D5FF0342DA5DA8A72E91A5
- transfers.css：51099C483D4720277A56B0DDA8B3D59BA271098D04050A5B0FBB9CD1985D6D89

### 构建与真实GET

最终冻结版本npm run build通过（tsc+vite999模块），git diff --check通过。未启动构建预览服务或重启现有服务。

独立浏览器transfers_prepare23首次导航前已安装写入守卫；正式成品采集又通过CDP禁用缓存并整页reload，不以HMR局部更新作为完成依据。实际DOM有transfers-page及transfers.css，标题28px、按钮圆角999px。real-after-results.json记录：

|视口宽|可用内容宽|原grid计算列|文档宽|
|---|---|---|---|
|1440|708|708单列|1440|
|1920|1060|520+520双列|1920|
|1101|411|411单列|1101|
|390|362|362单列|390|

1920首组两卡实际高465.53/719.66，自然收尾。1440/1920全页包含下方收款、旧即时对话与流水，主聊天已亲看；开发亦亲看。账户200两项、收款/流水/联系人真实200空态，没有fixture伪装真实数据。原payment-requests间歇500没有扩修，本次200不代表全局稳定性。

after-real-390-full.png曾因切视口时NOVA尚展开遮挡，不作为可用性通过证据。最终以after-real-390-nova-hidden-full.png为准：在390宽整页刷新，明确确认main无nova-open，documentWidth=390，等待动画结束；全部分区均可见，未改NOVA。全页长图中的底部固定导航位置属于原布局；可达性另以滚动后的trial点击验证，不从长图推断。

### 有界隔离自测（不是银行验收）

fixture-setup.js对所有业务/模型写入统一记录并合成，包含resolve/预览/confirm/accept/verify/reconcile/联系人/收款/chat/SMS；任何此类写入无route.continue。GET联系人/历史/申请/任务/流水等明确替身，其余GET仍真实。workspace/reset也合成。未调用真实转账、定时、联系人变更、短信、模型或银行执行。

已执行并有请求或截图证据：

- people/settings：长联系人、选择后两模式号码；历史保存、改备注PATCH、删除DELETE、清历史原生确认后DELETE、手工添加POST。原生confirm提示仍为清除历史列表且交易流水保留。添加上/下部补图含真实提交按钮，并trial命中及隔离提交。
- 即时phone请求与scheduled card请求；切换两套号码状态保留。定时原datetime-local/约2分钟按钮，payload含UTC ISO scheduled_at；操作仅替身，不会真实到期执行。
- AA两人0.01不可均分时错误和提交禁用；总额100可均分后batch payload含split_equally与同批幂等键。七项申请矩阵：本人PENDING接受/拒绝、ACCEPTED确认付款、出向PENDING取消及四种终态无按钮。实际隔离点击拒绝、取消及ACCEPTED付款confirm→accept链。
- 任务确认、清空备注→note=null/note_source=cleared并重新确认；红色confirm→verify；SMS purpose=transfer/reference_id、脱敏号码/有效期、倒计时和收件箱；安全锁使共享验证选择禁用/提示，解除后重新选因子；verify payload保留request_id/state_version/challenge_id/proof/method，后续accept链保持。
- 核对reconcile原请求及返回；原对话503保留待重试状态，重试完整payload（含request_id/operation_id）逐字段相等；候选选择继续使用原session/version/candidate_id。9条替身流水仍仅显示原8条，长备注/多状态保留。
- 390明确隐藏NOVA后，转账人/设置/生成、AA、新建旧会话/发送通过trial点击（包含滚动和遮挡命中检查）。二级联系人图保留。MFA390×600及390×400：短屏卡片x19.5、y21.41、宽351、高357.19（400视口），输入、提交和取消可命中；原遮罩/位置/关闭逻辑保持。

fixture-rest-results.json、fixture-final-results.json及supplement-results.json保留结果。fixture-final-results的连续29个写入包含前期全部实际操作，forwardedWrites=0；补图追加少量隔离操作同样不转发。最初fixture-forms在原生confirm处CLI提前返回脚本文本而未给最终结果，因此没有将其整个脚本宣称一次通过；按后续状态/请求日志确认已执行到即时phone建单，然后从定时分支单独接续。一次select标签精确匹配等待失败已修正测试为原combobox定位；没有改业务源码。

覆盖限制：未真实验收银行/模型/短信/TOTP；未穷举所有phase、网络竞争、全量联系人失败、付款PENDING首次接受链、TOTP未绑定跳转、所有Enter与跨会话恢复组合。这些原逻辑靠全文一致性证明未改，不冒称全部端到端通过。安全锁样例主要覆盖SecondFactor提示/禁用，不将其等同真实风控或全部全局banner验收。既有风险按接口清单保留。

### 新增图供视觉补审

统一目录 D:/Project/Fintechathon-2026-demo/output/playwright/transfers-20261023/：

- 真实成品：after-real-1440-full.png、after-real-1920-full.png、after-real-1101-full.png、after-real-390-nova-hidden-full.png、after-real-contacts-full.png。
- 完整添加：fixture-add-contact-top.png、fixture-add-contact-submit.png（原fixture-add-contact.png仅上部，不作为底部覆盖）。
- 窄屏：fixture-390-nova-hidden-full.png、fixture-390-contacts.png、fixture-mfa-390x400-viewport.png（直接视口图，已亲看）、fixture-mfa-390-short.png。
- 二级：fixture-contacts-long.png、fixture-contact-settings.png、fixture-scheduled.png、fixture-aa-exact.png、fixture-aa-indivisible.png、fixture-payment-states.png、fixture-task-confirm.png、fixture-task-note-clear.png、fixture-mfa.png、fixture-mfa-locked.png、fixture-mfa-sms.png、fixture-reconcile.png、fixture-chat-retry.png。
- 回归：regression-home-1440.png、regression-cards-1440.png、regression-subscriptions-1440.png。保护源码指纹不变，新CSS无跨页裸选择器；截图用于人工对照，未宣称逐像素相等。卡片回归图已亲看。

### 收尾状态

已移除全部fixture路由，清掉本独立浏览器仅由本次样例创建的QA任务/对话本地键，恢复真实GET与所有写入阻断，冷刷新后真实accounts200两项且页面无“隔离”标签，最终停1440转账页。restored-real-results.json记录收尾真实GET（含payment-requests200）及reset拦截。没有动黄瑜tab、用户草稿或原账本。

源码最后复核仍为冻结哈希；无提交/推送/合并/部署，无服务启停、3000、播种、私有配置或main修改。另有独立角色在本目录写入independent-*证据，原样保留，不冒充开发脚本产物。若主聊天提出源码阻断，先告知其冻结变化再修；否则交三路复核与黄瑜亲看。
