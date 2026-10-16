import {useMemo,useState} from 'react';
import * as w from '../../services/wealth';
import OrderCancellationDialog from './OrderCancellationDialog';
import OrderTable,{bankEligibility} from './OrderTable';
import type {EligibilityLoader} from './OrderTable';
import PositionTable from './PositionTable';
import {orderTableData,positionTableData} from './tableData';
import type {ProductDetailLoader} from './ProductDetail';
import RedemptionDialog from './RedemptionDialog';
import ReturnsView from './ReturnsView';
import type {ReturnsPreviewSource} from './ReturnsView';
import type {WealthReturns} from '../../services/wealthReturns';

interface Props {assets:w.Assets;scopeKey:string;panel?:'orders'|'positions'|'both';onActivity:()=>void;blockedAccounts?:string[];
  loadEligibility?:EligibilityLoader;loadDetail?:ProductDetailLoader;fixtureOnly?:boolean;refreshKey?:number;sessionId?:string;returnsPreview?:ReturnsPreviewSource}
const errorText=(e:unknown)=>(e as {response?:{data?:{detail?:{message?:string}}}})?.response?.data?.detail?.message??(e instanceof Error?e.message:'操作失败，请保留原号核对');
export default function TableWorkbench({assets,scopeKey,panel='both',onActivity,blockedAccounts=[],loadEligibility=bankEligibility,loadDetail,fixtureOnly=false,refreshKey=0,sessionId,returnsPreview}:Props){
  const businessDate=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(assets.business_at));
  const expectedAssetScope=assets.asset_summary?.asset_scope_id;
  const bankReturns=useMemo<ReturnsPreviewSource>(()=>({actualBank:true,scenarios:[],notice:'仅查询当前授权范围，不产生交易。',
    defaultDate:businessDate,scenarioNotice:()=> '整体和单产品分别核对；已赎回产品保留在历史贡献中。',
    load:async query=>{
      const result=await w.get<WealthReturns>('/returns?'+new URLSearchParams(Object.entries(query).filter(([,value])=>value!==undefined).map(([key,value])=>[key,String(value)])).toString());
      if(!expectedAssetScope||result.asset_scope_id!==expectedAssetScope)throw new Error('授权账户范围已变化，请先刷新登录账户记录，再查看收益。');
      return result;
    }
  }),[scopeKey,businessDate,refreshKey,expectedAssetScope]);
  const returnsSource=returnsPreview??(!fixtureOnly?bankReturns:undefined);
  const [cancel,setCancel]=useState<{scope:string;rows:{row:w.OrderRow;eligibility:w.CancellationEligibility}[]}|null>(null);
  const [notice,setNotice]=useState('');
  const [redeem,setRedeem]=useState<{scope:string;keys:string[]}|null>(null);
  const [returns,setReturns]=useState<{scope:string;product?:string}|null>(null);
  const orders=orderTableData(assets,scopeKey),positions=positionTableData(assets,scopeKey);
  const currentRedeem=redeem?.scope===scopeKey?positions.rows.filter(row=>redeem.keys.includes(row.position_key)):[];
  const act=async(work:()=>Promise<void>)=>{setNotice('');try{await work();}catch(error){setNotice(errorText(error));}};
  const prepareCancel=(rows:w.OrderRow[])=>void act(async()=>{
    setCancel(null);const views=await Promise.all(rows.map(async row=>({row,eligibility:await loadEligibility(row.operation_id)})));
    if(views.some(v=>v.eligibility.operation_id!==v.row.operation_id||v.eligibility.order_digest!==v.row.digest))throw new Error('订单或撤单资格已变化，请刷新后重新核对');
    setCancel({scope:scopeKey,rows:views});
  });
  const selectRedeem=(rows:w.PositionRow[])=>{
    setRedeem({scope:scopeKey,keys:rows.map(row=>row.position_key)});setNotice('');
  };
  return <section aria-label="订单与持仓工作台">
    {notice&&<p role="alert" className="consumer-warning">{notice}</p>}
    <div hidden={panel==='positions'}><OrderTable data={orders} loadEligibility={loadEligibility} loadDetail={loadDetail} onCancel={prepareCancel} blockedAccounts={blockedAccounts} refreshKey={refreshKey}/></div>
    <div hidden={panel==='orders'||(returns?.scope===scopeKey&&!!returnsSource)}><PositionTable data={positions} loadDetail={loadDetail} onRedeem={selectRedeem} onReturns={product=>setReturns({scope:scopeKey,product})} blockedAccounts={blockedAccounts}/></div>
    {returns?.scope===scopeKey&&returnsSource&&<div hidden={panel==='orders'}><ReturnsView key={`${scopeKey}:${returns.product??'all'}`} source={returnsSource} initialProduct={returns.product} onClose={()=>setReturns(null)}/></div>}
    {cancel?.scope===scopeKey&&<OrderCancellationDialog key={JSON.stringify(cancel.rows.map(v=>[v.row.operation_id,v.eligibility.order_digest]))} rows={cancel.rows} blockedAccounts={blockedAccounts} fixtureOnly={fixtureOnly} onClose={()=>setCancel(null)} onComplete={message=>{setCancel(null);setNotice(message);onActivity();}}/>}
    {!!currentRedeem.length&&<RedemptionDialog key={`${scopeKey}:${sessionId??'standalone'}`} rows={currentRedeem} contextId={assets.context_id??'legacy-v1'} fixtureOnly={fixtureOnly} sessionId={sessionId} blockedAccounts={blockedAccounts} onClose={()=>setRedeem(null)} onActivity={onActivity}/>}
  </section>;
}
