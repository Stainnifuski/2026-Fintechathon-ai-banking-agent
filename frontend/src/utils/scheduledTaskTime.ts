/** datetime-local has no timezone: interpret it in the browser's local zone. */
export function localDateTimeInput(value:Date):string{
  const two=(n:number)=>String(n).padStart(2,'0');
  return `${value.getFullYear()}-${two(value.getMonth()+1)}-${two(value.getDate())}T${two(value.getHours())}:${two(value.getMinutes())}`;
}

export function scheduledInputInstant(value:string):string{
  const parsed=new Date(value);
  if(!Number.isFinite(parsed.getTime())||localDateTimeInput(parsed)!==value.slice(0,16)){
    throw new Error('执行时间无效，请重新选择本地时间。');
  }
  return parsed.toISOString();
}

export function localTimeLabel(value:string):string{
  const parsed=new Date(value);
  return Number.isFinite(parsed.getTime())?new Intl.DateTimeFormat('zh-CN',{
    year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',timeZoneName:'short',
  }).format(parsed):'时间未知';
}

export function localZoneLabel():string{
  return Intl.DateTimeFormat().resolvedOptions().timeZone||'设备本地时区';
}
