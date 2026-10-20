// Playwright CLI run-code after login_redesign_check.js, logged out in login mode.
// Uses only field focus, empty validation and screenshots; no auth submissions.
async page=>{
 const results=[],check=(ok,label)=>{if(!ok)throw Error(label);results.push(label);};
 await page.setViewportSize({width:1440,height:900});
 const phone=page.getByLabel('手机号',{exact:true}),password=page.getByLabel('密码',{exact:true});
 await phone.focus();await page.keyboard.press('Tab');
 check(await password.evaluate(e=>e===document.activeElement),'keyboard Tab moves phone to password');
 check(await page.locator('.login-stage').getAttribute('data-state')==='focus','keyboard field transition retains spread');
 await page.keyboard.press('Tab');
 check(await page.getByRole('button',{name:'显示密码',exact:true}).evaluate(e=>e===document.activeElement),'password toggle keyboard reachable');
 await page.keyboard.press('Space');
 check(await password.getAttribute('type')==='text','Space toggles password without submitting');
 await page.keyboard.press('Space');
 await page.keyboard.press('Tab');
 check(await page.getByRole('button',{name:'登录 NOVA',exact:true}).evaluate(e=>e===document.activeElement),'submit keyboard reachable');
 await page.locator('#login-title').click();await page.mouse.move(1400,850);await page.waitForTimeout(900);
 await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:'output/playwright/login-final-desktop.png'});
 await page.emulateMedia({reducedMotion:'reduce'});
 await phone.focus();await page.screenshot({path:'output/playwright/login-reduced-motion.png'});
 await page.emulateMedia({reducedMotion:'no-preference'});
 await page.setViewportSize({width:390,height:844});await page.locator('#login-title').click();await page.mouse.move(1,1);await page.waitForTimeout(900);
 await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:'output/playwright/login-final-mobile.png',fullPage:true});
 await page.getByRole('button',{name:'创建账号',exact:true}).click();
 await page.getByLabel('姓名',{exact:true}).fill('');await phone.fill('');await password.fill('');
 await page.screenshot({path:'output/playwright/login-register-mobile.png',fullPage:true});
 await page.getByRole('button',{name:'注册并登录',exact:true}).click();
 check(await page.getByText('请输入姓名',{exact:true}).isVisible(),'mobile registration inline validation');
 await page.screenshot({path:'output/playwright/login-register-errors-mobile.png',fullPage:true});
 await page.getByRole('button',{name:'返回登录',exact:true}).click();
 await page.setViewportSize({width:1440,height:900});
 return {results};
}
