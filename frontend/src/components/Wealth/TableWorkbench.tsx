import {useMemo,useState} from 'react';
import * as w from '../../services/wealth';
import PositionTable from './PositionTable';
import {positionTableData} from './tableData';
import type {ProductDetailLoader} from './ProductDetail';
import RedemptionDialog from './RedemptionDialog';
import ReturnsView from './ReturnsView';
import type {ReturnsPreviewSource} from './ReturnsView';
import type {WealthReturns} from '../../services/wealthReturns';

interface Props {assets:w.Assets;scopeKey:string;onActivity:()=>void;blockedAccounts?:string[];
  loadDetail?:ProductDetailLoader;fixtureOnly?:boolean;refreshKey?:number;sessionId?:string;returnsPreview?:ReturnsPreviewSource}
export default function TableWorkbench({assets,scopeKey,onActivity,blockedAccounts=[],loadDetail,fixtureOnly=false,refreshKey=0,sessionId,returnsPreview}:Props){
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
  const [notice,setNotice]=useState('');
  const [redeem,setRedeem]=useState<{scope:string;keys:string[]}|null>(null);
  const [returns,setReturns]=useState<{scope:string;product?:string}|null>(null);
  const positions=positionTableData(assets,scopeKey);
  const currentRedeem=redeem?.scope===scopeKey?positions.rows.filter(row=>redeem.keys.includes(row.position_key)):[];
  const selectRedeem=(rows:w.PositionRow[])=>{
    setRedeem({scope:scopeKey,keys:rows.map(row=>row.position_key)});setNotice('');
  };
  return <section aria-label="持仓工作台">
    {notice&&<p role="alert" className="consumer-warning">{notice}</p>}
    <div hidden={(returns?.scope===scopeKey&&!!returnsSource)}><PositionTable data={positions} loadDetail={loadDetail} onRedeem={selectRedeem} onReturns={product=>setReturns({scope:scopeKey,product})} blockedAccounts={blockedAccounts}/></div>
    {returns?.scope===scopeKey&&returnsSource&&<div><ReturnsView key={`${scopeKey}:${returns.product??'all'}`} source={returnsSource} initialProduct={returns.product} onClose={()=>setReturns(null)}/></div>}
    {!!currentRedeem.length&&<RedemptionDialog key={`${scopeKey}:${sessionId??'standalone'}`} rows={currentRedeem} contextId={assets.context_id??'legacy-v1'} fixtureOnly={fixtureOnly} sessionId={sessionId} blockedAccounts={blockedAccounts} onClose={()=>setRedeem(null)} onActivity={onActivity}/>}
  </section>;
}
