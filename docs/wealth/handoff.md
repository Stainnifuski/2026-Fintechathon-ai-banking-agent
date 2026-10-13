## v1.2数据与迁移交接

> 2026-10-05授权更新：本模块已接入三号统一TOTP／短信校验，旧点击模拟授权及开关移除。授权使用、接口和迁移以[统一验证说明](simulated_authorization.md)为准；历史测试记录不代表当前授权行为。
schema3新增8类研究/上下文/同步/知识/消息请求表；旧8产品、净值、持仓和计价不改写。备份、停写、运行wealth_migration、核对余额/流水/订单/占款/批次/历史NAV、恢复服务；迁移重复幂等。失败保留新旧事实，非空业务不自动删表回退。大量历史只由管理员CLI显式init，不在每次API启动生成。见data_sources.md。

4号维护版本、日历、价格、预检、受理/占款/FIFO/结算、审计和管理员数据；5号通过HttpWealthBankApi和原操作号恢复；2号仅理解/受控查询和预览；1号展示来源/缺数、本人确认与模拟按钮；3号后续整体替换simulated/replacement_required提供者，保留参数、主体、期限和版本绑定。沙箱账户禁止普通转账/AA收款，汇总按资金上下文隔离。

模型本地配置WEALTH_NLU_MODE=model和WEALTH_MODEL_*，未设置继承TRANSFER_MODEL_*再LLM_*。密钥只.env。新模型不调用确认/验证/受理/管理员工具。知识与宏观均只读落库，不在交易中联网。

真实规则不足的10份额只可研究；真实身份+已审核模拟覆盖层的入口预留在统一导入器，本轮未提供真实发行人完整规则，不能直接交易。低并发独立worker与停止/重启见data_sources.md。比赛边界、渠道差距见competition/wealth_v1_2.md。


# 第4号理财增量数据库与联调交接

以任务书正文v1.1为准。完整接口和状态见 [理财契约](contract.md)，实施决策见 [设计](decisions.md)。这是虚构理财扩展，旧现金账本、转账API、风险等级API保留。

## 数据对象及主责

| 对象 | 主责及用途 |
|---|---|
| wealth_products/product_versions/navs | 4号当前产品索引、历史规则与披露、唯一营业日净值；已用历史版本不覆盖 |
| wealth_orders/lots | 4号权威受理订单、份额批次、剩余成本、解锁日期 |
| wealth_cash_holds/unit_holds | 4号现金/逐批份额占用；account锁与全部共享扣款同一串行边界 |
| wealth_settlements/clocks/schema_versions | 4号唯一计价/到账分录、隔离业务钟、升级版本 |
| wealth_assessments/authorizations | 3号本人量表结果、原始答案受控存储、本人确认与强验证绑定；银行独立核验 |
| wealth_profiles/goals/plans/conversations/executions | 5号画像、用途标签、当前会话临时方案/版本、未决提交观察（结束后清理）；不是银行账务事实 |
| audit_logs/transactions | 复用现有公共审计及现金流水；investment_purchase/redemption区别于消费 |

银行订单号与授权操作号唯一；分录事件键operation_id:pricing/cash唯一；占款与批次都有非负/正值约束。所有账务与银行审计同事务。主体读写先验归属，银行账户锁按ID排序；PG FOR UPDATE，SQLite写锁。自动结算、取消、重复请求不能绕开同一账户锁。

## 迁移步骤

1. 停止写入，保存数据库备份；先还原到隔离副本。不要执行 `docker compose down -v`，不要覆盖现有.env或资金文件。
2. 用当前代码、显式DATABASE_URL执行（仓库根包名backend.app；Compose镜像为app）：

```sh
python -m backend.app.services.bank.wealth_migration
# Docker Compose镜像部署时
# docker compose run --rm --no-deps backend python -m app.services.bank.wealth_migration
```

