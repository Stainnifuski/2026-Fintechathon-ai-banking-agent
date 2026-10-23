// Run with Playwright CLI in a fresh about:blank session. No enrollment or financial writes reach 8110.
async page => {
 const checks=[];const check=(ok,label)=>{if(!ok)throw new Error(label);checks.push(label);};
 const errors=[],blocked=[],queries=[];let serverUtc='2026-10-31T15:59:50Z';
 page.on('pageerror',error=>errors.push(error.message));
 await page.context().route('**/api/v1/**',route=>{
   const request=route.request();const path=request.url().replace(/^https?:\/\/[^/]+/,'').split('?')[0];
   if(['GET','HEAD','OPTIONS'].includes(request.method())||path.endsWith('/auth/login'))return route.continue();
   blocked.push(path);
   return route.fulfill({status:path.endsWith('/wealth/workspace/reset')?200:409,contentType:'application/json',body:path.endsWith('/wealth/workspace/reset')?'{}':JSON.stringify({detail:'UI隔离：禁止业务写入'})});
 });
 await page.route('**/api/v1/system/time',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({server_time_utc:serverUtc,business_timezone:'Asia/Shanghai'})}));
 await page.route('**/api/v1/bills/analysis?**',async route=>{
   const date=route.request().url().match(/[?&]date=([^&]+)/)?.[1];queries.push(date);
   await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({query:{period:'month',date},as_of:serverUtc,totals:{consumption_fen:0,refund_fen:0,net_consumption_fen:0},categories:[],partial:true,notices:['UI隔离跨月报告，仅验证月份查询与展示。']})});
 });
 await page.clock.install();
 await page.setViewportSize({width:1440,height:1000});await page.goto('http://127.0.0.1:3100/');
 if(await page.getByLabel('手机号',{exact:true}).count()){
   await page.getByLabel('手机号',{exact:true}).fill('13800000001');await page.getByLabel('密码',{exact:true}).fill('password123');await page.getByRole('button',{name:'登录 NOVA'}).click();
 }
 await page.getByRole('textbox',{name:'向 NOVA 提问'}).waitFor();
 const toggle=page.locator('#nova-toggle');
 await toggle.click();await page.locator('.nova-docked').waitFor();await page.clock.runFor(700);
 await page.waitForFunction(()=>document.querySelector('.home-bill-actions button')?.disabled===false);
 check(await page.locator('.home-bill-actions').innerText().then(s=>s.includes('2026-10')),'初次使用服务端北京月份');
 const input=page.getByRole('textbox',{name:'向 NOVA 提问'});await input.fill('修复验收：未发送草稿');
 const panel=await page.locator('#nova-panel').elementHandle();const traffic=[];
 const observe=request=>{if(/\/nova\/|conversations|workspace\/reset/.test(request.url()))traffic.push(request.url());};page.on('request',observe);
 let queryCount=queries.length;
 await page.clock.runFor(61_000);
 check(queries.length===queryCount,'同月定期校准不重查账单');
 serverUtc='2026-10-31T16:00:01Z';
 const november=page.waitForResponse(request=>request.url().includes('/bills/analysis')&&request.url().match(/[?&]date=([^&]+)/)?.[1]==='2026-11');
 await page.clock.runFor(60_000);await november;
 await page.waitForFunction(()=>document.querySelector('.home-bill-actions button')?.disabled===false);
 check(await page.locator('.home-bill-actions').innerText().then(s=>s.includes('2026-11')),'常驻跨北京月初查询新月份');
 check(queries.length===queryCount+1,'跨月只新增一次账单查询');
 serverUtc='2026-11-30T16:00:01Z';
 const december=page.waitForResponse(request=>request.url().includes('/bills/analysis')&&request.url().match(/[?&]date=([^&]+)/)?.[1]==='2026-12');
 await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await december;
 await page.waitForFunction(()=>document.querySelector('.home-bill-actions button')?.disabled===false);
 check(await page.locator('.home-bill-actions').innerText().then(s=>s.includes('2026-12')),'挂起后重新聚焦使用服务端新月份');
 queryCount=queries.length;
 await page.evaluate(()=>{window.dispatchEvent(new Event('focus'));document.dispatchEvent(new Event('visibilitychange'));});
 await page.clock.runFor(1000);
 check(queries.length===queryCount,'同月focus和visibility不重复读取报告');
 const manual=page.waitForResponse(request=>request.url().includes('/bills/analysis'));
 await page.getByRole('button',{name:'刷新账单',exact:true}).click();await manual;
 await page.waitForFunction(()=>document.querySelector('.home-bill-actions button')?.disabled===false);
 check(queries.length===queryCount+1&&queries.at(-1)==='2026-12','正常态手动刷新同月报告');
 check(traffic.length===0,'跨月聚焦手动刷新没有reset或NOVA发送/历史请求');
 check(await input.inputValue()==='修复验收：未发送草稿','月份更新保留聊天草稿');
 check(await panel.evaluate(node=>node===document.querySelector('#nova-panel')),'月份更新保留同一NOVA实例');
 page.off('request',observe);
 await page.screenshot({path:'output/playwright/home22-fix-month-boundary.png'});
 // Button exact color, hover/focus, width and fixed coordinates.
 const style=()=>toggle.evaluate(node=>{const s=getComputedStyle(node),r=node.getBoundingClientRect();return {bg:s.backgroundColor,color:s.color,outline:s.outlineStyle,x:r.x,y:r.y,width:r.width,height:r.height};});
 await page.mouse.move(0,0);await page.waitForFunction(()=>getComputedStyle(document.querySelector('#nova-toggle')).backgroundColor==='rgb(97, 80, 232)');const normal=await style();
 check(normal.bg==='rgb(97, 80, 232)'&&normal.color==='rgb(255, 255, 255)','模式按钮恢复确认稿紫底白字');
 await toggle.hover();await page.waitForFunction(()=>getComputedStyle(document.querySelector('#nova-toggle')).backgroundColor==='rgb(81, 65, 211)');const hover=await style();check(hover.bg==='rgb(81, 65, 211)'&&hover.color==='rgb(255, 255, 255)','模式按钮hover保留紫底白字');
 await page.mouse.move(0,0);await toggle.focus();await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');await page.clock.runFor(300);const focus=await style();check(focus.outline==='solid'&&focus.color==='rgb(255, 255, 255)','模式按钮键盘focus有明确描边');
 await toggle.click();await page.locator('.nova-expanded').waitFor();await page.clock.runFor(700);const expanded=await style();
 check(expanded.x===normal.x&&expanded.y===normal.y&&expanded.width===224&&normal.width===224,'两模式保持同位置224px且单入口');
 check(await toggle.count()===1,'模式切换按钮仅一个');
 await toggle.click();await page.locator('.nova-docked').waitFor();await page.clock.runFor(700);
 // Restore real server time before the real API layout screenshots.
 await page.unroute('**/api/v1/system/time');await page.unroute('**/api/v1/bills/analysis?**');await page.clock.resume();
 await page.reload();await input.waitFor();await toggle.click();await page.locator('.nova-docked').waitFor();await page.waitForFunction(()=>document.getAnimations().every(animation=>animation.playState!=='running'));
 await page.waitForFunction(()=>document.querySelector('.home-bill-actions button')?.disabled===false);
 await page.waitForFunction(()=>document.querySelectorAll('.home-account-cards small').length>=2&&!document.querySelector('.home-account-cards [role=status]'));
 const geometries=[];
 for(const width of [1101,1200,1440,1920]){
   await page.setViewportSize({width,height:1000});
   const geometry=await page.locator('.home-account-row').evaluateAll(rows=>rows.map(row=>{
     const name=row.children[1],balance=row.querySelector('.home-money strong');const nameText=name.querySelector('strong'),number=name.querySelector('small');
     const lines=element=>{const range=document.createRange();range.selectNodeContents(element);return [...range.getClientRects()].filter(r=>r.width>0).length;};
     return {nameWidth:name.getBoundingClientRect().width,nameLines:lines(nameText),numberLines:lines(number),balanceLines:lines(balance),overflow:row.scrollWidth>row.clientWidth};
   }));geometries.push({width,rows:geometry});
   check(geometry.every(row=>row.nameWidth>=75&&row.nameLines===1&&row.numberLines===1&&row.balanceLines===1&&!row.overflow),width+'账户名称卡号和金额不挤成逐字换行');
   check(await page.locator('.home-credit-fields>span').evaluateAll(nodes=>nodes.every(node=>{const r=document.createRange();r.selectNodeContents(node);return new Set([...r.getClientRects()].map(rect=>Math.round(rect.y))).size===1;})),width+'信用字段名与金额成组单行');
   check(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),width+'无横向溢出');
   check(await page.locator('.asset-summary-metrics dd').evaluateAll(nodes=>nodes.every(node=>node.scrollWidth<=node.clientWidth)),width+'资产明细金额不拆分或溢出');
   await input.evaluate(node=>node.blur());await page.screenshot({path:'output/playwright/home22-fix-accounts-'+width+'.png'});
 }
 // Keyboard access to mobile enrollment uses a blocked fixture response, never enables real enrollment.
 await page.setViewportSize({width:390,height:844});
 const auth=page.getByRole('button',{name:'验证器',exact:true});check(await auth.isVisible(),'390px验证器有清晰可见短标签');
 await auth.focus();check(await auth.evaluate(node=>node===document.activeElement),'390px验证器键盘可聚焦');
 const enroll=page.waitForRequest('**/api/v1/auth/totp/enroll');await page.keyboard.press('Enter');await enroll;
 check(blocked.filter(p=>p.endsWith('/auth/totp/enroll')).length===1,'验证器调用原处理器且绑定请求已隔离');
 check(await page.getByTitle('退出登录').isVisible()&&await toggle.isVisible(),'窄屏保留头像退出与NOVA操作');
 check(await page.evaluate(()=>document.documentElement.scrollWidth<=390),'窄屏头部新增入口无横向溢出');
 await page.screenshot({path:'output/playwright/home22-fix-mobile-auth.png'});
 check(errors.length===0,'定向验证无未捕获异常');
 return {checks,queries,normal,hover,focus,geometries,blocked,errors};
}
