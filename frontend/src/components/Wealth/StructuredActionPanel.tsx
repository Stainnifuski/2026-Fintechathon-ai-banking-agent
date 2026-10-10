import {useEffect,useState,useRef,useCallback} from 'react';
import * as w from '../../services/wealth';
import {sendAction} from '../../services/wealthActions';
import type {ActionName,ActionResult,ActionRequest} from '../../services/wealthActions';
import OrderCancellationDialog from './OrderCancellationDialog';
import type {CancellationRow} from './OrderCancellationDialog';
import {OrderDetail} from './OrderTable';
import {PositionDetail} from './PositionTable';
import SingleTradePreview from './SingleTradePreview';
import ProductDetail from './ProductDetail';
import type {ProductDetailData,ProductDetailLoader} from './ProductDetail';
import ProductComparison,{comparisonRows} from './ProductComparison';
import type {ComparisonLoader} from './ProductComparison';
import {WealthActionSession} from '../../services/wealthActionSession';
import type {WealthReturns} from '../../services/wealthReturns';
import {returnStatus,returnMoney} from '../../services/wealthReturns';
import {beijingTime} from '../../utils/time';
import ProductPicker from './ProductPicker';
import {readCandidateGroup,assertCurrentCandidates,sameCandidateFilter,replaceCandidateGroup} from '../../services/wealthCandidates';
import {readPortfolioPlan,planProductSelection} from '../../services/wealthPortfolioReview';
import StructuredPortfolioReview from './StructuredPortfolioReview';
import './structuredActions.css';
import {manualDrafts,draftScope} from '../../services/wealthManualDraft';
import {useManualDraft} from './useManualDraft';
type ResultSlot='planning'|'records'|'returns'|'product'|'redemption'|'cancellation'|'comparison';
const resultSlot=(action:ActionName):ResultSlot=>action==='list_orders'||action==='list_positions'?'records':action==='show_wealth_returns'?'returns':action==='show_product_detail'?'product':action==='preview_redemption'?'redemption':action==='preview_order_cancellation'?'cancellation':action==='compare_products'?'comparison':'planning';
const resultTitles:Record<ResultSlot,string>={planning:'规划与推荐结果',records:'订单 / 持仓查询结果',returns:'收益查询结果',product:'产品资料查询结果',redemption:'赎回核对结果',cancellation:'撤单资格核对结果',comparison:'产品比较结果'};
interface Page {rows:(w.OrderRow|w.PositionRow)[];page:number;pages:number;total:number;object_type:'orders'|'positions'}
interface Converted {preview:w.Preview;resolved_units:string;holding_ratio:string;estimated_market_value_fen:number;estimated_fee_fen:number;estimated_net_cash_fen:number;notice:string}
export default function StructuredActionPanel({userId,revision,onActivity}:{userId:string;revision:number;onActivity:()=>void}){
  const [facts,setFacts]=useState<{owner:string;assets:w.Assets;products:w.Product[];tasks:w.WealthTask[]}|null>(null);
  const ready=facts?.owner===userId?facts:null;
  const contextId=ready?.assets.context_id??'legacy-v1';
  const shared=useManualDraft(ready?draftScope(userId,contextId,ready.assets.cash.map(a=>a.account_id)):null,contextId);
  const [candidateGroups,setCandidateGroups]=shared('candidateGroups');
  const [candidateSelection,setCandidateSelection]=shared('candidateSelection');
  const [portfolio,setPortfolio]=shared('portfolio');
  const [ratios,setRatios]=shared('ratios');
  const [reviewing,setReviewing]=useState(false);
  const [results,setResults]=useState<Partial<Record<ResultSlot,ActionResult>>>({});
  const [pending,setPending]=useState<ResultSlot|null>(null);const [reading,setReading]=useState(true);const [reload,setReload]=useState(0);const busy=pending!==null||reading;
  const [cancelView,setCancelView]=useState<CancellationRow[]|null>(null);const [rowView,setRowView]=useState<w.OrderRow|w.PositionRow|null>(null);const [activityNotice,setActivityNotice]=useState('');
  const recordRows=useRef(new Map<string,w.OrderRow|w.PositionRow>());
  const [actionErrors,setActionErrors]=useState<Partial<Record<ResultSlot,string>>>({});const [error,setError]=useState('');
  const fail=(slot:ResultSlot,message:string)=>setActionErrors(current=>({...current,[slot]:message}));
  const scope=useRef<string|null>(null);
  const client=useRef(new WealthActionSession(sendAction));const latestRequest=useRef(0);
  useEffect(()=>()=>client.current.setContext(null),[]);
  const [detailView,setDetailView]=useState<{id:string;version:number}|null>(null);const [compareView,setCompareView]=useState(false);
  const [mode,setMode]=shared('mode');const [category,setCategory]=shared('category');const [theme,setTheme]=shared('theme');
  const [pageSize,setPageSize]=shared('pageSize');const [selected,setSelected]=shared('selected');
  const [inputMode,setInputMode]=shared('inputMode');const [value,setValue]=shared('value');const [target,setTarget]=shared('target');
  const [confirm,setConfirm]=useState<Converted|null>(null);const [period,setPeriod]=shared('period');const [date,setDate]=shared('date');
  useEffect(()=>{
    // A previous preview must not remain actionable after changing its inputs or bank facts.
    setResults(current=>{const next={...current};delete next.redemption;delete next.cancellation;return next;});
    setConfirm(null);setCancelView(null);
  },[selected,inputMode,value,revision,reload]);
  useEffect(()=>{let active=true;setReading(true);setCancelView(null);setRowView(null);setError('');void Promise.all([w.get<w.Assets>('/assets'),w.get<w.Product[]>('/products'),w.get<{items:w.WealthTask[]}>('/tasks')]).then(([assets,products,tasks])=>{
    if(!active)return;
    manualDrafts.bind(draftScope(userId,assets.context_id??'legacy-v1',assets.cash.map(a=>a.account_id)),assets.context_id??'legacy-v1');
    if(scope.current!==assets.context_id){setDetailView(null);setCompareView(false);setResults({});setActionErrors({});setConfirm(null);recordRows.current.clear();setReviewing(false);}
    client.current.setContext(assets.context_id??'legacy-v1');
    setFacts({owner:userId,assets,products,tasks:tasks.items});
  }).catch(()=>{if(active){client.current.setContext(null);setDetailView(null);setCompareView(false);setFacts(null);setError('登录账户资料读取失败，请重新登录或刷新。');}}).finally(()=>{if(active)setReading(false);});return()=>{active=false;};},[userId,revision,reload]);
  const blockedAccounts=ready?.tasks.filter(t=>!['COMPLETED','PARTIAL_COMPLETED','CANCELLED','FAILED'].includes(t.status)&&(t.control_mode!=='AGENT'||t.status==='PAUSED')).map(t=>t.account_id)??[];
  scope.current=ready?.assets.context_id??null;
  const controlKey=JSON.stringify(ready?.tasks.map(t=>[t.task_id,t.version,t.status,t.control_mode,t.owner_id]).sort()??[]);
  useEffect(()=>{
    ++latestRequest.current;setPending(null);setConfirm(null);setCancelView(null);setReviewing(false);
    setResults(current=>{const next={...current};delete next.redemption;delete next.cancellation;return next;});
    client.current.setContext(null);if(ready)client.current.setContext(contextId);
  },[controlKey,contextId]);
  useEffect(()=>{if(ready&&!date)setDate(new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai'}).format(new Date(ready.assets.business_at)));},[ready,date,setDate]);
  const run=useCallback(async(action:ActionName,params:Record<string,unknown>,selection?:ActionRequest['selection_context'])=>{
    if(!ready||reading)return;
    const slot=resultSlot(action);
    const latestDate=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai'}).format(new Date(ready.assets.business_at));
    if(action==='show_wealth_returns'&&typeof params.anchor_date==='string'&&params.anchor_date>latestDate){fail(slot,'未来还没有发生，不能查询未来收益。');return;}
    const requestNumber=++latestRequest.current;setPending(slot);setActionErrors(current=>({...current,[slot]:''}));
    try{const response=await client.current.request(action,params,selection);
      if(requestNumber!==latestRequest.current||scope.current!==(ready.assets.context_id??'legacy-v1'))return;
      if(action==='plan_portfolio'&&response.status==='ok'){
        const plan=readPortfolioPlan(response.data,ready.assets.context_id??'legacy-v1');
        for(const group of plan.candidate_groups)assertCurrentCandidates(group,ready.products);
        setPortfolio(plan);setCandidateGroups(plan.candidate_groups);setReviewing(true);
        setCandidateSelection(previous=>previous.items.length?previous:planProductSelection(plan));
      }
      if(action==='recommend_products'&&response.status==='ok'){
        const group=readCandidateGroup(response.data);assertCurrentCandidates(group,ready.products);
        if(!sameCandidateFilter(group.filter,params.filter))throw new Error('推荐结果与当前筛选条件不一致，请重新查询。');
        setCandidateGroups(previous=>params.cursor?replaceCandidateGroup(previous,group):[group]);
      }
      if(response.status==='ok'&&(action==='list_orders'||action==='list_positions'))for(const row of (response.data as Page).rows)recordRows.current.set(row.object_type==='orders'?(row as w.OrderRow).operation_id:(row as w.PositionRow).position_key,row);
      setResults(previous=>({...previous,[slot]:response}));return response;
    }catch(reason){if(scope.current===(ready.assets.context_id??'legacy-v1'))fail(slot,(reason as {response?:{data?:{detail?:{message?:string}}}})?.response?.data?.detail?.message??(reason instanceof Error?reason.message:'操作失败，请刷新并核对原请求。'));}finally{if(requestNumber===latestRequest.current)setPending(null);}
  },[ready,reading]);
  const loadDetail=useCallback<ProductDetailLoader>(async(id,period,version)=>{
    const response=await run('show_product_detail',{product_id:id,product_version:version,period},version?{object_type:'products',ids:[`${id}:${version}`]}:undefined);
    if(response?.status!=='ok')throw new Error(response?.message??'详情查询失败');
    return response.data as ProductDetailData;
  },[run]);
  const loadComparison=useCallback<ComparisonLoader>(async ids=>{
    const refs=ids.map(id=>ready?.products.find(p=>p.product_id===id)).filter((p):p is w.Product=>!!p);
    if(refs.length!==ids.length)throw new Error('产品范围已变化');
    const response=await run('compare_products',{product_ids:ids},{object_type:'products',ids:refs.map(p=>`${p.product_id}:${p.version}`)});
    if(response?.status!=='ok')throw new Error(response?.message??'比较查询失败');
    return comparisonRows(response.data as w.Product[]);
  },[run,ready]);
  const recommend=(filter:Record<string,string>)=>void run('recommend_products',{filter});
  const list=(type:'orders'|'positions',page=1)=>{if(selected.type!==type)setSelected({type,ids:[]});void run(type==='orders'?'list_orders':'list_positions',{page,page_size:pageSize});};
  const redeem=()=>{
    if(!ready||busy)return;setActionErrors(current=>({...current,redemption:''}));const redemption:Record<string,unknown>={context_id:ready.assets.context_id??'legacy-v1',input_mode:inputMode};
    if(value){
      if(!/^\d+(\.\d+)?$/.test(value)||Number(value)<=0){fail('redemption','请输入大于零的数字');return;}
      if(inputMode==='market_value'){
        if(!/^\d+(\.\d{1,2})?$/.test(value)){fail('redemption','市值最多两位小数');return;}
        const [a,b='']=value.split('.');const fen=BigInt(a)*100n+BigInt(b.padEnd(2,'0'));if(fen>2000000000n){fail('redemption','金额超过业务上限');return;}redemption.market_value_fen=Number(fen);
      }else if(inputMode==='units')redemption.units=value;
      else{if(!/^\d+(\.\d{1,6})?$/.test(value)||Number(value)>100){fail('redemption','比例不超过100%，最多六位小数');return;}const [a,b='']=value.split('.');const scaled=BigInt(a)*1000000n+BigInt(b.padEnd(6,'0'));redemption.holding_ratio=`${scaled/100000000n}.${(scaled%100000000n).toString().padStart(8,'0')}`;}
    }
    void run('preview_redemption',{redemption},selected.type==='positions'&&selected.ids.length?{object_type:'positions',ids:selected.ids}:undefined);
  };
  const renderResult=(slot:ResultSlot)=>{
    const result=results[slot];
    if(!result&&pending!==slot&&!actionErrors[slot])return null;
    const page=result?.status==='ok'&&(result.action==='list_orders'||result.action==='list_positions')?result.data as Page:null;
    const compared=result?.status==='ok'&&result.action==='compare_products'?result.data as w.Product[]:null;
    const converted=result?.status==='ok'&&result.action==='preview_redemption'?result.data as Converted:null;
    const detail=result?.status==='ok'&&result.action==='show_product_detail'?result.data as ProductDetailData:null;
    const cancellation=result?.status==='ok'&&result.action==='preview_order_cancellation'?result.data as {items:w.CancellationEligibility[]}:null;
    const returnData=result?.status==='ok'&&result.action==='show_wealth_returns'?result.data as WealthReturns:null;
    return <section className="action-result" aria-label={resultTitles[slot]} aria-live="polite" aria-busy={pending===slot}><h3>{resultTitles[slot]}</h3>{pending===slot&&<p role="status">正在查询 / 核对…</p>}{actionErrors[slot]&&<p role="alert" className="action-error">{actionErrors[slot]}</p>}{result&&<><p className="action-result-message">{result.message}</p>{(pending===slot||actionErrors[slot])&&<p className="consumer-note">下方保留上次结果，请以本次完成后的反馈为准。</p>}{result.status==='blocked'&&<p className="consumer-note">本次操作未执行；可继续查询本人订单、持仓与收益，操作限制见上述原因。</p>}
      {result.required_fields.map(field=><div key={field.field}><p>{field.reason}</p>{field.options&&field.options.length>0&&<label>补充选择<select defaultValue="" onChange={e=>{if(field.field==='redemption.position_key'){setSelected({type:'positions',ids:[e.target.value]});}else if(field.field==='product_id'){setTarget(e.target.value);}}}><option value="">请选择</option>{field.options.map(x=><option value={x.id} key={x.id}>{x.label}</option>)}</select></label>}</div>)}
      {page&&<><p>共{page.total}行 · 第{page.page}/{page.pages}页</p><div className="returns-table-scroll"><table><thead><tr><th>选择</th><th>产品</th><th>账户</th><th>状态/持有份额</th><th>事实详情</th></tr></thead><tbody>{page.rows.map(row=>{const key=page.object_type==='orders'?(row as w.OrderRow).operation_id:(row as w.PositionRow).position_key;return <tr key={key}><td><input type="checkbox" aria-label={`选择${key}`} checked={selected.type===page.object_type&&selected.ids.includes(key)} onChange={e=>setSelected(current=>({type:page.object_type,ids:e.target.checked?Array.from(new Set([...(current.type===page.object_type?current.ids:[]),key])):current.ids.filter(x=>x!==key)}))}/></td><td>{row.product_name}</td><td>{row.account_id}</td><td>{page.object_type==='orders'?(row as w.OrderRow).status:`${(row as w.PositionRow).units}份（可赎${(row as w.PositionRow).redeemable_units}）`}</td><td><button className="secondary" onClick={()=>setRowView(row)}>{page.object_type==='orders'?'查看订单详情':'查看持仓详情'}</button></td></tr>;})}</tbody></table></div>{page.rows.length===0&&<p>当前授权范围内暂无{page.object_type==='orders'?'订单':'持仓'}记录。</p>}<button className="secondary" disabled={busy||page.page<=1} onClick={()=>list(page.object_type,page.page-1)}>上一页</button><button className="secondary" disabled={busy||page.page>=page.pages} onClick={()=>list(page.object_type,page.page+1)}>下一页</button><button className="secondary" onClick={()=>setSelected({type:page.object_type,ids:[]})}>清空选择</button></>}
      {returnData&&<><p>{returnData.start_date===returnData.end_date?`${returnData.start_date} · 当日收益`:`${returnData.start_date} 至 ${returnData.end_date}`}</p><p>{returnStatus[returnData.status]}</p><dl className="action-summary">{[['所选期间收益',returnData.summary.period_pnl_fen],['累计收益',returnData.summary.cumulative_pnl_fen],['累计已实现',returnData.summary.realized_pnl_fen],['累计未实现',returnData.summary.unrealized_pnl_fen]].map(([label,amount])=><div key={String(label)}><dt>{label}</dt><dd>{returnMoney(amount as number|null)}</dd></div>)}</dl><p>{returnData.missing_fields.join('；')}</p></>}
      {converted&&<><p>赎回{converted.resolved_units}份 · 比例{(Number(converted.holding_ratio)*100).toFixed(4)}% · 市值{w.money(converted.estimated_market_value_fen)}</p><p>预计费用{w.money(converted.estimated_fee_fen)} · 预计到账{w.money(converted.estimated_net_cash_fen)}</p><p>{converted.notice}</p><button onClick={()=>setConfirm(converted)}>继续本人确认与验证</button></>}
      {detail&&<article><h4>{detail.name} · {detail.product_id}</h4><p>风险等级 {detail.risk_level??'尚未核实'} · 最新净值 {detail.latest_nav?.nav??'缺少资料'}（{detail.latest_nav?.date??'未知'}）</p><p>{detail.chart?.notice??'暂无行情'} · 已读取{detail.chart?.points.length??0}个行情点。可继续打开下方完整详情查看图表。</p><details><summary>起购、持有与到账规则</summary><p>起购 {w.money(detail.rules?.min_amount_fen)} · 持有期 {detail.rules?.holding_days??'未核实'}天 · 普通到账 {detail.rules?.cash_business_days??'未核实'}个营业日</p><p>资料时点（北京时间）：{beijingTime(detail.as_of)}</p></details><button className="secondary" onClick={()=>{const p=ready?.products.find(p=>p.product_id===detail.product_id);if(p)setDetailView({id:p.product_id,version:p.version});}}>打开完整产品详情</button></article>}
      {compared&&<><p>已比较{compared.length}只产品：{compared.map(p=>p.name).join('、')}。</p><button className="secondary" onClick={()=>setCompareView(true)}>重新选择并查看比较</button></>}
      {cancellation&&<><p>此处先核对资格，继续取消仍需本人明确确认。</p>{cancellation.items.map(x=><article key={x.operation_id}><strong>{x.operation_id} · {x.eligibility}</strong><p>{x.reason}</p><p>{x.consequence}</p></article>)}<button disabled={busy||!cancellation.items.some(x=>x.eligibility==='ALLOWED')} onClick={()=>{const views:CancellationRow[]=[];for(const eligibility of cancellation.items){const row=recordRows.current.get(eligibility.operation_id);if(!row||row.object_type!=='orders'||row.digest!==eligibility.order_digest){fail('cancellation','订单资料或版本已变化，请重新查询订单并核对资格。');return;}views.push({row,eligibility});}setCancelView(views);}}>继续核对并本人确认取消</button></>}
    </>}</section>;
  };
  return <section aria-label="统一手动操作" className="structured-actions"><h2>直接选择要做的事</h2><p>这里使用登录账户的模拟银行资料。按钮直接传递操作参数，不需要输入聊天指令。</p><p className="consumer-note">未提交的选择与输入在两个手动入口间共享；切换入口后需重新查询、校验和预览。暂停或交还任务不会自动提交草稿。退出登录或切换资金范围后清除。</p>
    {!ready&&<p>正在读取本人资料…</p>}{error&&<p role="alert">{error}</p>}{activityNotice&&<p role="status" className="consumer-warning">{activityNotice}</p>}<button className="secondary" disabled={busy} onClick={()=>{setResults({});recordRows.current.clear();setSelected({type:'positions',ids:[]});setReload(n=>n+1);}}>刷新账户记录与操作状态</button>{reading&&ready&&<p role="status">正在刷新账户及人工控制状态…</p>}{blockedAccounts.length>0&&<p className="consumer-warning">账户 {Array.from(new Set(blockedAccounts)).join('、')} 存在人工接管或暂停任务。资料可继续查询；相关交易仍受银行权限检查。</p>}
    <fieldset disabled={busy||!ready}><legend>规划与推荐</legend><div className="consumer-entry-grid">
      <button onClick={()=>void run('plan_portfolio',{})}>理财规划</button><button onClick={()=>recommend({mode:'general'})}>产品推荐</button>
      <button onClick={()=>recommend({mode:'category',category:'bond_fund'})}>推荐债券基金</button><button onClick={()=>recommend({mode:'theme',theme_key:'high_dividend'})}>想买高股息产品</button></div>
      <p className="consumer-note">队友配置/推荐服务尚未接入时会明确显示原因；旧F11样例仍在原入口。</p>
      <label>推荐方式<select value={mode} onChange={e=>setMode(e.target.value)}><option value="general">普通推荐</option><option value="category">按类别</option><option value="theme">按主题</option></select></label>
      {mode==='category'&&<label>产品类别<select value={category} onChange={e=>setCategory(e.target.value)}><option value="quick_redeem">可快速赎回</option><option value="bank_wealth">银行理财</option><option value="money_fund">货币基金</option><option value="bond_fund">债券基金</option><option value="mixed_fund">混合基金</option><option value="equity_fund">权益基金</option></select></label>}
      {mode==='theme'&&<label>产品主题<select value={theme} onChange={e=>setTheme(e.target.value)}><option value="high_dividend">高股息/红利</option></select></label>}
      <button className="secondary" onClick={()=>recommend({mode,...(mode==='category'?{category}:mode==='theme'?{theme_key:theme}:{})})}>按条件推荐</button>{renderResult('planning')}
      {candidateGroups.length>0&&ready&&<><ProductPicker groups={candidateGroups} scopeKey={ready.assets.context_id??'legacy-v1'} selection={candidateSelection} onSelectionChange={next=>{setCandidateSelection(next);setReviewing(false);}} busy={busy}
        onRefreshGroup={(group,ids)=>void run('recommend_products',{filter:group.filter,cursor:group.next_cursor,selected_family_ids:ids})}
        loadDetail={loadDetail} loadComparison={loadComparison} onContinue={()=>{if(!portfolio)void run('plan_portfolio',{});else setReviewing(true);}}/><p className="consumer-note">勾选与换批保留待校验产品，不会生成订单。尚无整体方案时，继续操作会先请求配置服务创建方案。</p></>}
      {portfolio&&reviewing&&<StructuredPortfolioReview key={`${portfolio.portfolio_id}:${portfolio.version}:${revision}:${reload}:${controlKey}`} plan={portfolio} selection={candidateSelection} ratios={ratios} onRatiosChange={setRatios} onActivity={onActivity}
        onRevise={async selection=>{const result=await run('revise_portfolio',{selection},{object_type:'products',ids:selection.items.map(i=>`${i.product_id}:${i.product_version}`)});if(result?.status!=='ok')throw new Error(result?.message??'比例校验未完成，请重新核对。');return readPortfolioPlan(result.data,portfolio.context_id);}}
        onPrepare={async plan=>{const result=await run('prepare_purchase',{portfolio_id:plan.portfolio_id,expected_version:plan.version,snapshot_id:plan.snapshot_id});if(result?.status!=='ok')throw new Error(result?.message??'差额准备未完成，请重新核对。');return result.data as w.PreparedPortfolio;}}/>}
    </fieldset>
    <fieldset disabled={busy||!ready}><legend>订单与持仓</legend><label>每页行数<select value={pageSize} onChange={e=>setPageSize(Number(e.target.value))}><option value={5}>5行</option><option value={10}>10行</option><option value={20}>20行</option></select></label><button onClick={()=>list('orders')}>查询我的订单</button><button onClick={()=>list('positions')}>查询我的持仓</button>{renderResult('records')}</fieldset>
    <fieldset disabled={busy||!ready}><legend>理财收益</legend>
      <label>收益统计<select value={period} onChange={e=>setPeriod(e.target.value)}><option value="day">日</option><option value="month">月</option><option value="year">年</option><option value="cumulative">累计</option></select></label><label>收益日期<input type="date" value={date} max={ready?new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai'}).format(new Date(ready.assets.business_at)):undefined} disabled={period==='cumulative'} onChange={e=>setDate(e.target.value)}/></label><button onClick={()=>void run('show_wealth_returns',{scope:'all',period,...(period==='cumulative'?{}:{anchor_date:date})})}>查询整体收益</button>{renderResult('returns')}</fieldset>
    <fieldset disabled={busy||!ready}><legend>产品资料</legend>
      <label>查看产品<select value={target} onChange={e=>setTarget(e.target.value)}><option value="">请选择产品及版本</option>{ready?.products.map(p=><option key={`${p.product_id}:${p.version}`} value={`${p.product_id}:${p.version}`}>{p.name} · v{p.version}</option>)}</select></label><button className="secondary" onClick={()=>{const p=ready?.products.find(x=>`${x.product_id}:${x.version}`===target);void run('show_product_detail',p?{product_id:p.product_id,product_version:p.version}:{});}}>查看产品资料</button>{renderResult('product')}
      <button className="secondary" onClick={()=>setCompareView(true)}>选择产品进行比较（2—4只）</button><p className="consumer-note">查看详情和比较是可选操作，不影响原产品选择，也不代表推荐。</p>{renderResult('comparison')}
    </fieldset>
    <fieldset disabled={busy||!ready}><legend>按所选持仓赎回</legend><p>已选{selected.type==='positions'?selected.ids.length:0}只持仓；多选时不会把一笔市值自动套给每只。</p><label>赎回输入方式<select value={inputMode} onChange={e=>setInputMode(e.target.value)}><option value="market_value">持仓市值（元）</option><option value="units">份额</option><option value="holding_ratio">本只持仓比例（%）</option></select></label><label>赎回数量<input inputMode="decimal" value={value} onChange={e=>setValue(e.target.value)}/></label><button onClick={redeem}>核对赎回输入</button>{renderResult('redemption')}</fieldset>
    <fieldset disabled={busy||!ready}><legend>所选订单撤单资格</legend><p>从上方订单查询结果中勾选订单后核对。核对后可进入本人确认；查询不会自动取消订单。</p><button className="secondary" disabled={selected.type!=='orders'||!selected.ids.length} onClick={()=>void run('preview_order_cancellation',{operation_ids:selected.ids},{object_type:'orders',ids:selected.ids})}>核对已选订单撤单资格</button>{renderResult('cancellation')}</fieldset>
    {rowView?.object_type==='orders'&&<OrderDetail row={rowView} loadDetail={loadDetail} onClose={()=>setRowView(null)}/>}
    {rowView?.object_type==='positions'&&<PositionDetail row={rowView} loadDetail={loadDetail} onClose={()=>setRowView(null)} onReturns={()=>{setRowView(null);void run('show_wealth_returns',{scope:'product',product_id:rowView.product_id,period,...(period==='cumulative'?{}:{anchor_date:date})});}}/>}
    {cancelView&&<OrderCancellationDialog rows={cancelView} blockedAccounts={blockedAccounts} onClose={()=>setCancelView(null)} onComplete={message=>{setCancelView(null);setActivityNotice(message);setSelected({type:'orders',ids:[]});onActivity();setReload(n=>n+1);void run('list_orders',{page:1,page_size:pageSize});}}/>}
    {detailView&&<ProductDetail productId={detailView.id} productVersion={detailView.version} loadDetail={loadDetail} onClose={()=>setDetailView(null)}/>}
    {compareView&&ready&&<ProductComparison products={ready.products} loadComparison={loadComparison} onClose={()=>setCompareView(false)}/>}
    {confirm&&<SingleTradePreview preview={confirm.preview} redemption={confirm} onClose={()=>setConfirm(null)} onActivity={onActivity}/>}
  </section>;
}
