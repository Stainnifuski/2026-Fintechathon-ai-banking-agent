import {pathToFileURL} from 'node:url';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
const {chromium}=await import(pathToFileURL(process.env.PORTFOLIO_PLAYWRIGHT_MODULE).href);
const out=process.env.WEALTH_JOINT_EVIDENCE||'.deps/real_fund_close/browser-exits';await mkdir(out,{recursive:true});
const browser=await chromium.launch({executablePath:process.env.PORTFOLIO_BROWSER_EXE,headless:true});const page=await browser.newPage({viewport:{width:1440,height:1100}});page.setDefaultTimeout(30000);
const checks=[],runtime=[],network=[],http=[];let token;
page.on('pageerror',e=>runtime.push(String(e)));page.on('requestfailed',r=>network.push({path:new URL(r.url()).pathname,error:r.failure()?.errorText}));
page.on('response',async r=>{let v;try{v=await r.json();}catch{return;}const path=new URL(r.url()).pathname;if(path.endsWith('/auth/login'))token=v.access_token;if(path.startsWith('/api'))http.push({path,http:r.status(),action:v?.action,code:v?.detail?.code,status:v?.status});});
const mark=(name,result)=>checks.push({name,result});
const req=async(method,path,body)=>{const r=await page.request.fetch('http://127.0.0.1:3013'+path,{method,headers:{Authorization:'Bearer '+token},...(body?{data:body}:{})});return {http:r.status(),body:await r.json()};};
const source=await readFile('tests/browser/wealth_joint_server.py','utf8');const password=/hash_password\('([^']+)'\)/.exec(source)[1];
try{
 await page.goto('http://127.0.0.1:3013/');await page.getByLabel('手机号',{exact:true}).fill('13800000001');await page.getByLabel('密码',{exact:true}).fill(password);await page.getByRole('button',{name:'登录',exact:true}).click();
 const nav=page.getByRole('navigation',{name:'主要功能'});await nav.getByRole('button',{name:/理财$/}).click();await page.getByText('直接选择操作（无需聊天）',{exact:true}).click();
 const panel=page.getByRole('region',{name:'统一手动操作'});await panel.getByRole('button',{name:'查询我的持仓',exact:true}).click();const table=panel.getByRole('region',{name:'独立持仓表',exact:true});await table.getByText(/可赎/).first().waitFor();
 const before=(await req('GET','/api/v1/wealth/assets')).body;assert.equal(before.lots.length,7);assert.ok(before.orders.filter(x=>x.direction==='purchase').every(x=>x.status==='SETTLED'));const initialCash=before.cash[0].balance_fen;
 await table.getByLabel('持仓每页行数').selectOption('10');
 const listed=await req('POST','/api/v1/wealth/actions',{schema_version:'wealth-action-v1',request_id:'paper-exit-resume-'+Date.now(),context_id:before.context_id,action:'list_orders',params:{page:1,page_size:100}});assert.equal(listed.http,200);
 const existing=listed.body.data.table_data.rows.filter(r=>r.direction==='redeem');
 for(const [code,mode,value] of [['000015','units','10.0000'],['005242','market_value','12.34']]){
  if(existing.some(r=>r.product_id===code)){mark('reuse already submitted '+mode+' redemption',{code,evidence:'previous actual UI response, retained original operation'});continue;}
  const row=table.locator('tbody tr').filter({hasText:code});await row.getByRole('button',{name:'赎回',exact:true}).click();const input=page.getByRole('dialog',{name:'持仓赎回输入',exact:true});
  await input.getByRole('combobox').first().selectOption(mode);await input.locator('input[inputmode="decimal"]').fill(value);await input.getByRole('button',{name:'预览本只赎回',exact:true}).click();
  const conversion=page.getByRole('dialog',{name:'赎回换算核对',exact:true});await conversion.waitFor();mark('J10 actual '+mode+' conversion',{code,text:await conversion.innerText()});await conversion.getByRole('button',{name:'继续本人确认与验证',exact:true}).click();
  const confirm=page.getByRole('dialog',{name:'赎回本人确认',exact:true});await confirm.getByRole('checkbox').check();await confirm.getByRole('button',{name:'本人确认本笔赎回',exact:true}).click();await confirm.getByRole('button',{name:'短信验证码',exact:true}).click();
  // Actual unified provider may throttle. Keep consent/nonce; never reuse a code from another operation.
  for(let attempt=0;attempt<8;attempt++){
   await confirm.getByRole('button',{name:'获取短信验证码',exact:true}).click();await page.waitForTimeout(600);
   if(await confirm.getByRole('button',{name:'查看模拟短信',exact:true}).count())break;
   if(attempt===7)throw Error('actual SMS provider did not issue this challenge');await page.waitForTimeout(10000);
  }
  await confirm.getByRole('button',{name:'查看模拟短信',exact:true}).click();await confirm.locator('.hint').filter({hasText:/验证码 \d{6}/}).waitFor();const proof=/验证码 (\d{6})/.exec(await confirm.innerText())[1];await confirm.getByLabel('赎回验证码',{exact:true}).fill(proof);
  const submitted=page.waitForResponse(r=>new URL(r.url()).pathname.includes('/previews/')&&new URL(r.url()).pathname.endsWith('/execute'));submitted.catch(()=>{});await confirm.getByRole('button',{name:'验证并提交赎回',exact:true}).click();const result=await submitted;assert.equal(result.status(),200);await page.waitForTimeout(500);if(await confirm.getByRole('button',{name:'关闭赎回预览',exact:true}).count())await confirm.getByRole('button',{name:'关闭赎回预览',exact:true}).click();
  if(await page.getByRole('dialog',{name:'持仓赎回输入',exact:true}).count())await page.getByRole('button',{name:'关闭赎回输入',exact:true}).click();
  await panel.getByRole('button',{name:'查询我的持仓',exact:true}).click();await page.waitForTimeout(500);
 }
 const accepted=(await req('GET','/api/v1/wealth/assets')).body;assert.equal(accepted.orders.filter(o=>o.direction==='redeem').length,2);assert.equal(accepted.cash[0].balance_fen,initialCash);assert.ok(accepted.lots.some(l=>Number(l.held_units)>0));mark('J10 redemption accepted, units held, no early cash',{redemptions:2,cash:initialCash});
 const next=await req('POST','/api/v1/joint-test/advance',{days:2});assert.equal(next.http,200);const pending=(await req('GET','/api/v1/wealth/assets')).body;assert.equal(pending.orders.filter(o=>o.status==='PENDING_CASH').length,2);assert.equal(pending.cash[0].balance_fen,initialCash);assert.ok(pending.receivable_fen>0);mark('J13 authoritative receivable not payable',{cash:initialCash,receivable:pending.receivable_fen,total:pending.asset_summary.total_assets_fen});
 const finish=await req('POST','/api/v1/joint-test/advance',{days:3});assert.equal(finish.http,200);const settled=(await req('GET','/api/v1/wealth/assets')).body;assert.equal(settled.orders.filter(o=>o.direction==='redeem'&&o.status==='SETTLED').length,2);assert.equal(settled.cash[0].balance_fen,initialCash+pending.receivable_fen);mark('J19 ordinary redemption unique T+2 cash',settled.cash[0].balance_fen);
 // Refresh reads the same ledger; no pending conversation/Plan constitutes authority.
 await page.reload();await nav.getByRole('button',{name:/理财$/}).click();await page.getByText('直接选择操作（无需聊天）',{exact:true}).click();await panel.getByRole('button',{name:'查询我的订单',exact:true}).click();await panel.getByRole('button',{name:'查询我的持仓',exact:true}).click();
 const afterReload=(await req('GET','/api/v1/wealth/assets')).body;assert.equal(afterReload.orders.length,9);assert.equal(afterReload.lots.length,7);mark('J15 refresh preserves nine bank orders and holdings',true);
 await panel.getByRole('button',{name:'查询整体收益',exact:true}).click();const returns=panel.getByRole('region',{name:'登录账户实际理财收益',exact:true});await returns.getByRole('table',{name:'收益时间序列表'}).waitFor();
 for(const period of ['日','月','年','累计']){
  const response=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/wealth/actions')&&r.request().postDataJSON()?.action==='show_wealth_returns');response.catch(()=>{});
  if(period!=='日')await returns.getByRole('button',{name:period,exact:true}).click();else{mark('J22/J23 initial day actual bank returns',true);continue;}
  const r=await response;const body=await r.json();assert.equal(r.status(),200,JSON.stringify(body));assert.equal(body.status,'ok');assert.ok(['complete','partial','unavailable','no_history'].includes(body.data.status),body.data.status);mark('J22/J23 actual '+period+' returns',{status:body.data.status,missing:body.data.missing_fields,products:body.data.products.length});
  if(period==='月'){await returns.getByRole('group',{name:'每日收益日历'}).count();mark('J23 actual month calendar rendered',await returns.locator('.returns-calendar button').count());}
 }
 await returns.getByLabel('统计范围',{exact:true}).selectOption('000015');await page.waitForTimeout(600);await returns.getByRole('table',{name:'分产品收益贡献'}).waitFor();mark('J21 actual product-specific return lookup',true);
 await page.screenshot({path:out+'/actual-holdings-and-returns.png',fullPage:true});
 assert.equal(http.filter(h=>h.path.includes('/messages')).length,0);assert.equal(runtime.length,0);assert.equal(network.length,0);
}catch(e){mark('failure',{message:String(e)});await writeFile(out+'/page.txt',await page.locator('body').innerText());await page.screenshot({path:out+'/failure.png',fullPage:true});throw e;}
finally{await writeFile(out+'/browser_results.json',JSON.stringify({source:'formal-real-public-paper-exits',checks,http,runtime,network},null,2));await browser.close();console.log(JSON.stringify({checks:checks.length,runtime:runtime.length,network:network.length}));}
