import {useState} from 'react';
import CommandReview from './CommandReview';
import type {ReviewCommand} from '../../services/wealthOperatorReview';
import {portfolioAdjustmentNote} from '../../services/wealthOperatorReview';
import AllocationReview from './AllocationReview';
import {recordedF11Plan,recordedF11Snapshot,recordedF11Selection} from './FixtureProductProvider';
const rows:ReviewCommand[]=[
  {command:{operation_id:'fixture-handoff-buy',product_id:'fixture-S1-A',product_version:1,disclosure_version:1,account_id:'fixture-account',direction:'purchase',amount_fen:10000,units:'0.0000'},status:'未提交样例'},
  {command:{operation_id:'fixture-handoff-sell',product_id:'fixture-G1-A',product_version:1,disclosure_version:1,account_id:'fixture-account',direction:'redeem',amount_fen:0,units:'10.0000'},status:'未提交样例'},
];
/** Development-only view. No role changes, operator requests or bank operations. */
export default function HandoffReviewPreview(){
  const [note,setNote]=useState('');
  return <details><summary>人工核对界面样例（不领取真实任务）</summary>
    <p className="consumer-warning">虚构界面样例：只演示核对和整理备注，不读取登录账户任务、不赋予操作员权限、不修改任何订单。</p>
    <CommandReview rows={rows} onAdjustment={setNote}/>
    <details><summary>整体配置比例核对样例</summary>
      <AllocationReview plan={recordedF11Plan()} snapshot={recordedF11Snapshot()} selection={recordedF11Selection()}
        onPropose={input=>setNote(portfolioAdjustmentNote(recordedF11Plan(),input))}/>
    </details>
    <label>处理说明样例<textarea rows={4} style={{width:'100%'}} aria-label="处理说明样例" value={note} maxLength={1000} onChange={e=>setNote(e.target.value)}/></label>
    <p>正式操作员须经服务器授权并领取任务。交还后本人重新预览、确认和验证。</p>
  </details>;
}
