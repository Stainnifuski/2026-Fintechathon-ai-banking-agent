# 第三方声明

原有依赖以backend/requirements.txt、frontend/package.json及锁文件为准；本文件不是全项目SBOM。产品、模拟行情、界面和规则为原创，不复制银行截图或暗示真实机构背书。

| 增量组件 | 固定版本 / 使用 | 许可与来源 |
|---|---|---|
| AKShare | 1.19.1，可选市场数据适配 | MIT；https://github.com/akfamily/akshare ，发行LICENSE需随再分发保留 |
| llama-index-core | 0.14.25，可选TextNode；默认不启用embedding | MIT；https://github.com/run-llama/llama_index |
| DeepSeek兼容API | deepseek-flash，有限结构化意图/引用解释联调 | 服务接口，不再分发模型权重；团队有效配置，仅本地密钥 |
| react-markdown | 10.1.0，NOVA回答Markdown渲染 | MIT；https://github.com/remarkjs/react-markdown |
| remark-gfm | 4.0.1，NOVA回答表格与列表支持 | MIT；https://github.com/remarkjs/remark-gfm |
| ECharts及React封装 | 复用已有前端依赖、研究曲线 | 授权以既有锁定发行包LICENSE/NOTICE为准 |

代码许可证不授予完整数据集公开再分发权。真实行情来源AKShare适配东方财富，官方定义摘要来源国家统计局/人民银行；原始数据、缓存、数据库、备份、密钥和受限原文默认不提交。源站链接、字段、时间和用途见docs/wealth/data_sources.md和knowledge.md。部署/分发者需保留所有实际发行依赖NOTICE并核实数据使用条件。
