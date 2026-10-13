> 当前版本（2026-10-13）：正式A/B已接线，并用七真实具体产品通过无新LLM的配置及银行模拟交易。本文冻结公式/数值参数保持；A含分红历史总回报/固定组合/真正样本外金融效果仍未完成，按决定移出运行封板门槛，不宣称金融有效。最新来源/资格/风险/交易与Codex验收见portfolio_handoff.md和wealth_joint_acceptance.md；下文独立时期未接线说明属于历史。

# 01 正式计算方法与独立评价

> 2026-10-12联合状态：本文件保留01原实现/独立验证说明。当前正式主应用已注册A提供器与规范路由并消费B全授权事实；正式接线、已关闭旧缺口和剩余产品/研究阻塞以 [portfolio_handoff.md](portfolio_handoff.md) 为准。独立入口不替代正式联合验收。

独立计算provider，不接主应用、交易、模型对话或数据库写入。资料/候选资格沿用 `portfolio_data.md`、`portfolio_candidates.md`；正常计算缺可信来源时关闭，没有隐式fixture回退。输出是资产目标和资料多样性建议，不是金融表现保证或交易执行证明。

## 方法与固定版本

| 部分 | 实现/版本 |
|---|---|
| 资格 | 第03步 `qualify_purchase`；各具体份额独立核对 |
| 选择 | `portfolio-greedy-cholesky-v1`，稀疏增量DPP贪心，q=1 |
| 特征 | `portfolio-bigram-tfidf-80-10-10-v1` |
| 政策 | `portfolio-policy-r1-r5-v1` |
| 板块求解 | `portfolio-geometric-projection-v1` |
| 产品投影 | `portfolio-capped-simplex-v1` |
| provider | `portfolio-provider-dpp-geometric-v1` |

仅Python标准库及已有Pydantic；没有新增依赖、训练框架或数值安装。

DPP语料由完整合格家族池建立：目标/策略/基准/跟踪标的按顺序处理，NFKC、小写、标点或空白切段，连续字母/数字字符二元组，单字符保留；不跨字段。tf=1+ln(count)，idf=1+ln((n+1)/(df+1))，TF-IDF向量单位化。v=[sqrt(.8)t;sqrt(.1)类型onehot;sqrt(.1)主体onehot]，S=v·v，L=S，q=1。不消费产品名称、收益、NAV、费用、经理、规模、exposure、宏观或用户信息。

只存稀疏向量、按需点积与至多三列Cholesky因子，不构建全池n×n核。每步选择最大残差，稳定family_id破并列；最多三个，残差≤1e-10停止。没有d²<1早停、jitter或独立对角特征。明显负残差/非法核拒绝，1e-9内浮点负误差才作0。语料绑定资料版本、完整家族成员和文本/类别/主体/引用指纹；换批排除集不改变n/词表/idf。选择器缓存最多8个池语料版本，资料索引仍最多2个版本。

MMR只供同池评价：score=.75q-.25maxS，最多三只，完全重复不凑数，稳定family_id。不是生产DPP替身，不要求DPP总获胜。

## 三板块基准与U

T=所有授权关联借记卡账面现金＋关联持仓市值＋赎回应收；冻结不重复。可支付现金另列，应收不转成可支付现金。

| 等级 | c_min | 参考c | 默认p0 | p_max |
|---|---:|---:|---:|---:|
| R1 | 1 | 1 | null | 全活钱 |
| R2 | .1 | .2 | 0 | .25 |
| R3 | .1 | .2 | .25 | .5 |
| R4 | .1 | .2 | .5 | .75 |
| R5 | .1 | .2 | .75 | 1 |

R1=(1,0,0)，滑块null；显式非活钱偏好返回不适用。R2—R5：b=(.2,.8(1-p),.8p)，P为和1、非负、c≥c_min、g≤p_max(1-c)，加板块可用性/仅可维持市值上限。现金资产分量始终存在。无明确p时默认只进b；有明确p时P_request再加g=p(1-c)。不能用c=1冒充兑现非活钱偏好。

f=||x-b||²+η||x-h||²，η=.25。完整估值但未分类时h未知、η=0，缺口保留；缺必要估值不求完整计划。T=0返回no_configurable_assets。连续求解用z=(c,g)，枚举活跃约束交点、边正交投影及中心，严格凸目标选唯一最小；独立校验约束与全顶点方向导数，不用粗网格或归一化改解。

U={x∈P:f(x)≤f(x*)+.01}，移除显式p等式，但不换b/h/η/x*。边际求界用多边形顶点、椭圆切点及椭圆/边交点；输出可核对端点。当前页滑块固定c=c*，求同一U切片g/(1-c*)；c*=1则null。边际端点不能任意拼接，调整入口检验完整P和f。

连续容差1e-10；整数分硬政策/维持上限用Fraction精确比较（0分容差）。目标金额和=T。无明确p时仅有界局部±3分修正；有明确p时按有理分母的可表示性找相邻非现金金额，不遍历T。每个整数候选重验P/U；不可表示返回AMOUNT_PRECISION_PREFERENCE_UNREPRESENTABLE，未确认求解失败不称数学不可行。残差、连续解、金额、实际p、约束及范围均在结构化元数据中。

## 产品投影与持有边界

每板块集合来自本次DPP建议或显式selected，加有可信持有许可的原份额。家族目标去重，原账户/lot/具体份额仍独立保留。已有维持目标不占三只新增展示名额。

