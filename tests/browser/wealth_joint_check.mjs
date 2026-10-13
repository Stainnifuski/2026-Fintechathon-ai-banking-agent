// Actual formal UI + A provider + B bank. No recorded Plan or response interception.
import {pathToFileURL} from 'node:url';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
const {chromium}=await import(pathToFileURL(process.env.PORTFOLIO_PLAYWRIGHT_MODULE).href);
const out=process.env.WEALTH_JOINT_EVIDENCE||'.deps/joint_validation/browser';await mkdir(out,{recursive:true});
const browser=await chromium.launch({executablePath:process.env.PORTFOLIO_BROWSER_EXE,headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1100}});page.setDefaultTimeout(30000);
const runtime=[],network=[],http=[],checks=[];let token=null;
page.on('pageerror',e=>runtime.push(String(e)));page.on('requestfailed',r=>network.push({path:new URL(r.url()).pathname,error:r.failure()?.errorText}));
page.on('response',async r=>{const path=new URL(r.url()).pathname;if(!path.startsWith('/api'))return;let v;try{v=await r.json();}catch{return;}if(path.endsWith('/auth/login')&&r.ok())token=v.access_token;const action=path.endsWith('/wealth/actions')?r.request().postDataJSON()?.action:v?.action;http.push({path,method:r.request().method(),http:r.status(),code:v?.detail?.code,action,status:v?.status,version:v?.version});});
const mark=(name,result)=>checks.push({name,result});
const pause=()=>page.waitForTimeout(1800);
const request=async(method,path,body)=>{const r=await page.request.fetch('http://127.0.0.1:3013'+path,{method,headers:{Authorization:'Bearer '+token},...(body?{data:body}:{})});return {http:r.status(),body:await r.json()};};
try{
 await page.goto('http://127.0.0.1:3013/');
 const source=await readFile('tests/browser/wealth_joint_server.py','utf8');const credential=/hash_password\('([^']+)'\)/.exec(source);assert.ok(credential);
 await page.getByLabel('手机号',{exact:true}).fill('13800000001');await page.getByLabel('密码',{exact:true}).fill(credential[1]);
 await page.getByRole('button',{name:'登录',exact:true}).click();await page.getByRole('navigation',{name:'主要功能'}).getByRole('button',{name:/理财$/}).waitFor();assert.ok(token);mark('normal public login',200);
 const home=await request('GET','/api/v1/wealth/asset-summary');assert.equal(home.http,200);assert.equal(home.body.total_assets_fen,2_000_000);mark('shared total before request',home.body);
 await page.getByRole('navigation',{name:'主要功能'}).getByRole('button',{name:/理财$/}).click();await pause();
 await page.locator('summary').filter({hasText:/^本人风险评估$/}).click();
 await page.getByText('填写／重新评估（本人回答，单日最多2次）',{exact:true}).click();
 const questionnaire=await request('GET','/api/v1/wealth/questionnaire');assert.equal(questionnaire.http,200);
 for(const q of questionnaire.body.questions)await page.getByRole('combobox',{name:q.title,exact:true}).selectOption('2');
 await page.getByRole('checkbox',{name:'我是成年、完全民事行为能力的模拟客户，以上由本人回答'}).check();
 await page.getByRole('button',{name:'提交本人答案',exact:true}).click();await page.getByRole('button',{name:'本人确认上述评估结果并继续原需求',exact:true}).waitFor();
 await page.getByRole('button',{name:'本人确认上述评估结果并继续原需求',exact:true}).click();await pause();
 const assessment=await request('GET','/api/v1/wealth/assessments/current');assert.equal(assessment.body.capacity_level,4);assert.equal(assessment.body.confirmed,true);mark('actual nine-question UI and personal confirmation',{capacity:4,confirmed:true});
 const snapshot=await request('GET','/api/v1/wealth/portfolio/snapshot');assert.equal(snapshot.http,200);assert.equal(snapshot.body.snapshot_id,home.body.snapshot_id);assert.equal(snapshot.body.asset_scope_id,home.body.asset_scope_id);assert.equal(snapshot.body.context_epoch,null);mark('A snapshot same B scope/snapshot',snapshot.body);
 const independent=await request('POST','/api/v1/wealth/portfolio/recommendations',{context_id:'legacy-v1',filter:{mode:'general'}});assert.equal(independent.http,200);assert.ok(!independent.body.reason_codes.includes('ASSESSMENT_CURRENT_VERSION_UNPROVEN'));mark('A05 no Plan, current profile proof',{http:200,status:independent.body.status,count:independent.body.items.length,reasons:independent.body.reason_codes});
 await page.getByText('直接选择操作（无需聊天）',{exact:true}).click();await pause();
 const panel=page.getByRole('region',{name:'统一手动操作'});
 await panel.getByRole('button',{name:'产品推荐',exact:true}).click();await pause();mark('formal Action recommendation UI',await panel.innerText());
 const planResponse=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/wealth/actions')&&r.request().postDataJSON()?.action==='plan_portfolio',{timeout:60000});
 await panel.getByRole('button',{name:'理财规划',exact:true}).click();const actualPlan=await planResponse;const planBody=await actualPlan.json();await pause();
 mark('formal A plan business outcome',{http:actualPlan.status(),status:planBody.status,code:planBody.detail?.code,selected_count:planBody.data?.selected_positions?.length,basis:planBody.data?.basis_fen,portfolio_id:planBody.data?.portfolio_id,version:planBody.data?.version});
 assert.equal(actualPlan.status(),200,JSON.stringify(planBody));assert.equal(planBody.status,'ok');
 if(actualPlan.ok()&&planBody.status==='ok'){await page.getByRole('region',{name:'整体配置',exact:true}).waitFor();assert.equal(await page.getByRole('region',{name:'整体配置',exact:true}).count(),1);mark('one actual A chart in formal B UI','visible');}
 if(actualPlan.ok()&&planBody.status==='ok'){
  const p=planBody.data;const url='/api/v1/wealth/portfolio/plans/'+p.portfolio_id;
  const same=await request('GET',url+'?context_id='+p.context_id);assert.equal(same.http,200);assert.equal(same.body.version,p.version);mark('Action Plan visible through direct A GET',{portfolio_id:p.portfolio_id,version:p.version});
  const revised=await request('PUT',url,{portfolio_id:p.portfolio_id,expected_version:p.version,snapshot_id:p.snapshot_id,context_id:p.context_id,items:[],revision_kind:'adjustment',cash_residual_fen:p.basis_fen});
  assert.equal(revised.http,200,JSON.stringify(revised.body));assert.equal(revised.body.version,p.version+1);mark('direct A revision in same store',{version:revised.body.version,cash_residual_fen:revised.body.cash_residual_fen});
 }
 const plans=http.filter(h=>h.path.endsWith('/wealth/actions')&&h.action==='plan_portfolio');mark('formal plan result',plans.at(-1));
 await page.screenshot({path:out+'/formal-plan.png',fullPage:true});
 const after=await request('GET','/api/v1/wealth/assets');assert.equal(after.body.cash[0].balance_fen,2_000_000);assert.equal(after.body.orders.length,0);assert.equal(after.body.lots.length,0);mark('recommend/configure does not trade',{cash:2_000_000,orders:0,lots:0});
 const bad=await request('POST','/api/v1/wealth/portfolio/plans',{context_id:'legacy-v1',risk_level:5});assert.equal(bad.http,422);mark('client risk rejected',422);
 for(const name of ['转账与收款','账单洞察','卡片','订阅','生活服务']){const button=page.getByRole('button',{name:new RegExp(name)}).first();if(await button.count()){await button.click();await pause();mark('existing navigation '+name,'loaded');}}
 if(planBody.status==='ok'){
  // Navigation closes the chat window, not necessarily the structured Action
  // session. Exercise that specific related session's real HTTP boundary.
  const closed=await request('POST','/api/v1/wealth/sessions/'+planBody.session_id+'/close',{});assert.equal(closed.http,200);
  const old=await request('GET','/api/v1/wealth/portfolio/plans/'+planBody.data.portfolio_id+'?context_id=legacy-v1');assert.equal(old.http,409);assert.equal(old.body.detail.code,'PORTFOLIO_SESSION_CLOSED');mark('actual related Action session HTTP close rejects old Plan',{http:old.http,code:old.body.detail.code});
 }
 assert.equal(http.filter(h=>h.path.includes('/messages')||h.path.startsWith('/api/v1/nova/messages')).length,0);mark('new structured workflow model requests',0);
 await page.screenshot({path:out+'/final-page.png',fullPage:true});
 assert.equal(runtime.length,0);assert.equal(network.length,0);
}catch(e){mark('failure',{message:String(e)});await page.screenshot({path:out+'/failure.png',fullPage:true});throw e;}
finally{await writeFile(out+'/browser_results.json',JSON.stringify({source:'formal-ui-real-A-and-B-isolated-bank',checks,http,runtime,network},null,2));await browser.close();console.log(JSON.stringify({checks:checks.length,runtime:runtime.length,network:network.length}));}
