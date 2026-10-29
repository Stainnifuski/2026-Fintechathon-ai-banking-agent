import { useEffect, useMemo, useRef, useState } from 'react';
import ReactECharts from './Chart';
import * as echarts from 'echarts/core';
import { PieChart, LineChart } from 'echarts/charts';
import { GridComponent, TooltipComponent, AriaComponent, LegendComponent } from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { NovaPageContext, BillResultTarget } from '../../services/nova';
import type { Account } from '../../services/bank';
import * as bills from '../../services/bills';
import {ChangeExplanationPanel,SubscriptionInsightsPanel,CashflowPanel} from './Enhancements';
import {BudgetPanel,AnomalyPanel,ForecastHistory,labels,money,errorText} from './Decisions';
import Foundations,{RecurringManagement} from './Foundations';
import Organization from './Organization';
import Transactions,{type DetailFilter} from './Transactions';
import {BillTabs,BillPanel,BillDisclosure,sections,submenus,type BillSection,type BillSelection} from './Workspace';
import {accountLabel} from '../../utils/customerLabels';
import './styles.css';
import './workspace.css';

echarts.use([PieChart,LineChart,GridComponent,TooltipComponent,AriaComponent,LegendComponent,CanvasRenderer]);
const currentMonth=()=>new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit'}).format(new Date());
const initialQuery=():bills.BillQuery=>({period:'month',date:currentMonth()});
const dateTime=(value:string)=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
const descriptions:Record<BillSection,string>={overview:'查看收支、消费去向与变化依据。',transactions:'查询流水，在详情中整理分类和标签。',budgets:'设置独立月份的预算，跟踪使用情况与预警。',checks:'展开需要核对的功能，查看依据并记录处理结果。',forecast:'管理周期收支，模拟未来现金流并复盘预测。',organize:'整理商户、分类规则与标签。'};
const defaultOpen:Record<string,boolean>={anomalies:true,subscriptions:false,recurring:true,cashflow:false,history:false,merchants:true,rules:false};
const quickQuestions:Record<string,string>={overview:'解释当前筛选的账单，并说明支出变化的主要依据',transactions:'查看当前筛选的交易明细',budgets:'查看当前预算月份的预算使用情况与超支预警',anomalies:'查看当前账期尚待核对的异常及其依据',subscriptions:'核对当前账期的订阅扣费及差异',recurring:'查看我的周期收支及纳入预测的设置',cashflow:'估计未来30天的储蓄现金流，说明计划与历史估计的区别',history:'查看我保存的基准预测与实际收支的偏差',merchants:'查看我的商户与别名目录',rules:'查看我的商户分类规则和流水标签'};

