// Verify production presentation and request payloads, with all APIs intercepted.
// This does not prove live-model routing or bank execution.
import {pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';
const {chromium}=await import(pathToFileURL(process.env.PORTFOLIO_PLAYWRIGHT_MODULE).href);
const browser=await chromium.launch({executablePath:process.env.PORTFOLIO_BROWSER_EXE,headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const requests=[],errors=[];let failure=0;
page.on('pageerror',e=>errors.push(String(e)));
await page.addInitScript(()=>{
 localStorage.setItem('token','isolated-placeholder');
 localStorage.setItem('bank-user',JSON.stringify({user_id:'UI-ISOLATED',name:'隔离验证',phone:'00000000000'}));
});
const conversation={session_id:'isolated',title:'隔离验证',created_at:'2026-10-30T12:00:00Z',updated_at:'2026-10-30T12:00:00Z'};
await page.route('**/api/**',async route=>{
 const r=route.request(),p=new URL(r.url()).pathname;
 const json=data=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
 if(p.endsWith('/security/status'))return json({locks:[],trade_locked:false,message:''});
 if(p.endsWith('/system/time'))return json({server_time_utc:new Date().toISOString(),business_timezone:'Asia/Shanghai'});
 if(p.endsWith('/voice/status'))return json({ready:false,message:'隔离',max_seconds:30,max_bytes:1000});
 if(p.includes('/bills/')||p.endsWith('/asset-summary'))return route.fulfill({status:503,contentType:'application/json',body:'{}'});
 if(p.endsWith('/nova/chat/stream')){
  requests.push(JSON.parse(r.postData()));
  if(failure)return route.fulfill({status:failure,contentType:'application/json',body:JSON.stringify({detail:failure===403?{code:'PROMPT_INJECTION_BLOCKED',message:'内部错误不直接展示'}:{code:'INTERNAL_ERROR',message:'内部错误'}})});
  return route.fulfill({status:200,contentType:'text/event-stream',body:'data: '+JSON.stringify({type:'done',reply:'隔离查询'+requests.length+'已返回，未产生业务操作。',mode:'model',tool_calls:[],session_id:'isolated'})+'\n\n'});
 }
 if(p.endsWith('/bank/nova/conversations'))return json(r.method()==='POST'?conversation:[]);
 if(p.endsWith('/bank/nova/conversations/isolated'))return json({...conversation,messages:[]});
 if(p.includes('/execution-queues/'))return json(null);
 if(p.includes('/task-sessions/'))return json({session_id:'isolated',session_version:1,tasks:[],collection_cards:[]});
 if(p.endsWith('/totp/status'))return json({enabled:false});
 return json(r.method()==='GET'?[]:{});
});
try{
 await page.goto('http://127.0.0.1:3000/');
 const input=page.getByRole('textbox',{name:'向 NOVA 提问',exact:true});await input.waitFor();
 for(const [label,text]of [
  ['账单与订阅一起看','先查看本月账单，再查看当前订阅及预计月费。'],
  ['日程与扣费一起看','查看明天的日程和即将扣款的订阅，分别列出结果。'],
 ]){
  const more=page.getByRole('button',{name:/更多示例/});if(await more.count())await more.click();
  const expected=requests.length+1;
  await page.getByRole('button',{name:'问：'+label,exact:true}).click();
  await page.getByText('隔离查询'+expected+'已返回，未产生业务操作。',{exact:true}).waitFor();
  assert.equal(requests.at(-1).message,text);
 }
 failure=500;await input.fill('查询测试');await page.getByRole('button',{name:'发送',exact:true}).click();
 await page.locator('.nova-error').filter({hasText:'结果尚未核实'}).waitFor();
 assert.doesNotMatch(await page.locator('.nova-error').innerText(),/尚未执行|INTERNAL_ERROR|请重新发送/);
 const original=requests.at(-1).request_id;
 failure=403;await page.getByRole('button',{name:'发送',exact:true}).click();
 await page.locator('.nova-error').filter({hasText:'请求未通过安全校验'}).waitFor();
 assert.equal(requests.at(-1).request_id,original,'presentation must not change uncertain-request identity');
 assert.doesNotMatch(await page.locator('.nova-error').innerText(),/PROMPT_INJECTION_BLOCKED|内部错误/);
 assert.deepEqual(errors,[]);
 console.log('PASS: two cross-domain examples preserve prompt payloads; unknown-result and security-denial copy differ; retry ID unchanged; APIs mocked');
}finally{await browser.close();}
