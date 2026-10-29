/** Keep legacy task anchors for card placement, without rendering a chat bubble. */
export function showNovaMessage(entry:{role:string;content:string;request_id?:string;streaming?:boolean}):boolean{
  if(entry.role!=='assistant'||entry.streaming)return true;
  if(!entry.content.trim())return false;
  return !(entry.request_id?.startsWith('action-')&&entry.content==='已更新操作，请核对下方卡片。');
}
