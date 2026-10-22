export function navPerformance(points:{date:string;nav:string}[]){
  if(points.length<2||points.some(p=>!/^\d{4}-\d{2}-\d{2}$/.test(p.date)||!Number.isFinite(Number(p.nav))||Number(p.nav)<=0))return [];
  const base=Number(points[0].nav);
  return points.map(p=>({date:p.date,percent:(Number(p.nav)/base-1)*100}));
}
export function performanceLabel(value:number){
  const rounded=Number(value.toFixed(2));
  return `${rounded>0?'+':''}${rounded.toFixed(2)}%`;
}
