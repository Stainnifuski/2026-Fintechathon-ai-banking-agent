// Real formal UI, saved public dataset, canonical A and bank B; no interception/NLP.
import {pathToFileURL} from 'node:url';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
const {chromium}=await import(pathToFileURL(process.env.PORTFOLIO_PLAYWRIGHT_MODULE).href);
const out=process.env.WEALTH_JOINT_EVIDENCE||'.deps/real_fund_close/browser';await mkdir(out,{recursive:true});
const browser=await chromium.launch({executablePath:process.env.PORTFOLIO_BROWSER_EXE,headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1100}});page.setDefaultTimeout(30000);
const checks=[],runtime=[],network=[],http=[];let token;
page.on('pageerror',e=>runtime.push(String(e)));page.on('requestfailed',r=>network.push({path:new URL(r.url()).pathname,error:r.failure()?.errorText}));
page.on('response',async r=>{const path=new URL(r.url()).pathname;if(!path.startsWith('/api'))return;let v;try{v=await r.json();}catch{return;}if(path.endsWith('/auth/login')&&r.ok())token=v.access_token;http.push({path,http:r.status(),action:v?.action,code:v?.detail?.code,status:v?.status});});
const mark=(name,result)=>checks.push({name,result});
const request=async(method,path,body)=>{const r=await page.request.fetch('http://127.0.0.1:3013'+path,{method,headers:{Authorization:'Bearer '+token},...(body?{data:body}:{})});return {http:r.status(),body:await r.json()};};
const waitAction=async(name,click)=>{const p=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/wealth/actions')&&r.request().postDataJSON()?.action===name);p.catch(()=>{});await click();const r=await p;const v=await r.json();assert.equal(r.status(),200,JSON.stringify(v));assert.equal(v.status,'ok',JSON.stringify(v));await page.waitForTimeout(400);return v;};
const sms=async(region,input,submit)=>{
 await region.getByRole('button',{name:'短信验证码',exact:true}).click();await region.getByRole('button',{name:'获取短信验证码',exact:true}).click();await region.getByRole('button',{name:'查看模拟短信',exact:true}).click();
 await region.locator('.hint').filter({hasText:/验证码 \d{6}/}).waitFor();const text=await region.innerText();const proof=/验证码 (\d{6})/.exec(text);assert.ok(proof,'mock SMS not actually received');await region.getByLabel(input,{exact:true}).fill(proof[1]);await region.getByRole('button',{name:submit,exact:true}).click();
};
try{
 await page.goto('http://127.0.0.1:3013/');
 const source=await readFile('tests/browser/wealth_joint_server.py','utf8');const credential=/hash_password\('([^']+)'\)/.exec(source);assert.ok(credential);
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
 }else{assert.equal(existingAssessment.body.capacity_level,4);mark('preserved real confirmed R4 assessment',{thisRun:false,evidence:'preserved personal assessment; see this round questionnaire evidence separately, never a default risk result'});}
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
 const plan=await waitAction('plan_portfolio',()=>panel.getByRole('button',{name:'继续核对所选产品',exact:true}).click());assert.equal(plan.data.selected_positions.length,7);assert.ok(plan.data.selected_positions.every(p=>samples.includes(p.product_id)));mark('J01 seven product Plan through actual A',plan.data.selected_positions.map(p=>({code:p.product_id,fen:p.target_fen})));
 const review=panel.getByRole('region',{name:'整体配置核对',exact:true});await review.waitFor();await waitAction('revise_portfolio',()=>review.getByRole('button',{name:'校验所选产品与比例',exact:true}).click());
 const prepared=await waitAction('prepare_purchase',()=>review.getByRole('button',{name:'核对差额与准备预览',exact:true}).click());assert.equal(prepared.data.execution_plan.items.length,7);mark('J07 actual seven delta preview no cash hold yet',prepared.data.actions.length);
 const execution=page.getByRole('region',{name:'整份方案可信操作',exact:true});await execution.getByRole('button',{name:'预览整份申购',exact:true}).click();const bankPreview=page.getByRole('region',{name:'整份银行预览',exact:true});await bankPreview.getByRole('checkbox').check();await bankPreview.getByRole('button',{name:'一次确认整份方案',exact:true}).click();
 await sms(bankPreview,'整份方案验证码','一次验证并提交整份方案');await panel.getByText(/银行操作已返回/).waitFor();
 const assets=await request('GET','/api/v1/wealth/assets');assert.equal(assets.body.orders.filter(o=>o.status!=='CANCELLED').length,7);assert.equal(assets.body.lots.length,0);assert.ok(assets.body.cash[0].held_fen>0);mark('J08/J09 actual one confirmation + unified SMS, seven held orders',{orders:7,held_fen:assets.body.cash[0].held_fen});
 await panel.getByRole('button',{name:'查询我的订单',exact:true}).click();const orderTable=panel.getByRole('region',{name:'独立订单表',exact:true});await orderTable.getByLabel('订单每页行数').selectOption('5');await orderTable.getByLabel(/勾选订单/).first().check();await orderTable.getByRole('button',{name:'下一页',exact:true}).click();assert.match(await orderTable.innerText(),/已选 1 行/);mark('J11 orders pagination selection retained',true);
 await orderTable.getByRole('button',{name:'上一页',exact:true}).click();await orderTable.getByRole('button',{name:'清空勾选',exact:true}).click();
 const advanced=await request('POST','/api/v1/joint-test/advance',{days:1});assert.equal(advanced.http,200);await panel.getByRole('button',{name:'查询我的持仓',exact:true}).click();const positions=panel.getByRole('region',{name:'独立持仓表',exact:true});await positions.getByLabel('持仓每页行数').selectOption('5');await positions.getByLabel(/勾选持仓 /).first().check();await positions.getByRole('button',{name:'下一页',exact:true}).click();assert.match(await positions.innerText(),/已选 1 行/);mark('J11 actual seven settled holdings pagination',true);
 await page.screenshot({path:out+'/seven-real-products.png',fullPage:true});
 const after=await request('GET','/api/v1/wealth/assets');assert.equal(after.body.orders.filter(x=>x.status==='SETTLED').length,7);assert.equal(after.body.lots.length,7);mark('J19 unique actual ledger settlement',{orders:7,lots:7,cash:after.body.cash[0].balance_fen});
 assert.equal(http.filter(h=>h.path.includes('/messages')).length,0);mark('J12 no new NLP calls',0);assert.equal(runtime.length,0);assert.equal(network.length,0);
}catch(e){mark('failure',{message:String(e)});await writeFile(out+'/page.txt',await page.locator('body').innerText());await page.screenshot({path:out+'/failure.png',fullPage:true});throw e;}
finally{await writeFile(out+'/browser_results.json',JSON.stringify({source:'formal-real-public-paper-A-B',checks,http,runtime,network},null,2));await browser.close();console.log(JSON.stringify({checks:checks.length,runtime:runtime.length,network:network.length}));}



