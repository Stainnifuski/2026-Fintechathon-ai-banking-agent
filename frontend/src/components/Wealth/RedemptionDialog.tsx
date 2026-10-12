import {useState} from 'react';
import * as w from '../../services/wealth';
import SingleTradePreview from './SingleTradePreview';

export interface Conversion {
  resolved_units:string;holding_units:string;redeemable_units:string;holding_ratio:string;
  estimated_market_value_fen:number;estimated_fee_fen:number;estimated_net_cash_fen:number;
  nav:string;nav_date:string;notice:string;preview:w.Preview;
}
export type RedemptionPreviewLoader=(params:Record<string,unknown>,row:w.PositionRow)=>Promise<Conversion>;
export default function RedemptionDialog({rows,contextId,fixtureOnly,blockedAccounts=[],sessionId,loadPreview,onCompleted,onClose,onActivity}:{
  rows:w.PositionRow[];contextId:string;fixtureOnly:boolean;blockedAccounts?:string[];sessionId?:string;loadPreview?:RedemptionPreviewLoader;onCompleted?:(message:string)=>void;onClose:()=>void;onActivity:()=>void
}){
  const [inputs,setInputs]=useState<Record<string,{mode:string;value:string;redemption:string}>>({});
  const [result,setResult]=useState<Conversion|null>(null);const [confirm,setConfirm]=useState(false);const [error,setError]=useState('');const [busy,setBusy]=useState(false);
  const change=(key:string,field:string,value:string)=>{setResult(null);setError('');setInputs(current=>({...current,[key]:{...(current[key]??{mode:'units',value:'',redemption:'standard'}),[field]:value}}));};
  const preview=async(row:w.PositionRow)=>{
    setBusy(true);setResult(null);setConfirm(false);setError('');
    try{
      const input=inputs[row.position_key]??{mode:'units',value:'',redemption:'standard'};
      if(!/^\d+(\.\d+)?$/.test(input.value)||Number(input.value)<=0)throw new Error('请填写大于零的数字。');
      const params:Record<string,unknown>={schema_version:'wealth-redemption-request-v1',context_id:contextId,
        position_key:row.position_key,input_mode:input.mode,redemption_mode:input.redemption};
      if(input.mode==='market_value'){
        if(!/^\d+(\.\d{1,2})?$/.test(input.value))throw new Error('金额最多保留两位小数。');
        const [yuan,fraction='']=input.value.split('.');const fen=BigInt(yuan)*100n+BigInt(fraction.padEnd(2,'0'));
        if(fen>2000000000n)throw new Error('金额超过业务上限。');params.market_value_fen=Number(fen);
      }else if(input.mode==='holding_ratio'){
        if(!/^\d+(\.\d{1,6})?$/.test(input.value)||Number(input.value)>100)throw new Error('比例须大于0且不超过100%，最多六位小数。');
        const [whole,fraction='']=input.value.split('.');
        const scaled=BigInt(whole)*1000000n+BigInt(fraction.padEnd(6,'0'));
        params.holding_ratio=`${scaled/100000000n}.${(scaled%100000000n).toString().padStart(8,'0')}`;
      }else{
        if(!/^\d+(\.\d{1,4})?$/.test(input.value))throw new Error('份额最多保留四位小数。');params.units=input.value;
      }
      if(fixtureOnly){setError('当前是F23录制持仓。输入已填写；可信换算和交易预览请切换“查询登录账户模拟银行”，样例不会生成银行授权。');return;}
      const converted=loadPreview?await loadPreview(params,row):await w.post<Conversion>('/redemptions/preview',params);
      if(sessionId&&!loadPreview)await w.post(`/sessions/${sessionId}/preview-link`,{authorization_id:converted.preview.authorization_id});
      setResult(converted);
    }catch(reason){setError((reason as {response?:{data?:{detail?:{message?:string}}}})?.response?.data?.detail?.message??(reason instanceof Error?reason.message:'预览失败，请刷新持仓后重试。'));}
    finally{setBusy(false);}
  };
  return <>{!result&&<div className="wealth-modal"><section className="card" role="dialog" aria-modal="true" aria-label="持仓赎回输入"><h3>填写要赎回的持仓</h3><p>每只产品分别填写，不自动均摊。比例以本只总持仓为分母，锁定和在途占用不计入可赎。</p>
    {rows.map(row=>{const input=inputs[row.position_key]??{mode:'units',value:'',redemption:'standard'};return <article key={row.position_key} className="table-operation-row"><strong>{row.product_name} · {row.account_id} · v{row.product_version}</strong><p>持有 {row.units}份 · 可赎 {row.redeemable_units}份 · 锁定 {row.locked_units} · 占用 {row.held_units}</p>
      <label>输入方式<select aria-label={`${row.position_key}输入方式`} disabled={busy} value={input.mode} onChange={e=>change(row.position_key,'mode',e.target.value)}><option value="units">按份额</option><option value="market_value">按持仓市值（元）</option><option value="holding_ratio">按持仓比例（%）</option></select></label>
      <label>{input.mode==='units'?'赎回份额':input.mode==='market_value'?'拟赎回市值（元）':'本只持仓比例（%）'}<input aria-label={`${row.position_key}赎回输入`} inputMode="decimal" value={input.value} disabled={busy} onChange={e=>change(row.position_key,'value',e.target.value)}/></label>
      <label>赎回方式<select value={input.redemption} disabled={busy} onChange={e=>change(row.position_key,'redemption',e.target.value)}><option value="standard">普通赎回</option><option value="quick">快速赎回（须符合银行合同）</option></select></label><button disabled={busy||!input.value||row.disclosure_version==null||blockedAccounts.includes(row.account_id)} onClick={()=>void preview(row)}>预览本只赎回</button>{blockedAccounts.includes(row.account_id)&&<p>该账户已有人工接管或暂停任务，暂不可发起赎回预览。</p>}
    </article>;})}
    {error&&<p role="alert">{error}</p>}<button className="secondary" disabled={busy} onClick={onClose}>关闭赎回输入</button>
  </section></div>}{result&&!confirm&&<div className="wealth-modal"><section className="card" role="dialog" aria-modal="true" aria-label="赎回换算核对"><h3>核对赎回换算</h3><p>份额 {result.resolved_units} ／总持仓 {result.holding_units} · 比例 {(Number(result.holding_ratio)*100).toFixed(4)}%</p><p>舍入后拟赎回市值 {w.money(result.estimated_market_value_fen)} · 净值 {result.nav}（{result.nav_date}）</p><p>预计费用 {w.money(result.estimated_fee_fen)} · 预计到账 {w.money(result.estimated_net_cash_fen)}</p><p>{result.notice}</p><button onClick={()=>setConfirm(true)}>继续本人确认与验证</button><button className="secondary" onClick={()=>setResult(null)}>返回修改输入</button></section></div>}
    {result&&confirm&&<SingleTradePreview preview={result.preview} redemption={result} onClose={()=>{setResult(null);setConfirm(false);}} onActivity={onActivity} onCompleted={onCompleted}/>}</>;
}
