# 01 第09步：历史评价与可复現输入

日期2026-10-09（America/Toronto）。HEAD `5e36e4c48c82c24a088c9f271df7774727d7435f`；未提交。复用第04/06步指标和模型，当前计算与生产推荐、02账户实际收益服务分开。前六步的规则/fixture验证不能证明金融效果；第07/08步可信正向推荐及Plan阻塞保持。

## 四项实际状态

| 项目 | 状态 | 实际结果 |
|---|---|---|
| 单产品含分红总回报 | blocked | 合格样本0；指标及窗口null |
| 补充真实NAV描述 | available/partial | 10份额，2025-01-02—2025-12-31，各243观测；货币2份额未纳入 |
| 固定初始权重买入持有总回报情景 | blocked | 缺经证明的成分财富指数，未计算对照、集中度或费用后收益 |
| 真正样本外模型评价 | blocked | 缺决策时点适用的身份/类别、风险、家族、渠道/可售、正文/基准和价格公布证据 |

上述三项原总回报/模型要求没有改名关闭。NAV变化、观测间波动及NAV回撤不是含分红总回报，既不证明DPP收益改善，也不是本人账户收益。000312/313/373/376的生产实质类别仍pending；描述性观察不放行购买。A/C家族不能作独立风险来源，000003/004未因名称认定新家族。

## 输入盘点及有限准备

旧214逻辑任务＝214唯一压缩响应，128具体symbol代码：单位NAV30、累计NAV29、累计收益率28、排名70、货币收益2、份额分红详情5、年度分红28、年度拆分22。全部对照第06步冻结指纹、函数/kwargs逐条复核；日期和数字列的缺失、重复、顺序、非正/非有限、实际区间及单位分别保存在新manifest。全部325数值列非有限0，全部实际解析日期非法0；30单位NAV序列日期不缺不重、NAV非正0。事件全市场同日重复和倒序不是单产品日序列缺陷。观察后的56个日期字段观测值出现在2026年度分红/拆分表（含计划2026-10-12/14），是待辨别事件，不进入2025评价；不能称56个唯一事件或未来净值。

补证清单先冻结，12具体代码：000001、000003、000004、000009、000015、000016、000082、000203、000312、000313、000373、000376。既有历史中按稳定低代码覆盖混合/债券/股票，2个既有货币样本；兼顾原三组已证明A/C关系，其中6份额缺旧历史，另取定向资料。不是收益排名/目录白名单。

实际2次公开搜索调用、6查询；28直接请求＝12行情JS＋12分红/拆分页＋2方法页＋1AKShare源码＋1LJSYLZS，全部200，0重试/重定向跟随，4并发、20秒超时、5MB上限，原文及请求账本在忽略目录。HTTP200不代表证据充分。12个事件页各分红/拆分表均核对，明示暂无也不当完整事件链。完整原文、旧缓存及数据库不改写，不恢复39接口队列。

公开市场JS保留原包装“测试数据”注释，具体fS_code绑定核对；来源为公开行情观察，不是本项目synthetic生成NAV，不当发行人认证总回报指数。4条既有NAV直接复用（000001/3/4/82），6条新增非货币历史使用保存JS；其他准备输入留作对照，没有联网回填普通请求。

## 方法证据与未取得项

