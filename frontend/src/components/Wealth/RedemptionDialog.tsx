import AgentStepCard,{WealthStepContext,useStepInstruction} from './AgentStepCard';
import {useRef,useState,useEffect,useContext} from 'react';
import * as w from '../../services/wealth';
import PlanExecution from './PlanExecution';
import {wealthRequestFailure} from '../../utils/wealthFeedback';

export default function RedemptionDialog({rows,contextId,fixtureOnly,blockedAccounts=[],sessionId,onCompleted,onClose,onActivity,onPrepared,inline=false,initialInput,novaTaskId,autoPrepare=false}:{
  autoPrepare?:boolean;inline?:boolean;initialInput?:{mode:string;value:string;redemption:string};novaTaskId?:string;onPrepared?:(id:string)=>Promise<void>;rows:w.PositionRow[];contextId:string;fixtureOnly:boolean;blockedAccounts?:string[];sessionId?:string;onCompleted?:(message:string)=>void;onClose:()=>void;onActivity:()=>void
}){
  const [inputs,setInputs]=useState<Record<string,{mode:string;value:string;redemption:string}>>(()=>initialInput?Object.fromEntries(rows.map(r=>[r.position_key,{...initialInput}])):{});
  const [result,setResult]=useState<{plan:w.Plan;preview:w.PlanPreview|null}|null>(null);
  const [error,setError]=useState('');const [busy,setBusy]=useState(false);
  const steps=useContext(WealthStepContext);
  const requestId=useRef<string|null>(null);
  const change=(key:string,field:string,value:string)=>{requestId.current=null;setError('');setInputs(current=>({...current,[key]:{...(current[key]??{mode:'units',value:'',redemption:'standard'}),[field]:value}}));};
  const parameters=(row:w.PositionRow)=>{
    const input=inputs[row.position_key]??{mode:'units',value:'',redemption:'standard'};
    if(!/^\d+(\.\d+)?$/.test(input.value)||Number(input.value)<=0)throw new Error(`${row.product_name}：请填写大于零的数字。`);
    const params:Record<string,unknown>={schema_version:'wealth-redemption-request-v1',context_id:contextId,
      position_key:row.position_key,input_mode:input.mode,redemption_mode:input.redemption};
    if(input.mode==='market_value'){
      if(!/^\d+(\.\d{1,2})?$/.test(input.value))throw new Error('金额最多保留两位小数。');
      const [yuan,fraction='']=input.value.split('.');const fen=BigInt(yuan)*100n+BigInt(fraction.padEnd(2,'0'));
      if(fen>2000000000n)throw new Error('金额超过业务上限。');params.market_value_fen=Number(fen);
    }else if(input.mode==='holding_ratio'){
      if(!/^\d+(\.\d{1,6})?$/.test(input.value)||Number(input.value)>100)throw new Error('比例须大于0且不超过100%，最多六位小数。');
      const [whole,fraction='']=input.value.split('.');const scaled=BigInt(whole)*1000000n+BigInt(fraction.padEnd(6,'0'));
      params.holding_ratio=`${scaled/100000000n}.${(scaled%100000000n).toString().padStart(8,'0')}`;
    }else{
      if(!/^\d+(\.\d{1,4})?$/.test(input.value))throw new Error('份额最多保留四位小数。');params.units=input.value;
    }
    return params;
  };
  const prepare=async()=>{
    setBusy(true);setError('');
    try{
      if(fixtureOnly)throw new Error('当前是录制持仓，请切换登录账户模拟银行；样例不会生成交易授权。');
      if(rows.some(row=>blockedAccounts.includes(row.account_id)))throw new Error('选中账户已有人工接管或暂停任务，暂不可发起赎回。');
      const items=rows.map(parameters);
      requestId.current??='WR'+crypto.randomUUID().replace(/-/g,'');
      const prepared=await w.post<{plan:w.Plan;preview:w.PlanPreview|null}>('/redemptions/prepare',{request_id:requestId.current,items,...(sessionId?{session_id:sessionId}:{})},novaTaskId);
      await onPrepared?.(prepared.plan.plan_id);setResult(prepared);steps?.complete('redemption_input');
    }catch(reason){setError(wealthRequestFailure(reason));}finally{setBusy(false);}
  };
  useStepInstruction('redemption_input',()=>{if(!busy&&!result)void prepare();});
  const attempted=useRef(false);
  useEffect(()=>{if(autoPrepare&&rows.length===1&&initialInput?.value&&!attempted.current){attempted.current=true;void prepare();}},[autoPrepare,rows.length]);
  return <div className={inline?'nova-inline-redemption':'wealth-modal'}><section className="card" role="dialog" aria-modal="true" aria-label="持仓赎回输入">
    {!result?<AgentStepCard stage="redemption_input" title="填写赎回数量"><h3>填写要赎回的持仓</h3><p>每只产品分别填写，填写完成后统一核对、一次本人确认和验证。不会自动均摊。</p>
      {rows.map(row=>{const input=inputs[row.position_key]??{mode:'units',value:'',redemption:'standard'};return <article key={row.position_key} className="table-operation-row"><strong>{row.product_name} · {row.account_id} · v{row.product_version}</strong><p>持有 {row.units}份 · 可赎 {row.redeemable_units}份 · 锁定 {row.locked_units} · 占用 {row.held_units}</p>
        <label>输入方式<select aria-label={`${row.position_key}输入方式`} disabled={busy} value={input.mode} onChange={e=>change(row.position_key,'mode',e.target.value)}><option value="units">按份额</option><option value="market_value">按持仓市值（元）</option><option value="holding_ratio">按持仓比例（%）</option></select></label>
        <label>{input.mode==='units'?'赎回份额':input.mode==='market_value'?'拟赎回市值（元）':'本只持仓比例（%）'}<input aria-label={`${row.position_key}赎回输入`} inputMode="decimal" value={input.value} disabled={busy} onChange={e=>change(row.position_key,'value',e.target.value)}/></label>
        <label>赎回方式<select value={input.redemption} disabled={busy} onChange={e=>change(row.position_key,'redemption',e.target.value)}><option value="standard">普通赎回</option><option value="quick">快速赎回（须符合银行合同）</option></select></label>
        {blockedAccounts.includes(row.account_id)&&<p>该账户已有人工接管或暂停任务，暂不可发起赎回。</p>}
      </article>;})}
      <button disabled={busy||!rows.length||rows.some(row=>!inputs[row.position_key]?.value||row.disclosure_version==null||blockedAccounts.includes(row.account_id))} onClick={()=>void prepare()}>{busy?'正在核对全部赎回…':`统一核对${rows.length}只赎回`}</button>
    </AgentStepCard>:<><h3>核对全部赎回</h3><p>以下逐笔列出份额、预计到账和披露；只需一次本人确认与验证码。部分失败时按原操作号核对，不自动撤销已成功赎回。</p><PlanExecution key={result.plan.plan_id} plan={result.plan} initialPreview={result.preview} onActivity={onActivity} onCompleted={onCompleted}/></>}
    {error&&<p role="alert">{error}</p>}<button className="secondary" disabled={busy} onClick={onClose}>关闭赎回窗口</button>
  </section></div>;
}
