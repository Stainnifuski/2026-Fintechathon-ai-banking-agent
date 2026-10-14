import { useEffect, useState } from 'react';
import { get, money } from '../../services/wealth';
import type { Product } from '../../services/wealth';

export interface ProductComparisonRow {
  product_id: string; name: string; share_class: string | null; product_version: number;
  product_risk_level: number | null; min_amount_fen: number | null;
  source_label?: string;
  purchase_fee_rate: string | null; sales_service_fee_rate: string | null;
  redemption_fee_rules: string | null; performance_label: string; performance_value: string; performance_dates: string;
}
export type ComparisonLoader = (ids: string[]) => Promise<ProductComparisonRow[]>;
const rate = (value: string | null) => value == null ? '未提供' : `${(Number(value) * 100).toLocaleString('zh-CN', { maximumFractionDigits: 4 })}%`;
const bankComparison: ComparisonLoader = async ids => {
  const rows = await get<Product[]>(`/compare?product_ids=${encodeURIComponent(ids.join(','))}`);
  return comparisonRows(rows);
};

export function comparisonRows(rows:Product[]):ProductComparisonRow[]{
  return rows.map(row => ({ product_id: row.product_id, name: row.name, share_class: null, product_version: row.version,
    product_risk_level: row.product_risk_level, min_amount_fen: row.rules.min_amount_fen,
    source_label: row.product_risk_source==='project_policy'?'真实基金资料；项目分级独立于发行人；模拟交易费用':'模拟产品等级与模拟交易费用',
    purchase_fee_rate: row.rules.purchase_fee_rate, sales_service_fee_rate: row.rules.sales_service_fee_rate??null, redemption_fee_rules: row.rules.redeem_fee_bands?.every(r=>Number(r.rate)===0)?'项目模拟赎回费为0':row.disclosure.fees,
    performance_label: '近一年业绩', performance_value: '此接口未提供，详见产品资料', performance_dates: '日期未提供' }));
}

export default function ProductComparison({ products, onClose, loadComparison = bankComparison }: {
  products: { product_id: string; name: string }[]; onClose: () => void; loadComparison?: ComparisonLoader;
}) {
  const [ids, setIds] = useState<string[]>(products.length <= 4 ? products.map(product => product.product_id) : []);
  const [rows, setRows] = useState<ProductComparisonRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { let live = true; setRows([]); setError(''); setBusy(false);
    if (ids.length < 2 || ids.length > 4) return;
    setBusy(true);
    loadComparison(ids).then(value => { if (live) setRows(value); }).catch(() => { if (live) setError('比较资料暂不可用，请重新选择或稍后重试。'); })
      .finally(() => { if (live) setBusy(false); });
    return () => { live = false; };
  }, [ids, loadComparison]);
  return <div className="wealth-modal"><section role="dialog" aria-modal="true" aria-label="产品比较" className="card picker-comparison">
    <div className="consumer-card-head"><h2>比较具体份额</h2><button className="secondary" onClick={onClose}>关闭比较</button></div>
    <p>请选择 2—4 只。比较和详情均可选，不影响已选产品，也不是继续操作的前置条件。</p>
    <div className="picker-compare-options">{products.map(product => <label key={product.product_id}><input type="checkbox" checked={ids.includes(product.product_id)} disabled={!ids.includes(product.product_id) && ids.length >= 4} onChange={event => {const checked=event.target.checked;setIds(current=>checked?(current.includes(product.product_id)||current.length>=4?current:[...current,product.product_id]):current.filter(id=>id!==product.product_id));}} />{product.name}</label>)}</div>
    {ids.length < 2 && <p>请至少选择两只产品。</p>}{busy && <p role="status">正在读取比较资料…</p>}{error && <p role="alert">{error}</p>}
    {!!rows.length && <div className="wealth-table"><table aria-label="所选产品比较"><thead><tr><th>产品／份额</th><th>风险／起购</th><th>费率</th><th>资料说明</th></tr></thead><tbody>{rows.map(row => <tr key={row.product_id}>
      <td>{row.name}<small>{row.share_class ? `${row.share_class}类 · ` : ''}{row.product_id} · v{row.product_version}</small></td>
      <td>{row.product_risk_level == null ? '风险未提供' : `R${row.product_risk_level}`}<small>{row.source_label}</small><small>起购 {money(row.min_amount_fen)}</small></td>
      <td>申购 {rate(row.purchase_fee_rate)}<small>销售服务 {rate(row.sales_service_fee_rate)}</small><small>{row.redemption_fee_rules || '赎回费规则未提供'}</small></td>
      <td>模拟成交以确认页价格及规则为准；公开费用未知不补零。<small>本金可能亏损，低风险不代表收益保证。</small></td>
    </tr>)}</tbody></table></div>}
    <p className="consumer-note">本表费用是模拟银行成交条件，不代表现实渠道完整费用；公开未知值仍未知。历史指标须按各自口径阅读，不代表本人实际收益或未来回报。</p>
  </section></div>;
}