u_i=x_b/n，h_i=当前可靠家族市值/T，y=(u+.25h)/1.25，投影到和x_b、非负单纯形。仅可持有目标上界是原持仓市值；具体份额不可增买。未知持有许可不伪造维持资格；已有估值/家族不明不补0，不输出完整产品目标。现金分量另加入活钱集合，h_cash=(账面现金＋应收)/T，货币基金/快赎市值只计自己一次。

投影用KKT标量阈值固定100次二分并独立验残差；金额最大余数按稳定家族顺序处理，持有上限精确按分复验。所有新家族等权，单产品得全部板块预算，0预算为0。正目标无产品承接返回partial/PRODUCT_SET_INFEASIBLE，不擅自移回现金。没有60%上限、旧五笔上限或强制每类产品。

等权对照在同一集合和板块预算上比较，活钱含现金分量；违反仅持有上限时标baseline_infeasible，不二次投影冒充等权。未选原持仓仍在positions中，目标/方向只是建议，不自动交易。

## 独立调用与第05步复用

`PortfolioModelProvider(candidates: CandidateService, asset_source=None)` 仅接受正式DppSelector；未配置selector时注入该算法，不注入fixture事实。

```python
await provider.recommend(user_id, CandidateFilter(...))
await provider.plan(user_id, p=None, selected=None)
allocate(level, total, h, available, maintain_caps, p=None)
validate_adjustment(problem, continuous_star, complete_x)
project_products(families, budget_fen, total_fen)
provider.validate_weights(user_id, facts, index, now, asset,
                          validation_contract, targets_fen, bucket_amounts_fen)
```

plan(p=新明确值)重新规划b/x*/U；validate_adjustment只检验原U；合法手动权重原样返回，不调用优化回去。`validation_contract`来自服务器本次结果元数据，含原计算、scope snapshot/qualification绑定、确切selected份额、可用性和上限；第05步须由服务器保存并验证，不能信任客户端自行修改此结构。手动入口重新复用共用资格核对新增份额。源事实/快照/评估版本改变返回MODEL_FACTS_CHANGED。

整体读取 `CandidateFactsSource` 与 `read_asset_model_facts`，核对同一本人、资产范围、snapshot/context、资料/日频/银行版本及当前评估来源、ID、版本、有效期。计算固定本次事实，输出前再次读取并比较；正式source缺失保持503，不能以旧单context或某张付款卡拼总资产。

`ModelResult`外层含status、PortfolioPlan或null、CandidateGroup、metadata及missing_fields；沿用wealth-portfolio-plan-v1/input-v1，三个板块和占T比例、整数分一致。metadata包含模型版本、f/Δ/P/U/连续与按分解、投影目标、持仓、现金/应收、validation_contract。未显式选择时selected_positions为空，自动DPP只是建议；产品目标在metadata中。

最小兼容扩展：RecommendationFilter可表述显式theme_keys＋AND/OR，原单theme_key照常使用；内部FamilyCandidate新增实质类型与资料版本。正式CandidateGroup份额A/C/null；内部B/R等原身份保留证据，不改变购买判断。未知风险/行情/必要资料导致无法推荐为unavailable，证实无合格/耗尽为no_eligible_products，全匹配风险已知且全部超风险才risk_mismatch；ok不携带整组风险警告。未核实行情窗口明确null，不虚构收益。

## 运行、评价和限制

```powershell
python -m pytest tests/unit/test_wealth_portfolio_model.py -q -p no:cacheprovider
python -B -m scripts.wealth_portfolio_model --fixture
python -B -m scripts.wealth_portfolio_model --actual-version <本地资料快照版本>
```

CLI禁socket连接、无LLM/SQL调用/银行写入；fixture必须显式参数，输出到忽略目录 `.deps/wealth_portfolio_model/`。输出实际Plan、同池DPP/MMR和同集合投影/等权指标，包括合规率、ILAD/ILMD、重复/覆盖、板块HHI（注明现金）、全联合当前/目标调整量代理、明确偏好误差、分阶段延迟中位/P95和次数。空列表、零预算、身份/估值不完整或未给偏好均NA；不称手续费，不报告CTR、满意度或金融收益提升。

真实风险/家族、当前评估/银行许可以及全授权资产source仍有缺口；当前真实候选0，正式入口503。fixture仅证明算法与契约，不是实际用户计划或金融效果验收。第05步负责计划存取/修订/UI；交易、中央接线及历史金融评价不在本模块完成。

## 标准参考

板块组织参见 [Thaler 1999](https://doi.org/10.1002/(SICI)1099-0771(199909)12:3%3C183::AID-BDM318%3E3.0.CO;2-F) 和 [Das et al. 2010](https://doi.org/10.1017/S0022109010000141)；凸约束与投影参见 [Boyd/Vandenberghe](https://web.stanford.edu/~boyd/cvxbook/)、[Boyd et al. 2017](https://doi.org/10.1561/2400000023)。DPP实现参照 [Chen et al. 2018](https://papers.nips.cc/paper_files/paper/2018/hash/dbbf603ff0e99629dda5d75b6f75f966-Abstract.html)，MMR对照参见 [Carbonell/Goldstein 1998](https://doi.org/10.1145/290941.291025)，同集合等权对照参见 [DeMiguel et al. 2009](https://doi.org/10.1093/rfs/hhm075)，资料多样性指标参见 [Wu et al. 2024](https://doi.org/10.1109/TKDE.2024.3382262)。这些是方法背景；数值/政策/特征参数是项目定义，不代表论文给出的金融保证。
