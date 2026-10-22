/** Candidate guidance only. The server independently computes and validates trades. */
export function incrementalBudget(level:number,total:number,held:number[],reserve:number,available:number,unavailable:number,model:number[],fee='0'){
  if(![1,2,3,4,5].includes(level)||held.length!==3||[total,reserve,available,unavailable,...held,...model].some(n=>!Number.isSafeInteger(n)||n<0)||reserve>available||held.reduce((a,b)=>a+b,0)+available+unavailable!==total)return null;
  const T=BigInt(total),H=held.map(BigInt),M=model.map(BigInt),fixed=BigInt(reserve+unavailable);
  const ceil=(a:bigint,b:bigint)=>(a+b-1n)/b;
  const max=(a:bigint,b:bigint)=>a>b?a:b,min=(a:bigint,b:bigint)=>a<b?a:b;
  let targets:bigint[],sales:bigint[];
  if(level===1){targets=[T,0n,0n];sales=[0n,H[1],H[2]];}
  else{
    const p=BigInt(level-1),den=4n,liquidMin=max(fixed+H[0],ceil(T,10n)),n=T-liquidMin;
    let sellG=max(0n,H[2]-p*n/den);const sellS=max(0n,H[1]+H[2]-n-sellG);
    if(!/^\d+(\.\d+)?$/.test(fee))return null;
    const [whole,decimal='']=fee.split('.'),rateDen=10n**BigInt(decimal.length),rate=BigInt(whole+decimal);
    if(rate>=rateDen)return null;
    if((sellG||sellS)&&rate)sellG=min(H[2],sellG+ceil(p*rate*(sellG+sellS),den*rateDen-p*rate));
    const keepS=H[1]-sellS,keepG=H[2]-sellG;
    const cashMax=min(T-keepS-keepG,T-ceil(keepG*den,p));
    const liquid=max(liquidMin,min(max(M[0],fixed+H[0]),cashMax)),noncash=T-liquid;
    const desired=M[1]+M[2]?noncash*M[2]/(M[1]+M[2]):0n;
    const growth=max(keepG,min(desired,min(p*noncash/den,noncash-keepS)));
    targets=[liquid,noncash-growth,growth];sales=[0n,sellS,sellG];
  }
  const purchases=targets.map((v,i)=>v-H[i]+sales[i]-(i===0?fixed:0n));
  return {targets:targets.map(Number),sales:sales.map(Number),purchases:purchases.map(Number)};
}
