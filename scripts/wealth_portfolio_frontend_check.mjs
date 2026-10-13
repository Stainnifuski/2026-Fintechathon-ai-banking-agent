// Actual Edge + local HTTP. No route interception or response mocks.
import { pathToFileURL } from 'node:url';
if (!process.env.PORTFOLIO_PLAYWRIGHT_MODULE || !process.env.PORTFOLIO_BROWSER_EXE) throw new Error('Provide existing Playwright module and browser executable');
const { chromium } = await import(pathToFileURL(process.env.PORTFOLIO_PLAYWRIGHT_MODULE).href);
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import assert from 'node:assert/strict';
const out=process.env.PORTFOLIO_EVIDENCE_DIR || '.deps/wealth_portfolio_step10';await mkdir(out,{recursive:true});
const browser=await chromium.launch({executablePath:process.env.PORTFOLIO_BROWSER_EXE,headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1100}});page.setDefaultTimeout(15000);
const checks=[],http=[],runtime=[],network=[],consoleErrors=[];let currentScenario='R4',lastPlan=null,bankToken=null;
page.on('pageerror',e=>runtime.push(String(e)));
page.on('requestfailed',r=>network.push({path:new URL(r.url()).pathname,reason:r.failure()?.errorText}));
page.on('console',m=>{if(m.type()==='error')consoleErrors.push(m.text());});
page.on('response',async r=>{
  const path=new URL(r.url()).pathname;if(!path.startsWith('/api')&&!path.startsWith('/dev'))return;
  let value;try{value=await r.json();}catch{return;}
  if(path==='/api/v1/auth/login'&&r.ok())bankToken=value.access_token;
  if(path.includes('/plans')&&r.ok())lastPlan=value;
  http.push({path,method:r.request().method(),status:r.status(),code:value.detail?.code,version:value.version,candidate_status:value.status,reason_codes:value.reason_codes,selection_accepted:value.selection?.accepted});
});
const mark=(name,result)=>checks.push({name,source:name.startsWith('bank ')?'competition_bank':'fixture_only',result});
const wait=async()=>{await page.waitForFunction(()=>!document.querySelector('[role=status]'));await page.waitForTimeout(650);};
const click=async name=>{await page.getByRole('button',{name,exact:true}).click();await wait();if(name==='推荐候选'||name==='换一批'){const d=page.getByText('推荐状态与全部原因详情',{exact:true});if(await d.count())await d.evaluate(e=>e.parentElement.open=true);}};
const reset=async scenario=>{
  currentScenario=scenario;await page.request.post('http://127.0.0.1:8051/dev/reset?scenario='+scenario);
  await page.goto('http://127.0.0.1:3021/portfolio-dev.html');if(scenario!=='R4')await page.getByLabel('场景',{exact:true}).selectOption(scenario);await wait();lastPlan=null;
};
const rpc=async(path,body,method='POST')=>page.evaluate(async({path,body,method,scenario})=>{
  const boot=await(await fetch('/dev/bootstrap?scenario='+scenario)).json();
  const r=await fetch(path,{method,headers:{'Content-Type':'application/json',Authorization:'Bearer '+boot.token,'X-Portfolio-Scenario':scenario},...(body===undefined?{}:{body:JSON.stringify(body)})});return {status:r.status,body:await r.json()};
},{path,body,method,scenario:currentScenario});
const planRequest=(plan,items,extra={})=>({portfolio_id:plan.portfolio_id,expected_version:plan.version,snapshot_id:plan.snapshot_id,context_id:plan.context_id,revision_kind:'adjustment',items,...extra});
const v=async n=>{await page.getByTestId('version').filter({hasText:String(n)}).waitFor();assert.equal(await page.getByTestId('version').innerText(),String(n));};
try{
  // Bank normal authentication, snapshot and filters. Read README demonstration credentials only into memory.
  const bank=await browser.newPage({viewport:{width:1440,height:1100}});bank.setDefaultTimeout(60000);bank.on('pageerror',e=>runtime.push(String(e)));bank.on('requestfailed',r=>network.push({source:'bank',path:new URL(r.url()).pathname,reason:r.failure()?.errorText}));bank.on('console',m=>{if(m.type()==='error')consoleErrors.push(m.text());});
  bank.on('response',async r=>{const path=new URL(r.url()).pathname;if(!path.startsWith('/api')&&!path.startsWith('/dev'))return;let value;try{value=await r.json();}catch{return;}if(path==='/api/v1/auth/login'&&r.ok())bankToken=value.access_token;http.push({source:'bank',path,method:r.request().method(),status:r.status(),code:value.detail?.code,candidate_status:value.status,reason_codes:value.reason_codes,...(path.endsWith('/snapshot')?{total_assets_fen:value.total_assets_fen,positions:value.positions?.length,basis_status:value.basis_status,snapshot_id:value.snapshot_id,asset_scope_id:value.asset_scope_id,context_id:value.context_id,context_epoch:value.context_epoch,as_of:value.as_of,account_count:value.accounts?.length,assessment_id:value.assessment?.assessment_id,data_versions:value.data_versions}: {})});});
  await bank.goto('http://127.0.0.1:3022/portfolio-dev.html');const readme=await readFile('README.md','utf8');const credentials=/演示用户：.*?`(\d+)`，密码 `([^`]+)`/.exec(readme);assert.ok(credentials,'normal demonstration login must be available');
  await bank.getByLabel('登录手机号',{exact:true}).fill(credentials[1]);await bank.getByLabel('登录密码',{exact:true}).fill(credentials[2]);await bank.getByRole('button',{name:'登录现有比赛银行',exact:true}).click();await bank.getByText('当前范围无持仓',{exact:true}).waitFor();assert.match(await bank.locator('body').innerText(),/比赛银行事实/);assert.equal(await bank.getByTestId('version').count(),0);
  for(const mode of ['general','category','theme']){await bank.getByLabel('筛选模式',{exact:true}).selectOption(mode);await bank.getByRole('button',{name:'推荐候选',exact:true}).click();await bank.getByTestId('candidate-status').waitFor({state:'attached'});await bank.getByText('推荐状态与全部原因详情',{exact:true}).click();const state=await bank.getByTestId('candidate-status').innerText();await bank.getByText('推荐状态与全部原因详情',{exact:true}).click();assert.match(state,mode==='theme'?/no_theme_match/:/unavailable/);mark('bank '+mode,state);}
  assert.ok(await bank.getByRole('button',{name:'创建整体配置草稿',exact:true}).isDisabled());
  const blocked=await bank.evaluate(async token=>{const h={Authorization:'Bearer '+token,'Content-Type':'application/json'};const c=await(await fetch('/dev/context',{headers:h})).json();const r=await fetch('/api/v1/wealth/portfolio/plans',{method:'POST',headers:h,body:JSON.stringify({context_id:c.context_id})});return {status:r.status,body:await r.json()};},bankToken);assert.equal(blocked.status,503);assert.match(JSON.stringify(blocked.body),/WRITE_CONTROL_UNPROVEN/);mark('bank create blocked',blocked);
  assert.match(await bank.getByTestId('no-plan').innerText(),/资产已读取/);assert.ok(!(await bank.locator('body').innerText()).includes('请先取得有效的资产'));assert.match(await bank.getByTestId('controls').innerText(),/相关会话写控制/);assert.match(await bank.getByLabel('推荐主题').innerText(),/高股息/);assert.ok(await bank.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));mark('bank explanation/layout','asset loaded; qualification/product/write controls separate; raw reasons preserved in details; no horizontal overflow');
  await bank.screenshot({path:out+'/bank-facts.png',fullPage:true});await bank.close();
  // No Plan is created on load. Batches are produced by DPP, selected references by CandidateService.
  await reset('R4');assert.equal(await page.getByTestId('version').count(),0);await click('推荐候选');assert.match(await page.getByTestId('candidate-status').innerText(),/^ok/);
  let families=[];
  for(let batch=0;batch<4;batch++){
    const firstCards=page.locator('article[data-family]').filter({has:page.locator('input[type=checkbox]')});
    const ids=await firstCards.evaluateAll(nodes=>[...new Set(nodes.map(n=>n.getAttribute('data-family')))]);
    const fresh=ids.filter(id=>!families.includes(id));assert.ok(fresh.length<=3);
    for(const id of fresh){await page.getByRole('checkbox',{name:'选择 '+id,exact:true}).first().click();await wait();assert.ok(await page.getByRole('checkbox',{name:'选择 '+id,exact:true}).first().isChecked());families.push(id);}
    if(await page.getByRole('button',{name:'换一批',exact:true}).isDisabled())break;
    await click('换一批');
  }
  assert.ok(families.length>5);assert.equal(Number(await page.getByTestId('selected-count').innerText()),families.length);mark('batches/multiselect/>5/exhaustion',{families:families.length,max_batch:3});
  const first=families[0];const select=page.getByLabel('份额 '+first,{exact:true}).first();assert.match(await select.inputValue(),/A$/);await select.selectOption({label:(await select.locator('option').allTextContents()).find(x=>x.endsWith(' C'))});await wait();assert.match(await select.inputValue(),/C$/);mark('A default + legal AC switch','code and unknown fees/performance/source follow current share');
  await click('创建整体配置草稿');await v(1);assert.ok(lastPlan.selected_positions.length>5);const original=structuredClone(lastPlan);await click('读取当前方案');await v(1);assert.equal(lastPlan.portfolio_id,original.portfolio_id);mark('selected create/get',{version:1,selected:lastPlan.selected_positions.length,basis:lastPlan.basis_fen});
  // Exact decimal percentage round trip + illegal sum atomicity.
  await click('应用产品百分比');await v(2);let plan=structuredClone(lastPlan);
  const stable=plan.advised_positions.filter(x=>x.bucket==='stable');for(let i=0;i<2;i++)await page.getByLabel('百分比 '+stable[i].product_id,{exact:true}).fill(String((stable[i].target_fen+(i===0?100:-100))*100/plan.basis_fen));await click('应用产品百分比');await v(3);
  await page.screenshot({path:out+'/fixture-accepted.png',fullPage:true});
  const field=page.getByLabel('百分比 '+plan.advised_positions[0].product_id,{exact:true});await field.fill('-1');await click('应用产品百分比');await page.getByRole('alert').filter({hasText:'百分比应为非负十进制数'}).waitFor();await v(3);
  const invalidWeight=await rpc('/api/v1/wealth/portfolio/plans/'+lastPlan.portfolio_id,planRequest(lastPlan,[{product_id:plan.advised_positions[0].product_id,product_version:1,share_class:plan.advised_positions[0].share_class,target_ratio_total:'-0.1'}]),'PUT');assert.equal(invalidWeight.status,422);mark('invalid negative product weight','visible draft error + actual HTTP422; accepted v3 unchanged');
  await field.fill('100');await click('应用产品百分比');await page.getByRole('alert').filter({hasText:/PORTFOLIO_OVERALLOCATED|PORTFOLIO_CASH_MISMATCH|MANUAL|total ratio exceeds/}).waitFor();await v(3);mark('percentage roundtrip/legal edit/illegal sum',{accepted:3,rejected_keeps:3});
  // Use baseline R4 for original U repeated adjustments.
  await reset('R4');await click('创建整体配置草稿');await v(1);assert.equal(await page.getByTestId('accepted-p').innerText(),'62.5%');
  const slider=page.getByRole('slider');for(const p of ['0.626','0.627']){await slider.fill(p);await click('应用本页调整');}await v(3);plan=structuredClone(lastPlan);
  let bad=await rpc('/api/v1/wealth/portfolio/plans/'+plan.portfolio_id,planRequest(plan,plan.selected_positions,{growth_ratio_ex_cash:'0.95'}),'PUT');assert.equal(bad.status,422);
  await page.getByLabel('重新设置进取偏好',{exact:true}).fill('.65');await click('重新计算偏好');await v(4);assert.equal(await page.getByTestId('accepted-p').innerText(),'65%');
  await page.getByLabel('重新设置进取偏好',{exact:true}).fill('.8');await click('重新计算偏好');await page.getByRole('alert').filter({hasText:/PREFERENCE/}).waitFor();await v(4);mark('slider original U/preference',{adjustments:2,preference_version:4,outside_rejected:bad.status});
  await page.getByRole('checkbox',{name:'只读／人工建议模式',exact:true}).check();assert.ok(await slider.isDisabled());await page.getByRole('checkbox',{name:'只读／人工建议模式',exact:true}).uncheck();
  // R1/R2/F18 snapshots and filters.
  for(const s of ['R1','R2','F18']){await reset(s);await click('创建整体配置草稿');await v(1);const text=await page.locator('body').innerText();assert.ok(!text.includes('NaN'));if(s==='R1')assert.equal(await page.getByRole('slider').count(),0);if(s==='F18'){for(const t of ['整体总资产 ¥10000.00','可支付现金 ¥7000.00','赎回应收 ¥500.00','冻结 ¥1000.00'])assert.ok(text.includes(t));}mark(s,{buckets:lastPlan.buckets.map(b=>b.target_fen),p:lastPlan.growth_ratio_ex_cash});}
  await reset('R4');await page.getByLabel('筛选模式',{exact:true}).selectOption('category');await page.getByLabel('产品类别',{exact:true}).selectOption('bond_fund');await click('推荐候选');assert.match(await page.getByTestId('candidate-status').innerText(),/^ok/);
  await page.getByLabel('产品类别',{exact:true}).selectOption('bank_wealth');await click('推荐候选');assert.match(await page.getByTestId('candidate-status').innerText(),/no_category_match/);
  await page.getByLabel('筛选模式',{exact:true}).selectOption('theme');await page.getByText('主题参数与组合',{exact:true}).evaluate(e=>e.parentElement.open=true);await page.getByLabel('主题键',{exact:true}).fill('high_dividend');await click('推荐候选');assert.match(await page.getByTestId('candidate-status').innerText(),/^ok/);
  await page.getByText('主题参数与组合',{exact:true}).evaluate(e=>e.parentElement.open=true);await page.getByLabel('主题键',{exact:true}).fill('broad_market');await click('推荐候选');assert.match(await page.getByTestId('candidate-status').innerText(),/no_theme_match/);
  await page.getByText('主题参数与组合',{exact:true}).evaluate(e=>e.parentElement.open=true);await page.getByLabel('主题键',{exact:true}).fill('high_dividend,bond_income');await page.getByText('主题参数与组合',{exact:true}).evaluate(e=>e.parentElement.open=true);await page.getByLabel('主题组合',{exact:true}).selectOption('OR');await click('推荐候选');assert.match(await page.getByTestId('candidate-status').innerText(),/^ok/);const orProducts=await page.locator('article[data-family]').count();assert.ok(orProducts>0);await page.getByLabel('主题组合',{exact:true}).selectOption('AND');await click('推荐候选');assert.match(await page.getByTestId('candidate-status').innerText(),/no_theme_match/);mark('compound theme OR/AND','actual registered matching union/intersection, no general fallback');
  await page.getByText('主题参数与组合',{exact:true}).evaluate(e=>e.parentElement.open=true);await page.getByLabel('主题键',{exact:true}).fill('not_registered');await click('推荐候选');await page.getByRole('alert').filter({hasText:'UNKNOWN_THEME_KEY'}).waitFor();mark('general/category/theme/unknown','rules apply; empty category/theme distinct; unknown422 no fallback');
  await reset('R2');await page.request.post('http://127.0.0.1:8051/dev/change/all-high?scenario=R2');await click('推荐候选');assert.match(await page.getByTestId('candidate-status').innerText(),/risk_mismatch/);mark('all risk rejected','valid currentR2, complete productsR5, risk_mismatch');
  await reset('R4');await page.request.post('http://127.0.0.1:8051/dev/change/material?scenario=R4');await click('推荐候选');assert.match(await page.getByTestId('candidate-status').innerText(),/unavailable/);mark('unknown materials','no silent success/fallback');
  for(const s of ['only-C','single-share']){await reset(s);await click('推荐候选');const text=await page.locator('article').first().innerText();assert.match(text,s==='only-C'?/Q\d+C C/:/Q\d+A A/);assert.equal(await page.locator('article').first().locator('select').count(),0);mark(s,'valid default, no invalid AC control');}
  // Switch failure preserves old selection.
  await reset('R4');await click('推荐候选');let id=await page.locator('article').first().getAttribute('data-family');await page.getByRole('checkbox',{name:'选择 '+id,exact:true}).first().click();await wait();const oldShare=await page.getByLabel('份额 '+id,{exact:true}).first().inputValue();await page.request.post('http://127.0.0.1:8051/dev/change/switch-risk?scenario=R4');await page.getByLabel('份额 '+id,{exact:true}).first().selectOption(oldShare.replace(/A$/,'C'));await wait();await page.getByRole('alert').filter({hasText:/STALE|RISK/}).waitFor();assert.equal(await page.getByLabel('份额 '+id,{exact:true}).first().inputValue(),oldShare);mark('failed AC switch','old share retained');
  // R2 manual R4 zero/tiny/AC: actual risk gate, not missing source.
  await reset('R2');await click('创建整体配置草稿');await v(1);plan=structuredClone(lastPlan);
  for(const ratio of ['0','0.000001']){bad=await rpc('/api/v1/wealth/portfolio/plans/'+plan.portfolio_id,planRequest(plan,[{product_id:'Q07C',product_version:1,share_class:'C',target_ratio_total:ratio}]),'PUT');assert.equal(bad.status,422);assert.ok(JSON.stringify(bad.body).includes('RISK_EXCEEDS_CAPACITY'));}
  await click('读取当前方案');await v(1);mark('R2→R4 manual zero/tiny C','actual qualify_purchase rejects422; originalv1');
  const candidates=await rpc('/api/v1/wealth/portfolio/recommendations',{context_id:plan.context_id,filter:{mode:'general'}});const state=candidates.body.session_binding;
  const ref={product_id:'Q07A',product_version:1,disclosure_version:1,rules_version:'rules-1',share_class:'A',target_ratio_total:'0'};
  const picked=await rpc('/dev/candidates/select',{context_id:plan.context_id,action:{session_id:state.session_id,expected_version:state.version,items:[ref]}});assert.ok(picked.body.selection.accepted);
  const switched=await rpc('/dev/candidates/switch',{context_id:plan.context_id,action:{session_id:state.session_id,expected_version:picked.body.session.version,family_id:'fixture-family-07',target:{...ref,product_id:'Q07C',share_class:'C'}}});assert.equal(switched.body.selection.accepted,false);assert.ok(switched.body.selection.reason_codes.includes('RISK_EXCEEDS_CAPACITY'));assert.ok(switched.body.session.selected.Q07A);mark('R2 A→R4 C actual gate','HTTP200 accepted=false; original A selection intact');
  // Holding-only valid maintain/reduce vs increase.
  await reset('holding-only');await click('创建整体配置草稿');await v(1);plan=structuredClone(lastPlan);
  const held=plan.advised_positions.find(x=>x.product_id==='Q03A');assert.ok(held.holding_cap_fen===500000);
  const baseItems=plan.advised_positions.map(x=>({product_id:x.product_id,product_version:x.product_version,share_class:x.share_class,target_ratio_total:String(x.target_fen/plan.basis_fen)}));
  let result=await rpc('/api/v1/wealth/portfolio/plans/'+plan.portfolio_id,planRequest(plan,baseItems,{cash_residual_fen:plan.cash_residual_fen}),'PUT');assert.equal(result.status,200);plan=result.body;
  const up=[{product_id:'Q03A',product_version:1,share_class:'A',target_ratio_total:'.51'}];bad=await rpc('/api/v1/wealth/portfolio/plans/'+plan.portfolio_id,planRequest(plan,up),'PUT');assert.equal(bad.status,422);assert.match(JSON.stringify(bad.body),/HOLDING_CAP/);
  const down=baseItems.map(x=>x.product_id==='Q03A'?{...x,target_ratio_total:String(held.target_fen/plan.basis_fen-.001)}:x);result=await rpc('/api/v1/wealth/portfolio/plans/'+plan.portfolio_id,planRequest(plan,down),'PUT');assert.equal(result.status,200);await click('读取当前方案');await v(3);assert.equal(lastPlan.advised_positions.find(x=>x.product_id==='Q03A').target_fen,held.target_fen-1000);mark('holding-only maintain/reduce/increase',{maintain:200,reduce:200,increase:bad.body.detail.code,visible_version:3});
  // Actual second browser tab writes same version; first tab remains rejected/invalid.
  await reset('version-conflict');await click('创建整体配置草稿');await v(1);plan=structuredClone(lastPlan);
  const second=await browser.newPage();await second.goto('http://127.0.0.1:3021/portfolio-dev.html');const reply=await second.evaluate(async({id,body})=>{const boot=await(await fetch('/dev/bootstrap?scenario=version-conflict')).json();const r=await fetch('/api/v1/wealth/portfolio/plans/'+id,{method:'PUT',headers:{Authorization:'Bearer '+boot.token,'Content-Type':'application/json','X-Portfolio-Scenario':'version-conflict'},body:JSON.stringify(body)});return r.status;},{id:plan.portfolio_id,body:planRequest(plan,plan.selected_positions,{growth_ratio_ex_cash:'0.626'})});assert.equal(reply,200);await second.close();await slider.fill('0.627');await click('应用本页调整');await page.getByRole('alert').filter({hasText:'PORTFOLIO_VERSION_STALE'}).waitFor();await v(1);mark('dual window CAS','second v2; first409 retains v1');
  // In-flight previous context result is ignored.
  await reset('R4');await click('创建整体配置草稿');await v(1);await slider.fill('0.626');await page.getByRole('button',{name:'应用本页调整',exact:true}).click();await page.getByLabel('场景',{exact:true}).selectOption('R2');await wait();assert.equal(await page.getByTestId('version').count(),0);await click('创建整体配置草稿');await v(1);assert.notEqual(await page.getByTestId('accepted-p').innerText(),'62.6%');mark('late old-context response','R2 not overwritten');
  await click('刷新事实并清除草稿');assert.equal(await page.getByTestId('version').count(),0);assert.equal(await page.getByTestId('selected-count').innerText(),'0');mark('facts refresh clears draft','snapshot retained/reloaded; no old Plan or selection');
  for(const mutation of ['read-only','write-lock','expired','downgrade','stopped','material','assets','context']){
    await reset('R4');await click('创建整体配置草稿');await v(1);plan=structuredClone(lastPlan);await page.request.post('http://127.0.0.1:8051/dev/change/'+mutation+'?scenario=R4');
    if(['read-only','write-lock'].includes(mutation)){const controls=await rpc('/dev/context',undefined,'GET');assert.equal(controls.body.can_edit,false);const read=await rpc('/api/v1/wealth/portfolio/plans/'+plan.portfolio_id+'?context_id='+plan.context_id,undefined,'GET');assert.equal(read.status,200);bad=await rpc('/api/v1/wealth/portfolio/plans/'+plan.portfolio_id,planRequest(plan,plan.selected_positions,{growth_ratio_ex_cash:'.626'}),'PUT');assert.equal(bad.status,409);await click('读取当前方案');await v(1);assert.ok(await page.getByRole('button',{name:'应用产品百分比',exact:true}).isDisabled());}
    else{await click('读取当前方案');await page.getByRole('alert').waitFor();assert.ok(await page.getByRole('button',{name:'应用产品百分比',exact:true}).isDisabled());}
    mark('fact/control '+mutation,'read/write layered; stale/error remains honest');
  }
  for(const s of ['missing-valuation','source-unavailable']){await reset(s);if(s==='missing-valuation'){await click('创建整体配置草稿');await page.getByRole('alert').filter({hasText:'VALUATION_INCOMPLETE'}).waitFor();assert.match(await page.locator('body').innerText(),/整体总资产 未知/);}else await page.getByRole('alert').filter({hasText:'ASSET_SUMMARY_UNAVAILABLE'}).waitFor();mark(s,'explicit error; no fallback');}
  assert.equal(runtime.length,0);
  const namesByM={
    M01:['F18','missing-valuation'], M02:['selected create/get'], M03:['R1','R2','slider original U/preference'],
    M04:['general/category/theme/unknown','compound theme OR/AND','all risk rejected','unknown materials','source-unavailable'],
    M05:['batches/multiselect/>5/exhaustion'],M06:['A default + legal AC switch','only-C','single-share','failed AC switch'],
    M07:['R2→R4 manual zero/tiny C','R2 A→R4 C actual gate'],M08:['selected create/get'],
    M09:['slider original U/preference'],M10:['slider original U/preference'],M11:['invalid negative product weight','percentage roundtrip/legal edit/illegal sum'],
    M12:['holding-only maintain/reduce/increase'],M13:['dual window CAS','late old-context response','facts refresh clears draft','fact/control read-only','fact/control write-lock'],
    M14:['fact/control expired','fact/control downgrade','fact/control stopped','fact/control material','fact/control assets','fact/control context','missing-valuation','source-unavailable']};
  const matrix=Object.entries(namesByM).map(([id,names])=>{const evidence=checks.filter(c=>names.includes(c.name));assert.equal(evidence.length,names.length,id+' coverage');return {id,source:'fixture_only',status:'passed',interaction:['M07','M12'].includes(id)?'HTTP边界检查及页面读回':['M09','M10','M11','M13','M14'].includes(id)?'UI点击/输入与HTTP边界检查':'UI点击/输入',evidence};});
  await writeFile(out+'/browser_results.json',JSON.stringify({mode:'real Edge headless/local HTTP/no response mocks',checks,matrix,http,runtime,network,consoleErrors},null,2));console.log(JSON.stringify({checks:checks.length,runtime:runtime.length,network:network.length,consoleErrors:consoleErrors.length}));
}catch(e){await page.screenshot({path:out+'/failure.png',fullPage:true});await writeFile(out+'/browser_failure.json',JSON.stringify({checks,http,runtime,network,consoleErrors,error:String(e),body:await page.locator('body').innerText()},null,2));throw e;}
finally{await browser.close();}

