# NOVA · AI Banking Agent

通过自然语言查询银行资料、准备业务任务，并在本人确认和风险分级验证后办理模拟银行业务。项目面向银行AI智能体赛题，提供统一Web工作台。

> 系统使用比赛模拟账户、资金、交易、商户及短信通道，公开基金资料和净值标明独立来源及日期。配置DeepSeek API后启用自然语言理解、任务规划和知识解释。

![NOVA登录后的首页：业务导航、自然语言输入与快捷示例](docs/images/nova-home.png)

登录后的NOVA首页：通过自然语言或快捷示例提出需求，也可从左侧进入业务工作台；交易操作按风险等级完成本人确认和验证。

## 快速导航

- [部署说明：从准备环境到成功登录](docs/setup/deployment.md)
- [测试用例：逐步输入、点击与预期结果](docs/testing/test_cases.md)
- [开发者自动化测试与证据索引](docs/testing/automated_tests.md)
- [技术文档：系统架构图、核心算法与安全设计](技术文档.md)
- [安全自评报告：权限分级、安全措施与风险处置](安全自评报告.md)
- [第三方声明：组件来源、许可与数据使用说明](THIRD_PARTY_NOTICES.md)

从[部署说明](docs/setup/deployment.md)启动项目，再按[测试用例](docs/testing/test_cases.md)体验各场景。实现原理和安全评估分别见技术文档与安全自评报告。

## 项目解决的问题

把分散的银行入口变成“表达需求—查看任务—本人核对—执行—核对结果”的统一流程。用户既可以让NOVA理解模糊表达、补充上下文和规划多目标任务，也可以在工作台手动办理；人工接管保留已有输入和操作编号。模型负责理解和规划，银行服务核验身份、金额、资格、版本及授权，办理结果可通过业务状态、流水和操作记录核对。

## 场景与当前能力

| 赛题场景 | 当前可体验的能力 | 使用说明 |
|---|---|---|
| 智能转账 | 按联系人／手机号准备转账，多轮修改，单次定时转账，AA收款及原结果核对 | 转账须本人确认，达到红色条件须MFA；定时转账按指定时点单次执行 |
| 账单分析 | 月度／年度统计、分类和变化追溯、异常核对、预算与订阅关联查询 | 新库先生成模拟流水，再体验统计、变化追溯和异常核对 |
| 理财操作 | 本人风险评估、整体规划、类别／名称／代码候选、详情与比较、申购、赎回、持仓和整体收益 | 选择产品并核验有效评估、产品资格和金额后，由本人确认并完成MFA |
| 卡片管理 | 查询、虚拟卡申请、限额／渠道调整、锁卡、解锁、挂失及补卡任务 | 高风险操作执行强验证；临时锁卡经解锁恢复，挂失卡通过补卡流程办理 |
| 订阅／代扣 | 已有订阅及预计月费查询、扣费关联核对、本人确认取消、模拟扣费和测试恢复 | 查询及管理模拟银行已有订阅，核对扣费与预计月费 |
| 跨场景联动 | 多目标任务图及按依赖推进；账单＋订阅、日程＋订阅查询；日程、资金锁定任务、模拟花店／蛋糕店与银行付款 | 按能力目录规划任务，商户下单及付款由本人核对和授权 |

另支持语音转文字、历史会话、业务上下文、操作审计、中断与人工接管。通过浏览器Web应用使用。详细步骤和可观察判据见 [测试用例](docs/testing/test_cases.md)。

## 主要实现特点

- **受控办理**：自然语言准备任务，可信界面完成授权，查询、本人确认和强验证按明确步骤衔接。
- **可干预的规划**：多目标呈现依赖及状态；本人可修改草稿、暂停后续步骤或接管，已完成交易按银行最终状态保留。
- **事实可核对**：回复使用工具结果，分别展示总资产和可支付现金；按成功、失败及结果待核对状态组织回复，响应中断时沿原操作编号核对银行结果。
- **配置可解释**：理财规划参考理查德·塞勒的心理账户理论及Das、马科维茨等人的整体协调研究，运用凸优化及持仓约束投影，将个人事实和偏好转成三板块配置；目标产品推荐采用逐产品候选和金额填报流程。

理财模型将资金用途、风险承受能力、现有资产和投资偏好转为可核对、可调整的活钱／稳健／进取配置。研究依据见[理财知识说明](backend/app/knowledge/wealth_planning.md)，算法实现和验证结果见[技术文档](技术文档.md)。

## 安全设计原则与参考

系统围绕幻觉防护、异常熔断、安全锁定、提示词注入防范、资料投毒防范、防篡改和越权防范建立分层控制。模型负责理解与规划，服务端通过权限分级、事实核验、资料签名、发布清单校验、本人确认及MFA控制业务执行；操作审计和人工接管贯穿任务全过程。认证会话与服务端策略授予业务权限，银行状态和审计记录提供执行依据。

设计参考国家金融监督管理总局2026年6月18日发布的 [《关于银行业保险业人工智能安全开发应用的指导意见》](https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=1261784&generaltype=1&itemId=4216)（金发〔2026〕8号）中的安全治理方向。具体机制、权限分级和风险处置见[技术文档](技术文档.md)与[安全自评报告](安全自评报告.md)。

## 安装与启动

