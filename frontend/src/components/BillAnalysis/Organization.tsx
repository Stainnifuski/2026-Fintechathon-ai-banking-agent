import { useEffect, useState } from 'react';
import * as bills from '../../services/bills';
import {labels,errorText} from './Decisions';
import type {BillSelection} from './Workspace';

export default function Organization({catalog,revision,onChanged,onSelect,onTag}:{
  catalog:bills.FoundationCatalog|null;revision:number;onChanged:()=>void;onSelect:(selection:BillSelection|null)=>void;onTag:(id:string)=>void;
}) {
  const [rules,setRules]=useState<bills.CategoryRule[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false),[retry,setRetry]=useState(0);
  useEffect(()=>{let live=true;setError('');bills.categoryRules().then(rows=>{if(live)setRules(rows);}).catch(e=>{if(live)setError(errorText(e));});return()=>{live=false;};},[revision,retry]);
  const remove=async(rule:bills.CategoryRule)=>{setBusy(true);setError('');try{await bills.removeCategoryRule(rule.merchant_id,rule.version);onSelect(null);onChanged();}catch(e){setError(errorText(e));}finally{setBusy(false);}};
  return <section aria-label="分类与标签管理">
    <h3>商户分类规则</h3><p className="hint">规则影响同商户无单笔修正的消费。新增或调整规则时，在交易详情中修正分类并选择应用到同商户。</p>
    {!rules.length&&<p className="bill-empty">尚无商户分类规则。</p>}
    {rules.map(rule=><article className="bill-record" key={rule.merchant_id}>
      <div><strong>{catalog?.merchants.find(m=>m.merchant_id===rule.merchant_id)?.name||'已保存商户'}</strong><p>消费分类：{labels[rule.category]||rule.category}</p></div>
      <div className="bill-actions"><button type="button" className="secondary" onClick={()=>onSelect({type:'category_rule',id:rule.merchant_id})}>选择供 NOVA 查询</button><button type="button" className="secondary" disabled={busy} onClick={()=>void remove(rule)}>撤销规则</button></div>
    </article>)}
    <h3>流水标签</h3><p className="hint">标签在交易详情中编辑，最多10个。可按标签查看关联流水。</p>
    {!catalog?.tags.length&&<p className="bill-empty">尚无标签。打开一笔交易详情可添加标签。</p>}
    {catalog?.tags.map(tag=><article className="bill-record" key={tag.tag_id}><div><strong>{tag.name}</strong><p>关联 {tag.transaction_count} 笔流水</p></div><div className="bill-actions"><button type="button" className="secondary" onClick={()=>onTag(tag.tag_id)}>查看关联流水</button><button type="button" className="secondary" onClick={()=>onSelect({type:'tag',id:tag.tag_id})}>选择供 NOVA 查询</button></div></article>)}
    {error&&<p role="alert" className="error">{error} <button onClick={()=>setRetry(n=>n+1)}>重新加载</button></p>}
  </section>;
}