type Props={navigation?:{id:string;target:BillResultTarget};accounts:Account[];revision:number;onViewCashflows?:()=>void;onContextChange?:(context:NovaPageContext)=>void;onManageSubscription?:(id:string)=>void;onAskNova:(text:string,context:NovaPageContext)=>void};
export default function BillAnalysis({navigation,accounts,revision,onViewCashflows,onContextChange,onManageSubscription,onAskNova}:Props) {
 const [section,setSection]=useState<BillSection>('overview');
 const [expanded,setExpanded]=useState(defaultOpen),[focused,setFocused]=useState<Record<string,string>>({checks:'anomalies',forecast:'recurring',organize:'merchants'});
 const [selections,setSelections]=useState<Record<string,BillSelection|null>>({});
 const [selectionLabels,setSelectionLabels]=useState<Record<string,string>>({});
 const [metadataRevision,setMetadataRevision]=useState(0),[forecastRevision,setForecastRevision]=useState(0);
 const [draft,setDraft]=useState(initialQuery),[result,setResult]=useState<bills.BillAnalysis|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const [catalog,setCatalog]=useState<bills.FoundationCatalog|null>(null),[catalogError,setCatalogError]=useState(''),[catalogRetry,setCatalogRetry]=useState(0);
 const [detailFilter,setDetailFilter]=useState<DetailFilter>({label:'报告内已完成明细'}),[page,setPage]=useState(1);
 const [budgetMonth,setBudgetMonth]=useState(currentMonth),[budgetMonthDraft,setBudgetMonthDraft]=useState(currentMonth);
 const [forecastAccount,setForecastAccount]=useState<string|null>(null);
 const applied=useRef(initialQuery()),sequence=useRef(0);
 const changed=()=>setMetadataRevision(n=>n+1);
 const selectAt=(key:string,item:BillSelection|null,label='')=>{setSelections(items=>({...items,[key]:item}));setSelectionLabels(items=>({...items,[key]:label}));};
 const load=async(query:bills.BillQuery,applyDraft=false)=>{
   const id=++sequence.current;setBusy(true);setError('');
   try{const data=await bills.analysis(query);if(id!==sequence.current)return;
     if(JSON.stringify(applied.current)!==JSON.stringify(data.query)){setPage(1);setDetailFilter({label:data.query.category?`${labels[data.query.category]}消费`:'报告内已完成明细'});setSelections(items=>({...items,overview:null,transactions:null,anomalies:null,subscriptions:null}));}
     applied.current=data.query;setResult(data);if(applyDraft)setDraft(data.query);
   }catch(e){if(id===sequence.current)setError(errorText(e));}finally{if(id===sequence.current)setBusy(false);}
 };
 useEffect(()=>{void load(applied.current);return()=>{sequence.current++;};},[revision,metadataRevision]);
 useEffect(()=>{let live=true;setCatalogError('');bills.foundations().then(data=>{if(live)setCatalog(data);}).catch(e=>{if(live)setCatalogError(errorText(e));});return()=>{live=false;};},[revision,metadataRevision,catalogRetry]);
 const submenu=section in submenus?submenus[section as keyof typeof submenus]:null;
 const subsection=submenu?.find(([id])=>id===focused[section]&&expanded[id])?.[0]||submenu?.find(([id])=>expanded[id])?.[0];
 const featureKey=subsection||section;
 const selected=selections[featureKey]||null;
 const historical=['overview','transactions','checks'].includes(section);
 const pageContext=useMemo<NovaPageContext>(()=>({version:1,page:'insights',
   ...(historical&&result?{filters:result.query,observed_as_of:result.as_of,details:{category:detailFilter.category,tx_ids:detailFilter.tx_ids?.split(',').slice(0,10)||[],page}}:{}),
   ...(selected?.type==='transaction'?{selected:{type:'transaction',id:selected.id}}:{}),
   bill_workspace:{section,...(subsection?{subsection}:{}),...(section==='budgets'?{budget_month:budgetMonth}:{}),...(subsection==='cashflow'?{account_id:forecastAccount}:{}),...(selected?{selected}:{} )},
 }),[section,subsection,historical,result,detailFilter,page,selected,budgetMonth,forecastAccount]);
 useEffect(()=>{onContextChange?.(pageContext);},[pageContext,onContextChange]);
 const drill=(category?:string,tx_ids?:string,label='报告内已完成明细')=>{setDetailFilter({category,tx_ids,label});setPage(1);selectAt('transactions',null);setSection('transactions');};
 // A result-card link deliberately opens current bank data, never rewrites the snapshot.
 const handledNavigation=useRef<string>();
 useEffect(()=>{
   if(!navigation||handledNavigation.current===navigation.id)return;
   let live=true;
   const apply=async()=>{
     const dest=navigation.target;
     if(dest.query){await load(dest.query,true);if(!live)return;}
     setSection(dest.section);
     if(dest.subsection){setExpanded(items=>({...items,[dest.subsection!]:true}));setFocused(items=>({...items,[dest.section]:dest.subsection!}));}
     if(dest.budget_month){setBudgetMonth(dest.budget_month);setBudgetMonthDraft(dest.budget_month);}
     if(dest.subsection==='cashflow')setForecastAccount(dest.account_id??null);
     if(dest.section==='transactions'){
       setPage(1);setDetailFilter({tx_ids:dest.tx_ids?.length?dest.tx_ids.join(','):undefined,label:dest.tx_ids?.length?'关联流水 · 最新查询':'查询范围内明细 · 最新查询'});
       selectAt('transactions',dest.tx_id?{type:'transaction',id:dest.tx_id}:null);
     }
     handledNavigation.current=navigation.id;
   };
   void apply();return()=>{live=false;};
 },[navigation]);
 const selectTransaction=(key:string,id:string)=>selectAt(key,{type:'transaction',id},'已选中一笔流水');
 const selectedTransaction=selected?.type==='transaction'?selected.id:null;
 const toggle=(id:string,open:boolean)=>{setExpanded(items=>({...items,[id]:open}));if(open)setFocused(items=>({...items,[section]:id}));};
 const focus=(group:string,id:string)=>setFocused(items=>items[group]===id?items:{...items,[group]:id});
 const ask=()=>onAskNova(selected?.type==='transaction'?'解释这笔选中的流水及其分类、标签和关联记录':selected?.type==='budget'?'查看这个预算的使用情况和预警':selected?.type==='anomaly'?'解释这项异常及其核对依据':selected?.type==='recurring'?'解释这项周期收支及是否纳入预测':selected?.type==='merchant'?'查看这个商户的别名与分类规则':selected?.type==='tag'?'解释这个标签的关联流水范围':selected?.type==='category_rule'?'查看这个商户的分类规则':quickQuestions[featureKey]||quickQuestions[section],pageContext);
 const transactionSelection=selections.transactions;
 const reportContent=result?<>
      <div className="bill-metrics">{[
        ['消费原额', result.totals.consumption_fen], ['报告流入', result.totals.inflow_fen],
        ['报告流出', result.totals.outflow_fen], ['报告净流入', result.totals.net_inflow_fen],
      ].map(([label, value]) => <div key={label}><span>{label}</span><strong>{money(Number(value))}</strong></div>)}</div>
      <div className="bill-submetrics"><span>退款 {money(result.totals.refund_fen)}（已关联 {money(result.totals.linked_refund_fen)} · 未关联 {money(result.totals.unlinked_refund_fen)}）</span><span>净消费 {money(result.totals.net_consumption_fen)}</span><span>转入 {money(result.totals.transfer_in_fen)}</span><span>转出 {money(result.totals.transfer_out_fen)}</span><span>已完成 {result.totals.count} 笔</span></div>
      <p className="bill-comparison">消费较上期变化 <strong>{money(result.change_fen)}</strong> · {result.change_percent === null ? '上期为零，不计算增长率' : `${result.change_percent > 0 ? '+' : ''}${result.change_percent.toFixed(2)}%`}<small>对比范围：{dateTime(result.previous_start)} 至 {dateTime(result.previous_end_exclusive)}（不含终点）</small></p>
      {result.totals.count === 0 && <p className="bill-empty">这个范围内暂无纳入报告的已完成交易。可以切换月份或账户查看。</p>}
      <div className="bill-charts"><div><h3>消费去向</h3>{result.totals.consumption_fen ? <ReactECharts echarts={echarts} style={{ height: 250 }} option={{ aria: { enabled: true, description: `消费分类：${result.categories.map(c => `${c.label}${money(c.amount_fen)}`).join('，')}。点击分类可查看流水。` }, tooltip: { trigger: 'item', valueFormatter: (v: number) => money(v) }, series: [{ type: 'pie', radius: ['43%', '68%'], label: { formatter: '{b}\n{d}%' }, data: result.categories.filter(c => c.amount_fen > 0).map(c => ({ name: c.label, value: c.amount_fen, category: c.category })) }], color: ['#1769e0', '#159a8c', '#e8a346', '#815ac0', '#dd7184', '#899bb6'] }} onEvents={{ click: (params: { data: { category: string }; name: string }) => drill(params.data.category, undefined, `${params.name}消费`) }} /> : <p className="bill-empty">暂无消费数据</p>}</div>
        <div><h3>{result.query.period === 'month' ? '每日' : '每月'}消费趋势</h3><ReactECharts echarts={echarts} style={{ height: 250 }} option={{ aria: { enabled: true, description: `消费趋势，金额单位人民币元：${result.trend.map(p => `${p.date} ${money(p.consumption_fen)}`).join('，')}` }, tooltip: { trigger: 'axis', valueFormatter: (v: number) => money(v) }, grid: { left: 65, right: 15, bottom: 35, top: 25 }, xAxis: { type: 'category', data: result.trend.map(p => p.date.slice(5)) }, yAxis: { type: 'value', axisLabel: { formatter: (v: number) => `¥${v / 100}` } }, series: [{ name: '消费原额', type: 'line', data: result.trend.map(p => p.consumption_fen), itemStyle: { color: '#1769e0' }, areaStyle: { color: '#e8f1ff' }, showSymbol: result.trend.length < 15 }] }} /></div></div>
      <div className="bill-category-list">{result.categories.map(c => <button className="secondary" key={c.category} onClick={() => drill(c.category, undefined, `${c.label}消费`)}>{c.label} · {money(c.amount_fen)} · {c.share_percent}% · {c.count} 笔 · 关联退款 {money(c.refund_fen)} · 净额 {money(c.net_amount_fen)}</button>)}</div>
      <div className="bill-report"><h3>账单总结</h3>{result.report.map(line => <p key={line}>{line}</p>)}</div>
      <details className="bill-notices"><summary>统计口径与数据说明</summary><ul>{result.notices.map(line => <li key={line}>{line}</li>)}</ul><p>数据截止：{dateTime(result.as_of)}。报告按交易时间查询，未保存历史快照；稍后补录的数据可能改变结果。</p></details>
 </>:<p className="bill-empty">正在准备账单报告。</p>;
 return <section className="bills bill-workspace" aria-label="账单工作台">
  <header className="bill-workspace-head"><div><h2>账单工作台</h2><p>{descriptions[section]}</p></div></header>
  <BillTabs items={sections} value={section} onChange={value=>setSection(value as BillSection)} label="账单功能导航" prefix="bill"/>
  <div className="bill-nova-context"><div><strong>当前功能：{sections.find(([key])=>key===section)?.[1]}{subsection&&` / ${submenu?.find(([key])=>key===subsection)?.[1]}`}</strong><small>可手动办理，也可在同一个 NOVA 对话中查询或整理。</small></div><button type="button" className="secondary" onClick={ask}>让 NOVA 分析当前内容</button></div>
  {selected&&<div className="bill-selection" role="status"><span>{selectionLabels[featureKey]||'已选择当前对象'} · 已同步给 NOVA</span><button type="button" className="text-button" onClick={()=>selectAt(featureKey,null)}>清除选择</button></div>}
  <div hidden={!historical} className="bill-report-scope">
    <details className="bill-scope-filter"><summary>调整账单筛选 · {result?.query.date||applied.current.date} · {result?.query.account_id?accountLabel(result.query.account_id,accounts):'全部账户'}</summary>
    <form className="bill-filters" onSubmit={e => { e.preventDefault(); void load(draft, true); }}>
      <label>报告类型<select value={draft.period} onChange={e => { const period = e.target.value as 'month' | 'year'; setDraft({ ...draft, period, date: period === 'year' ? draft.date.slice(0, 4) : `${draft.date.slice(0, 4)}-${currentMonth().slice(5)}` }); }}><option value="month">月度账单</option><option value="year">年度账单</option></select></label>
      <label>{draft.period === 'month' ? '月份' : '年份'}<input aria-label="账单时间" required type={draft.period === 'month' ? 'month' : 'number'} min={draft.period === 'month' ? '2000-01' : '2000'} max={draft.period === 'month' ? currentMonth() : currentMonth().slice(0, 4)} value={draft.date} onChange={e => setDraft({ ...draft, date: e.target.value })} /></label>
      <label>账户<select value={draft.account_id || ''} onChange={e => setDraft({ ...draft, account_id: e.target.value || null })}><option value="">全部账户</option>{accounts.map(a => <option key={a.account_id} value={a.account_id}>{a.card_number}</option>)}</select></label>
      <label>消费分类<select value={draft.category || ''} onChange={e => setDraft({ ...draft, category: e.target.value || null })}><option value="">全部分类及收支</option>{Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>用户标签<select aria-label="用户标签" value={draft.tag_id || ''} onChange={e => setDraft({ ...draft, tag_id: e.target.value || null })}><option value="">全部标签</option>{catalog?.tags.map(t => <option key={t.tag_id} value={t.tag_id}>{t.name} · {t.transaction_count} 笔</option>)}</select></label>
      <button disabled={busy}>{busy ? '正在分析…' : '查看账单'}</button>
    </form>

    </details>
    {result&&<div className="bill-scope-caption">报告范围：{result.query.date} · {result.partial?'本期尚未结束，比较上期同期':'完整账期'} · 截止 {dateTime(result.as_of)}</div>}
    {busy&&<p role="status">正在更新账单报告…</p>}{error&&<p className="error" role="alert">{error} <button onClick={()=>void load(applied.current)}>重新加载</button></p>}
  </div>
  <BillPanel active={section==='overview'} id="bill-panel-overview" labelledBy="bill-tab-overview"><div className="card bill-block" data-bill-feature="overview">{reportContent}{result&&<ChangeExplanationPanel result={result} selectedId={selectedTransaction} onSelect={id=>selectTransaction('overview',id)}/>}<p className="hint">{onViewCashflows&&<button type="button" className="text-button" onClick={onViewCashflows}>查看含理财申赎的资金流水</button>}</p></div></BillPanel>
  <BillPanel active={section==='transactions'} id="bill-panel-transactions" labelledBy="bill-tab-transactions"><div className="card bill-block" data-bill-feature="transactions">{result&&<Transactions result={result} accounts={accounts} filter={detailFilter} page={page} onPage={value=>{setPage(value);selectAt('transactions',null);}} selected={transactionSelection?.type==='transaction'?transactionSelection.id:null} onSelect={id=>selectTransaction('transactions',id)} onReset={()=>drill()} revision={revision+metadataRevision} onSaved={changed}/>}</div></BillPanel>
  <BillPanel active={section==='budgets'} id="bill-panel-budgets" labelledBy="bill-tab-budgets"><div className="card bill-block" data-bill-feature="budgets"><form className="bill-inline-scope" onSubmit={event=>{event.preventDefault();setBudgetMonth(budgetMonthDraft);selectAt('budgets',null);}}><label>预算月份<input type="month" required min="2000-01" max="2099-12" value={budgetMonthDraft} onChange={e=>setBudgetMonthDraft(e.target.value)}/></label><button>查看本月预算</button></form><p className="hint">当前预算月份：{budgetMonth}。预算独立于历史报告的分类和标签筛选。</p><BudgetPanel month={budgetMonth} accounts={accounts} revision={revision+metadataRevision} onChanged={changed} onSelect={item=>selectAt('budgets',item?{type:'budget',id:item.budget_id}:null,item?`${item.month} · ${item.category?labels[item.category]:'总预算'}`:'')}/></div></BillPanel>
  <BillPanel active={section==='checks'} id="bill-panel-checks" labelledBy="bill-tab-checks">
   <BillDisclosure id="anomalies" title="异常核对" description="查看提示与依据，记录核对状态和备注。" open={expanded.anomalies} onToggle={open=>toggle('anomalies',open)} onFocus={()=>focus('checks','anomalies')}>{result&&<AnomalyPanel result={result} onChanged={changed} onDrill={item=>drill(item.category||undefined,item.tx_ids.join(',')||undefined,item.title)} onSelect={item=>selectAt('anomalies',{type:'anomaly',id:item.anomaly_id},item.title)}/>}</BillDisclosure>
   <BillDisclosure id="subscriptions" title="订阅扣费核对" description="区分已发生扣费与订阅计划，核对涨价和重复扣费。" open={expanded.subscriptions} onToggle={open=>toggle('subscriptions',open)} onFocus={()=>focus('checks','subscriptions')}>{result&&<SubscriptionInsightsPanel result={result} revision={revision+metadataRevision} selectedId={selections.subscriptions?.type==='transaction'?selections.subscriptions.id:null} onSelect={id=>selectTransaction('subscriptions',id)} onManage={onManageSubscription}/>}</BillDisclosure>
  </BillPanel>
  <BillPanel active={section==='forecast'} id="bill-panel-forecast" labelledBy="bill-tab-forecast">
   <BillDisclosure id="recurring" title="周期收支" description="建立或编辑固定收支，选择纳入预测的记录。" open={expanded.recurring} onToggle={open=>toggle('recurring',open)} onFocus={()=>focus('forecast','recurring')}><RecurringManagement accounts={accounts} revision={revision+metadataRevision} onChanged={changed} onManage={onManageSubscription} onSelect={id=>selectAt('recurring',id?{type:'recurring',id}:null,id?'已选择一项周期收支':'')}/></BillDisclosure>
   <BillDisclosure id="cashflow" title="现金流预测" description="从现实现在起算，调整减支或临时收支假设。" open={expanded.cashflow} onToggle={open=>toggle('cashflow',open)} onFocus={()=>focus('forecast','cashflow')}><label>预测账户<select aria-label="预测账户" value={forecastAccount||''} onChange={event=>setForecastAccount(event.target.value||null)}><option value="">全部有效储蓄账户</option>{accounts.filter(a=>a.account_type==='savings').map(a=><option key={a.account_id} value={a.account_id}>{accountLabel(a.account_id,accounts)}</option>)}</select></label><CashflowPanel accountId={forecastAccount} revision={revision+metadataRevision} onSaved={()=>setForecastRevision(n=>n+1)}/></BillDisclosure>
   <BillDisclosure id="history" title="预测复盘" description="查看保存的基准，按原预测区间与实际收支比较。" open={expanded.history} onToggle={open=>toggle('history',open)} onFocus={()=>focus('forecast','history')}><ForecastHistory revision={revision+metadataRevision+forecastRevision}/></BillDisclosure>
  </BillPanel>
  <BillPanel active={section==='organize'} id="bill-panel-organize" labelledBy="bill-tab-organize">
   {catalogError&&<p role="alert" className="error">{catalogError} <button onClick={()=>setCatalogRetry(n=>n+1)}>重新加载目录</button></p>}
   <BillDisclosure id="merchants" title="商户管理" description="管理别名、归并与拆分，查看历史变更。" open={expanded.merchants} onToggle={open=>toggle('merchants',open)} onFocus={()=>focus('organize','merchants')}><Foundations revision={revision+metadataRevision} onChanged={changed} onSelect={id=>selectAt('merchants',id?{type:'merchant',id}:null,id?catalog?.merchants.find(m=>m.merchant_id===id)?.name||'已选择商户':'')}/></BillDisclosure>
   <BillDisclosure id="rules" title="分类与标签" description="查看分类规则及标签目录，定位关联流水。" open={expanded.rules} onToggle={open=>toggle('rules',open)} onFocus={()=>focus('organize','rules')}><Organization catalog={catalog} revision={revision+metadataRevision} onChanged={changed} onSelect={item=>selectAt('rules',item)} onTag={id=>{void load({...applied.current,tag_id:id},true);drill();}}/></BillDisclosure>
  </BillPanel>
 </section>;
}
