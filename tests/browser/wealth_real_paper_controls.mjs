// Real formal UI, saved public dataset, canonical A and bank B; no interception/NLP.
import {pathToFileURL} from 'node:url';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
const {chromium}=await import(pathToFileURL(process.env.PORTFOLIO_PLAYWRIGHT_MODULE).href);
const out=process.env.WEALTH_JOINT_EVIDENCE||'.deps/real_fund_close/browser-controls';await mkdir(out,{recursive:true});
const browser=await chromium.launch({executablePath:process.env.PORTFOLIO_BROWSER_EXE,headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1100}});page.setDefaultTimeout(30000);
const checks=[],runtime=[],network=[],http=[];let token;
page.on('pageerror',e=>runtime.push(String(e)));page.on('requestfailed',r=>network.push({path:new URL(r.url()).pathname,error:r.failure()?.errorText}));
page.on('response',async r=>{const path=new URL(r.url()).pathname;if(!path.startsWith('/api'))return;let v;try{v=await r.json();}catch{return;}if(path.endsWith('/auth/login')&&r.ok())token=v.access_token;http.push({path,http:r.status(),action:v?.action,code:v?.detail?.code,status:v?.status});});
const mark=(name,result)=>checks.push({name,result});
const request=async(method,path,body)=>{const r=await page.request.fetch('http://127.0.0.1:3014'+path,{method,headers:{Authorization:'Bearer '+token},...(body?{data:body}:{})});return {http:r.status(),body:await r.json()};};
const waitAction=async(name,click)=>{const p=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/wealth/actions')&&r.request().postDataJSON()?.action===name);p.catch(()=>{});await click();const r=await p;const v=await r.json();assert.equal(r.status(),200,JSON.stringify(v));assert.equal(v.status,'ok',JSON.stringify(v));await page.waitForTimeout(400);return v;};
const sms=async(region,input,submit)=>{
 await region.getByRole('button',{name:'短信验证码',exact:true}).click();await region.getByRole('button',{name:'获取短信验证码',exact:true}).click();await region.getByRole('button',{name:'查看模拟短信',exact:true}).click();
 await region.locator('.hint').filter({hasText:/验证码 \d{6}/}).waitFor();const text=await region.innerText();const proof=/验证码 (\d{6})/.exec(text);assert.ok(proof,'mock SMS not actually received');await region.getByLabel(input,{exact:true}).fill(proof[1]);await region.getByRole('button',{name:submit,exact:true}).click();
};
try{
 await page.goto('http://127.0.0.1:3014/');
 const source=await readFile('backend/app/services/bank/seed_service.py','utf8');const credential=/hash_password\(['"]([^'"]+)['"]\)/.exec(source);assert.ok(credential);
 await page.getByLabel('手机号',{exact:true}).fill('13800000001');await page.getByLabel('密码',{exact:true}).fill(credential[1]);await page.getByRole('button',{name:'登录',exact:true}).click();
 const nav=page.getByRole('navigation',{name:'主要功能'});await nav.getByRole('button',{name:/理财$/}).waitFor();assert.ok(token);
 const summary=await request('GET','/api/v1/wealth/asset-summary');assert.equal(summary.body.total_assets_fen,2000000);mark('J18 overview before planning',summary.body.total_assets_fen);
 await nav.getByRole('button',{name:/理财$/}).click();await page.waitForTimeout(1200);
 const existingAssessment=await request('GET','/api/v1/wealth/assessments/current');
 if(!existingAssessment.body?.confirmed){
  await page.locator('summary').filter({hasText:/^本人风险评估$/}).click();await page.getByText('填写／重新评估（本人回答，单日最多2次）',{exact:true}).click();
  const q=await request('GET','/api/v1/wealth/questionnaire');for(const question of q.body.questions)await page.getByRole('combobox',{name:question.title,exact:true}).selectOption('2');
  await page.getByRole('checkbox',{name:'我是成年、完全民事行为能力的模拟客户，以上由本人回答'}).check();await page.getByRole('button',{name:'提交本人答案',exact:true}).click();
  await page.getByRole('button',{name:'本人确认上述评估结果并继续原需求',exact:true}).click();await page.waitForTimeout(600);mark('actual nine-question personal R4 confirmation',{thisRun:true});
 }else{assert.equal(existingAssessment.body.capacity_level,4);mark('preserved real confirmed R4 assessment',{thisRun:false,evidence:'normal-service isolated preparation confirmed through actual assessment API; actual browser answers separately verified on joint bank'});}
 await page.getByText('直接选择操作（无需聊天）',{exact:true}).click();const panel=page.getByRole('region',{name:'统一手动操作'});await panel.getByRole('button',{name:'按条件推荐',exact:true}).waitFor();
 const first=await waitAction('recommend_products',()=>panel.getByRole('button',{name:'按条件推荐',exact:true}).click());assert.ok(first.data.items.length>0);mark('J04 real positive general recommendation',first.data.items.length);
 const samples=JSON.parse(await readFile('config/wealth/paper_products.json','utf8')).joint_samples;const picked=new Set();
 for(let batch=0;batch<18&&picked.size<samples.length;batch++){
  const cards=panel.locator('.picker-card');
  for(let i=0;i<await cards.count();i++){const card=cards.nth(i);const text=await card.innerText();const code=samples.find(c=>text.includes(c));if(code&&!picked.has(code)){await card.getByRole('checkbox').check();picked.add(code);}}
  if(picked.size===samples.length)break;
  const next=panel.getByRole('button',{name:/^换一批/}).first();assert.ok(await next.count(),'pool ended before chosen seven');await waitAction('recommend_products',()=>next.click());
 }
 assert.equal(picked.size,7);assert.equal(await panel.locator('.picker-selection li').count(),7);assert.equal(await panel.getByRole('button',{name:/切换C类|切回A类/}).count(),0);mark('J05 seven exact real codes across actual batches, no AC switching',[...picked]);
 // Optional detail/comparison must retain selection.
 const detailButton=panel.locator('.picker-selection').getByRole('button',{name:'查看已选产品详情',exact:true}).first();await detailButton.click();const detail=page.getByRole('dialog',{name:'产品详情',exact:true});await detail.getByText(/已发布净值/).waitFor();assert.match(await detail.innerText(),/真实公开基金资料/);await detail.getByRole('button',{name:'关闭',exact:true}).click();assert.equal(await panel.locator('.picker-selection li').count(),7);mark('J06 actual detail and selection retention',true);
 let plan=await waitAction('plan_portfolio',()=>panel.getByRole('button',{name:'继续核对所选产品',exact:true}).click());assert.equal(plan.data.selected_positions.length,7);assert.ok(plan.data.selected_positions.every(p=>samples.includes(p.product_id)));mark('J01 seven product Plan through actual A',plan.data.selected_positions.map(p=>({code:p.product_id,fen:p.target_fen})));
 const review=panel.getByRole('region',{name:'整体配置核对',exact:true});await review.waitFor();const validated=await waitAction('revise_portfolio',()=>review.getByRole('button',{name:'校验所选产品与比例',exact:true}).click());

 plan=validated;const before=plan.data;const slider=review.getByRole('slider',{name:'待提交进取比例',exact:true});await slider.focus();await slider.press('ArrowRight');
 const changed=await waitAction('revise_portfolio',()=>review.getByRole('button',{name:'应用本页调整',exact:true}).click());assert.equal(changed.data.cash_residual_fen,before.cash_residual_fen);assert.deepEqual(changed.data.buckets.map(b=>[b.min_ratio,b.max_ratio]),before.buckets.map(b=>[b.min_ratio,b.max_ratio]));assert.ok(changed.data.version>before.version);mark('J02 real UI slider preserves cash and original U',true);plan=changed;
 const prepared=await waitAction('prepare_purchase',()=>review.getByRole('button',{name:'核对差额与准备预览',exact:true}).click());const b=prepared.data.execution_plan;assert.equal(b.items.length,7);
 const preview=await request('POST',`/api/v1/wealth/plans/${b.plan_id}/preview`,{version:b.version});assert.equal(preview.http,200,JSON.stringify(preview.body));
 const current=plan.data;const revision=await request('PUT',`/api/v1/wealth/portfolio/plans/${current.portfolio_id}`,{portfolio_id:current.portfolio_id,expected_version:current.version,snapshot_id:current.snapshot_id,context_id:current.context_id,items:current.selected_positions,revision_kind:'adjustment',cash_residual_fen:current.cash_residual_fen});assert.equal(revision.http,200,JSON.stringify(revision.body));
 const confirmations=preview.body.items.map(p=>({authorization_id:p.authorization_id,digest:p.digest,disclosure_version:p.command.disclosure_version,accepted:true}));
 for(const [action,extra] of [['preview',{}],['confirm',{items:confirmations}],['verify',{method:'sms',proof:'000000'}],['execute',{}]]){const rejected=await request('POST',`/api/v1/wealth/plans/${b.plan_id}/${action}`,{version:b.version,...extra});assert.equal(rejected.http,409,JSON.stringify(rejected));assert.equal(rejected.body.detail.code,'STALE_PORTFOLIO');}mark('J07 old A revision rejects old preview/confirm/verify/execute before replacement prepare',true);
 const newPlan=revision.body;const fresh=await request('POST',`/api/v1/wealth/portfolio/plans/${newPlan.portfolio_id}/prepare`,{expected_version:newPlan.version,snapshot_id:newPlan.snapshot_id});assert.equal(fresh.http,200,JSON.stringify(fresh.body));
 let task=(await request('GET','/api/v1/wealth/tasks')).body.items.find(t=>t.plan_id===fresh.body.execution_plan_id);assert.ok(task);await page.getByRole('button',{name:'刷新银行事实',exact:true}).click();const controls=page.locator('summary').filter({hasText:/^未完成任务与人工接管/}).first().locator('..');await controls.locator(':scope > summary').click();const card=controls.locator('article.wealth-product').filter({hasText:task.task_id.slice(-8)});await card.waitFor();
 await card.getByRole('button',{name:'暂停该任务',exact:true}).click();await card.getByRole('button',{name:'继续该任务',exact:true}).click();await card.getByRole('button',{name:'申请人工接管',exact:true}).click();await card.getByText(/等待人工领取/).waitFor();mark('J14 customer UI pause/resume/handoff',true);
 assert.equal((await request('GET','/api/v1/wealth/operator/tasks')).http,403);mark('J15 customer cannot claim operator role',true);
 const context2=await browser.newContext({viewport:{width:1440,height:1100}});const op=await context2.newPage();op.setDefaultTimeout(30000);op.on('pageerror',e=>runtime.push(String(e)));await op.goto('http://127.0.0.1:3014/');await op.getByLabel('手机号',{exact:true}).fill('13800000002');await op.getByLabel('密码',{exact:true}).fill(credential[1]);await op.getByRole('button',{name:'登录',exact:true}).click();await op.getByRole('navigation',{name:'主要功能'}).getByRole('button',{name:/理财$/}).click();await op.locator('summary').filter({hasText:'操作员处理工作台（服务端角色）'}).click();const desk=op.locator('summary').filter({hasText:'操作员处理工作台（服务端角色）'}).locator('..');const owned=desk.locator('article.wealth-product').filter({hasText:task.task_id.slice(-8)});await owned.getByRole('button',{name:'领取任务',exact:true}).click();await owned.getByRole('button',{name:'读取原账户银行事实',exact:true}).click();const review2=owned.getByRole('region',{name:'整体配置核对',exact:true});await review2.waitFor();assert.equal(await review2.locator('tbody tr').count()>=7,true);await review2.getByRole('button',{name:'整理比例调整建议',exact:true}).click();await owned.getByRole('button',{name:'交还助手并重新核验',exact:true}).click();await desk.getByText(/已交还，任务保持暂停/).waitFor();mark('J14 actual authorized operator seven-code review/note/release no impersonated consent',true);await op.screenshot({path:out+'/operator-return.png',fullPage:true});await context2.close();
 await page.getByRole('button',{name:'刷新银行事实',exact:true}).click();await card.getByRole('button',{name:'继续该任务',exact:true}).click();const resumed=await request('GET',`/api/v1/wealth/tasks/${task.task_id}`);assert.equal(resumed.body.waiting_reason,'REVALIDATION_REQUIRED');const assets=await request('GET','/api/v1/wealth/assets');assert.equal(assets.body.orders.length,0);assert.equal(assets.body.cash[0].held_fen,0);mark('J14 return requires revalidation and no implicit order/hold',true);
 await page.screenshot({path:out+'/slider-and-old-preview.png',fullPage:true});assert.equal(runtime.length,0);assert.equal(network.length,0);
}catch(e){mark('failure',{message:String(e)});await writeFile(out+'/page.txt',await page.locator('body').innerText());await page.screenshot({path:out+'/failure.png',fullPage:true});throw e;}
finally{await writeFile(out+'/browser_results.json',JSON.stringify({source:'normal-main-real-public-paper-A-B',checks,http,runtime,network},null,2));await browser.close();console.log(JSON.stringify({checks:checks.length,runtime:runtime.length,network:network.length}));}