[华夏2021-12-23投教](https://edu.chinaamc.com/c/2021-12-23/511783.shtml)区分单位、累计及考虑再投资的复权净值，并说明当日净值白天尚未公布；可用于方法边界，不能证明某个上游指数实现。原文SHA `2f84d453e8e66984c7da54cb74a760a020855c8f14c01da0e5fe513f91916d27`，HTML detail-time为实际发布日期，其他适用时点null。

[东方财富计算器](https://data.eastmoney.com/money/calc/CalcFundKF.html)分别提供现金分红/红利再投，存在空费率按0的计算器约定；本程序没有继承这种未知费率默认0。该页声明GB2312，已按声明解析，不替换乱码。发布日期/生效时点未声明。SHA `5d6f279c14c0d2384c885d3b9e622ceaaaf1a918fbde8ad8a08ba196231ad62e`。

[000015 LJSYLZS](https://api.fund.eastmoney.com/pinzhong/LJSYLZS?fundCode=000015&indexcode=000300&type=se)返回本份额、同类平均及沪深300三组，各207抽样点；首值0不证明百分数锚点或分红再投/拆分方法，也不是连续日频。核对[AKShare本地及公开源码](https://github.com/akfamily/akshare/blob/master/akshare/fund/fund_em.py)确认仅取Data[0]和字段转换，不认证上游总回报。SHA `c855969b5ad1e62473ff6820f02be1aa4fa9224558d8ef616801f4a5051b24ce`；发布时间和调整方法仍未知。现金分红、每10份金额、折算比例、再投资时点和完整历史链未合并为已认证财富指数。

## 冻结manifest与实际指标

内容寻址manifest `2fc49014ac80e3d04b8c5319277be316ba4b163fefb61b835421bec05965a825`，结果 `e9f9724c777d45e8198709b87ba1fa2fa1f41af6fa7abbc32b269f8696b5fdd9`。完整输入在 `.deps/wealth_portfolio_step09/evaluation_manifest_2fc49014ac80e3d04b8c5319277be316ba4b163fefb61b835421bec05965a825.json`，结果在 `.deps/wealth_portfolio_step09/evaluation_result_e9f9724c777d45e8198709b87ba1fa2fa1f41af6fa7abbc32b269f8696b5fdd9.json`；库外完整原料不提交。manifest含全部输入指纹/具体份额、来源版本、方法用途、纳入/排除、窗口及日历边界、代码版本/参数、分割、初始研究假设及缺口。旧数据版本 `38005c79535cb83a2d5be663d8a7a1812ba21946fe8f9fa2ab21ca80aea338a9` 保持。

先按完整性冻结前一完整自然年2025，不看收益选择最好窗口；从10条有效NAV取披露日期交集243天（不是认定每个自然日缺数据）。不前填或补0，未证明可比披露日历，不年化。每条指标对应自身窗口与原文指纹，保留未来行但不消费。收益小数转百分数仅展示；累计收益率转换必须证明百分数/小数单位、零点锚、调整及事件方法，不能直接diff；累计NAV不替代复权。

| 份额 | NAV变化 | 观测间波动(ddof=1) | NAV最大回撤 | 观测数 |
|---|---:|---:|---:|---:|
| 000001 | 34.869240% | 1.372165% | 12.478032% | 243 |
| 000003 | 23.458283% | 0.922619% | 9.922822% | 243 |
| 000004 | 22.962963% | 0.926591% | 9.909910% | 243 |
| 000009 | NA | NA | NA | 0（未纳入） |
| 000015 | 1.356689% | 0.033405% | 0.304697% | 243 |
| 000016 | 0.965850% | 0.033203% | 0.325287% | 243 |
| 000082 | 30.703883% | 0.958698% | 9.729419% | 243 |
| 000203 | NA | NA | NA | 0（未纳入） |
| 000312 | 26.588013% | 0.913629% | 9.865954% | 243 |
| 000313 | 26.096375% | 0.913899% | 9.887976% | 243 |
| 000373 | 12.960208% | 1.170921% | 14.418867% | 243 |
| 000376 | 12.510902% | 1.170974% | 14.509225% | 243 |

## 计算、研究假设与样本外边界

既有总回报函数仍按正值G计算、ddof=1，少于2回报波动NA，只有证明连续交易日才sqrt252；解析100/110/99/118.8测试不是实际金融输入。原买入持有采用sum(w_i G_i/G_i0)，终点权重漂移；例末值1.5不冒充每日再平衡1.875。

独立research_buy_hold显式gross/scenario配置必须声明常数现金指数、未模拟外部费用、不调仓，原现金未知拒绝语义不变。HHI不伪造未知家族。实际固定情景预声明T=1000000分、现金20%，其余在合格成分同集合等权；合格财富指数0，实际权重/共同窗口及对照保持NA。没有将NAV-only组合买入持有改名完成原要求。

样本外冻结2024-12-31北京时间末决策、一次决策、2025评价窗，T/持仓占比/R4偏好仅研究初始假设。缺历史真实客户账务不是新增必要条件；缺历史产品评级/许可/正文及可售证明仍是必要条件。2014家族公告仅证明当时存在，2026公告不得提前回填，今天无日期文本不得当2024已知。require_point_in_time要求published available_at及适用起止，拒绝同日未公布价格/未来资料。因缺输入，本次没有运行一个伪样本外DPP批次；既有属性DPP/等权规则检查不称金融表现通过。

## 复现与验证

已完成准备时才能运行freeze，生成新内容寻址manifest，先于指标；平常复现只evaluate固定manifest，不重取网络。两模式均封禁socket connect/connect_ex/create_connection，读取214原响应和28本轮原文指纹，代码变动或份额/日期/指纹错误拒绝。

```powershell
$env:PYTHONPATH="$PWD;$PWD/.deps"
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m scripts.wealth_portfolio_history evaluate --manifest .deps/wealth_portfolio_step09/evaluation_manifest_2fc49014ac80e3d04b8c5319277be316ba4b163fefb61b835421bec05965a825.json
python -B -m pytest tests/unit/test_wealth_portfolio_acceptance.py tests/unit/test_wealth_portfolio_history.py tests/unit/test_wealth_portfolio_data.py tests/unit/test_wealth_portfolio_evidence.py tests/unit/test_wealth_portfolio_candidates.py tests/unit/test_wealth_portfolio_model.py -q -p no:cacheprovider
```


宿主已有Python3.12.14使用已有依赖（本机若默认Python未包含依赖，采用原bundled Python路径）；既有后端容器Python3.11.17：docker exec -w /workspace -e PYTHONPATH=/workspace -e PYTHONDONTWRITEBYTECODE=1 wealth-demo-backend python -B -m scripts.wealth_portfolio_history evaluate --manifest .deps/wealth_portfolio_step09/evaluation_manifest_2fc49014ac80e3d04b8c5319277be316ba4b163fefb61b835421bec05965a825.json --output-dir /tmp/portfolio-step09。仓库挂载只读，临时目录是结果输出，输入仍同一内容版本。

最终同一检查集合两环境230 passed、0 failed、0 skipped，交集230（不累计460）。两个环境实际完整评价各重复2次，规范结果跨环境SHA一致，公开网络0；初次容器计算后只读输出失败日志保留，修复输出位置后完整成功。没有运行浏览器/02交易/账户收益/PostgreSQL或LLM，不复制318历史结果。

## 保护及交接

正式算法/评级/资格、来源适配/写控制、公共DTO、固定银行/synthetic产品、.env/lock、F样例和旧快照保持。仅A评价domain、独立历史CLI/测试及本说明/验收/handoff变动；准确保护清单在本地protection.json。无新增公共字段，00需移除条目：无；旧“模型待讨论”状态文字建议仍保留。内部交接见同一[portfolio_handoff.md](portfolio_handoff.md)§7；未发送02。停止于09，不恢复采集、提交或GitHub，不推进10。
