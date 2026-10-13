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

 const panel=page.getByRole('region',{name:'统一手动操作'});await panel.getByRole('button',{name:'选择产品进行比较（2—4只）',exact:true}).click();const modal=page.getByRole('dialog',{name:'产品比较',exact:true});await modal.getByRole('checkbox',{name:'华夏纯债债券A',exact:true}).check();await modal.getByRole('checkbox',{name:'中欧时代智慧混合C',exact:true}).check();const table=modal.getByRole('table',{name:'所选产品比较',exact:true});await table.locator('tbody tr').nth(1).waitFor();assert.match(await table.innerText(),/000015/);assert.match(await table.innerText(),/005242/);assert.match(await table.innerText(),/项目分级独立于发行人/);assert.match(await modal.innerText(),/不代表现实渠道完整费用/);mark('J05 actual two-code comparison with separate simulated fee/project risk labels',true);await page.screenshot({path:out+'/real-product-comparison.png',fullPage:false});await modal.getByRole('button',{name:'关闭比较',exact:true}).click();assert.equal((await req('GET','/api/v1/wealth/assets')).body.orders.filter(o=>o.status==='SETTLED').length,7);mark('read-only comparison preserves seven settled investments',true);assert.equal(runtime.length,0);assert.equal(network.length,0);
}catch(e){mark('failure',{message:String(e)});await writeFile(out+'/page.txt',await page.locator('body').innerText());throw e;}
finally{await writeFile(out+'/browser_results.json',JSON.stringify({source:'formal-real-public-paper-comparison',checks,http,runtime,network},null,2));await browser.close();console.log(JSON.stringify({checks:checks.length,runtime:runtime.length,network:network.length}));}
