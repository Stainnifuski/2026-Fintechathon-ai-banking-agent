import AgentStepCard,{WealthStepContext,useStepInstruction,useStepReady} from './AgentStepCard';
import type {NovaWealthView,NovaWealthCommand} from '../../services/nova';
import {ManualDraftStore} from '../../services/wealthManualDraft';
import {toggleProduct,changeShare,emptyProductSelection} from './productSelection';
import {wealthFeedback,wealthRequestFailure} from '../../utils/wealthFeedback';
import {useEffect,useState,useRef,useCallback,useMemo,useContext} from 'react';
import * as w from '../../services/wealth';
import {sendAction} from '../../services/wealthActions';
import type {ActionName,ActionResult,ActionRequest} from '../../services/wealthActions';
import PositionTable from './PositionTable';
import {holdingsSelection} from './holdingsSelection';
import ReturnsView from './ReturnsView';
import type {ReturnsPreviewSource} from './ReturnsView';
import {currentTableState} from './tableState';
import {positionTableData} from './tableData';
import SingleTradePreview from './SingleTradePreview';
import TargetPurchase from './TargetPurchase';
import PlanningCashInput from './PlanningCashInput';
import {planningSelectionHint} from './planningSelectionHint';
import {planningCash,availablePlanningCash,type CashDraft} from './planningCash';
import RedemptionDialog from './RedemptionDialog';

