import React,{useState} from 'react';
import {createRoot} from 'react-dom/client';
import NovaCalendarMutationCard from '../src/components/NovaCalendarMutationCard';
import NovaCollectionCard from '../src/components/NovaCollectionCard';
import NovaCardOperationCard from '../src/components/NovaCardOperationCard';
import {apiClient} from '../src/services/api';
apiClient.defaults.adapter=async()=>{throw new Error('Bank requests forbidden in isolated refresh test');};
const base={
 calendar:{entry_id:'calendar',action:'add',status:'PENDING',changes:{title:'原日程',start_date:'2026-10-29',start_time:'10:00'},targets:[]},
 collection:{collection_id:'collection',status:'DRAFT',selected_payer_user_ids:[],receive_account_id:'',amount_fen:null,split_equally:false,note:'',contacts:[{contact_id:'C1',user_id:'U2',name:'测试联系人',label:'测试联系人'}],accounts:[],ambiguous_names:[],unknown_names:[]},
 card:{entry_id:'card',action:'card_set_transaction_limits',card_id:'CARD1',prefill:{single_txn_limit:'100',daily_txn_limit:'200'},card:{card_id:'CARD1',card_type:'DEBIT',status:'ACTIVE',card_number:'0000',single_txn_limit_fen:10000,daily_txn_limit_fen:20000,single_txn_cap_fen:100000,daily_txn_cap_fen:100000}},
};
function Harness(){
 const [data,setData]=useState<any>(base);
 return <><button onClick={()=>setData(structuredClone(data))}>模拟无关刷新</button>
  <button onClick={()=>setData((v:any)=>({...v,calendar:{...v.calendar,changes:{...v.calendar.changes,title:'模型修改日程'}},card:{...v.card,prefill:{...v.card.prefill,single_txn_limit:'150'}},collection:{...v.collection,selected_payer_user_ids:['U2']}}))}>模拟真实字段更新</button>
  <NovaCalendarMutationCard task={data.calendar} sessionId="isolated" disabled={false} onUpdated={()=>{}}/>
  <NovaCollectionCard card={data.collection} session={{session_id:'isolated'} as any} disabled={false} onUpdated={()=>{}}/>
  <NovaCardOperationCard task={data.card} sessionId="isolated" accounts={[]} disabled={false} onUpdated={()=>{}}/>
 </>;
}
createRoot(document.getElementById('root')!).render(<Harness/>);
