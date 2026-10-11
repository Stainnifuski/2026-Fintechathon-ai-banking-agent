# NOVA 回答 Markdown 渲染验证（2026-10-10）

问题：NOVA 的模型回答以纯文本段落显示，Markdown 标题、加粗、表格分隔符直接出现在消息中，长账单报告不易阅读。

实现：新增 NovaMarkdown 组件，使用 react-markdown 10.1.0 和 remark-gfm 4.0.1 渲染助手回答；用户消息保持纯文本。标题、段落、列表、引用、代码与表格使用 NOVA 范围内样式，抽屉最大宽度从430px调整为600px。表格提供独立水平滚动和键盘焦点，长流水号可换行，手机仍按屏幕宽度展示，并保留关闭按钮。查询记录保留独立折叠区。原始HTML不渲染，不启用远程图片加载，外部链接使用noopener/noreferrer和默认安全URL处理。

验证：

- frontend 下 npm run build：TypeScript/Vite通过；依然有原有大包提示。
- 使用真实 NovaMarkdown 组件进行 React 服务端静态渲染检查：月度账单示例正确生成h1/h2、两张表格、strong、有序列表和嵌套无序列表，Markdown分隔符不作为正文显示。
- 检查表格滚动容器；原始script、javascript链接、远程图片没有生成可执行元素。
- git diff --check：通过。

未进行浏览器截图或视觉自动化验收。本次没有改动模型调用及银行数据逻辑，也未重新运行后端测试。前端依赖及锁文件、第三方声明同步更新；已有本地构建已更新，运行中的Docker前端需重建才能生效。
