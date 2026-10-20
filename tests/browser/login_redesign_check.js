// Playwright CLI run-code; use an independent browser on the 3100/8110 preview.
// Requires seeded 13800000001/password123 and the isolated frontend-preview bank.db.
// Creates ONE uniquely named zero-balance demo user; no payments or financial actions.
// UI-only long error/network fixtures are removed in finally. Never use production.
async page => {
  const results=[], runtimeErrors=[];
  const check=(ok,label)=>{if(!ok)throw Error(label);results.push(label);};
  const onError=e=>runtimeErrors.push(e.message);page.on('pageerror',onError);
  const phone=page.getByLabel('手机号',{exact:true}), password=page.getByLabel('密码',{exact:true});
  const stage=page.locator('.login-stage');
  const settle=()=>page.waitForTimeout(880);
  const state=()=>stage.getAttribute('data-state');
  const geometry=()=>page.evaluate(()=>{
    const rect=s=>{const r=document.querySelector(s).getBoundingClientRect();return {x:r.x,y:r.y+scrollY,width:r.width,height:r.height,bottom:r.bottom,right:r.right};};
    return {left:rect('.login-t1'),right:rect('.login-t2'),title:rect('#login-title'),form:rect('.login-form'),phone:rect('#login-phone'),submit:rect('.login-submit'),scrollWidth:document.documentElement.scrollWidth,width:innerWidth};
  });
  let releaseHeld;
  try {
    await page.goto('http://127.0.0.1:3100/');
    if(await page.getByTitle('退出登录',{exact:true}).isVisible()) await page.getByTitle('退出登录',{exact:true}).click();
    await page.setViewportSize({width:1440,height:900});
    await page.mouse.move(1400,850);await settle();
    check(await page.getByRole('heading',{name:'NOVA 你的银行智能体'}).isVisible(),'approved two-line title renders');
    check(await page.locator('.login-tag').count()===7,'seven approved decorative modules');
    check(await page.locator('.review-bar,.references').count()===0,'review controls and reference images excluded');
    check(await page.getByRole('timer').count()===0,'login clock removed; workspace clock untouched');
    check(await phone.getAttribute('autocomplete')==='username'&&await password.getAttribute('autocomplete')==='current-password','login autocomplete semantics');
    await page.screenshot({path:'output/playwright/login-after-desktop.png'});
    const idle=await geometry();
    await phone.hover();await settle();let current=await geometry();
    check(await state()==='hover'&&Math.abs(current.left.x-idle.left.x+14)<1&&Math.abs(current.right.x-idle.right.x-14)<1,'hover spreads each side 14px');
    await phone.focus();await settle();current=await geometry();
    check(await state()==='focus'&&Math.abs(current.left.x-idle.left.x+38)<1&&Math.abs(current.right.x-idle.right.x-38)<1,'focus spreads each side 38px');
    check(Math.abs(current.title.y-idle.title.y)<1&&Math.abs(current.form.y-idle.form.y)<1,'title and form stay fixed through motion');
    await phone.fill('13800000001');
    check(await state()==='typing','typing enters transient 46px state');
    await page.waitForFunction(()=>document.querySelector('.login-stage').dataset.state==='focus');
    check(await state()==='focus','typing returns to focus after 360ms');
    await password.focus();
    check(await state()==='focus','moving between inputs keeps focus spread');
    await page.screenshot({path:'output/playwright/login-focus-desktop.png'});
    await password.fill('password123');
    await page.getByRole('button',{name:'显示密码',exact:true}).click();
    check(await password.getAttribute('type')==='text'&&await password.inputValue()==='password123','password reveal preserves value without submit');
    await page.getByRole('button',{name:'隐藏密码',exact:true}).click();
    check(await password.getAttribute('type')==='password','password hide restores mask');
    await page.mouse.move(1400,850);await page.locator('#login-title').click();await settle();
    check(await state()==='idle','blur retracts to idle');
    await page.emulateMedia({reducedMotion:'reduce'});await settle();const reduced=await geometry();
    await phone.focus();await settle();current=await geometry();
    check(Math.abs(current.left.x-reduced.left.x)<.1,'reduced motion prevents displacement');
    check(await page.locator('.login-pill').first().evaluate(e=>getComputedStyle(e).animationName)==='none','reduced motion stops floating');
    await page.emulateMedia({reducedMotion:'no-preference'});
    await phone.fill('');await password.fill('');
    await page.getByRole('button',{name:'登录 NOVA',exact:true}).click();
    check(await phone.getAttribute('aria-invalid')==='true'&&await password.getAttribute('aria-invalid')==='true','empty fields show associated inline errors');
    check(await phone.getAttribute('aria-describedby')==='login-phone-error'&&await phone.evaluate(e=>e===document.activeElement),'first invalid field focused with error description');
    await phone.fill('abc');await password.fill('short');
    await page.getByRole('button',{name:'登录 NOVA',exact:true}).click();
    check(await page.getByText('请输入有效手机号',{exact:true}).isVisible()&&await page.getByText('密码至少需要 8 位',{exact:true}).isVisible(),'invalid phone and short password are rejected');
    await page.getByRole('button',{name:'创建账号',exact:true}).click();
    check(await page.getByLabel('姓名',{exact:true}).isVisible()&&await password.getAttribute('autocomplete')==='new-password','registration keeps existing extra field and autocomplete');
    await page.getByRole('button',{name:'注册并登录',exact:true}).click();
    check(await page.getByText('请输入姓名',{exact:true}).isVisible(),'registration validates name');
    await page.getByRole('button',{name:'返回登录',exact:true}).click();
    check(await page.getByLabel('姓名',{exact:true}).count()===0&&await phone.inputValue()==='abc','switch back preserves input and clears validation');
    await page.getByText('演示账号帮助',{exact:true}).click();
    check(await page.getByText('演示账号须已完成当前数据库初始化；通用种子示例：13800000001 / password123',{exact:true}).isVisible(),'original demo account help accessible');
    await page.getByRole('button',{name:'填入演示账号',exact:true}).click();
    check(await phone.inputValue()==='13800000001'&&await password.inputValue()==='password123','demo helper fills credentials without submitting');
    await page.getByText('演示账号帮助',{exact:true}).click();
    for(const size of [{width:1920,height:1080},{width:900,height:900},{width:390,height:844},{width:320,height:568},{width:390,height:400},{width:720,height:450}]){
      await page.setViewportSize(size);await phone.blur();await page.mouse.move(1,1);await settle();
      const g=await geometry();
      check(g.scrollWidth<=size.width&&g.phone.x>=0&&g.phone.right<=size.width&&g.submit.width>220,'responsive form reachable without horizontal overflow '+size.width+'x'+size.height);
      await page.locator('.login-submit').scrollIntoViewIfNeeded();
      check(await page.locator('.login-submit').isVisible(),'submit remains scroll reachable '+size.width+'x'+size.height);
      if(size.width===390&&size.height===844){
        check(await page.locator('.login-tag').evaluateAll(els=>els.filter(e=>getComputedStyle(e).display!=='none').length)===4,'mobile retains four ornaments');
        await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:'output/playwright/login-after-mobile.png',fullPage:true});
        const mobileIdle=await geometry();await phone.focus();await settle();const mobileFocus=await geometry();
        check(Math.abs(mobileFocus.left.x-mobileIdle.left.x+12)<1,'mobile focus spreads only 12px');
        await page.screenshot({path:'output/playwright/login-focus-mobile.png',fullPage:true});
      }
      if(size.width===1920){await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:'output/playwright/login-after-wide.png'});}
      if(size.width===720){await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:'output/playwright/login-reflow-720.png',fullPage:true});}
    }
    await page.setViewportSize({width:1440,height:900});
    await phone.fill('13800000001');await password.fill('invalid-password-frontend');
    const rejected=page.waitForResponse(r=>r.url().endsWith('/auth/login'));
    await page.getByRole('button',{name:'登录 NOVA',exact:true}).click();
    check((await rejected).status()===401,'real backend rejects wrong password');
    await page.getByRole('alert').waitFor();
    check(await phone.inputValue()==='13800000001'&&await password.inputValue()==='invalid-password-frontend','real login error preserves entered values');
    await page.screenshot({path:'output/playwright/login-real-error.png'});
    let release, intercepted=0;const held=new Promise(resolve=>release=resolve);releaseHeld=release;
    await page.route('**/api/v1/auth/login',async route=>{intercepted++;await held;await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:{message:'UI隔离：'+ '较长的服务端错误说明，请稍后重试。'.repeat(15)}})});});
    await password.fill('password123');const beforeLoading=await geometry();
    await page.getByRole('button',{name:'登录 NOVA',exact:true}).click();
    await page.getByRole('button',{name:'正在登录…'}).waitFor();
    check(await phone.isDisabled()&&await password.isDisabled()&&await page.getByRole('button',{name:'创建账号',exact:true}).isDisabled(),'loading disables fields and mode switching');
    await page.locator('.login-form').evaluate(e=>{e.requestSubmit();e.requestSubmit();});
    check(intercepted===1,'duplicate submissions produce one request');
    const loadingGeometry=await geometry();
    check(Math.abs(loadingGeometry.title.y-beforeLoading.title.y)<1&&Math.abs(loadingGeometry.submit.y-beforeLoading.submit.y)<1,'loading keeps title and submit position fixed '+JSON.stringify({before:beforeLoading.submit.y,after:loadingGeometry.submit.y,titleBefore:beforeLoading.title.y,titleAfter:loadingGeometry.title.y}));
    await page.screenshot({path:'output/playwright/login-loading.png'});
    release();await page.getByRole('alert').waitFor();
    check(await password.isEnabled()&&await page.getByRole('alert').innerText().then(t=>t.includes('UI隔离')),'long service error restores retry state');
    await page.setViewportSize({width:390,height:400});await page.locator('.login-submit').scrollIntoViewIfNeeded();
    check((await geometry()).scrollWidth<=390,'long error wraps on short mobile viewport');
    await page.screenshot({path:'output/playwright/login-long-error-mobile.png',fullPage:true});
    await page.unroute('**/api/v1/auth/login');
    await page.route('**/api/v1/auth/login',route=>route.abort('failed'));
    await page.getByRole('button',{name:'登录 NOVA',exact:true}).click();await page.getByRole('alert').filter({hasText:'Network Error'}).waitFor();
    check(await password.isEnabled(),'network failure preserves retry path');await page.unroute('**/api/v1/auth/login');
    await page.setViewportSize({width:1440,height:900});
    await page.getByRole('button',{name:'登录 NOVA',exact:true}).click();
    await page.getByRole('heading',{name:'你好，张三',exact:true}).waitFor();
    check(await page.locator('.login-page').count()===0,'real login uses existing successful workspace transition');
    check(await page.getByRole('timer').isVisible(),'workspace business clock remains visible');
    await page.getByTitle('退出登录',{exact:true}).click();
    if(await page.getByRole('button',{name:'创建账号',exact:true}).isVisible()) await page.getByRole('button',{name:'创建账号',exact:true}).click();
    await page.getByLabel('姓名',{exact:true}).fill('前端登录验收');
    const testPhone='199'+Date.now().toString().slice(-8);
    await phone.fill(testPhone);await password.fill('Ui-check-password123');
    await page.screenshot({path:'output/playwright/login-register.png'});
    const registered=page.waitForResponse(r=>r.url().endsWith('/auth/register'));
    await page.getByRole('button',{name:'注册并登录',exact:true}).click();
    check((await registered).status()===201,'real isolated preview registration succeeds');
    await page.getByRole('heading',{name:'你好，前端登录验收',exact:true}).waitFor();
    check(await page.locator('.login-page').count()===0,'registered user enters existing workspace');
    await page.getByTitle('退出登录',{exact:true}).click();
    if(await page.getByRole('button',{name:'创建账号',exact:true}).isVisible()) await page.getByRole('button',{name:'创建账号',exact:true}).click();
    await page.getByLabel('姓名',{exact:true}).fill('前端登录验收');
    await phone.fill(testPhone);await password.fill('Ui-check-password123');
    await page.getByRole('button',{name:'注册并登录',exact:true}).click();await page.getByRole('alert').waitFor();
    check((await page.getByRole('alert').innerText()).startsWith('注册失败：'),'real duplicate registration error uses accurate mode label');
    await page.getByRole('button',{name:'返回登录',exact:true}).click();await phone.fill('');await password.fill('');
    await page.locator('#login-title').click();await page.mouse.move(1400,850);await settle();await page.evaluate(()=>scrollTo(0,0));
    await page.screenshot({path:'output/playwright/login-final-desktop.png'});
    check(runtimeErrors.length===0,'no uncaught runtime exceptions');
    return {results,runtimeErrors,testPhone,notes:['503/long error/network failure are UI fixtures','registration creates one zero-balance demo user in independent 8110 DB','720x450 is 200%-zoom-equivalent layout viewport, not native text zoom','short viewport is keyboard-space simulation, not physical phone keyboard']};
  }finally{releaseHeld?.();page.off('pageerror',onError);await page.unroute('**/api/v1/auth/login');await page.emulateMedia({reducedMotion:'no-preference'});await page.setViewportSize({width:1440,height:900});}
}
