# 第三方声明

依赖以[后端依赖清单](backend/requirements.txt)、[前端依赖清单](frontend/package.json)及锁文件为准；本文件不是全项目SBOM。界面中的银行业务及交易为比赛模拟，不暗示真实机构背书。公开基金资料及行情保留其第三方来源，不作原创或自由再分发声明。

| 组件／服务 | 固定版本 / 使用 | 许可与来源 |
|---|---|---|
| AKShare | 1.19.1，可选市场数据适配 | MIT；https://github.com/akfamily/akshare ，发行LICENSE需随再分发保留 |
| SenseVoice代码 | 固定提交 `586bbdb2b052f4fe19322581825228b6810a9fb3`，本地CPU语音识别实现 | [官方代码及该版本MIT许可](https://github.com/QwenAudio/SenseVoice/blob/586bbdb2b052f4fe19322581825228b6810a9fb3/LICENSE)；原文保留在[UPSTREAM_LICENSE.txt](services/sensevoice/UPSTREAM_LICENSE.txt)及语音镜像中 |
| SenseVoiceSmall模型 | `FunAudioLLM/SenseVoiceSmall`，固定版本 `3847d57b6bdf2dd8875cb1508d2af43d80a16bf7` | [该版本模型卡](https://huggingface.co/FunAudioLLM/SenseVoiceSmall/blob/3847d57b6bdf2dd8875cb1508d2af43d80a16bf7/README.md)标注独立[模型许可](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE)，不沿用代码MIT；权重不随源码包提交 |
| llama-index-core | 0.14.25，可选TextNode；默认不启用embedding | MIT；https://github.com/run-llama/llama_index |
| DeepSeek兼容API | 模型名按部署配置，用于自然语言理解、任务规划及知识解释 | [服务来源](https://www.deepseek.com/)；服务接口，不再分发模型权重；服务条款单独适用，真实密钥不提交 |
| react-markdown | 10.1.0，NOVA回答Markdown渲染 | MIT；https://github.com/remarkjs/react-markdown |
| remark-gfm | 4.0.1，NOVA回答表格与列表支持 | MIT；https://github.com/remarkjs/remark-gfm |
| ECharts | 账单分类／趋势和产品净值图表（当前组件直接引用ECharts） | 授权以既有锁定发行包LICENSE/NOTICE为准 |

## 基础框架与运行组件

以下组件用于当前正式入口或部署构建；许可原文以实际安装／分发版本为准。官方许可链接用于查阅，不将它们统一改授为本项目许可。

| 组件 | 实际用途 | 官方来源／许可入口 |
|---|---|---|
| React、React DOM | 挂载和渲染Web应用与交互组件 | [React](https://github.com/facebook/react)、[许可](https://github.com/facebook/react/blob/main/LICENSE) |
| TypeScript、Vite | 类型检查、前端开发和构建 | [TypeScript](https://github.com/microsoft/TypeScript)、[Vite](https://github.com/vitejs/vite)；见各项目LICENSE |
| FastAPI、Pydantic、Uvicorn | HTTP接口、结构化数据校验、ASGI服务 | [FastAPI](https://github.com/fastapi/fastapi)、[Pydantic](https://github.com/pydantic/pydantic)、[Uvicorn](https://github.com/encode/uvicorn)；见各项目LICENSE |
| PostgreSQL、SQLAlchemy、asyncpg | 正式Compose数据库与异步访问 | [PostgreSQL许可](https://www.postgresql.org/about/licence/)、[SQLAlchemy](https://github.com/sqlalchemy/sqlalchemy)、[asyncpg](https://github.com/MagicStack/asyncpg) |
| Axios、HTTPX | 前端请求和后端HTTP客户端 | [Axios](https://github.com/axios/axios)、[HTTPX](https://github.com/encode/httpx)；见各项目LICENSE |
| Docker Compose、Nginx | 服务编排、静态页面和同源代理 | [Compose](https://github.com/docker/compose)、[Nginx](https://nginx.org/)，发行包及镜像内许可分别适用 |

未在README展开的认证、日历、语音运行时及其他直接／传递依赖仍须遵守各自许可。仅被依赖清单列出、开发测试使用或历史入口保留的组件，不据此宣称是当前正式功能的实现依据。

代码许可证不授予完整数据集公开再分发权。真实行情来源AKShare适配东方财富，官方定义摘要来源国家统计局/人民银行；原始数据、缓存、数据库、备份、密钥和受限原文默认不提交。源站链接、字段、时间和用途见docs/wealth/data_sources.md和knowledge.md。部署/分发者需保留所有实际发行依赖NOTICE并核实数据使用条件。