3. 增量添加wealth_*表，schema版本2（兼容升级1→2）；PG将transactions.type从20拓宽到32。目录/NAV只补缺，不播种账户、余额或流水。CLI单事务包含DDL及种子；SQLite显式BEGIN IMMEDIATE。重复执行核对版本并保留历史价格。启动也会执行兼容升级；正式切换前仍推荐停机跑CLI及副本核对。
4. 核对账户和流水行数、金额、所有字段指纹；检查占款、份额、流水、审计及版本；再启新版后端/前端。旧代码不能与新占款账本混跑。
5. 本次已有真实PG运行账本副本实际升级两次、空业务回退及重新升级：7账户/7流水，账户分合计22000000、流水分合计1603300，所有字段指纹一致；源库也未变。备份留本地忽略目录 `.deps/wealth-migration/before.dump`，不提交Git。

空业务回退：

```sh
python -m backend.app.services.bank.wealth_migration --rollback
```

只允许理财个人/业务表完全空，目录表可删。存在评估、画像、会话、授权、占款、订单、份额或结算任何记录就拒绝删表；不能运行旧代码忽略冻结款，也不能用旧备份覆盖新业务。无新业务时才可按已核对备份恢复；有业务保留新版并核对，不自动反向交易。type32拓宽无损保留，无需缩窄。

## 其他组员须注意

- 1/2号：用公开API和银行返回卡片，不由模型编金额/份额/状态；验证码只走可信组件。对话只生成意图，不能代答问卷或同意协议。页面初始确认未勾选。刷新不恢复聊天、方案或未提交预览；银行订单由原号查询。
- 3号：申购和赎回都RED。每笔授权独立；产品风险、本人承受能力和动作安全级别不同。旧User.risk_level不能申购，强验证不能提升适当性；画像改版须重评。已调用3号统一第二因素校验，整份方案一次验证码、逐笔证据；需要有效TOTP或绑定当前完整集合的短信验证码。
- 5号：只通过HttpWealthBankApi访问4号；纯购买计划1—5笔原子batch，不能按子单拆开占款。修改plan_version使旧授权失效；原号查询404仍不能判断永未受理；保留未知/成功事实。
- 共享扣款：使用available_fen，不仅balance。转账、定时、AA最终付款复用转账扣款，订阅同样检查占款；不得另写直接减余额路径。
- 账单：投资流水是资金流，消费原额仍仅purchase/subscription，避免把调仓误判消费。时间范围仍按真实账单时钟；隔离演示业务钟推进到未来时，未来流水不会提前出现在“截至现在”报告。
- 目录更新必须创建新规则/披露版本；已受理订单使用原版本与固定计价日。后台只结算已受理，不自动配置、买入或卖出。管理员不授给AI。

## 数据与秘密保留

本任务没有真实资料、行情、付费模型调用或第三方UI资产。原始答案存在受控评估表，凭据和答案不进对话/业务审计；审计留规则版本和确认事实，不存隐藏思维链。测试秘密、隔离数据库、备份和截图均被Git忽略。财务画像只允许虚构CNY数据；无自动删用户/金融记录功能，清除隔离演示和审计保留需分别决定。

模拟简化及真实上线待审查见 [安全自评](security.md)，不能把本次验收当作监管许可或生产安全认证。

## 2026-10-01 普通演示库模拟授权修订

理财已移除点击授权和旧开关。版本6扩展验证方法约束支持SMS，提供者版本unified-mfa-v1；升级前停止写入并备份。管理员时钟仍保留隔离库要求，旧验证和订单记录不可改写。

新TOTP/SMS证据replacement_required=false；SMS默认mock通道simulated=true仅指发送模拟。旧点击证据只保留历史，不能受理新交易。接口、迁移与模拟边界见[统一验证说明](simulated_authorization.md)。

## 最新全量基金补充

默认混合研究池、全量目录、池外详情／比较、可调整清单、官方原始规则和模拟变更、TTL及日更见[补充入口](preload_and_directory.md)。真实基金缺审核规则仅研究，银行B产品交易使用独立模拟资金上下文；不复制旧余额或改变旧订单引用。数量不是固定上限。

## 2026-10-04生命周期变更