import ProductDetail from './ProductDetail';
import type {ProductDetailData,ProductDetailLoader} from './ProductDetail';
import ProductComparison,{comparisonRows} from './ProductComparison';
import type {ComparisonLoader} from './ProductComparison';
import {WealthActionSession} from '../../services/wealthActionSession';
import type {WealthReturns} from '../../services/wealthReturns';
import {beijingTime} from '../../utils/time';
import ProductPicker from './ProductPicker';
import {readCandidateGroup,assertCurrentCandidates,sameCandidateFilter,replaceCandidateGroup} from '../../services/wealthCandidates';
import {readPortfolioPlan,planProductSelection} from '../../services/wealthPortfolioReview';
import StructuredPortfolioReview from './StructuredPortfolioReview';
import './structuredActions.css';
import {manualDrafts,draftScope} from '../../services/wealthManualDraft';
import {useManualDraft} from './useManualDraft';
type ResultSlot='planning'|'positions'|'returns'|'product'|'redemption'|'comparison';
const resultSlot=(action:ActionName):ResultSlot=>action==='list_positions'?'positions':action==='show_wealth_returns'?'returns':action==='show_product_detail'?'product':action==='preview_redemption'?'redemption':action==='compare_products'?'comparison':'planning';
const resultTitles:Record<ResultSlot,string>={planning:'规划与推荐结果',positions:'持仓查询结果',returns:'收益查询结果',product:'产品资料查询结果',redemption:'赎回核对结果',comparison:'产品比较结果'};
interface Page {rows:w.PositionRow[];page:number;pages:number;total:number;object_type:'positions';table_data:w.PositionTableData;snapshot_id:string}
interface Converted {preview:w.Preview;resolved_units:string;holding_ratio:string;estimated_market_value_fen:number;estimated_fee_fen:number;estimated_net_cash_fen:number;notice:string}
export default function StructuredActionPanel({userId,revision,onActivity,agentCommand,agentContext,agentTaskId,agentSelection,onView,onPrepared}:{userId:string;revision:number;onActivity:()=>void;agentCommand?:NovaWealthCommand;agentContext?:string;agentTaskId?:string;agentSelection?:NovaWealthView['selected_products'];onView?:(view:NovaWealthView)=>void;onPrepared?:(id:string)=>Promise<void>}){
  const [draftStore]=useState(()=>agentCommand?new ManualDraftStore():manualDrafts);
  const commandApplied=useRef(false);
  const [facts,setFacts]=useState<{owner:string;assets:w.Assets;products:w.Product[];tasks:w.WealthTask[];accountId:string}|null>(null);
  const ready=facts?.owner===userId?facts:null;
  const contextId=ready?.assets.context_id??'legacy-v1';
  const shared=useManualDraft(ready?draftScope(userId,contextId,ready.assets.cash.map(a=>a.account_id)):null,contextId,draftStore);
  const [candidateGroups,setCandidateGroups]=shared('candidateGroups');
  const steps=useContext(WealthStepContext);
  useStepReady('match',candidateGroups.length>0);
  const [candidateSelection,setCandidateSelection]=shared('candidateSelection');
  const [portfolio,setPortfolio]=shared('portfolio');
  const [planningBaseline,setPlanningBaseline]=useState<w.PortfolioPlan|null>(()=>portfolio&&!portfolio.reasons.includes('PLANNING_CASH_BUDGET_V1')?portfolio:null);
  const [selectionAttempted,setSelectionAttempted]=useState(false);
  const [ratios,setRatios]=shared('ratios');
  const [reviewing,setReviewing]=useState(false);
  const [executionActive,setExecutionActive]=useState(false);
  useEffect(()=>{setExecutionActive(false);},[userId,contextId]);
  const [cashDraft,setCashDraft]=useState<CashDraft>({field:'reserve',text:''});
  useEffect(()=>{
    if(!onView||!candidateGroups.length)return;
    const timer=setTimeout(()=>onView({visible_products:candidateGroups.flatMap(g=>g.items.flatMap(c=>c.share_classes.map(s=>({product_id:s.product_id,product_version:s.product_version,name:c.name,bucket:c.bucket})))).slice(0,60),cash_field:cashDraft.field,cash_yuan:cashDraft.text,selected_products:candidateSelection.items.map(({candidate,share})=>({product_id:share.product_id,product_version:share.product_version,name:candidate.name,bucket:candidate.bucket}))}),300);
    return()=>clearTimeout(timer);
  },[candidateGroups,candidateSelection,onView,cashDraft]);

  const availableCash=availablePlanningCash(ready?.assets.cash??[]);
  const currentHoldings=ready?.assets.asset_summary?.positions_market_value_fen??null;
  const cashBudget=currentHoldings===null?null:planningCash(availableCash,cashDraft,currentHoldings);
  useEffect(()=>{setPlanningBaseline(null);setSelectionAttempted(false);},[userId,contextId,ready?.assets.asset_summary?.snapshot_id]);
  const poolHint=planningSelectionHint(planningBaseline?.context_id===contextId?planningBaseline:null,cashBudget?.reserve??null,candidateSelection.items.map(i=>i.candidate.bucket));
  useEffect(()=>{setCashDraft({field:'reserve',text:''});setReviewing(false);},[userId,contextId,availableCash]);
  const [results,setResults]=useState<Partial<Record<ResultSlot,ActionResult>>>({});
  const [pending,setPending]=useState<ResultSlot|null>(null);const [reading,setReading]=useState(true);const [reload,setReload]=useState(0);const busy=pending!==null||reading;
  const [activityNotice,setActivityNotice]=useState('');
  const [redemptionRows,setRedemptionRows]=useState<w.PositionRow[]|null>(null);
  const [actionErrors,setActionErrors]=useState<Partial<Record<ResultSlot,string>>>({});const [error,setError]=useState('');
  const fail=(slot:ResultSlot,message:string)=>setActionErrors(current=>({...current,[slot]:message}));
  const scope=useRef<string|null>(null);
  const factScope=useRef<string|null>(null);
  // Catalogue labels are static display data, never purchase permission.
  // Every action still uses current server risk/rules/scope and bank facts.
  const displayCatalogue=useRef<{user:string;context:string;products:w.Product[]}|null>(null);
  const catalogueRequest=useRef<{scope:string;promise:Promise<w.Product[]>}|null>(null);
  const client=useRef(new WealthActionSession(request=>sendAction(request,agentTaskId)));const latestRequest=useRef(0);
  useEffect(()=>()=>client.current.setContext(null),[]);
  const [detailView,setDetailView]=useState<{id:string;version:number}|null>(null);const [compareView,setCompareView]=useState(false);
  const [mode,setMode]=shared('mode');const [category,setCategory]=shared('category');
  const [workflow,setWorkflow]=shared('workflow');
  const [nameQuery,setNameQuery]=useState('');
  const [positionState,setPositionState]=shared('positionTable');
  const returnsRef=useRef<HTMLFieldSetElement>(null);
  const [inputMode]=shared('inputMode');const [value,setValue]=shared('value');const [,setTarget]=shared('target');
  const [confirm,setConfirm]=useState<Converted|null>(null);
  useEffect(()=>{
    // A previous preview must not remain actionable after changing its inputs or bank facts.
    setResults(current=>{const next={...current};delete next.redemption;return next;});
    setConfirm(null);
  },[positionState.selected,inputMode,value,revision,reload]);
  useEffect(()=>{let active=true;++latestRequest.current;setPending(null);setReading(true);setRedemptionRows(null);setError('');void Promise.all([w.get<w.Assets>('/assets'),w.get<{items:w.WealthTask[]}>('/tasks'),w.get<{selected:string;items:{context_id:string;account_id:string}[]}>('/contexts')]).then(async([assets,tasks,contexts])=>{
    if(agentContext&&assets.context_id!==agentContext)throw new Error('理财任务的账户范围已变化，请重新生成任务。');
    const context=assets.context_id??'legacy-v1';const cached=displayCatalogue.current;
    const requestScope=userId+':'+context;
    const needsCatalogue=agentCommand?.kind!=='returns';
    if(needsCatalogue&&!(cached?.user===userId&&cached.context===context) && catalogueRequest.current?.scope!==requestScope){
      const promise=w.get<w.Product[]>('/products').catch(e=>{if(catalogueRequest.current?.scope===requestScope)catalogueRequest.current=null;throw e;});
      catalogueRequest.current={scope:requestScope,promise};
    }
    const products=!needsCatalogue?[]:cached?.user===userId&&cached.context===context?cached.products:await catalogueRequest.current!.promise;
    if(!active)return;
    displayCatalogue.current={user:userId,context,products};
    draftStore.bind(draftScope(userId,assets.context_id??'legacy-v1',assets.cash.map(a=>a.account_id)),assets.context_id??'legacy-v1');
    const nextScope=draftScope(userId,assets.context_id??'legacy-v1',assets.cash.map(a=>a.account_id));
    if(factScope.current!==nextScope){setActivityNotice('');setDetailView(null);setCompareView(false);setResults({});setActionErrors({});setConfirm(null);setReviewing(false);client.current.setContext(null);}
    else if(assets.asset_summary){
      // After acceptance/cancellation/settlement, refresh already-open tables
      // from the complete bank read immediately, keeping their independent UI state.
      const assetScope=assets.asset_summary.asset_scope_id;
      const positions=positionTableData(assets,assetScope);
      const key=JSON.stringify([userId,assets.context_id??'legacy-v1',assetScope]);
      setPositionState(current=>currentTableState(current,key,positions.rows,row=>row.position_key));
      setResults(previous=>{
        const next={...previous};
        for(const [slot,table] of [['positions',positions]] as const){
          const old=next[slot];if(old?.status==='ok')next[slot]={...old,data:{...(old.data as Page),table_data:table,snapshot_id:assets.asset_summary!.snapshot_id,total:table.rows.length}};
        }
        return next;
      });
    }
    factScope.current=nextScope;
    client.current.setContext(assets.context_id??'legacy-v1');
    setFacts({owner:userId,assets,products,tasks:tasks.items,accountId:contexts.selected===context?contexts.items.find(c=>c.context_id===context)?.account_id??'':''});
  }).catch(()=>{if(active){client.current.setContext(null);setDetailView(null);setCompareView(false);setFacts(null);setError('银行资料读取暂未完成，请刷新重试；已提交交易保持。');}}).finally(()=>{if(active)setReading(false);});return()=>{active=false;};},[userId,revision,reload]);
  useEffect(()=>{const refresh=()=>{if(document.visibilityState==='visible')setReload(n=>n+1);};window.addEventListener('focus',refresh);return()=>window.removeEventListener('focus',refresh);},[]);
  const blockedAccounts=ready?.tasks.filter(t=>!['COMPLETED','PARTIAL_COMPLETED','CANCELLED','FAILED'].includes(t.status)&&(t.control_mode!=='AGENT'||t.status==='PAUSED')).map(t=>t.account_id)??[];
  scope.current=ready?.assets.context_id??null;
  const controlKey=JSON.stringify(ready?.tasks.map(t=>[t.task_id,t.version,t.status,t.control_mode,t.owner_id]).sort()??[]);
  useEffect(()=>{
    ++latestRequest.current;setPending(null);setConfirm(null);setRedemptionRows(null);setReviewing(false);
    setResults(current=>{const next={...current};delete next.redemption;return next;});
    client.current.setContext(null);if(ready)client.current.setContext(contextId);
  },[controlKey,contextId]);
  const run=useCallback(async(action:ActionName,params:Record<string,unknown>,selection?:ActionRequest['selection_context'],throwOnError=false)=>{
    if(!ready||reading){if(throwOnError)throw new Error('银行事实正在加载，请等待完成后再准备预览。');return;}
    const slot=resultSlot(action);
    const latestDate=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai'}).format(new Date(ready.assets.business_at));
    if(action==='show_wealth_returns'&&typeof params.anchor_date==='string'&&params.anchor_date>latestDate){fail(slot,'未来还没有发生，不能查询未来收益。');return;}
    const requestNumber=++latestRequest.current;setPending(slot);setActionErrors(current=>({...current,[slot]:''}));
    try{const response=await client.current.request(action,params,selection);
      if(requestNumber!==latestRequest.current||scope.current!==(ready.assets.context_id??'legacy-v1')){if(throwOnError)throw new Error('账户范围或当前操作已变化，请重新读取方案后再准备预览。');return;}
      if(action==='plan_portfolio'&&response.status==='ok'){
        const plan=readPortfolioPlan(response.data,ready.assets.context_id??'legacy-v1');
        for(const group of plan.candidate_groups)assertCurrentCandidates(group,ready.products);
        setPortfolio(plan);if(!params.selected){setPlanningBaseline(plan);setSelectionAttempted(false);setCandidateGroups(plan.candidate_groups);}setReviewing(params.cash_reserve_fen!==undefined);
        setCandidateSelection(previous=>previous.items.length?previous:planProductSelection(plan));
        if(params.cash_reserve_fen!==undefined)steps?.complete('budget');
      }
      if(action==='revise_portfolio'&&response.status==='ok')setPortfolio(readPortfolioPlan(response.data,contextId));
      if(action==='recommend_products'&&response.status==='ok'){
        const group=readCandidateGroup(response.data);assertCurrentCandidates(group,ready.products);
        if(!sameCandidateFilter(group.filter,params.filter))throw new Error('推荐结果与当前筛选条件不一致，请重新查询。');
        setCandidateGroups(previous=>params.cursor||workflow==='planning'?replaceCandidateGroup(previous,group):[group]);
      }
      if(response.status==='ok'&&action==='list_positions'){
        const page=response.data as Page;const expected=ready.assets.asset_summary?.asset_scope_id;
        if(!page.table_data||page.table_data.object_type!=='positions'||page.table_data.scope_key!==expected)throw new Error('表格范围已变化，请刷新账户记录后重新查询。');
        const key=JSON.stringify([userId,contextId,page.table_data.scope_key]);
        setPositionState(current=>currentTableState(current,key,page.table_data.rows as w.PositionRow[],row=>row.position_key));
      }
      setResults(previous=>({...previous,[slot]:response}));return response;
    }catch(reason){const message=wealthRequestFailure(reason);if(scope.current===(ready.assets.context_id??'legacy-v1'))fail(slot,message);if(throwOnError)throw new Error(message);}finally{if(requestNumber===latestRequest.current)setPending(null);}
  },[ready,reading,workflow]);
  useEffect(()=>{
    if(!agentCommand||!ready||reading||commandApplied.current)return;
    commandApplied.current=true;
    const cmd=agentCommand;
    const apply=async()=>{
      if(cmd.cash_field&&cmd.cash_yuan)setCashDraft({field:cmd.cash_field==='invest'?'investment':cmd.cash_field,text:cmd.cash_yuan});
      if(cmd.kind==='planning'){
        setWorkflow('planning');const response=await run('plan_portfolio',{});
        const planningRefs=agentSelection??cmd.products;
        if(response?.status==='ok'&&planningRefs.length){
          let groups=readPortfolioPlan(response.data,contextId).candidate_groups;
          let selected=emptyProductSelection(contextId);
          for(const ref of planningRefs){
            let candidate=groups.flatMap(g=>g.items).find(c=>c.share_classes.some(s=>s.product_id===ref.product_id&&s.product_version===ref.product_version));
            if(!candidate){
              const match=await run('recommend_products',{filter:{mode:'name',name_query:ref.product_id}});
              if(match?.status==='ok')candidate=readCandidateGroup(match.data).items.find(c=>c.share_classes.some(s=>s.product_id===ref.product_id&&s.product_version===ref.product_version));
              if(candidate){const found=candidate;groups=groups.map(g=>g.bucket===found.bucket?{...g,items:[...g.items,found]}:g);}
            }
            if(candidate){selected=changeShare(selected,candidate,ref.product_id);selected=toggleProduct(selected,candidate);}
          }
          setCandidateGroups(groups);setCandidateSelection(selected);
        }
        return;
      }
      if(cmd.kind==='recommend'||cmd.kind==='purchase'){
        setWorkflow('target');setCandidateGroups([]);setCandidateSelection(emptyProductSelection(contextId));
        setMode(cmd.products.length?'name':'category');setCategory(cmd.category??'');
        const groups:w.CandidateGroup[]=[];
        if(!cmd.products.length&&cmd.category){const response=await run('recommend_products',{filter:{mode:'category',category:cmd.category}});if(response?.status==='ok')groups.push(readCandidateGroup(response.data));}
        const requested=agentSelection??cmd.products;
        if(requested.length){
          for(const p of requested){
            if(groups.some(g=>g.items.some(c=>c.share_classes.some(s=>s.product_id===p.product_id))))continue;
            const response=await run('recommend_products',{filter:{mode:'name',name_query:p.product_id}});
            if(response?.status==='ok')groups.push(readCandidateGroup(response.data));
          }
          setCandidateGroups(groups);
          let selected=emptyProductSelection(contextId);
          for(const p of requested){
            const candidate=groups.flatMap(g=>g.items).find(c=>c.share_classes.some(x=>x.product_id===p.product_id&&x.product_version===p.product_version));
            if(candidate){selected=changeShare(selected,candidate,p.product_id);selected=toggleProduct(selected,candidate);}
          }
          setCandidateSelection(selected);
        }else setCandidateGroups(groups);
        return;
      }
      if(cmd.kind==='positions'||cmd.kind==='redeem'){
        const response=await run('list_positions',{page:1,page_size:100});
        if(cmd.kind==='redeem'&&response?.status==='ok'){
          const rows=(response.data as Page).table_data.rows as w.PositionRow[];
          const selected=cmd.products.length?rows.filter(r=>cmd.products.some(p=>p.product_id===r.product_id&&p.product_version===r.product_version)):[];
          if(selected.length){setPositionState(current=>({...current,selected:selected.map(r=>r.position_key)}));setRedemptionRows(selected);}
        }
        return;
      }
      if(cmd.kind==='returns')return;
      if(cmd.kind==='detail'&&cmd.products.length){setDetailView({id:cmd.products[0].product_id,version:cmd.products[0].product_version});return;}
      if(cmd.kind==='compare'){setCompareView(true);}
    };
    void apply().catch(e=>setError(wealthRequestFailure(e)));
  },[ready,reading,agentCommand,run,contextId]);
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
  const changeWorkflow=(next:'planning'|'target')=>{
    if(workflow===next)return;
    setWorkflow(next);setPortfolio(null);setPlanningBaseline(null);setSelectionAttempted(false);setRatios(null);setCandidateGroups([]);
    setCandidateSelection({scopeKey:contextId,items:[],preferredShares:{}});setReviewing(false);
    setResults(current=>{const next={...current};delete next.planning;return next;});
    setActionErrors(current=>({...current,planning:''}));
  };
  const list=()=>void run('list_positions',{page:1,page_size:5});
  const selectRedeem=(rows:w.PositionRow[])=>{
    setPositionState(current=>({...current,selected:rows.map(row=>row.position_key)}));setValue('');
    const result=results.positions;
    const allRows=result?.status==='ok'&&result.action==='list_positions'?(result.data as Page).table_data.rows as w.PositionRow[]:rows;
    if(onView)onView(holdingsSelection(allRows,rows.map(row=>row.position_key)));
    setRedemptionRows(rows);
  };
  const openReturns=()=>{returnsRef.current?.scrollIntoView({behavior:'smooth',block:'start'});};
  const returnsSource=useMemo<ReturnsPreviewSource>(()=>({actualBank:true,scenarios:[],notice:'',defaultDate:'',scenarioNotice:()=>'',
    load:async query=>{const result=await w.get<WealthReturns>('/returns?'+new URLSearchParams(Object.entries(query).filter(([,v])=>v!==undefined).map(([k,v])=>[k,String(v)])).toString());
      if(result.asset_scope_id!==ready?.assets.asset_summary?.asset_scope_id)throw new Error('授权资产范围已变化');return result;}
  }),[ready]);
  useStepInstruction('budget',()=>{
    if(executionActive||busy)return;
    setSelectionAttempted(true);
    if(!cashBudget||poolHint.warning){fail('planning',poolHint.warning||'请先填写有效的规划资金。');return;}
    const selected=candidateSelection.items.map(({share})=>({product_id:share.product_id,product_version:share.product_version,share_class:share.share_class}));
    void run('plan_portfolio',{selected,cash_reserve_fen:cashBudget.reserve,available_cash_fen:availableCash});
  });
  const autoPlanAttempted=useRef(false);
  useEffect(()=>{
    if(!agentCommand||(!agentCommand.auto_prepare&&!steps?.completed.includes('budget'))||agentCommand.kind!=='planning'||autoPlanAttempted.current||reviewing||busy||!cashBudget||!planningBaseline||poolHint.warning)return;
    autoPlanAttempted.current=true;
    const selected=candidateSelection.items.map(({share})=>({product_id:share.product_id,product_version:share.product_version,share_class:share.share_class}));
    void run('plan_portfolio',{selected,cash_reserve_fen:cashBudget.reserve,available_cash_fen:availableCash}).then(result=>{
      if(result?.status==='ok')steps?.complete('cash_management','stable','growth','selected','budget');
    });
  },[agentCommand,reviewing,busy,cashBudget,planningBaseline,poolHint.warning,candidateSelection,run]);
  const renderResult=(slot:ResultSlot)=>{
    if(slot==='redemption'&&(redemptionRows||confirm))return null;
    const result=results[slot];
    const selectionWarning=slot==='planning'&&workflow==='planning'&&selectionAttempted?poolHint.warning:'';
    const actionError=selectionWarning||actionErrors[slot];
    if(!result&&pending!==slot&&!actionError)return null;
    const candidatePage=result?.status==='ok'&&result.action==='list_positions'?result.data as Page:null;
    const page=ready&&candidatePage?.table_data.scope_key===ready.assets.asset_summary?.asset_scope_id?candidatePage:null;
    const compared=result?.status==='ok'&&result.action==='compare_products'?result.data as w.Product[]:null;
    const converted=result?.status==='ok'&&result.action==='preview_redemption'?result.data as Converted:null;
    const detail=result?.status==='ok'&&result.action==='show_product_detail'?result.data as ProductDetailData:null;

    return <section className="action-result" aria-label={resultTitles[slot]} aria-live="polite" aria-busy={pending===slot}><h3>{resultTitles[slot]}</h3>{pending===slot&&<p role="status">正在查询 / 核对…</p>}{actionError&&<p role="alert" className="action-error">{wealthFeedback(actionError)}</p>}{result&&<><p className="action-result-message">{wealthFeedback(result.message)}</p>{(pending===slot||actionErrors[slot])&&<p className="consumer-note">下方保留上次结果，请以本次完成后的反馈为准。</p>}{result.status==='blocked'&&<p className="consumer-note">本次操作未执行；可继续查询本人持仓与收益，操作限制见上述原因。</p>}
      {result.required_fields.map(field=><div key={field.field}><p>{field.reason}</p>{field.options&&field.options.length>0&&<label>补充选择<select defaultValue="" onChange={e=>{if(field.field==='redemption.position_key'){setPositionState(current=>({...current,selected:[e.target.value]}));}else if(field.field==='product_id'){setTarget(e.target.value);}}}><option value="">请选择</option>{field.options.map(x=><option value={x.id} key={x.id}>{x.label}</option>)}</select></label>}</div>)}
      {page&&page.table_data.object_type==='positions'&&<PositionTable data={{...page.table_data,scope_key:JSON.stringify([userId,contextId,page.table_data.scope_key])}} control={{state:positionState,onChange:next=>{setPositionState(next);if(onView)onView(holdingsSelection(page.table_data.rows as w.PositionRow[],next.selected));}}} loadDetail={loadDetail} blockedAccounts={blockedAccounts} onRedeem={selectRedeem} onReturns={openReturns}/>}
      {converted&&<><p>赎回{converted.resolved_units}份 · 比例{(Number(converted.holding_ratio)*100).toFixed(4)}% · 市值{w.money(converted.estimated_market_value_fen)}</p><p>预计费用{w.money(converted.estimated_fee_fen)} · 预计到账{w.money(converted.estimated_net_cash_fen)}</p><p>{converted.notice}</p><button onClick={()=>setConfirm(converted)}>继续本人确认与验证</button></>}
      {detail&&<article><h4>{detail.name} · {detail.product_id}</h4><p>风险等级 {detail.risk_level??'尚未核实'} · 最新净值 {detail.latest_nav?.nav??'缺少资料'}（{detail.latest_nav?.date??'未知'}）</p><p>{detail.chart?.notice??'暂无行情'} · 已读取{detail.chart?.points.length??0}个行情点。可继续打开下方完整详情查看图表。</p><details><summary>起购、持有与到账规则</summary><p>起购 {w.money(detail.rules?.min_amount_fen)} · 持有期 {detail.rules?.holding_days??'未核实'}天 · 普通到账 {detail.rules?.cash_business_days??'未核实'}个营业日</p><p>资料时点（北京时间）：{beijingTime(detail.as_of)}</p></details><button className="secondary" onClick={()=>{const p=ready?.products.find(p=>p.product_id===detail.product_id);if(p)setDetailView({id:p.product_id,version:p.version});}}>打开完整产品详情</button></article>}
      {compared&&<><p>已比较{compared.length}只产品：{compared.map(p=>p.name).join('、')}。</p><button className="secondary" onClick={()=>setCompareView(true)}>重新选择并查看比较</button></>}
    </>}</section>;
  };
  return <section aria-label="统一手动操作" className={`structured-actions ${agentCommand?'nova-wealth-actions':''}`}><h2>{agentCommand?'理财任务':'直接选择要做的事'}</h2><p>这里使用登录账户的模拟银行资料。按钮直接传递操作参数，不需要输入聊天指令。</p><p className="consumer-note">输入修改后系统会在继续时自动核对最新产品、金额和资金；交易仍由本人确认和验证。已受理交易按原号查询。</p>
    {!ready&&<p>正在读取本人资料…</p>}{error&&<p role="alert">{wealthFeedback(error)}</p>}{activityNotice&&<p role="status" className="consumer-warning">{activityNotice}</p>}{agentCommand?.kind==='assets'&&ready&&<section aria-label="本人理财资产"><p>当前可用现金 {w.money(availableCash)} · 持仓市值 {w.money(currentHoldings)}{ready.assets.receivable_fen>0&&<> · 历史赎回应收 {w.money(ready.assets.receivable_fen)}</>}</p><p>总资产 {w.money(ready.assets.asset_summary?.total_assets_fen)}</p></section>}{reading&&ready&&<p role="status">正在刷新账户及人工控制状态…</p>}{blockedAccounts.length>0&&<p className="consumer-warning">账户 {Array.from(new Set(blockedAccounts)).join('、')} 存在人工接管或暂停任务。资料可继续查询；相关交易仍受银行权限检查。</p>}
    <fieldset hidden={!!agentCommand&&!['planning','recommend','purchase'].includes(agentCommand.kind)} disabled={busy||!ready}><legend>规划与推荐</legend>
      <AgentStepCard stage="match" onAdvance={()=>{if(executionActive||busy)return;if(workflow==='planning')void run('plan_portfolio',{});else if(mode==='name'?nameQuery.trim():category)recommend(mode==='name'?{mode:'name',name_query:nameQuery.trim()}:{mode:'category',category});}} title={workflow==='planning'?'规划设置':'产品匹配'}><label>办理方式<select disabled={executionActive} value={workflow} onChange={e=>changeWorkflow(e.target.value as 'planning'|'target')}><option value="planning">理财规划</option><option value="target">目标产品推荐</option></select></label>
      {workflow==='planning'?<><p>根据本人授权资产和正式风险评估，使用资产配置模型生成整体方案及配置比例。</p><button disabled={executionActive} onClick={()=>void run('plan_portfolio',{})}>生成理财规划</button></>:<>
        <p>请选择希望购买的产品类别，或填写产品名称、代码进行匹配。</p>
        <label>匹配方式<select value={mode==='name'?'name':'category'} onChange={e=>setMode(e.target.value)}><option value="category">按类别</option><option value="name">按产品名称或代码</option></select></label>
        {mode==='name'?<label>产品名称或代码<input aria-label="推荐产品名称或代码" value={nameQuery} onChange={e=>setNameQuery(e.target.value)}/></label>:<label>产品类别<select value={category} onChange={e=>setCategory(e.target.value)}>{agentCommand&&!category&&<option value="" disabled>请选择产品类别</option>}<option value="quick_redeem">可快速赎回</option><option value="bank_wealth">银行理财</option><option value="bond_fund">债券基金</option><option value="mixed_fund">混合基金</option><option value="equity_fund">权益基金</option></select></label>}
        <button disabled={mode==='name'?!nameQuery.trim():!category} onClick={()=>recommend(mode==='name'?{mode:'name',name_query:nameQuery.trim()}:{mode:'category',category})}>匹配产品</button>
      </>}
      </AgentStepCard>{pending==='planning'&&<p role="status">{workflow==='planning'?'正在计算整体配置…':'正在匹配产品…'}，请等待当前请求完成。</p>}{renderResult('planning')}
      {candidateGroups.length>0&&ready&&<><ProductPicker requiredBuckets={workflow==='planning'?poolHint.required:undefined} selectionLocked={executionActive} allowEmptySelection={workflow==='planning'&&!!planningBaseline&&poolHint.required.length===0} selectionDescription={workflow==='planning'?poolHint.description:undefined} planningRefresh={workflow==='planning'} continueDisabled={workflow==='planning'&&(!cashBudget||!planningBaseline)} beforeContinue={<PlanningCashInput disabled={executionActive} holdings={currentHoldings} available={availableCash} draft={cashDraft} onChange={next=>{setCashDraft(next);setReviewing(false);setActionErrors(current=>({...current,planning:''}));}}/>} showBuckets={workflow==='planning'} groups={candidateGroups} scopeKey={ready.assets.context_id??'legacy-v1'} selection={candidateSelection} onSelectionChange={next=>{setCandidateSelection(next);setReviewing(false);setActionErrors(current=>({...current,planning:''}));}} busy={busy}
        onRefreshGroup={(group,ids)=>void run('recommend_products',{filter:group.filter,cursor:group.next_cursor,selected_family_ids:workflow==='planning'?[...ids,...group.items.map(i=>i.family_id)]:ids,selected:workflow==='planning'?candidateSelection.items.map(({share})=>({product_id:share.product_id,product_version:share.product_version,share_class:share.share_class})):undefined})}
        loadDetail={loadDetail} loadComparison={loadComparison} onContinue={workflow==='planning'?()=>{setSelectionAttempted(true);if(poolHint.warning){setActionErrors(current=>({...current,planning:''}));return;}const selected=candidateSelection.items.map(({share})=>({product_id:share.product_id,product_version:share.product_version,share_class:share.share_class}));if(cashBudget)void run('plan_portfolio',{selected,cash_reserve_fen:cashBudget.reserve,available_cash_fen:availableCash});}:undefined}/><p className="consumer-note">勾选与换批保留待校验产品，不会生成订单。</p>{workflow==='target'&&<TargetPurchase autoPrepare={agentCommand?.auto_prepare} novaTaskId={agentTaskId} initialAmounts={agentCommand?Object.fromEntries(agentCommand.products.map((p,i)=>[p.product_id,agentCommand.amounts_yuan[i]??''])):undefined} onPrepared={onPrepared} selection={candidateSelection} assets={ready.assets} accountId={ready.accountId} onActivity={onActivity}/>}</>}
      {workflow==='planning'&&portfolio&&(reviewing||executionActive)&&<StructuredPortfolioReview onPrepared={onPrepared} onFlowActive={setExecutionActive} key={`${portfolio.portfolio_id}:${portfolio.snapshot_id}`} plan={portfolio} selection={candidateSelection} ratios={ratios} onRatiosChange={setRatios} onActivity={()=>{setActivityNotice('银行操作已返回，资金与持仓已刷新；请以银行实际结果为准。');onActivity();}}
        onRevise={async selection=>{const result=await run('revise_portfolio',{selection},{object_type:'products',ids:selection.items.map(i=>`${i.product_id}:${i.product_version}`)},true);if(result?.status!=='ok')throw new Error(result?.message??'比例调整未完成，请按当前范围检查输入后重试。');return readPortfolioPlan(result.data,portfolio.context_id??contextId);}}
        onPrepare={async (plan)=>{if(!ready?.assets.cash.some(a=>a.account_id===ready.accountId&&['savings','checking'].includes(a.account_type)))throw new Error('当前理财绑定的借记账户不可用，请刷新账户资料。');const paymentAccountId=ready.accountId;const result=await run('prepare_purchase',{portfolio_id:plan.portfolio_id,expected_version:plan.version,snapshot_id:plan.snapshot_id,...(paymentAccountId?{payment_account_id:paymentAccountId}:{})},undefined,true);if(result?.status!=='ok')throw new Error(result?.message??'差额准备未完成，请重新核对。');return result.data as w.PreparedPortfolio;}}/>}
    </fieldset>
    <fieldset hidden={!!agentCommand&&!['positions','redeem'].includes(agentCommand.kind)} disabled={busy||!ready}><legend>持仓查询</legend><button onClick={()=>list()}>查询我的持仓</button>{renderResult('positions')}</fieldset>
    <fieldset aria-label="整体理财收益" hidden={!!agentCommand&&agentCommand.kind!=='returns'} ref={returnsRef} disabled={reading||!ready}>
      {ready&&(!agentCommand||agentCommand.kind==='returns')&&<ReturnsView key={agentCommand?.period??'overall'} source={returnsSource} initialPeriod={agentCommand?.kind==='returns'?agentCommand.period:'day'}/>}
    </fieldset>
    {detailView&&<ProductDetail productId={detailView.id} productVersion={detailView.version} loadDetail={loadDetail} onClose={()=>setDetailView(null)}/>}
    {compareView&&ready&&<ProductComparison products={agentCommand?.kind==='compare'&&agentCommand.products.length>=2?agentCommand.products:candidateSelection.items.length>=2&&candidateSelection.items.length<=4?candidateSelection.items.map(r=>({product_id:r.share.product_id,name:r.candidate.name})):ready.products} loadComparison={loadComparison} onClose={()=>setCompareView(false)}/>}
    {confirm&&<SingleTradePreview preview={confirm.preview} redemption={confirm} onClose={()=>setConfirm(null)} onActivity={onActivity} onCompleted={setActivityNotice}/>}
    {redemptionRows&&<RedemptionDialog autoPrepare={agentCommand?.auto_prepare} novaTaskId={agentTaskId} inline={!!agentCommand} onPrepared={onPrepared} initialInput={agentCommand?.redemption_value?{mode:agentCommand.redemption_input,value:agentCommand.redemption_value,redemption:agentCommand.redemption_mode}:undefined} rows={redemptionRows} contextId={contextId} fixtureOnly={false} blockedAccounts={blockedAccounts} onClose={()=>setRedemptionRows(null)} onCompleted={message=>{setActivityNotice(message);setRedemptionRows(null);}} onActivity={onActivity}/>}
  </section>;
}
