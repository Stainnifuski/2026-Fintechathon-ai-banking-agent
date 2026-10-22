// Playwright CLI run-code. Requires an authenticated independent 3100 browser.
// Browser fixtures block workspace reset and financial writes; never use a production session.
async page => {
 const checks=[];const check=(value,label)=>{if(!value)throw new Error(label);checks.push(label);};
 const errors=[];const onError=error=>errors.push(error.message);page.on('pageerror',onError);
 const reset='**/api/v1/wealth/workspace/reset';const report='**/api/v1/bills/analysis**';
 await page.route(reset,route=>route.fulfill({status:200,contentType:'application/json',body:'{}'}));
 const nav=name=>page.getByRole('navigation',{name:'主要功能'}).getByRole('button',{name,exact:true});
 const charts=async count=>{await page.getByRole('heading',{name:'账单分析',exact:true}).waitFor();await page.waitForFunction(min=>document.querySelectorAll('.bills canvas').length>=min,count);};
 try{
 await page.setViewportSize({width:1440,height:1000});await page.reload();await nav('◫ 账单洞察').click();await charts(2);checks.push('真实空账单ECharts初始化正常');
 for(let i=0;i<3;i++){await nav('⌂ 概览').click();await nav('◫ 账单洞察').click();await charts(2);}checks.push('首页账单往返三次无白屏');
 const canvas=await page.locator('.bills canvas').first().elementHandle();
 await page.locator('#nova-toggle').click();await page.locator('.nova-expanded').waitFor();
 await page.locator('#nova-toggle').click();await page.locator('.nova-docked').waitFor();
 check(await canvas.evaluate(node=>node===document.querySelector('.bills canvas')),'大屏往返保留已挂载账单图表');
 await page.getByRole('button',{name:'更多示例',exact:true}).click();await page.getByRole('button',{name:'问：解释当前账单',exact:true}).waitFor();checks.push('账单上下文提问保留');await page.getByRole('button',{name:'收起示例',exact:true}).click();
 await page.route(report,async route=>{const response=await route.fetch();const data=await response.json();data.totals.consumption_fen=12300;data.totals.net_consumption_fen=12300;data.categories=[{category:'dining',label:'餐饮（UI隔离样例）',amount_fen:12300,count:1,share_percent:100,refund_fen:0,net_amount_fen:12300}];data.trend=data.trend.map((p,i)=>({...p,consumption_fen:i===0?12300:0}));data.report=['UI隔离非空图表样例，不是银行实际交易。'];await route.fulfill({response,json:data});});
 await nav('⌂ 概览').click();await nav('◫ 账单洞察').click();await charts(3);await page.getByText('UI隔离非空图表样例，不是银行实际交易。',{exact:true}).waitFor();checks.push('非空饼图趋势图现金流图正常');
 await page.waitForFunction(()=>document.getAnimations().every(a=>a.playState!=='running'));await page.screenshot({path:'output/playwright/home22-bills-fixture-nonempty.png'});
 await page.unroute(report);await page.route(report,route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:{message:'UI隔离：账单读取失败，请重试。'}})}));
 await nav('⌂ 概览').click();await nav('◫ 账单洞察').click();await page.getByRole('alert').filter({hasText:'UI隔离：账单读取失败，请重试。'}).waitFor();check(await page.getByRole('navigation',{name:'主要功能'}).isVisible(),'账单失败保留导航和错误外壳');
 await page.unroute(report);await page.getByRole('button',{name:'查看账单',exact:true}).click();await charts(2);checks.push('账单错误移除后原接口重试恢复');
 await page.setViewportSize({width:390,height:844});check(await page.evaluate(()=>document.documentElement.scrollWidth<=390),'390px账单与助手无横向溢出');
 await page.getByRole('button',{name:'收起 NOVA',exact:true}).click();check(await page.getByRole('heading',{name:'账单分析',exact:true}).isVisible(),'窄屏关闭NOVA保留账单可操作');
 await page.setViewportSize({width:1440,height:1000});await page.locator('#nova-toggle').click();await page.locator('.nova-expanded').waitFor();checks.push('窄屏关闭后桌面展开正确返回大屏');
 await page.getByTitle('退出登录').click();await page.locator('#login-title').waitFor();check((await page.locator('#login-title').innerText()).includes('你的银行智能体'),'已确认登录页保留');
 check(await page.getByLabel('手机号',{exact:true}).inputValue()==='','登录初始不填凭据');await page.screenshot({path:'output/playwright/home22-login-preserved.png'});
 await page.getByLabel('手机号',{exact:true}).fill('13800000001');await page.getByLabel('密码',{exact:true}).fill('password123');await page.getByRole('button',{name:'登录 NOVA'}).click();await page.getByRole('textbox',{name:'向 NOVA 提问'}).waitFor();checks.push('原登录链路正常进入NOVA');
 check(errors.length===0,'账单与登录流程无未捕获异常');
 return {checks,errors};
 }finally{await page.unroute(report);page.off('pageerror',onError);await page.setViewportSize({width:1440,height:1000});}
}
