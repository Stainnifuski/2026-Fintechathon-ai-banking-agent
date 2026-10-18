# 理财规划与目标产品推荐拆分、夏普覆盖率

日期：2026-10-17（America/Toronto）。本地主目录main，基线766a179；本轮变更未提交、未推送。

## 流程及实际代码

旧普通推荐本身为资格筛选后类别/代码轮换（每批最多3只），但前端ProductPicker继续操作会调用plan_portfolio，造成目标买入混入整体资产配置。本轮仅保留“理财规划”和“目标产品推荐”两条工作台流程：第一条保留原配置模型/比例；第二条只接受类别、产品名称/代码，勾选后逐产品指定金额，复用现有银行预检/确认/MFA/原号执行。没有给目标推荐调用整体配置、比例调整或prepare差额。

新增name/name_query结构化筛选，连续名称子串或完整代码匹配；空白/混合配置板块参数拒绝，风险及资格校验不变。LLM接线依用户本轮答复留给队友，页面不宣称已完成自然语言理解。原始内部general协议保留兼容及规划用途，正式普通推荐入口移除。未恢复主题/A/C/真实货币/全量采集。

## AkShare与实测覆盖

官方源码：https://github.com/akfamily/akshare/blob/main/akshare/fund/fund_xq.py ，函数fund_individual_analysis_xq，读取雪球`/djapi/fund/base/quote/data/index/analysis/{code}`的`self_index.sharpe_rank`，输出“年化夏普比率”。同一对象中的investment_cost_performance为同类排名百分位，不当作夏普数值。

本次用标准库请求相同源URL/字段，没有调用pandas包装器。范围严格为compact_pool.json中900个代码，以paper_products.json固定类别分组；保存逐代码响应、抓取UTC时刻及SHA256到忽略目录`.deps/sharpe_coverage_20261017/observations.jsonl`，汇总summary.json。脚本`scripts/wealth_sharpe_coverage.py`最多6并发，失败有界重试；仅复核93个空身份响应。没有更新运行数据库、离线包或交易规则。

|类别|总数|近1年|近3年|近5年|
|---|---:|---:|---:|---:|
|全部|900|806（89.56%）|626（69.56%）|435（48.33%）|
|债券|271|242（89.30%）|154（56.83%）|101（37.27%）|
|混合|560|501（89.46%）|421（75.18%）|294（52.50%）|
|权益|69|63（91.30%）|51（73.91%）|40（57.97%）|

本轮任一周期有值与近1年有值集合一致。93只返回成功代码但data={}（债券28、混合59、权益6），另有债券023012返回本人代码但index_data_list=[]；两种均不填0、不借用关联份额。请求失败0，非空异码响应0。未覆盖不意味着该基金所有其他来源均无夏普，也不能未经成立日核对断言都是新基金。源响应未提供统一指标截止日/无风险基准，observed_at仅表示抓取时间；不直接视为同期可比排名。未自行按净值补算。

## 本轮验证

- 新增目标名称/代码匹配、风险拒绝、非法参数、规划估值来源断开时独立推荐且不调用模型/不创建Plan的测试。
- 新测试及候选/规划生命周期回归：157 passed（8.12秒），隔离fixture，无本人交易。
- 前端产品选择、比例调整和草稿隔离已有测试：21 passed；历史夹具中的份额切换用例不代表正式页面恢复A/C入口。
- Docker前端正式构建：TypeScript及Vite通过，保留既有大包提示。本机裸tsc首次遇既有node_modules缺react-markdown/remark-gfm，正式Docker npm ci依赖构建通过；不是忽略类型检查。
- 未重跑全历史验收；未使用本人账户执行验证性申购/赎回；自然语言联调未做。
- 正式启动器已成功重建前后端并等待就绪；3000返回portfolio_ready=true、source_revision=766a179-dirty。实际运行数据库仍为postgresql+asyncpg / bank_local_wealth_20261017；运行AkShare版本1.19.1。实际OpenAPI已包含name/name_query。当前浏览器登录已过期，未把旧页面或未完成的本人登录交互当作本轮UI完整验收。
