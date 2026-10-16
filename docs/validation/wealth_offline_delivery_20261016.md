# 固定资料、安装器和交接发布核对

基线为已合并PR #12的main `4ae64ab990bfc8d79a160be6735f54ed0bb5b7ea`。本次只整理安装/交接和README，不改理财算法、交易或原工作区配置，不恢复采集。

## 实际包范围

直接读取最终ZIP的gzip快照及全部paper_products配置：真实货币条目0，固定1元计价模式0；有效静态普通池13,578，债券4,093、混合8,444、股票1,041；模拟银行补充19。目录总数不等于可交易数量，运行资格及报价仍动态核验。实际记录`.deps/final_round/release-money-audit.json`，不是仅据README推断。

## 安装器与接口材料

- Windows入口`install_wealth_offline.cmd`→PowerShell→Python；固定ZIP通过SHA256核验后调用原完整schema/公共配置/版本安装校验，不覆盖源码config、.env或运行数据库。
- 独立空目录禁网实测首次安装、重复安装、损坏包拒绝、配置冲突拒绝及原快照保留通过，49.48秒；Windows PowerShell至Python非交互整链也实际通过。没有GUI双击/目录选择点击录像，不冒充已做GUI点击验收。
- 可移植拒绝测试`tests/unit/test_wealth_offline_installer.py`验证损坏包、错误目录、源码版本不匹配和固定版本一致。
- 交接材料含完整38个业务节点编号及Mermaid图、11种动作各自schema、实际OpenAPI的46个相关路径与68个引用schema。5个合法动作样例及2个非法参数样例已按真实Pydantic校验；不把静态schema核对算新LLM联调。
- 之前PR #12的156项披露/风险/双库回归未受本次业务代码修改影响，作为原版本证据保留，不重复宣称本次重跑。

## 分发和安全边界

固定资料ZIP不进入Git历史，通过版本化GitHub Release资产分发；源码保留安装器、接口/DAG文档及README下载入口。完整交接资产含数据ZIP及安装器，源码单独取得；下载后仍需项目Python依赖、数据库、合法本人账号和MFA配置。资料包没有密钥、密码、运行库、原始采集缓存或真实货币产品。

固定资料SHA256：`63094dcbefbc4e92745859db095458fa6e29a10019844b17fe4bc41bc8655184`。Release标签`wealth-offline-data-20261015-v2`，版本以`config/wealth/portfolio_content_binding_v2.json`为准。README删除过期的本地收尾/待PR阶段说明及旧合成60份额初始化路径，保留当前产品边界和统一NLP待接事实。

新源码交付通过独立PR，不自动合并main；数据Release发布与代码PR审核是不同操作，不能称为安装器已合入main。
