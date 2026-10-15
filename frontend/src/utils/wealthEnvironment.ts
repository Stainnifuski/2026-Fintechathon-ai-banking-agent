/** Formal wealth entry only: existing owned competition contexts, no initialization. */
export interface WealthContexts {
  selected:string; version:number;
  items:{context_id:string;label:string;mode:string;dataset_key?:string;run_state?:string}[];
}
export function competitionEnvironment(value:WealthContexts):WealthContexts {
  const items=value.items.filter(c=>c.context_id!=='legacy-v1' && c.dataset_key?.startsWith('real-public-paper-v1:') && (!c.run_state || c.run_state==='ACTIVE'));
  const selected=items.find(c=>c.context_id===value.selected) ?? items[items.length-1];
  if(!selected)throw new Error('当前账户尚无可用比赛理财环境，请按准备指南初始化；不会回退到原银行账本。');
  return {...value,selected:selected.context_id,items};
}