旧理财会话/需求/方案不再长期存档；兼容历史读取返回空值。保留表结构供当前窗口、2号契约及最小未完依赖使用。单笔受理及无未决节点终态压缩为银行操作号最小回执，不删除银行订单/审计/占款/流水。清理须停写、预览、备份、核对保护表指纹；[脚本与新契约](workspace_simplification.md)。旧刷新恢复和逐笔采用页面说明以本条为准。

## 2026-10-12 01／02本地联合接线：03工程索引

以[portfolio_handoff.md](portfolio_handoff.md)和[wealth_joint_acceptance.md](wealth_joint_acceptance.md)为当前工程事实。旧“模型待讨论”“02未接A”等历史交接不能作为当前状态；模型参数已冻结，但必要J未全部通过，尚不能登记L0通过或接新LLM/NLP。

- Action：`schemas/wealth_action.py`、`services/orchestration/wealth_actions.py`、`routers/wealth.py` POST `/api/v1/wealth/actions`。request_id/context_id/会话版本/selection_context是可信对象引用；身份来自JWT。Action不直接执行自然语言。
- A注册：`services/orchestration/wealth_portfolio_joint.py` 三个async handler；main.py install；canonical `wealth_portfolio*.py` schema与前端`types/wealthPortfolio.ts`。Direct create/get/revise和Action同store，无第二模型。
- 接线：客户StructuredActionPanel→AllocationReview→A PortfolioAllocation；B ProductPicker/双表/Returns保持。operator A图只读；人工备注不是客户授权。推荐不需要Plan/预算/期限。
- 资产：GET `/asset-summary`与唯一GET `/portfolio/snapshot`同全授权scope/snapshot；账面现金+持仓+赎回应收；可付available另列，冻结不重复。历史lot采用显式product_version。
- 交易：A Plan ID→B prepare差额→现行本人确认/统一验证→执行→原操作号查询/结算回读。旧版本/closed/stop/HUMAN/PAUSED拒绝；未知提交不能新号重发，已受理不因清会话/改方案回滚。prepare不占款；接受即时占用；具体份额等级最终重验。
- 双表/收益：订单operation_id与持仓position_key不混用；独立分页/跨页选择/当前页全选，scope改变清选择。show_wealth_returns按整体/产品及日月年累计调用B实际银行收益，费用不双扣、全赎贡献不丢；投资现金流保留原流水但从消费账单排除。
- 成功样例：正式无金额/期限plan_portfolio读取20,000元并得到全现金Plan，随后可Direct GET（最终结果见联合报告）；**不等于推荐/购买成功**。
- 失败样例：CandidateGroup status=unavailable/items=[]带产品证据原因；final prepare R2/R4 ->409 SUITABILITY_MISMATCH；客户端risk_level ->422；缺资料包503；closed/版本变更409。Action外层成功状态不能覆盖A业务错误。不要把失败翻译为“已购买”。
- 03责任仍仅后续统一NLP结构化动作与有据表达；无新模型接入/训练。本轮模型实际新调用0。相关允许操作必须由服务器状态推导，LLM不能制造可购标记、评估或授权。

## 2026-10-13 当前03接入边界

当前代码、双池、正常注册、离线包、性能及必要门禁见portfolio_handoff.md和wealth_joint_acceptance.md。真实基金材料不等于可交易SKU：真实当前映射/资格为0，需要明确真实资料＋比赛模拟风险/交易政策决定，尚未实施、未可封板。七S产品补目标方案不采用。03不得将CandidateGroup unavailable/空候选或全现金Plan表达为推荐/申购成功。

原01 A6历史总回报、固定组合、样本外金融效果已移出本轮运行门禁，保持未完成、不承诺金融效果；B实际持仓收益/四粒度/日历/费用/已赎贡献和J23保持必需。03仍等真实正向必要J闭合后再接新LLM/NLP，不用语言层替代资格、交易、本人确认或MFA。内部fee_rules仅补读已有版本费用，未知仍null；外部ActionV1/Fees/Plan/Selection协议不变。
