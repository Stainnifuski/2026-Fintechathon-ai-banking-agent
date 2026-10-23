// Playwright CLI run-code. Requires an authenticated independent 3100 browser.
// Browser fixtures block workspace reset and financial writes; never use a production session.
async page => {
 try {
 const checks=[];const check=(value,label)=>{if(!value)throw new Error(label);checks.push(label);};
 const json=(route,body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const writes=[]; let resets=0;
 // All writes remain in-browser fixtures; no task/ledger/model writes reach 8110.
 await page.route('**/api/v1/**',route=>{
   if(route.request().method()==='GET')return route.continue();
   if(route.request().url().endsWith('/wealth/workspace/reset')){resets++;return json(route,{});}
   writes.push(route.request().method()+' '+new URL(route.request().url()).pathname);return route.abort();
 });
 const sessions=[{session_id:'ui-session-a',title:'UI隔离：任务状态',created_at:'2026-10-22T00:00:00Z',updated_at:'2026-10-22T00:00:00Z'},{session_id:'ui-session-b',title:'UI隔离：历史对话',created_at:'2026-10-21T00:00:00Z',updated_at:'2026-10-21T00:00:00Z'}];
 let newCount=0;
 const task={chat_task_id:'ui-task-a',origin_request_id:'ui-origin',status:'COLLECTING',recipient:'UI隔离收款人',form:{recipient:'UI隔离收款人',amount_yuan:'100.00',payer_account_id:'',note:''},missing_fields:['收款账户'],candidates:[]};
 const verifyTask={...task,chat_task_id:'ui-task-mfa',recipient:'UI隔离验证',form:{...task.form,recipient:'UI隔离验证'},missing_fields:[],status:'AWAITING_MFA',task:{task_id:'ui-bank-task',phase:'WAITING_MFA',state_version:1,plan_version:1,message:'UI隔离验证状态，不执行业务',allowed_actions:['verify'],operation:{amount:100},outcome:null}};
 const wealth={task_id:'ui-wealth',version:1,nova_session_id:'ui-session-a',request_id:'ui-origin',context_id:'ui-context',closed:false,command:{kind:'clarify',products:[],amounts_yuan:[],redemption_input:'units',redemption_mode:'standard',period:'month',question:'UI隔离：补充理财需求，未生成交易'}};
 await page.route('**/api/v1/bank/nova/conversations',route=>{if(route.request().method()==='POST'){newCount++;return json(route,{...sessions[0],session_id:'ui-new',title:'UI新对话'});}return json(route,sessions);});
 await page.route('**/api/v1/bank/nova/conversations/*',route=>{const id=route.request().url().split('/').pop();return json(route,{...sessions.find(s=>s.session_id===id),messages:[{role:'assistant',content:id==='ui-session-a'?'UI隔离任务卡，仅验证展示。':'UI隔离历史回复',request_id:'ui-origin'}]});});
 await page.route('**/api/v1/nova/task-sessions/*',route=>json(route,{session_id:route.request().url().split('/').pop(),session_version:1,tasks:route.request().url().endsWith('ui-session-a')?[task,verifyTask]:[]}));
 await page.route('**/api/v1/nova/wealth-sessions/*',route=>json(route,route.request().url().endsWith('ui-session-a')?[wealth]:[]));
 let release;let gate;let fail=false;const requests=[];
 await page.route('**/api/v1/nova/chat/stream',async route=>{
   const payload=route.request().postDataJSON();requests.push(payload);
   if(gate)await gate;
   if(fail)return json(route,{detail:{code:'UI_FIXTURE_503',message:'UI隔离失败，请重试'}},503);
   return route.fulfill({status:200,contentType:'text/event-stream',body:'data: '+JSON.stringify({type:'delta',text:'UI隔离响应'})+'\n\ndata: '+JSON.stringify({type:'done',reply:'UI隔离请求完成',mode:'offline',tool_calls:[],session_id:'ui-session-a',session_version:1,tasks:[task,verifyTask],wealth_cards:[wealth]})+'\n\n'});
 });
 await page.setViewportSize({width:1440,height:1000});await page.reload();
 const card=page.getByRole('article',{name:'转账任务：UI隔离收款人'});await card.waitFor();
 // section has an accessible name; use its stable existing class for DOM continuity.
 await page.locator('.nova-wealth-card').waitFor();
 await card.getByLabel('金额（人民币元）').fill('234.56');
 await card.getByLabel('备注（可选）').fill('未保存的卡片输入');
 await page.getByRole('textbox',{name:'转账验证码'}).fill('123456');
 await page.locator('.nova-wealth-card').getByRole('button',{name:'折叠',exact:true}).click();
 const transferNode=await card.elementHandle();const wealthNode=await page.locator('.nova-wealth-card').elementHandle();
 const input=page.getByRole('textbox',{name:'向 NOVA 提问'});await input.fill('UI未发草稿');
 const noRequests=[];const observe=req=>{if(/nova|conversations|workspace\/reset/.test(req.url()))noRequests.push(req.url());};page.on('request',observe);
 const toggle=async()=>{const full=await page.locator('.nova-expanded').count()>0;await page.locator('#nova-toggle').click();await page.locator(full?'.home-workspace.nova-docked':'.home-workspace.nova-expanded').waitFor();await page.waitForFunction(()=>document.getAnimations().every(a=>a.playState!=='running'));};
 await toggle();await toggle();page.off('request',observe);
 check(noRequests.length===0,'任务存在时模式切换无新增请求');
 check(await input.inputValue()==='UI未发草稿','任务场景未发草稿连续');
 check(await card.getByLabel('金额（人民币元）').inputValue()==='234.56','转账卡未保存金额连续');
 check(await card.getByLabel('备注（可选）').inputValue()==='未保存的卡片输入','转账卡未保存备注连续');
 check(await page.getByRole('textbox',{name:'转账验证码'}).inputValue()==='123456','验证码输入连续且没有提交');
 check(await transferNode.evaluate(node=>node===document.querySelector('.nova-transfer-card')),'转账卡没有重挂载');
 check(await wealthNode.evaluate(node=>node===document.querySelector('.nova-wealth-card')),'理财卡没有重挂载');
 check(await page.locator('.nova-wealth-card').getByRole('button',{name:'展开',exact:true}).count()===1,'理财卡折叠状态连续');
 gate=new Promise(resolve=>{release=resolve;});
 const requestStarted=page.waitForRequest('**/api/v1/nova/chat/stream');
 await input.fill('UI隔离在途请求');await page.getByRole('button',{name:'发送',exact:true}).click();await requestStarted;
 check(await input.isDisabled(),'在途请求保持忙状态');
 await toggle();await toggle();check(requests.length===1,'在途切换没有重复发送');
 check(await input.isDisabled(),'在途切换没有解除请求锁');
 release();gate=null;await input.waitFor({state:'visible'});await page.getByText('UI隔离请求完成',{exact:true}).waitFor();
 check(requests[0].session_id==='ui-session-a','在途请求沿用原会话');
 check(await card.getByLabel('金额（人民币元）').inputValue()==='234.56','响应完成后未保存卡片仍保留');
 // 503 retry keeps the original request identity.
 fail=true;await input.fill('UI隔离失败重试');await page.getByRole('button',{name:'发送',exact:true}).click();await page.getByRole('alert').filter({hasText:'UI隔离失败'}).waitFor();
 check(await input.inputValue()==='UI隔离失败重试','失败恢复原输入');
 const failedId=requests[1].request_id;await toggle();fail=false;await page.getByRole('button',{name:'发送',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('.agent-compose input').disabled);
 check(requests[2].request_id===failedId,'切换后失败重试沿用原请求号');
 // Body scroll never displaces the composer.
 const composerBefore=await page.locator('.agent-compose').boundingBox();await page.locator('.agent-body').evaluate(node=>{node.scrollTop=0;});const composerAfter=await page.locator('.agent-compose').boundingBox();
 check(composerBefore.y===composerAfter.y,'对话独立滚动且输入位置固定');
 await page.screenshot({path:'output/playwright/home22-fixture-tasks-docked.png'});
 await page.getByRole('button',{name:'＋ 新建/历史对话',exact:true}).click();await page.getByRole('button',{name:'UI隔离：历史对话',exact:true}).click();await page.getByText('UI隔离历史回复',{exact:true}).waitFor();checks.push('侧栏可切换真实历史接口形状');
 await toggle();await page.getByRole('button',{name:'＋ 新建/历史对话',exact:true}).click();await page.getByRole('button',{name:'UI隔离：任务状态',exact:true}).click();await card.waitFor();checks.push('大屏可恢复原会话与任务引用');
 await page.getByRole('button',{name:'＋ 新建/历史对话',exact:true}).click();await page.getByRole('button',{name:'＋ 新建对话',exact:true}).click();await page.getByRole('heading',{name:'今天想办理什么？'}).waitFor();
 check(newCount===1,'显式新建仅创建一次');
 check(writes.length===0,'零金融或其他未隔离写入');check(errors.length===0,'任务与流式场景无未捕获异常');
 await page.unrouteAll({behavior:'wait'});
 return {checks,requests:requests.map(r=>({session:r.session_id,request_id:r.request_id})),writes,resets,errors};
 } finally {
  await page.unrouteAll({behavior:'wait'});
  await page.route('**/api/v1/wealth/workspace/reset',route=>route.fulfill({status:200,contentType:'application/json',body:'{}'}));
  await page.setViewportSize({width:1440,height:1000});
 }
}
