// Real production UI, fully intercepted synthetic APIs. No bank writes or LLM calls.
import {pathToFileURL} from 'node:url';
import {mkdir} from 'node:fs/promises';
import assert from 'node:assert/strict';
const {chromium}=await import(pathToFileURL(process.env.PORTFOLIO_PLAYWRIGHT_MODULE).href);
const browser=await chromium.launch({executablePath:process.env.PORTFOLIO_BROWSER_EXE,headless:true});
const output='.deps/login-orb-check';await mkdir(output,{recursive:true});
try{
 for(const width of [1440,390]){
  const page=await browser.newPage({viewport:{width,height:width===390?844:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(String(e)));
  let reject=true;
  await page.route('**/api/**',async route=>{
   const path=new URL(route.request().url()).pathname;
   const json=data=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
   if(path.endsWith('/auth/login')){
    await new Promise(resolve=>setTimeout(resolve,180));
    return reject?route.fulfill({status:401,contentType:'application/json',body:JSON.stringify({detail:'隔离验证：密码错误'})}):json({access_token:'isolated',user:{user_id:'UI-ISOLATED',name:'隔离用户',phone:'13800000001'}});
   }
   if(path.endsWith('/security/status'))return json({locks:[],trade_locked:false,message:''});
   if(path.endsWith('/system/time'))return json({server_time_utc:new Date().toISOString(),business_timezone:'Asia/Shanghai'});
   if(path.endsWith('/voice/status'))return json({ready:false,message:'隔离',max_seconds:30,max_bytes:1000});
   if(path.includes('/bills/')||path.endsWith('/asset-summary'))return route.fulfill({status:503,contentType:'application/json',body:'{}'});
   if(path.endsWith('/totp/status'))return json({enabled:false});
   return json(route.request().method()==='GET'?[]:{});
  });
  await page.goto('http://127.0.0.1:3000/');
  await page.locator('.nova-global-orb[data-ready="true"]').waitFor();
  assert.equal(await page.locator('link[rel="icon"]').getAttribute('href'),'/nova.svg');
  assert.equal((await page.request.get('http://127.0.0.1:3000/nova.svg')).status(),200);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.getByLabel('手机号',{exact:true}).fill('13800000001');await page.getByLabel('密码',{exact:true}).fill('password123');
  await page.evaluate(()=>window.loginCanvas=document.querySelector('.nova-global-orb canvas'));
  await page.screenshot({path:`${output}/login-${width}.png`});
  await page.getByRole('button',{name:'登录 NOVA',exact:false}).click();
  await page.getByRole('alert').filter({hasText:'密码错误'}).waitFor();
  assert.equal(await page.locator('.login-header').isVisible(),true);
  reject=false;
  await page.evaluate(()=>{window.orbFrames=[];const sample=t=>{const orb=document.querySelector('.nova-global-orb'),home=document.querySelector('.home-workspace');window.orbFrames.push({t,phase:document.documentElement.dataset.novaAuth,transform:orb.style.transform,visibility:home?getComputedStyle(home).visibility:null,opacity:home?Number(getComputedStyle(home).opacity):null,inert:home?.inert,pointer:home?getComputedStyle(home).pointerEvents:null});if(!window.orbDone)requestAnimationFrame(sample);};requestAnimationFrame(sample);});
  await page.getByRole('button',{name:'登录 NOVA',exact:false}).click();
  await page.waitForFunction(()=>document.documentElement.dataset.novaAuth==='moving');
  await page.screenshot({path:`${output}/moving-${width}.png`});
  await page.waitForFunction(()=>document.querySelector('.home-workspace')&& !document.documentElement.dataset.novaAuth);
  const result=await page.evaluate(()=>{window.orbDone=true;const frames=window.orbFrames.filter(f=>f.phase==='moving'),reveal=window.orbFrames.filter(f=>f.phase==='revealing');return {sameCanvas:window.loginCanvas===document.querySelector('.nova-global-orb canvas'),frames,reveal,revealDuration:reveal.at(-1)?.t-reveal[0]?.t,interactive:!document.querySelector('.home-workspace').inert,duration:frames.at(-1)?.t-frames[0]?.t};});
  assert.equal(result.sameCanvas,true);
  assert.ok(result.frames.length>5);assert.ok(result.frames.every(f=>f.visibility==='hidden'));
  assert.ok(result.duration>=450&&result.duration<=650,JSON.stringify(result));
  assert.ok(new Set(result.frames.map(f=>f.transform)).size>5);
  assert.ok(result.revealDuration>=450&&result.revealDuration<=650,JSON.stringify(result));
  assert.ok(result.reveal.every(f=>f.inert&&f.pointer==='none'));
  assert.ok(result.reveal.some(f=>f.opacity>0&&f.opacity<1));
  assert.equal(result.interactive,true);
  await page.screenshot({path:`${output}/home-${width}.png`});
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({width,frames:result.frames.length,duration:result.duration,revealDuration:result.revealDuration,sameCanvas:result.sameCanvas}));
  await page.close();
 }
}finally{await browser.close();}
