# 完整对话与账务一致性验收

基线926d89a；工作分支codex/feature；任务书632行最新修订。

状态仅PASS/FAIL/NOT_RUN/BLOCKED。本轮尚在开发，不声明原回归数字证明新增功能。

| 用例 | 状态 | 当前证据/下一步 |
|---|---|---|
| E01 | NOT_RUN | 搜索/前两项比较 |
| E02 | NOT_RUN | 资金事实与投入意愿 |
| E03 | NOT_RUN | 问卷自动续接 |
| E04 | NOT_RUN | 复合修改暂停 |
| E05 | PASS | eb2a808；真实DeepSeek隔离双库申购金额修改、预览、旧确认拒绝、本人确认/模拟授权、受理、银行结算；live-full-redemption-host-recheck.log，2 passed/105.83s；各库独立JSON保留银行事实 |
| E06 | PASS | 同次真实模型双库：未结算持仓先澄清，查询实际持仓后半仓预览、份额确认、模拟授权、真实银行服务到账、剩余批次/零在途及唯一operation_id流水核对 |
| E07 | NOT_RUN | 先卖后买DAG |
| E08 | NOT_RUN | 查询插入 |
| E09 | NOT_RUN | 多义/写任务冲突 |
| E10 | NOT_RUN | 暂停恢复回退 |
| E11 | NOT_RUN | 银行取消边界 |
| E12 | NOT_RUN | 接管并发与交还 |
| E13 | NOT_RUN | 未知/重复执行 |
| E14 | NOT_RUN | 部分成功 |
| E15 | NOT_RUN | 持久化重启 |
| E16 | NOT_RUN | 模型故障/注入 |
| E17 | NOT_RUN | 真实基金研究资格 |
| E18 | NOT_RUN | 旧确认/过期 |
| E19 | NOT_RUN | 普通/沙箱未来时钟 |
| E20 | NOT_RUN | 时区与双库 |
| E21 | NOT_RUN | 历史修复副本 |
| E22 | NOT_RUN | 桌面/390px |
| E23 | NOT_RUN | 越权/旧入口 |
| E24 | PASS | eb2a808；cancel-contract-host-recheck.log：双库申购/普通赎回14:59:59、15:00:00、15:00:01；全撤确认、拒绝部分撤单/陈旧摘要、重复不重复释放，余额与流水数量不变；6 passed/19.98s（含M2/M3） |
| E25 | NOT_RUN | 挂单/节假日 |
| E26 | PASS | eb2a808；cancel-contract-host-recheck.log双库M2非可撤合同挂单DENIED、M3旧合同缺字段UNKNOWN，银行拒绝实际撤单，金额未变；缺字段不默认为可撤 |
| E27 | NOT_RUN | 撤单worker竞争 |
| E28 | NOT_RUN | 解款/解份额 |
| E29 | NOT_RUN | 撤回DAG与接管 |
| E30 | NOT_RUN | 两产品兼容升级 |
| E31 | NOT_RUN | 快赎窗口与不可撤 |
| E32 | NOT_RUN | 净值/方式绑定 |
| E33 | NOT_RUN | 快赎同事务与重启 |
| E34 | PASS | b04a73e/c966759；quick-demo-midnight-current.log：17 passed，跨账户并发额度原子、失败不占额、资金调整不重置、北京午夜维护与次日额度；quick-exact-limit-current.log：双库2 passed/15.25s，B018实际成交累计恰好10000元，随后0.01元被拒，现金及结算数量不变 |
| E35 | NOT_RUN | 快赎未知与后续买入 |
| E36 | NOT_RUN | 顶部校时时钟 |
| E37 | NOT_RUN | 重置/旧worker屏障 |
| E38 | NOT_RUN | 测试资金调整 |
| E39 | PASS | 67fcc6a；archive-concurrency-current.log：双库4 passed/43.26s，删除多个及全部剩余归档、取消确认无副作用、重复同结果、旧回调GONE、当前及普通账户保留；current-integration-regression.log中原单归档/任务历史/墓碑用例通过。删除旧范围不是历史修复手段 |
| E40 | NOT_RUN | 删除租约/权限 |
