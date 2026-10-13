import { useEffect, useState } from 'react';
import type { CandidateGroup } from '../../services/wealth';
import { money } from '../../services/wealth';
import ProductDetail from './ProductDetail';
import type { ProductDetailLoader } from './ProductDetail';
import ProductComparison from './ProductComparison';
import type { ComparisonLoader } from './ProductComparison';
import { changeShare, displayedShare, groupMessage, performanceText, selectionForScope, toggleProduct, validShares } from './productSelection';
import type { ProductSelectionState } from './productSelection';
import './productPicker.css';

const bucketNames: Record<string, string> = { cash_management: '活钱', stable: '稳健', growth: '进取' };
const feeRate = (rate: string | null) => rate == null ? '未提供' : `${(Number(rate) * 100).toLocaleString('zh-CN', { maximumFractionDigits: 4 })}%`;
const classExplanation = 'A类通常更适合较长期持有：一般收取申购费，不收销售服务费。C类通常更适合较短期持有：一般不收申购费，持续收取销售服务费。两类均可能收取赎回费，具体以产品费率为准。';

interface Props {
  groups: CandidateGroup[]; scopeKey: string; selection: ProductSelectionState;
  onSelectionChange: (selection: ProductSelectionState) => void;
  onRefreshGroup: (group: CandidateGroup, selectedFamilyIds: string[]) => void;
  onContinue?: (selection: ProductSelectionState) => void;
  loadDetail?: ProductDetailLoader; loadComparison?: ComparisonLoader;
  busy?: boolean; error?: string;
}
export default function ProductPicker({ groups, scopeKey, selection, onSelectionChange, onRefreshGroup, onContinue, loadDetail, loadComparison, busy = false, error }: Props) {
  const state = selectionForScope(selection, scopeKey);
  const [detailId, setDetailId] = useState<{id:string;version:number} | null>(null);
  const [comparing, setComparing] = useState(false);
  useEffect(() => { setDetailId(null); setComparing(false); }, [scopeKey]);
  return <section className="product-picker" aria-label="产品选择">
    <div className="consumer-card-head"><h2>选择适合的产品</h2><span>已选 {state.items.length} 个家族</span></div>
    <p>可多选。每个家族保留一个具体份额，详情、比较和 A/C 切换都可以按需使用。</p>
    {error && <p role="alert">{error}</p>}{busy && <p role="status">正在读取候选…</p>}
    {groups.map((group, index) => {
      const message = groupMessage(group);
      return <section className="picker-group" key={`${group.bucket ?? 'general'}-${index}`} aria-label={`${bucketNames[group.bucket || ''] || '产品'}候选组`}>
        <div className="consumer-card-head"><h3>{bucketNames[group.bucket || ''] || '产品'}候选</h3>{group.next_cursor && <button className="secondary" disabled={busy} onClick={() => onRefreshGroup(group, state.items.map(item => item.candidate.family_id))}>换一批{bucketNames[group.bucket || ''] || '产品'}</button>}</div>
        {message ? <p className="consumer-empty">{message}</p> : !group.items.length ? <p>当前没有候选资料。</p> : <div className="picker-cards">{group.items.map(candidate => {
          const share = displayedShare(candidate, state);
          const selected = state.items.some(item => item.candidate.family_id === candidate.family_id);
          const performance = share && performanceText(share.performance.one_year);
          return <article key={candidate.family_id} className={`picker-card ${selected ? 'selected' : ''}`}>
            <label className="picker-check"><input type="checkbox" checked={selected} disabled={busy || !share} onChange={() => onSelectionChange(toggleProduct(state, candidate))} /><strong>{candidate.name}</strong></label>
            {!share ? <p>当前没有可申购的有效份额，不能选择。</p> : <>
              <div className="picker-share"><span>{share.share_class ? `${share.share_class}类` : '单一产品'} · R{share.product_risk_level}</span><small>{share.product_id} · v{share.product_version}</small></div>
              <p className="consumer-note">{candidate.reason}</p>
              <div className="picker-performance"><strong>{performance?.label}：{performance?.value}</strong><small>{performance?.date}</small><small>资料时点 {share.data_as_of?.slice(0, 10)??'未知'} · {share.data_source === 'contract_fixture_only' ? '虚构样例' : share.data_source}</small></div>
              <p className="picker-fees">起购 {money(share.min_amount_fen)} · 申购 {feeRate(share.fees.purchase_fee_rate)} · 销售服务 {feeRate(share.fees.sales_service_fee_rate)}</p>
              <p className="consumer-note">赎回费：{share.fees.redemption_fee_rules == null ? '规则未提供' : JSON.stringify(share.fees.redemption_fee_rules)}</p>
              {candidate.share_classes.some(item => item.share_class === 'A' || item.share_class === 'C') && <details className="picker-class-note"><summary>A/C 费用说明</summary><p>{classExplanation}</p></details>}
              <div className="picker-actions"><button className="secondary" disabled={busy} onClick={() => setDetailId({id:share.product_id,version:share.product_version})}>查看{candidate.name}详情</button>{validShares(candidate).filter(item => item.product_id !== share.product_id && item.share_class != null).map(item => <button className="secondary" key={item.product_id} disabled={busy} onClick={() => onSelectionChange(changeShare(state, candidate, item.product_id))}>{item.share_class === 'C' ? '切换C类' : '切回A类'}</button>)}</div>
            </>}
          </article>;
        })}</div>}
      </section>;
    })}
    <section className="picker-selection" aria-label="已选产品"><div className="consumer-card-head"><h3>已选产品 · 换批后保留</h3><button className="secondary" disabled={state.items.length < 2} onClick={() => setComparing(true)}>比较已选产品</button></div>
      {!state.items.length ? <p>勾选产品后会显示在这里；无需先打开详情或比较。</p> : <ul>{state.items.map(({ candidate, share }) => <li key={candidate.family_id}><span><strong>{candidate.name}{share.share_class ? ` · ${share.share_class}类` : ''}</strong><small>{bucketNames[candidate.bucket]} · {share.product_id} · v{share.product_version}</small></span><button className="secondary" onClick={() => onSelectionChange(toggleProduct(state, candidate))}>移除{candidate.name}</button></li>)}</ul>}
      {onContinue && <button disabled={busy || !state.items.length} onClick={() => onContinue(state)}>继续核对所选产品</button>}
    </section>
    {detailId && <ProductDetail productId={detailId.id} productVersion={detailId.version} onClose={() => setDetailId(null)} loadDetail={loadDetail} />}
    {comparing && <ProductComparison products={state.items.map(item => ({ product_id: item.share.product_id, name: `${item.candidate.name}${item.share.share_class ? ' · '+item.share.share_class+'类' : ''}` }))} onClose={() => setComparing(false)} loadComparison={loadComparison} />}
  </section>;
}
