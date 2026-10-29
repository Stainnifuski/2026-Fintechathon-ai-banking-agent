// Isolated real-component regression: synthetic data and an adapter that rejects
// every endpoint except preparation. Never connects to a banking service.
import React,{useCallback,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {apiClient} from '../src/services/api';
import TargetPurchase from '../src/components/Wealth/TargetPurchase';
import {WealthStepContext} from '../src/components/Wealth/AgentStepCard';
import {activeStep,stepOrder,reconcileCompletedSteps} from '../src/components/Wealth/stepFlow';
let preparations=0;
apiClient.defaults.adapter=async config=>{
 if(!config.url?.endsWith('/target-purchases/prepare'))throw new Error('Unexpected isolated request: '+config.url);
 preparations++;
 return {config,status:200,statusText:'OK',headers:{},data:{
  plan:{plan_id:'isolated',version:1,status:'AWAITING_CONFIRMATION',stage:'BUY',items:[]},
  preview:{plan_id:'isolated',version:1,items:[],verification_reference_id:'isolated'},
 }};
};
const selection:any={scopeKey:'isolated',preferredShares:{},items:[{candidate:{name:'测试产品'},share:{product_id:'P1',product_version:1,disclosure_version:1}}]};
const assets:any={context_id:'isolated',cash:[{account_id:'A1',account_type:'savings',available_fen:100000}]};
function Harness(){
 const [completed,setCompleted]=useState<string[]>(['amounts']);
 const [registered,setRegistered]=useState<string[]>([]);
 const [version,setVersion]=useState(1);
 const [restored,setRestored]=useState(false);
 const register=useCallback((stage:string)=>{setRegistered(v=>v.includes(stage)?v:[...v,stage]);return()=>setRegistered(v=>v.filter(s=>s!==stage));},[]);
 const complete=useCallback((...stages:string[])=>setCompleted(v=>Array.from(new Set([...v,...stages]))),[]);
 const invalidate=useCallback((...stages:string[])=>setCompleted(v=>v.filter(s=>!stages.includes(s))),[]);
 return <><p data-testid="preparations">{preparations}</p><p data-testid="version">{version}</p>
  <button onClick={()=>{setCompleted(['amounts']);setRestored(true);}}>恢复已保存金额</button>
  <WealthStepContext.Provider value={{taskId:'isolated',completed,active:activeStep(stepOrder('recommend'),registered,completed),register,complete,invalidate,focus:()=>{},backtrack:()=>{throw new Error('Rollback should not be needed');}}}>
   <TargetPurchase key={restored?'restored':'empty'} selection={selection} assets={assets} accountId="A1" initialAmounts={restored?{P1:'100'}:{}} onActivity={()=>{}}
    onPrepared={async()=>{setVersion(v=>v+1);setCompleted(v=>reconcileCompletedSteps(v,['amounts'],false));}}/>
  </WealthStepContext.Provider></>;
}
createRoot(document.getElementById('root')!).render(<Harness/>);