默认使用Docker Compose部署，统一管理前端、后端、数据库和语音服务；本机运行步骤见[本机部署方案](docs/setup/deployment.md#5-无docker部署本机运行非推荐方案)。

1. 安装并启动Docker，确认 `docker --version` 和 `docker compose version` 可运行。
2. 首次部署解压完整源码，在根目录复制 `.env.example` 为 `.env`，填写自己的DeepSeek API Key；已有部署保留原配置，真实密钥由本机配置管理。
3. Windows运行 `start_bank.cmd`；Linux／macOS运行 `bash scripts/start_bank.sh`。默认启动包含SenseVoice语音服务，首次构建及模型下载需要网络。
4. 启动器报告成功后访问 [http://localhost:3000/](http://localhost:3000/)，登录并按 [测试用例](docs/testing/test_cases.md) 体验。

详细的逐平台命令、成功判据、演示账号、数据准备、更新停止和排障见 [部署说明](docs/setup/deployment.md)。启动后分别检查网页登录、服务健康和DeepSeek模型连通性；源码更新通过启动器完成镜像重建和加载。

## 产品资料与业务流程

固定资料包随源码提供，包含806只真实基金（债券242、混合501、权益63）及19个模拟银行产品定义；购买资格、有效净值、交易窗口及额度按产品逐项核验。项目使用模拟适当性评级，当前目录涵盖普通债券、混合和权益基金及模拟银行产品。资料升级见 [固定资料与旧库保护](docs/setup/wealth_compact_upgrade.md)。

新申赎使用最近已公布有效NAV，服务端核验持有期、交易窗口、额度及资格后，由本人确认并完成MFA，随后模拟即时记账，订单进入最终成交状态。收益按整体产品口径展示。语音识别文字进入输入框，由本人检查后手动发送；语音服务与文字输入独立运行。

## 仓库结构

```text
README.md                    项目入口和体验导航
backend/                     API、Agent、银行服务与安全实现
frontend/                    Web工作台与可信操作卡片
security/                    共用权限定义
config/、data/                产品配置与随源码固定资料
scripts/                     启动、安装和开发工具
tests/                       后端与浏览器测试代码
frontend/tests/              前端逻辑与组件测试
docs/setup/deployment.md      面向评委的部署步骤
docs/testing/test_cases.md    测试用例（手工操作步骤与预期结果）
docs/testing/automated_tests.md  开发者自动测试及证据索引
技术文档.md                  架构图、核心算法和安全设计
安全自评报告.md              权限分级、安全措施及风险处置
```

仓库保留技术文档和安全自评报告的Markdown原稿，便于随源码维护和导出。历史研究与验收资料按各自标注的日期、版本和环境查阅。

## 测试

评委按 [测试用例](docs/testing/test_cases.md) 体验输入、任务、授权与结果；开发者按 [自动化测试说明](docs/testing/automated_tests.md) 运行对应测试。测试索引列明模型配置、数据库、执行环境和验证内容。

## 主要技术与第三方组件

主要组件按业务用途、源码引用和构建配置列示，依赖清单及锁文件记录对应版本。

| 组件／服务 | 本项目用途 | 官方来源 |
|---|---|---|
| React＋TypeScript | 登录页、NOVA对话、业务工作台与操作卡片交互 | [React](https://github.com/facebook/react)、[TypeScript](https://github.com/microsoft/TypeScript) |
| Vite | 前端开发服务及生产资源构建 | [官方项目](https://github.com/vitejs/vite) |
| FastAPI＋Pydantic | 后端业务／Agent接口、请求与结构化模型输出校验 | [FastAPI](https://github.com/fastapi/fastapi)、[Pydantic](https://github.com/pydantic/pydantic) |
| PostgreSQL＋SQLAlchemy | 持久化账户、模拟交易、任务、会话和审计，提供异步数据库访问 | [PostgreSQL](https://www.postgresql.org/)、[SQLAlchemy](https://github.com/sqlalchemy/sqlalchemy) |
| ECharts | 账单分类与趋势图、产品净值曲线 | [官方项目](https://github.com/apache/echarts) |
| react-markdown＋remark-gfm | 渲染NOVA回答中的Markdown、表格和列表 | [react-markdown](https://github.com/remarkjs/react-markdown)、[remark-gfm](https://github.com/remarkjs/remark-gfm) |
| Docker Compose＋Nginx | 编排前后端、数据库及语音服务，提供前端静态资源与同源接口代理 | [Compose](https://github.com/docker/compose)、[Nginx](https://nginx.org/) |
| SenseVoice／SenseVoiceSmall | 本地CPU语音转文字 | [SenseVoice](https://github.com/QwenAudio/SenseVoice)、[SenseVoiceSmall模型](https://huggingface.co/FunAudioLLM/SenseVoiceSmall/tree/3847d57b6bdf2dd8875cb1508d2af43d80a16bf7) |
| AKShare | 可选市场数据获取适配及资料准备 | [官方项目](https://github.com/akfamily/akshare) |
| DeepSeek API | 自然语言理解、任务规划及知识解释 | [DeepSeek](https://www.deepseek.com/) |

完整依赖见依赖清单和锁文件，组件来源、许可及数据使用说明见 [第三方声明](THIRD_PARTY_NOTICES.md)，语音部署见 [语音说明](docs/setup/voice_input.md)。源码包提供应用代码和固定资料，各部署环境独立管理API密钥、业务数据库及本地研究资料。代码、模型及数据分别适用其来源对应的许可与使用条件。
