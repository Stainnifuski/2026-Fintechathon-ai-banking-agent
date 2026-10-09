import { useState } from 'react';
import fixture from '../../../../tests/fixtures/wealth_portfolio_contract_v1/f01.json';
import multiAccountFixture from '../../../../tests/fixtures/wealth_portfolio_contract_v1/f18.json';
import missingValuationFixture from '../../../../tests/fixtures/wealth_portfolio_contract_v1/f10.json';
import { money } from '../../services/wealth';
import type { Account } from '../../services/bank';
import Wealth from './index';
import ProductPicker from './ProductPicker';
import { recordedGroups, recordedNextGroup, recordedDetail, recordedComparison, recordedF11Plan, recordedF11Snapshot, recordedF11Selection, recordedRevision, recordedPrepare } from './FixtureProductProvider';
import { emptyProductSelection, selectionForScope } from './productSelection';
import type { ProductSelectionState } from './productSelection';
import AllocationReview from './AllocationReview';
import './consumerPreview.css';

const bucketLabels: Record<string, string> = { cash_management: '活钱', stable: '稳健', growth: '进取' };
const stages = ['读取资产与评估', '查看配置与候选', '选择产品与比例', '核对差额与预览', '本人确认与验证', '订单与持仓'];
const percent = (ratio: string) => (Number(ratio) * 100).toLocaleString('zh-CN', { maximumFractionDigits: 2 });

interface Props { accounts: Account[]; revision: number; onActivity: () => void; userId: string }

