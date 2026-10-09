import {useEffect,useState,useRef} from 'react';
import * as w from '../../services/wealth';
import {sendAction} from '../../services/wealthActions';
import type {ActionName,ActionResult,ActionRequest} from '../../services/wealthActions';
import SingleTradePreview from './SingleTradePreview';
import {returnMoney} from '../../services/wealthReturns';
import './structuredActions.css';
interface Page {rows:(w.OrderRow|w.PositionRow)[];page:number;pages:number;total:number;object_type:'orders'|'positions'}
interface Converted {preview:w.Preview;resolved_units:string;holding_ratio:string;estimated_market_value_fen:number;estimated_fee_fen:number;estimated_net_cash_fen:number;notice:string}
export default function StructuredActionPanel({userId,revision,onActivity}:{userId:string;revision:number;onActivity:()=>void}){
  const [facts,setFacts]=useState<{owner:string;assets:w.Assets;products:w.Product[]}|null>(null);
  const [result,setResult]=useState<ActionResult|null>(null);const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  const scope=useRef<string|null>(null);
  const session=useRef<{context:string;session_id:string;version:number}|null>(null);
  const [mode,setMode]=useState('general');const [category,setCategory]=useState('bond_fund');const [theme,setTheme]=useState('high_dividend');
  const [pageSize,setPageSize]=useState(5);const [selected,setSelected]=useState<{type:'orders'|'positions';ids:string[]}>({type:'positions',ids:[]});
  const [inputMode,setInputMode]=useState('market_value');const [value,setValue]=useState('');const [target,setTarget]=useState('');
  const [confirm,setConfirm]=useState<Converted|null>(null);const [period,setPeriod]=useState('day');const [date,setDate]=useState('');
  useEffect(()=>{let active=true;setError('');void Promise.all([w.get<w.Assets>('/assets'),w.get<w.Product[]>('/products')]).then(([assets,products])=>{
    if(!active)return;
    if(session.current?.context!==assets.context_id){session.current=null;setResult(null);setSelected({type:'positions',ids:[]});setTarget('');setConfirm(null);}
    setFacts({owner:userId,assets,products});setDate(new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai'}).format(new Date(assets.business_at)));
  }).catch(()=>{if(active){setFacts(null);setError('登录账户资料读取失败，请重新登录或刷新。');}});return()=>{active=false;};},[userId,revision]);
  const ready=facts?.owner===userId?facts:null;
  scope.current=ready?.assets.context_id??null;
  const run=async(action:ActionName,params:Record<string,unknown>,selection?:ActionRequest['selection_context'])=>{
    if(!ready||busy)return;
    const latestDate=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai'}).format(new Date(ready.assets.business_at));
    if(action==='show_wealth_returns'&&typeof params.anchor_date==='string'&&params.anchor_date>latestDate){setError('未来还没有发生，不能查询未来收益。');return;}
    setBusy(true);setError('');
    try{const current=session.current;const response=await sendAction({schema_version:'wealth-action-v1',request_id:'WA'+crypto.randomUUID().replace(/-/g,''),context_id:ready.assets.context_id??'legacy-v1',
      ...(current?{session_id:current.session_id,expected_session_version:current.version}:{}),...(selection?{selection_context:selection}:{}),action,params});
      if(scope.current!==(ready.assets.context_id??'legacy-v1'))return;
      session.current={context:response.context_id,session_id:response.session_id,version:response.session_version};setResult(response);
    }catch(reason){setError((reason as {response?:{data?:{detail?:{message?:string}}}})?.response?.data?.detail?.message??'操作失败，请刷新并核对原请求。');}finally{setBusy(false);}
  };
  const recommend=(filter:Record<string,string>)=>void run('recommend_products',{filter});
  const list=(type:'orders'|'positions',page=1)=>{if(selected.type!==type)setSelected({type,ids:[]});void run(type==='orders'?'list_orders':'list_positions',{page,page_size:pageSize});};
  const redeem=()=>{
    if(!ready)return;const redemption:Record<string,unknown>={context_id:ready.assets.context_id??'legacy-v1',input_mode:inputMode};
    if(value){
      if(!/^\d+(\.\d+)?$/.test(value)||Number(value)<=0){setError('请输入大于零的数字');return;}
      if(inputMode==='market_value'){
        if(!/^\d+(\.\d{1,2})?$/.test(value)){setError('市值最多两位小数');return;}
        const [a,b='']=value.split('.');const fen=BigInt(a)*100n+BigInt(b.padEnd(2,'0'));if(fen>2000000000n){setError('金额超过业务上限');return;}redemption.market_value_fen=Number(fen);
      }else if(inputMode==='units')redemption.units=value;
      else{if(!/^\d+(\.\d{1,6})?$/.test(value)||Number(value)>100){setError('比例不超过100%，最多六位小数');return;}const [a,b='']=value.split('.');const scaled=BigInt(a)*1000000n+BigInt(b.padEnd(6,'0'));redemption.holding_ratio=`${scaled/100000000n}.${(scaled%100000000n).toString().padStart(8,'0')}`;}
    }
    void run('preview_redemption',{redemption},selected.type==='positions'&&selected.ids.length?{object_type:'positions',ids:selected.ids}:undefined);
  };
  const page=result?.status==='ok'&&(result.action==='list_orders'||result.action==='list_positions')?result.data as Page:null;
  const converted=result?.status==='ok'&&result.action==='preview_redemption'?result.data as Converted:null;
  const detail=result?.status==='ok'&&result.action==='show_product_detail'?result.data as {name:string;product_id:string;risk_level?:number;latest_nav?:{nav:string;date:string};chart?:{points:unknown[];notice:string}|null}:null;
  const cancellation=result?.status==='ok'&&result.action==='preview_order_cancellation'?result.data as {items:{operation_id:string;eligibility:string;reason:string;consequence:string}[]}:null;
  const returnData=result?.status==='ok'&&result.action==='show_wealth_returns'?result.data as {start_date:string;end_date:string;summary:{period_pnl_fen:number|null};missing_fields:string[]}:null;
  return <section aria-label="统一手动操作" className="structured-actions"><h2>直接选择要做的事</h2><p>这里使用登录账户的模拟银行资料。按钮直接传递操作参数，不需要输入聊天指令。</p>{result&&<p role="status" className="consumer-warning">{result.message}</p>}
    {!ready&&<p>正在读取本人资料…</p>}
    <fieldset disabled={busy||!ready}><legend>规划与推荐</legend><div className="consumer-entry-grid">
      <button onClick={()=>void run('plan_portfolio',{})}>理财规划</button><button onClick={()=>recommend({mode:'general'})}>产品推荐</button>
      <button onClick={()=>recommend({mode:'category',category:'bond_fund'})}>推荐债券基金</button><button onClick={()=>recommend({mode:'theme',theme_key:'high_dividend'})}>想买高股息产品</button></div>
      <p className="consumer-note">队友配置/推荐服务尚未接入时会明确显示原因；旧F11样例仍在原入口。</p>
      <label>推荐方式<select value={mode} onChange={e=>setMode(e.target.value)}><option value="general">普通推荐</option><option value="category">按类别</option><option value="theme">按主题</option></select></label>
      {mode==='category'&&<label>产品类别<select value={category} onChange={e=>setCategory(e.target.value)}><option value="quick_redeem">可快速赎回</option><option value="bank_wealth">银行理财</option><option value="money_fund">货币基金</option><option value="bond_fund">债券基金</option><option value="mixed_fund">混合基金</option><option value="equity_fund">权益基金</option></select></label>}
      {mode==='theme'&&<label>产品主题<select value={theme} onChange={e=>setTheme(e.target.value)}><option value="high_dividend">高股息/红利</option></select></label>}
      <button className="secondary" onClick={()=>recommend({mode,...(mode==='category'?{category}:mode==='theme'?{theme_key:theme}:{})})}>按条件推荐</button>
    </fieldset>
    <fieldset disabled={busy||!ready}><legend>查询与操作</legend><label>每页行数<select value={pageSize} onChange={e=>setPageSize(Number(e.target.value))}><option value={5}>5行</option><option value={10}>10行</option><option value={20}>20行</option></select></label><button onClick={()=>list('orders')}>查询我的订单</button><button onClick={()=>list('positions')}>查询我的持仓</button>
      <label>收益统计<select value={period} onChange={e=>setPeriod(e.target.value)}><option value="day">日</option><option value="month">月</option><option value="year">年</option><option value="cumulative">累计</option></select></label><label>收益日期<input type="date" value={date} max={ready?new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai'}).format(new Date(ready.assets.business_at)):undefined} disabled={period==='cumulative'} onChange={e=>setDate(e.target.value)}/></label><button onClick={()=>void run('show_wealth_returns',{scope:'all',period,...(period==='cumulative'?{}:{anchor_date:date})})}>查询整体收益</button>
      <label>查看产品<select value={target} onChange={e=>setTarget(e.target.value)}><option value="">请选择产品及版本</option>{ready?.products.map(p=><option key={`${p.product_id}:${p.version}`} value={`${p.product_id}:${p.version}`}>{p.name} · v{p.version}</option>)}</select></label><button className="secondary" onClick={()=>{const p=ready?.products.find(x=>`${x.product_id}:${x.version}`===target);void run('show_product_detail',p?{product_id:p.product_id,product_version:p.version}:{});}}>查看产品资料</button>
    </fieldset>
    <fieldset disabled={busy||!ready}><legend>按所选持仓赎回</legend><p>已选{selected.type==='positions'?selected.ids.length:0}只持仓；多选时不会把一笔市值自动套给每只。</p><label>赎回输入方式<select value={inputMode} onChange={e=>setInputMode(e.target.value)}><option value="market_value">持仓市值（元）</option><option value="units">份额</option><option value="holding_ratio">本只持仓比例（%）</option></select></label><label>赎回数量<input inputMode="decimal" value={value} onChange={e=>setValue(e.target.value)}/></label><button onClick={redeem}>核对赎回输入</button><button className="secondary" disabled={selected.type!=='orders'||!selected.ids.length} onClick={()=>void run('preview_order_cancellation',{operation_ids:selected.ids},{object_type:'orders',ids:selected.ids})}>核对已选订单撤单资格</button></fieldset>
    {busy&&<p role="status">正在核对…</p>}{error&&<p role="alert">{error}</p>}
    {result&&<section aria-label="结构化操作结果"><h3>{result.message}</h3>{result.status==='blocked'&&<p className="consumer-note">本次操作未执行；可继续查询本人订单、持仓与收益，操作限制见上述原因。</p>}
      {result.required_fields.map(field=><div key={field.field}><p>{field.reason}</p>{field.options&&field.options.length>0&&<label>补充选择<select defaultValue="" onChange={e=>{if(field.field==='redemption.position_key'){setSelected({type:'positions',ids:[e.target.value]});}else if(field.field==='product_id'){setTarget(e.target.value);}}}><option value="">请选择</option>{field.options.map(x=><option value={x.id} key={x.id}>{x.label}</option>)}</select></label>}</div>)}
      {page&&<><p>共{page.total}行 · 第{page.page}/{page.pages}页</p><div className="returns-table-scroll"><table><thead><tr><th>选择</th><th>产品</th><th>账户</th><th>状态/持有份额</th></tr></thead><tbody>{page.rows.map(row=>{const key=page.object_type==='orders'?(row as w.OrderRow).operation_id:(row as w.PositionRow).position_key;return <tr key={key}><td><input type="checkbox" aria-label={`选择${key}`} checked={selected.type===page.object_type&&selected.ids.includes(key)} onChange={e=>setSelected(current=>({type:page.object_type,ids:e.target.checked?Array.from(new Set([...(current.type===page.object_type?current.ids:[]),key])):current.ids.filter(x=>x!==key)}))}/></td><td>{row.product_name}</td><td>{row.account_id}</td><td>{page.object_type==='orders'?(row as w.OrderRow).status:`${(row as w.PositionRow).units}份（可赎${(row as w.PositionRow).redeemable_units}）`}</td></tr>;})}</tbody></table></div><button className="secondary" disabled={busy||page.page<=1} onClick={()=>list(page.object_type,page.page-1)}>上一页</button><button className="secondary" disabled={busy||page.page>=page.pages} onClick={()=>list(page.object_type,page.page+1)}>下一页</button><button className="secondary" onClick={()=>setSelected({type:page.object_type,ids:[]})}>清空选择</button></>}
      {returnData&&<><p>{returnData.start_date===returnData.end_date?`${returnData.start_date} · 当日收益`:`${returnData.start_date} 至 ${returnData.end_date}`}</p><strong>{returnMoney(returnData.summary.period_pnl_fen)}</strong><p>{returnData.missing_fields.join('；')}</p></>}
      {converted&&<><p>赎回{converted.resolved_units}份 · 比例{(Number(converted.holding_ratio)*100).toFixed(4)}% · 市值{w.money(converted.estimated_market_value_fen)}</p><p>预计费用{w.money(converted.estimated_fee_fen)} · 预计到账{w.money(converted.estimated_net_cash_fen)}</p><p>{converted.notice}</p><button onClick={()=>setConfirm(converted)}>继续本人确认与验证</button></>}
      {detail&&<article><h4>{detail.name} · {detail.product_id}</h4><p>风险等级 {detail.risk_level??'尚未核实'} · 最新净值 {detail.latest_nav?.nav??'缺少资料'}（{detail.latest_nav?.date??'未知'}）</p><p>{detail.chart?.notice??'暂无行情'} · 已读取{detail.chart?.points.length??0}个行情点。完整披露与图表仍可从原产品详情查看。</p></article>}
      {cancellation&&<><p>这里只读资格；实际取消仍从原订单表本人确认。</p>{cancellation.items.map(x=><article key={x.operation_id}><strong>{x.operation_id} · {x.eligibility}</strong><p>{x.reason}</p><p>{x.consequence}</p></article>)}</>}
    </section>}
    {confirm&&<SingleTradePreview preview={confirm.preview} redemption={confirm} onClose={()=>setConfirm(null)} onActivity={onActivity}/>}
  </section>;
}
