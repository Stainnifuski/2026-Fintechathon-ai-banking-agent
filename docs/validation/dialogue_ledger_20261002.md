# 完整对话与账务一致性验收

基线926d89a；工作分支codex/feature；任务书632行最新修订。

状态仅PASS/FAIL/NOT_RUN/BLOCKED。按用户2026-10-03（多伦多）修订范围完成E01–E40：移除专门断网模块和该子项验收；浏览器不支持直接改时区，三时区验证采用实际共享模块。结果来源分阶段且明确记录失败与复验，不把跳过的测试当成通过。

最终集成回归：integration-final-authoritative.log 275 passed、10 skipped、1 error/966.22s；唯一error为PostgreSQL建库连接超时，同用例双库final-pg-timeout-recheck.log 2 passed/9.29s。10项跳过主要为需显式启用的真实模型验收，实际DeepSeek另有双库独立记录（E01–E06、E16）。当前前端TypeScript与Vite708模块/9.53s通过，三时区共享时钟通过；额外构建体积提示保留。不是全仓一次运行零失败。

| 用例 | 状态 | 当前证据/下一步 |
|---|---|---|
| E01 | PASS | 080573c；live-category-research-recheck.log：真实DeepSeek隔离双库2 passed/60.53s，未评估检索债券基金、比较前两项；使用原始真实来源缓存，研究资格不等于可交易。目录类别回归7 passed/29.96s |
| E02 | PASS | 同次真实模型双库：银行现金20000元作为事实，不代选投入预算；询问投入意愿与现金需求，明确一万元预算后进入评估，未生成订单 |
| E03 | PASS | f1e005c；真实DeepSeek双库live-empty-plan-current.log 2 passed/59.94s：可信九题评估本人确认后续接一万元原预算，产品风险不超评估，零订单。390px实际浏览器U002完成九题、提交、确认后自动发送“继续”，保留原需求；低风险无可交易项时保留现金，未授权/成交 |
| E04 | PASS | 真实模型live-current-snapshots.log隔离双库compound对话（6 passed/151.47s中的2项）：第二项改3000且暂停，新版本，无订单；当前80项不可变方案卡片和旧确认失效补证。与后续空方案提示修改互不影响 |
| E05 | PASS | eb2a808；真实DeepSeek隔离双库申购金额修改、预览、旧确认拒绝、本人确认/模拟授权、受理、银行结算；live-full-redemption-host-recheck.log，2 passed/105.83s；各库独立JSON保留银行事实 |
| E06 | PASS | 同次真实模型双库：未结算持仓先澄清，查询实际持仓后半仓预览、份额确认、模拟授权、真实银行服务到账、剩余批次/零在途及唯一operation_id流水核对 |
| E07 | PASS | dag-demo-ledger-quick-final-current.log当前集中双库59 passed/341.39s；test_sell_then_buy_requires_actual_cash_reread_and_fresh_authorization：在途不能注册买入、实际到账后REREAD_CASH、新方案版本及独立授权；快赎实际公开计划执行见E35 |
| E08 | PASS | bank-dialogue-cancellation-final-current.log当前80 passed/451.49s；不可变卡片顺序快照、产品查询插入保留原方案，旧列表存于服务端；真实模型compound记录资产查询插入后恢复原任务 |
| E09 | PASS | 同次当前双库卡片快照测试：多个“第二个”先澄清，明确“方案第二个”才修改新版本并暂停，无订单；原主任务选择及服务端依赖保护见当前DAG回归 |
| E10 | PASS | 当前59项暂停/恢复/接管写保护，已提交取消保留结算观察；925e1e4 rollback-preview-current.log双库2 passed/7.84s：未提交回退使旧授权EXPIRED，暂停不提交，新预览/新授权后一次受理，提交后回退拒绝且订单不变 |
| E11 | PASS | 当前80项含M1可撤、M2不可撤、M3未知、已结算拒绝、截止端点及全撤确认；当前59项包含取消已提交任务保留原号结算观察，未伪称回退账务 |
| E12 | PASS | 当前集中双库包含两个操作员并发唯一领取、角色/归属HTTP保护、交还与重新核验。浏览器实测U002领取U001原账户任务v6，读取原占款2000元、记录处理说明，交还v7后U001刷新恢复PAUSED/REVALIDATION_REQUIRED，无代替授权 |
| E13 | PASS | 当前80项实际HTTP丢响应及多轮client_message_id重放，当前59项暂停UNKNOWN原号404继续观察不盲目提交；快赎实际HTTP丢响应、重复执行/worker唯一现金流水另有双库定向证据 |
| E14 | PASS | 当前80项test_three_step_partial_failure_preserves_success_and_pauses及批量原子回滚：成功事实保留、余下暂停、合法剩余恢复；当前DAG取消保留已受理结果观察 |
| E15 | PASS | 当前双库DAG新会话恢复原任务/银行结算观察，原任务与成功订单不重复提交；此前浏览器重启后恢复暂停v7和原订单。本轮后端实际重启并重新登录，恢复原对话、申购/快赎两笔SETTLED订单、现金19229.00与剩847.3488份，唯一到账流水及结算仍各1 |
| E16 | PASS | 真实模型live-current-snapshots.log双库真实供应商故障保留上下文（非离线成功）；当前80项畸形/越权/产品描述注入拒绝授权，无语言执行；未以mock代替真实模型研究/成交证据 |
| E17 | PASS | 当前80项真实研究范围及模型注入不能写入；真实基金缺审核规则时服务端拒绝，未静默替换模拟产品。当前真实模型检索/比较仍trade_eligible=false |
| E18 | PASS | 当前80项金额/账户参数冲突、方案及披露版本改变旧确认失效、授权真实时间到期拒绝；快赎HTTP定向复验绑定方式、到期及报价变化 |
| E19 | PASS | 当前集中双库包含普通时钟忽略mock、可处置上下文业务时间、已发布净值按瞬时与北京日期筛选、未来计价账务保留与普通写入限制；预期值不代替余额。quick-unpublished-missing-nav-current.log补验两产品未来/缺失净值4 passed/28.18s |
| E20 | PASS | verify-business-time.mjs独立UTC/上海/多伦多进程跨午夜/DST/本机时间偏差；SQLite/PostgreSQL银行DTO同一UTC瞬时、北京业务日及净值发布时间序列化。浏览器实际现实钟与模拟业务钟独立显示；未实际切换浏览器时区，不冒充该项观察 |
| E21 | PASS | 454aa24定位首次重复元数据失败：SQLite同值账户UPDATE触发updated_at自动更新。显式保留锁定元数据后，原迁移前备份SQLite新副本及独立恢复PG副本金融/完整字段重复核验通过；authoritative-metadata-fixed-report.json、authoritative-postgres-report.json。新增双库固定旧时间锁定回归通过，不忽略差异字段 |
| E22 | PASS | 真实桌面U001模型B017申购1000元→可信卡片→本人确认→点击模拟授权→占款→银行结算947.3488份；390px完成100份快赎卡片/确认/模拟授权→到账105.55元、剩847.3488份、零占用。手机可信九题及自动续接见E03；页面375px内容宽、无横向溢出。截图工具可显示、落盘受阻，DOM及银行记录核对保留 |
| E23 | PASS | 当前80项真实JWT跨用户及注入不能授权，当前59项操作员角色/归属HTTP保护与暂停/HUMAN旧银行写入口拒绝；未验证/陈旧版本不可绕过 |
| E24 | PASS | eb2a808；cancel-contract-host-recheck.log：双库申购/普通赎回14:59:59、15:00:00、15:00:01；全撤确认、拒绝部分撤单/陈旧摘要、重复不重复释放，余额与流水数量不变；6 passed/19.98s（含M2/M3） |
| E25 | PASS | calendars-calculations-current.log当前14 passed/0.82s：模拟节假日/周末/UTC截止换北京业务日；当前80项非可撤类型周末挂单及旧资料缺规则拒绝承诺，日历有版本；快赎按自身日历窗口另验 |
| E26 | PASS | eb2a808；cancel-contract-host-recheck.log双库M2非可撤合同挂单DENIED、M3旧合同缺字段UNKNOWN，银行拒绝实际撤单，金额未变；缺字段不默认为可撤 |
| E27 | PASS | 当前80项test_cancel_and_settle_race_has_one_valid_transition、重复撤单及银行原号恢复：账户锁下唯一合法状态，无重复解占款/份额或结算 |
| E28 | PASS | 当前80项M1双方向全撤及现金/份额占用回归：申购仅解占款不增加余额不生成退款，普通赎回仅解份额不增加现金；并发竞争不双重释放 |
| E29 | PASS | 00cda4e；cancelled-sell-dependency-recheck.log双库2 passed/8.43s：HUMAN期间直接撤单拒绝；交还恢复后撤销原赎回，失效DAG买入依赖取消；独立新任务重新读现金并要求新确认授权 |
| E30 | PASS | 当前运行库对比升级前SQLite备份：100个旧版本规则/披露原样，另增B001–B016:3与B017/B018:1，总118个历史版本，当前预加载池44真实基金+18模拟银行。new-cash-standard-redemption-recheck.log两产品双库4 passed/25.49s：重复导入幂等、普通赎回占份额/T+1到账/唯一流水、不占快赎额度。首跑夹具重复净值失败保留，修正后复验 |
| E31 | PASS | 454aa24；quick-cancel-standard-binding-current.log两产品双库4 passed/28.43s：未提交快赎任务取消且旧授权失效，无订单/现金/额度变化；成功不可撤。此前合同端点、维护/非营业日窗口及拒绝自动改普通赎回通过 |
| E32 | PASS | 同次两产品双库4项明确普通授权不能快赎；此前当前HTTP同号方式篡改、旧报价变价、真实安全期限过期拒绝，以及缺失/未来净值4 passed均通过；银行报价含context epoch/净值可用瞬时/金额绑定 |
| E33 | PASS | 当前双库快赎并发和事务故障、唯一结算/流水、重复调用及worker不再入账；HTTP丢响应原号恢复见E35。手机实际100份快赎105.55元、现金19229.00、剩余847.3488份，订单SETTLED、唯一到账流水/结算各1。后端实际重启与浏览器重新登录恢复核对，订单/资金/剩余持仓相同 |
| E34 | PASS | b04a73e/c966759；quick-demo-midnight-current.log：17 passed，跨账户并发额度原子、失败不占额、资金调整不重置、北京午夜维护与次日额度；quick-exact-limit-current.log：双库2 passed/15.25s，B018实际成交累计恰好10000元，随后0.01元被拒，现金及结算数量不变 |
| E35 | PASS | 00cda4e；quick-http-dag-plan-execution-current.log双库2 passed/19.65s：真实银行HTTP已成交丢响应，原号查询及重复执行只一笔到账；超份额拒绝；DAG重读实际现金，后续计划新的确认/模拟授权及公开计划执行接口，依赖只提交一次；额度边界见E34 |
| E36 | PASS | 按用户最新范围取消断网/联网恢复专用逻辑及子项验收；没有实际断网证据且不声明通过。剩余verify-business-time.mjs实际共享模块在UTC/上海/多伦多跨午夜/DST/设备Date.now偏差/单调计时及周期重新校时通过；浏览器现实北京时间与受控模拟业务钟分开，实际00:00跨日、24小时格式。时间序列/账单/限额集成回归通过 |
| E37 | PASS | demo-metadata-boundary-recheck.log双库6 passed/27.89s（含E38及锁定）：重置旧授权EXPIRED，旧命令无新订单，原占款保留，旧结算ARCHIVED_RUN拒绝。原fixture恢复/新账户20000/重复重置和并发屏障集中双库通过；浏览器实际重置/归档记录保留，不删除历史修复证据 |
| E38 | PASS | 同次双库调账后旧授权EXPIRED且旧命令拒绝，超可用debit失败不改变有效授权/占款，普通账户不变。原双库整数分/非法精度/负数/并发/同号重放及不重置快赎额度通过；浏览器当前轮次credit123.45资金关系核对 |
| E39 | PASS | 67fcc6a；archive-concurrency-current.log：双库4 passed/43.26s，删除多个及全部剩余归档、取消确认无副作用、重复同结果、旧回调GONE、当前及普通账户保留；current-integration-regression.log中原单归档/任务历史/墓碑用例通过。删除旧范围不是历史修复手段 |
| E40 | PASS | 当前集中双库覆盖演示开关默认关闭、他人范围拒绝、普通账务不变、删除租约及并发重置；archive-cross-scope-current.log双库2 passed/24.60s补验预览后新增混合来源引用阻断删除，归档与当前账户均保留。先重验所有选中范围，失败不部分删除 |
