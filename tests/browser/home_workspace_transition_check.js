// Playwright CLI run-code. Requires an authenticated independent 3100 browser.
// Browser fixtures block workspace reset and financial writes; never use a production session.
async page => {
 try {
 const checks=[];const check=(value,label)=>{if(!value)throw new Error(label);checks.push(label);};
 const json=(route,body)=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
 await page.route('**/api/v1/wealth/workspace/reset',route=>json(route,{}));
 await page.setViewportSize({width:1440,height:1000});await page.reload();await page.getByRole('textbox',{name:'向 NOVA 提问'}).waitFor();
 const original=await page.evaluate(()=>{const method=document.startViewTransition;if(!method)return false;window.homeTransitionCalls=0;document.startViewTransition=function(...args){window.homeTransitionCalls++;return method.apply(document,args);};return true;});
 await page.locator('#nova-toggle').click();await page.locator('.nova-docked').waitFor();
 await page.screenshot({path:'output/playwright/home22-transition-1.png'});
 await page.evaluate(()=>new Promise(resolve=>{let count=0;const tick=()=>++count>=8?resolve():requestAnimationFrame(tick);requestAnimationFrame(tick);}));
 await page.screenshot({path:'output/playwright/home22-transition-2.png'});
 await page.evaluate(()=>new Promise(resolve=>{let count=0;const tick=()=>++count>=8?resolve():requestAnimationFrame(tick);requestAnimationFrame(tick);}));
 await page.screenshot({path:'output/playwright/home22-transition-3.png'});
 await page.waitForFunction(()=>document.getAnimations().every(a=>a.playState!=='running'));
 check(!original||await page.evaluate(()=>window.homeTransitionCalls===1),'正常模式使用一次连续空间过渡');
 const opaque=await page.evaluate(()=>({background:getComputedStyle(document.documentElement,'::view-transition-image-pair(home-agent)').backgroundColor,oldOpacity:getComputedStyle(document.documentElement,'::view-transition-old(home-agent)').opacity}));
 check(opaque.background==='rgb(226, 221, 245)'&&opaque.oldOpacity==='1','过渡具有浅紫不透明底与保留旧画面');
 for(const width of [1101,1200,1440,1920]){
   await page.setViewportSize({width,height:1000});
   check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),width+'桌面无横向溢出');
   const columns=await page.locator('.financial-overview').evaluate(node=>getComputedStyle(node).gridTemplateColumns.split(' '));
   check(columns.length===2&&columns[0]===columns[1],width+'概要保持等宽两列');
 }
 await page.setViewportSize({width:1440,height:1000});await page.emulateMedia({reducedMotion:'reduce'});
 const count=await page.evaluate(()=>window.homeTransitionCalls);
 await page.locator('#nova-toggle').click();await page.locator('.nova-expanded').waitFor();
 check(await page.evaluate(()=>window.homeTransitionCalls)===count,'减少动画不启动ViewTransition');
 await page.emulateMedia({reducedMotion:'no-preference'});
 // Existing life services remain directly navigable. Block any automatic context selection writes.
 const writes=[];await page.route('**/api/v1/**',route=>{
   if(route.request().method()==='GET')return route.continue();
   writes.push(new URL(route.request().url()).pathname);return json(route,{});
 });
 await page.getByRole('navigation',{name:'主要功能'}).getByRole('button',{name:'生活服务'}).click();
 await page.getByRole('button',{name:/日历/}).first().waitFor();checks.push('生活服务原入口可达');
 await page.getByRole('navigation',{name:'主要功能'}).getByRole('button',{name:'理财',exact:false}).click();
 await page.getByRole('heading',{name:'理财规划与购买',exact:true}).waitFor();
 check(await page.getByRole('button',{name:'进入测试模式',exact:true}).count()===0,'理财测试模式入口已移除');checks.push('理财原页可达');
 await page.getByRole('navigation',{name:'主要功能'}).getByRole('button',{name:'概览'}).click();
 check(await page.getByTitle(/绑定验证器/).count()===1,'验证器原功能入口保留');
 check(await page.getByTitle('退出登录').count()===1,'头像退出入口保留');
 await page.unrouteAll({behavior:'wait'});await page.route('**/api/v1/wealth/workspace/reset',route=>json(route,{}));
 await page.reload();await page.getByRole('textbox',{name:'向 NOVA 提问'}).waitFor();
 return {checks,opaque,interceptedWrites:writes};
 } finally {
  await page.unrouteAll({behavior:'wait'});
  await page.route('**/api/v1/wealth/workspace/reset',route=>route.fulfill({status:200,contentType:'application/json',body:'{}'}));
  await page.setViewportSize({width:1440,height:1000});
 }
}