/** B0样例消费与银行联调入口；样例本身不能生成银行命令。 */
export default function PortfolioConsumerPreview(props: Props) {
  const [scenario, setScenario] = useState<'F01' | 'F18' | 'F10' | 'F11'>('F01');
  const [panel, setPanel] = useState<'plan' | 'products' | 'orders' | 'positions' | 'manual'>('plan');
  const [groups, setGroups] = useState(recordedGroups);
  const [productSelection, setProductSelection] = useState(() => emptyProductSelection(fixture.snapshot.asset_scope_id));
  const [reviewing, setReviewing] = useState(false);
  const [reviewEpoch, setReviewEpoch] = useState(0);
  const snapshot = scenario === 'F01' ? fixture.snapshot : scenario === 'F11' ? recordedF11Snapshot() : scenario === 'F18' ? multiAccountFixture.snapshot : missingValuationFixture.snapshot;
  const missing = !snapshot.valuation_complete;
  const available = snapshot.accounts.reduce((sum, account) => sum + account.available_fen, 0);
  const currentCash = snapshot.accounts.reduce((sum, account) => sum + account.balance_fen, 0);
  const selection = selectionForScope(productSelection, snapshot.asset_scope_id);
  const changeSelection = (next: ProductSelectionState) => { setProductSelection(next); setReviewing(false); };
  const changeScenario = (next: typeof scenario) => {
    setScenario(next); setReviewing(false); setGroups(recordedGroups());
    const scope = next === 'F01' ? fixture.snapshot.asset_scope_id : next === 'F11' ? recordedF11Snapshot().asset_scope_id : next === 'F18' ? multiAccountFixture.snapshot.asset_scope_id : missingValuationFixture.snapshot.asset_scope_id;
    setProductSelection(emptyProductSelection(scope));
  };
  const loadSeven = () => {setScenario('F11');setGroups(recordedGroups());setProductSelection(recordedF11Selection());setPanel('products');setReviewEpoch(value=>value+1);setReviewing(true);};

  return <section className="consumer-preview" aria-label="理财手动流程开发预览">
    <div className="consumer-fixture-banner"><strong>B2 · 独立开发预览</strong><span>{panel === 'manual' ? '当前入口使用登录账户的模拟银行资金；申购必须本人确认与验证。' : '当前数字与候选为虚构契约样例，与登录账户资金无关；样例不产生订单。'}</span><a href="/">返回原有银行工作台</a></div>
    <header className="consumer-heading"><p className="eyebrow">理财 · 手动操作流程</p><h1>先看清资产，再决定怎么安排。</h1><p>资产、当前方案和操作入口保持可见。每一步完成后，再明确进入下一步。</p></header>
    <div className="consumer-columns">
      <main>
        {panel !== 'manual' && <section className="consumer-card" aria-label="示例资产概况"><div className="consumer-card-head"><h2>资产概况</h2><span className="consumer-tag">契约样例 · {scenario}</span></div>
          <div className="consumer-total"><small>示例模拟总资产</small><strong>{missing ? '暂无法完整估值' : money(snapshot.total_assets_fen)}</strong><span>{missing ? '持仓市值缺失，不能把可见现金冒充总资产。' : '含现金、持仓和赎回应收；冻结资金不重复计入。'}</span></div>
          <dl className="consumer-metrics"><div><dt>可用现金</dt><dd>{money(available)}</dd></div><div><dt>冻结资金</dt><dd>{money(currentCash - available)}</dd></div><div><dt>持仓市值</dt><dd>{missing ? '缺少估值' : money(snapshot.positions.reduce((sum, position) => sum + (position.market_value_fen ?? 0), 0))}</dd></div><div><dt>赎回应收</dt><dd>{money(snapshot.receivable_fen)}</dd></div></dl>
          <p className="consumer-note">{scenario === 'F01'||scenario==='F11' ? '一个虚构账户，现金 ¥10,000，无持仓。' : '两个虚构账户，现金分别 ¥6,000 / ¥2,000，其中冻结 ¥1,000。'} 样例时点：2026-10-08 12:00（北京时间）。</p>
        </section>}
        <section className="consumer-card" aria-label="操作入口"><h2>你想先做什么？</h2><div className="consumer-entry-grid">
          <button aria-pressed={panel === 'plan'} onClick={() => setPanel('plan')}><strong>理财规划</strong><span>查看整体配置样例</span></button>
          <button aria-pressed={panel === 'products'} onClick={() => setPanel('products')}><strong>产品推荐</strong><span>候选多选、A/C 切换和比较</span></button>
          <button aria-pressed={panel === 'orders'} onClick={() => setPanel('orders')}><strong>查看订单</strong><span>受理、结算与撤单</span></button>
          <button aria-pressed={panel === 'positions'} onClick={() => setPanel('positions')}><strong>查看持仓</strong><span>份额、赎回与实际收益</span></button>
          <button aria-pressed={panel === 'manual'} onClick={() => setPanel('manual')}><strong>手选模拟产品</strong><span>现有产品 → 预览 → 本人确认与验证</span></button>
        </div></section>
        <section className="consumer-card" aria-live="polite" aria-label="当前操作内容">
          {panel === 'manual' ? <Wealth {...props} developmentManual /> : panel === 'plan' && scenario !== 'F01' && scenario !== 'F11' ? <><h2>当前资产场景</h2><p>{missing ? '持仓估值缺失，不能生成完整的整体配置或准备交易。' : '该场景用于核对多账户、冻结资金和应收的汇总方式。'}</p><p className="consumer-note">当前未提供绑定此快照的配置方案，不套用其他快照的比例。</p><button className="secondary" onClick={() => changeScenario('F01')}>查看 F01 整体配置样例</button></> : panel === 'plan' ? <><div className="consumer-card-head"><h2>整体配置示例</h2><span>{scenario} · 非投资建议</span></div><p>目标占比的分母是全部示例资产 ¥10,000，不是本次新增投入金额。</p>
            {fixture.plan.buckets.map(bucket => <div className="consumer-bucket" key={bucket.bucket}><div><strong>{bucketLabels[bucket.bucket]}</strong><span>{percent(bucket.target_ratio)}% · {money(bucket.target_fen)}</span></div><div className="consumer-track"><span className={bucket.bucket} style={{ width: `${percent(bucket.target_ratio)}%` }} /></div><small>示例允许区间 {percent(bucket.min_ratio)}%—{percent(bucket.max_ratio)}%</small></div>)}
            <p className="consumer-note">扣除活钱后，进取占其余部分的 62.5%。这些比例仅来自 F01，不能作为队友模型的固定算法。</p>
            <button onClick={() => setPanel('products')}>选择产品</button><button className="secondary" onClick={loadSeven}>体验七产品整体核对</button><p className="consumer-note">配置图／滑块由 A 提供；当前可体验候选选择、最终比例编辑和差额核对。</p>
          </> : panel === 'products' ? <><p className="consumer-note">虚构候选与行情样例。七产品样例可直接核对；也可将青禾债券调为15%、清岚债券调为5%，其余不变，体验比例修订。其他比例等待配置服务接入。</p><button className="secondary" onClick={loadSeven}>加载 F11 七产品核对样例</button><ProductPicker groups={groups} scopeKey={snapshot.asset_scope_id} selection={selection} onSelectionChange={changeSelection}
            onRefreshGroup={(group, ids) => { const next = recordedNextGroup(group, ids); setGroups(current => current.map(item => item.bucket === group.bucket ? next : item)); }}
            onContinue={() => setReviewing(true)} loadDetail={recordedDetail} loadComparison={recordedComparison} />
          </> : panel === 'orders' ? <><h2>示例订单</h2><p className="consumer-empty">当前只读样例没有订单。真实订单表和撤单将在 B3a 接入；不会读取或清空登录账户的订单。</p></> : <><h2>示例持仓</h2><p className="consumer-empty">{scenario === 'F01' ? 'F01 无持仓。' : '该场景包含已有持仓，但具体份额行尚未展示。'} 持仓表、两种赎回输入和实际收益分阶段接入，空白不代表收益为零。</p></>}
        </section>
        {reviewing && <section className="consumer-card" hidden={panel!=='products'}><AllocationReview key={`${snapshot.asset_scope_id}:${reviewEpoch}:${selection.items.map(i=>i.share.product_id).join(',')}`} plan={scenario==='F11'?recordedF11Plan():fixture.plan} snapshot={snapshot} selection={selection} onRevise={recordedRevision} onPrepare={recordedPrepare} fixtureOnly /></section>}
      </main>
      <aside className="consumer-sidebar"><section className="consumer-card"><h2>当前步骤</h2><ol className="consumer-steps">{stages.map((label, i) => <li key={label}><span>{i + 1}</span><div><strong>{label}</strong><small>{i === 0 ? 'B0：只读样例已可展示' : i === 1 ? 'B1：候选与份额选择已接入' : i === 2 ? `已选 ${selection.items.length} 个家族；B2 可编辑比例` : i===3 ? 'B2：差额与执行适配已接入' : i===4 ? '七笔授权已通过隔离银行测试；样例不下单' : '后续阶段接入，不代表已完成'}</small></div></li>)}</ol></section>
        <section className="consumer-card"><h2>资料与操作说明</h2><p>正式操作仍需本人风险评估、本人确认和验证码。这里没有代你评估或授权。</p><p>订单“已受理”不等于“已结算”。预览不冻结，受理才占用，撤单释放原占用。</p>{panel === 'manual' ? <p className="consumer-note">当前使用现有模拟银行产品和本人评估。手选产品不经过 LLM；下单金额不来自虚构样例。</p> : <><label htmlFor="consumer-scenario">检查不同资产场景</label><select id="consumer-scenario" value={scenario} onChange={event => changeScenario(event.target.value as typeof scenario)}><option value="F01">F01 · 空持仓整体配置</option><option value="F11">F11 · 七产品整体目标</option><option value="F18">F18 · 两账户、冻结与应收</option><option value="F10">F10 · 持仓估值缺失</option></select><p className="consumer-note">切换资产范围会清空产品选择；样例不向后端发送交易或模型请求。</p></>}</section>
      </aside>
    </div>
  </section>;
}
