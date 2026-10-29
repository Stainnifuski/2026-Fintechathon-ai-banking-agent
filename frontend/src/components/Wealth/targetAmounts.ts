/** Validate explicit form values once for both Continue and purchase preparation. */
export function targetAmountState(ids:string[],amounts:Record<string,string>){
 let total=0n,valid=ids.length>0;
 for(const id of ids){
  const value=amounts[id]??'';
  if(!/^\d+(\.\d{1,2})?$/.test(value)){valid=false;continue;}
  const [whole,fraction='']=value.split('.');
  const fen=BigInt(whole)*100n+BigInt(fraction.padEnd(2,'0'));
  if(fen<=0n||fen>2000000000n)valid=false;
  total+=fen;
 }
 return {valid,totalFen:Number(total)};
}
