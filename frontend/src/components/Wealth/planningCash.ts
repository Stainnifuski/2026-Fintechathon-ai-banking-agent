export type CashDraft = {field:'reserve'|'investment'|'total';text:string};
export function availablePlanningCash(accounts:{account_type:string;available_fen:number}[]):number{
  return accounts.filter(a=>a.account_type!=='credit').reduce((sum,a)=>sum+a.available_fen,0);
}
export function planningCash(available:number,draft:CashDraft,holdings=0):{reserve:number;investment:number}|null{
  if(!Number.isSafeInteger(available)||available<0||!/^\d+(\.\d{1,2})?$/.test(draft.text))return null;
  const [whole,part='']=draft.text.split('.');
  const value=BigInt(whole)*100n+BigInt(part.padEnd(2,'0'));
  if(!Number.isSafeInteger(holdings)||holdings<0)return null;
  if(draft.field==='total'){
    const increment=value-BigInt(holdings);
    if(increment<0n||increment>BigInt(available))return null;
    return {reserve:available-Number(increment),investment:Number(increment)};
  }
  if(value>BigInt(available))return null;
  const entered=Number(value);
  return draft.field==='reserve'?{reserve:entered,investment:available-entered}:{reserve:available-entered,investment:entered};
}
