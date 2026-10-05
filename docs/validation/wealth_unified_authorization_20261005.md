# 理财统一高风险授权验收（2026-10-05）

基线main 79f84d542bff62a683cf5a4452e910c01e922ac8，初始工作区干净。分支codex/wealth-unified-authorization。用户明确授权理财接入三号authenticator并移除旧点击授权；本轮未发布远端或修改main。

## 已实现

复用verify_second_factor与SecondFactor；本人确认后单笔/整份TOTP或短信验证。方案reference绑定完整当前授权集合和摘要，一次验证码原子写每笔证据。申购、普通赎回、快赎及原号查询/结算规则保留。

移除全部理财verify/demo路由、点击签发函数、前端按钮和旧配置开关；历史SIMULATED_CLICK证据保留但新受理拒绝，已受理事实不追溯撤销。结构版本6扩展SMS约束，不删除账务。README、契约、演示、迁移、安全边界同步。

## 实际验证结果

- 最终TypeScript通过；Vite711模块构建通过/8.91s，既有500k大包警告保留。
- 首批3 failed/9 passed：两个HTTP夹具没挂auth短信路由，另一个既有wealth_bank外部证据读取越界。补齐auth夹具，外部事实读取原样移入银行包，不改变交易规则。
- 新增授权及主线/公共短信批次21 passed/1 failed（70.73s）：PG旧表夹具错用无时区TIMESTAMP，已修正为原有WITH TIME ZONE，非生产迁移故障。
- 最终受影响整合回归101 passed（307.39s）：unified_authorization、wealth_bank、wealth_quick、workspace_mainline、workspace_lifecycle、task_transfer_flow、sms_2fa、bank_boundary。SQLite独立临时文件及PostgreSQL bank_test_wealth_20260930随机schema，未指向演示业务库。覆盖申购、普通／快赎、完整集合、异参旧短信拒绝、TOTP批量单次消费、迁移与旧订单保护。
- 成功审计补齐permission=red及mfa_method后，group_sms/group_totp双库定向复验4 passed（19.30s）。各批次有重叠，不累计为105项独立覆盖。
- 自动主线NLU使用确定性夹具；验证码发送/校验、银行HTTP及测试库账务实际运行。浏览器另实际调用一次本地有效DeepSeek：“我要投资1000元”显示真实模型理解，生成500＋200＋300元三产品方案。不声称全面重跑旧模型专项。
- 浏览器http://localhost:3010/实际检查三产品统一预览、一次本人确认后TOTP／短信选择、短信获取入口、6位输入及无验证码时提交禁用。旧点击入口不存在；未在本人演示库提交新银行订单，交易执行证据来自上述隔离双库。关闭预览并新对话后聊天和方案清空，原9笔在途订单及账面20100／占款16000／可用4100元保留。截图通过工具查看，未落盘。

## 本地演示库升级

停止后端写入，SQLite备份到`/demo/backups/wealth-before-unified-mfa-20261005T044614Z.db`（wealth-v12-demo数据卷）。备份SHA256：`37e9bdbb399b0a6a9a74b093d449159b0bed108e4b3a1b2a3600e994104c7eac`。

版本6升级后accounts、transactions、wealth_orders、wealth_lots、wealth_verifications全字段指纹与升级前一致，integrity_check=ok。原点击证据只保留历史，不改为真实证明。后端最终重启，健康检查正常；普通银行容器及业务库未修改。升级辅助脚本首次因容器误加载宿主依赖导入失败，发生在数据库写入前；纠正搜索路径后升级成功，未安装新依赖或清库。

## 交付边界

本轮修改完成并本地提交，未推远端、未修改main或自动合并。默认短信是mock运输，仍需有效验证码；运营商接入与生产身份强化不在本轮范围。真实TOTP绑定仍通过公共入口由本人操作。旧SMS关闭配置会使短信路径不可用；可使用TOTP。密钥与本地私密配置保留，不进Git。
