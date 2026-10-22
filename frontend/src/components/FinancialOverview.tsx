import {useEffect, useState, type CSSProperties} from 'react';
import {listCards, type BankCard, type Account, type Subscription} from '../services/bank';
import * as bills from '../services/bills';
import {apiClient} from '../services/api';
import AssetSummaryCard from './AssetSummary';
import type {useAssetSummary} from './AssetSummary/useAssetSummary';
import {beijingTime} from '../utils/time';
import {calibrateTime, serverInstant} from '../utils/serverClock';

type Section = 'home'|'transfer'|'insights'|'cards'|'subscriptions'|'wealth';
interface Props {
  userId:string; revision:number; accounts:Account[]; subscriptions:Subscription[];
  loading:boolean; error:string; onRetry:()=>void;
  assetSummary:ReturnType<typeof useAssetSummary>; onNavigate:(section:Section)=>void;
}
const money=(amount:number)=>amount.toLocaleString('zh-CN',{style:'currency',currency:'CNY'});
const colors=['#ad490d','#7135bb','#087b87','#b52169','#2458cc','#36733c'];
const barColors=['#ff9347','#a755e5','#21b7c7','#ea6299','#547bec','#89ad42'];
const categoryIcon=(category:string)=>({food:'🍜',dining:'🍜',shopping:'🛍',transport:'🚆',entertainment:'🎟',subscription:'↻',housing:'⌂'}[category]||'◈');
export default function FinancialOverview({userId,revision,accounts,subscriptions,loading,error,onRetry,assetSummary,onNavigate}:Props){
  const [cards,setCards]=useState<BankCard[]>([]);
  const [cardLoading,setCardLoading]=useState(true);
  const [cardError,setCardError]=useState('');
  const [cardRetry,setCardRetry]=useState(0);
  useEffect(()=>{let live=true;setCardLoading(true);setCardError('');setCards([]);void listCards().then(items=>{if(live)setCards(items);}).catch(()=>{if(live)setCardError('卡片资料暂时无法读取。');}).finally(()=>{if(live)setCardLoading(false);});return()=>{live=false;};},[userId,revision,cardRetry]);
  const [report,setReport]=useState<bills.BillAnalysis|null>(null);
  const [month,setMonth]=useState('');
  const [billLoading,setBillLoading]=useState(true);
  const [billError,setBillError]=useState('');
  const [retry,setRetry]=useState(0);
  useEffect(()=>{
    let live=true;
    let pending=false;
    let refreshQueued=false;
    let loadedMonth='';
    async function syncMonth(force=false){
      if(pending){refreshQueued ||= force;return;}
      pending=true;
      if(force){setBillLoading(true);setBillError('');setReport(null);}
      try {
        const sent=performance.now();
        const {data}=await apiClient.get<{server_time_utc:string}>('/system/time');
        const received=performance.now();
        const anchor=calibrateTime(data.server_time_utc,sent,received);
        const parts=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit'}).formatToParts(new Date(serverInstant(anchor,received)));
        const date=`${parts.find(p=>p.type==='year')!.value}-${parts.find(p=>p.type==='month')!.value}`;
        if(!live || (!force&&date===loadedMonth))return;
        setMonth(date);setBillLoading(true);setBillError('');setReport(null);
        const result=await bills.analysis({period:'month',date});
        if(live){loadedMonth=date;setReport(result);}
      } catch {if(live){setBillError('本月支出暂时无法读取。');}}
      finally {
        pending=false;
        if(live){
          setBillLoading(false);
          if(refreshQueued){refreshQueued=false;void syncMonth(true);}
        }
      }
    }
    const checkMonth=()=>{if(document.visibilityState==='visible')void syncMonth();};
    void syncMonth(true);
    const timer=window.setInterval(checkMonth,60_000);
    window.addEventListener('focus',checkMonth);
    document.addEventListener('visibilitychange',checkMonth);
    return()=>{live=false;window.clearInterval(timer);window.removeEventListener('focus',checkMonth);document.removeEventListener('visibilitychange',checkMonth);};
  },[userId,revision,retry]);
  const active=subscriptions.filter(item=>item.status==='ACTIVE').sort((a,b)=>Date.parse(a.next_charge_at)-Date.parse(b.next_charge_at));
  const categories=report?.categories.filter(item=>item.amount_fen>0)||[];
  const categoryTotal=categories.reduce((sum,item)=>sum+item.amount_fen,0);
  const sourceState=loading?<p role="status">正在读取账户与订阅…</p>:error?<div role="alert"><p>{error}</p><button className="text-button" onClick={onRetry}>重新读取</button></div>:null;
  return <section className="financial-overview" aria-label="财务概要">
    <div className="overview-column">
      <AssetSummaryCard {...assetSummary} onRefresh={assetSummary.refresh} variant="hero" />
      <article className="card home-data-card"><div className="section-heading"><h2>账户与卡片</h2><button className="text-button home-destination" data-destination="cards" onClick={()=>onNavigate('cards')}>管理卡片 ↗</button></div>
        {sourceState || (accounts.length?accounts.map(account=><div className="home-data-row home-account-row" key={account.account_id}>
          <span className="bank-card-graphic" data-account-type={account.account_type} aria-hidden="true"><svg viewBox="0 0 48 32" fill="none"><rect x="1" y="1" width="46" height="30" rx="5" fill="currentColor"/><rect x="7" y="9" width="9" height="7" rx="1.5" stroke="white" opacity=".8"/><path d="M27 24h3m4 0h3m4 0h1" stroke="white" strokeWidth="2"/>{account.account_type==='credit'&&<path d="M30 10h11" stroke="white" strokeWidth="2" opacity=".55"/>}</svg></span>
          <div><strong>{account.account_type==='savings'?'活期账户':account.account_type==='credit'?'信用账户':account.account_type}</strong><small>{account.card_number}</small></div>
          <div className="home-money"><strong>{money(account.balance)}</strong><small>{account.account_type==='credit'?'信用账户余额 · 不计入总资产':'账户余额'}</small></div>
          <div className="home-account-cards">
            {cardLoading?<small role="status">正在读取卡片…</small>:cardError?<small role="alert">{cardError}</small>:cards.filter(card=>card.account_id===account.account_id).map(card=><small key={card.card_id}>{card.is_virtual?'虚拟':''}{card.card_type==='CREDIT'?'信用卡':'借记卡'} · {card.card_number} · {{ACTIVE:'正常',LOCKED:'已锁定',LOST:'已挂失',CANCELLED:'已注销'}[card.status]}{card.card_type==='CREDIT'&&<span className="home-credit-fields"><span>总授信 {card.credit_limit_fen==null?'暂无':money(card.credit_limit_fen/100)}</span><span>已用 {money(card.used_credit_fen/100)}</span><span>可用 {card.available_credit_fen==null?'暂无':money(card.available_credit_fen/100)}</span></span>}</small>)}
            {!cardLoading&&!cardError&&!cards.some(card=>card.account_id===account.account_id)&&<small>暂无关联卡片</small>}</div>
        </div>):<p className="hint">暂无账户记录</p>)}
        <button className="text-button" hidden={!cardError} onClick={()=>setCardRetry(value=>value+1)}>重试卡片</button>
        <button className="text-button home-card-link home-destination" data-destination="transfer" onClick={()=>onNavigate('transfer')}>转账与收款 →</button>
      </article>
    </div>
    <div className="overview-column">
      <article className="card home-data-card"><div className="section-heading"><h2>本月支出</h2><div className="home-bill-actions"><span>{month || '月份读取中'}</span><button className="text-button" disabled={billLoading} onClick={()=>setRetry(value=>value+1)}>刷新账单</button></div></div>
        {billLoading?<p role="status">正在读取本月账单…</p>:billError?<div role="alert"><p>{billError}</p><button className="text-button" onClick={()=>setRetry(value=>value+1)}>重试账单</button></div>:report&&<>
          {report.as_of&&<small className="home-bill-as-of">查询于 {beijingTime(report.as_of)}</small>}
          <div className="home-spend-total"><strong>{money(report.totals.consumption_fen/100)}</strong><small>消费原额</small></div>
          {categories.length?<><div className="home-spend-bar" aria-label="消费分类占比">{categories.map((item,index)=><i key={item.category} style={{flex:`${item.amount_fen/categoryTotal} 1 0%`,background:barColors[index%barColors.length]}} title={`${item.label} ${money(item.amount_fen/100)} · ${(item.amount_fen/categoryTotal*100).toFixed(1)}%`} aria-label={`${item.label}，占消费分类合计的 ${(item.amount_fen/categoryTotal*100).toFixed(1)}%`}/>)}</div>
          {categories.map((item,index)=><div className="home-data-row home-category" key={item.category}><span className="category-label" style={{'--category-color':colors[index%colors.length]} as CSSProperties}><span aria-hidden="true">{categoryIcon(item.category)}</span>{item.label}</span><strong>{money(item.amount_fen/100)}</strong></div>)}</>:<p className="hint">本月暂无消费记录</p>}
          <p className="home-source-note">按消费原额分类，不含转账及理财现金流。退款 {money(report.totals.refund_fen/100)} · 净消费 {money(report.totals.net_consumption_fen/100)}。</p>
          {report.partial&&<p className="hint">当前月份尚未结束，统计截至查询时点。</p>}
          {report.notices.length>0&&<details className="home-report-notices"><summary>统计口径与说明</summary>{report.notices.map((notice,index)=><p className="hint" key={index}>{notice}</p>)}</details>}
        </>}
        <button className="text-button home-card-link home-destination" data-destination="insights" onClick={()=>onNavigate('insights')}>查看账单洞察 →</button>
      </article>
      <article className="card home-data-card"><div className="section-heading"><h2>订阅与下次扣款</h2>{!loading&&!error&&<span>{active.length} 项生效中</span>}</div>
        {sourceState || (active.length?active.map(item=><div className="home-data-row" key={item.subscription_id}><div><strong className="home-subscription-name">↻ {item.merchant_name}</strong><small>计划 {beijingTime(item.next_charge_at)}</small></div><strong className="home-money">{money(item.amount_fen/100)}</strong></div>):<p className="hint">暂无生效中的订阅</p>)}
        <button className="text-button home-card-link home-destination" data-destination="subscriptions" onClick={()=>onNavigate('subscriptions')}>管理订阅 →</button>
      </article>
    </div>
  </section>;
}
