// Current fixed catalogue, actual formal UI. This verifies truthful blocking,
// never substitutes a positive investment acceptance or a recorded Plan.
import {pathToFileURL} from 'node:url';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
const {chromium}=await import(pathToFileURL(process.env.PORTFOLIO_PLAYWRIGHT_MODULE).href);
const out=process.env.WEALTH_JOINT_EVIDENCE;await mkdir(out,{recursive:true});
const browser=await chromium.launch({executablePath:process.env.PORTFOLIO_BROWSER_EXE,headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1100}});page.setDefaultTimeout(120000);
const checks=[],runtime=[],network=[],paths=[];
page.on('pageerror',e=>runtime.push(String(e)));page.on('requestfailed',r=>network.push(new URL(r.url()).pathname));page.on('request',r=>paths.push(new URL(r.url()).pathname));
try{
 await page.goto('http://127.0.0.1:3013/');
 const source=await readFile('tests/browser/wealth_joint_server.py','utf8');const credential=/hash_password\('([^']+)'\)/.exec(source)[1];
 await page.getByLabel('手机号',{exact:true}).fill('13800000001');await page.getByLabel('密码',{exact:true}).fill(credential);
 await page.getByRole('button',{name:'登录',exact:true}).click();
 await page.getByRole('navigation',{name:'主要功能'}).getByRole('button',{name:/理财$/}).click();
 await page.getByText('直接选择操作（无需聊天）',{exact:true}).click();
 const panel=page.getByRole('region',{name:'统一手动操作'});
 const response=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/wealth/actions')&&r.request().postDataJSON()?.action==='recommend_products');
 await panel.getByRole('button',{name:'产品推荐',exact:true}).click();const recommendation=await response;const group=await recommendation.json();
 assert.equal(recommendation.status(),200);assert.equal(group.data.items.length,0);assert.ok(group.data.reason_codes.includes('INVESTMENT_TEXT_SOURCE_MISSING'));
 checks.push({name:'current complete fixed source reports exact missing investment material',status:'passed',candidate_count:0,reason_codes:group.data.reason_codes});
 const configured=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/wealth/actions')&&r.request().postDataJSON()?.action==='plan_portfolio');
 await panel.getByRole('button',{name:'理财规划',exact:true}).click();const result=await configured;const plan=await result.json();
 assert.equal(result.status(),200,JSON.stringify(plan));assert.equal(plan.status,'ok');assert.equal(plan.data.selected_positions.length,0);
 const warning=page.getByRole('note').filter({hasText:'尚未完成可投资产品配置'});await warning.waitFor();
 checks.push({name:'cash fallback clearly states not an investable allocation',status:'passed',basis_fen:plan.data.basis_fen,portfolio_id:plan.data.portfolio_id,version:plan.data.version,selected:0});
 const chart=page.getByRole('region',{name:'整体配置',exact:true});await chart.screenshot({path:out+'/cash-configuration.png'});
 await warning.screenshot({path:out+'/candidate-limitation.png'});
 assert.equal(paths.filter(p=>p.includes('/messages')).length,0);assert.equal(runtime.length,0);assert.equal(network.length,0);
 checks.push({name:'new NLP calls',status:'passed',count:0});
}finally{
 await writeFile(out+'/browser_results.json',JSON.stringify({source:'real-formal-A-B-fixed-catalogue-isolated-SQLite',scope:'blocking diagnosis only; no positive trade or new questionnaire UI acceptance',checks,runtime,network},null,2));
 await browser.close();console.log(JSON.stringify({checks:checks.length,runtime:runtime.length,network:network.length}));
}
