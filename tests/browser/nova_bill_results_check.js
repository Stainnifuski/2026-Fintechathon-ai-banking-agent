/** Independent bill-results demo only. Query snapshots and all reads/updates use
 * real local bank APIs. Model invocation is covered by the authenticated API
 * integration test using an isolated transport. */
async page => {
 const checks=[],errors=[],warnings=[];
 const check=(ok,label)=>{if(!ok)throw new Error(label);checks.push(label);};
 page.on('pageerror',error=>errors.push(error.message));
 page.on('console',message=>{if(message.type()==='warning'&&message.text().includes('[ECharts]'))warnings.push(message.text());});
 await page.setViewportSize({width:1600,height:1100});
 await page.reload();
 const token=await page.evaluate(()=>localStorage.getItem('token'));
 const headers={Authorization:'Bearer '+token};
 const read=async path=>{const response=await page.request.get(new URL('/api/v1'+path,page.url()).href,{headers});if(!response.ok())throw new Error('Local API failed '+response.status());return response.json();};
 const balances=(await read('/accounts/U001/accounts')).map(a=>[a.account_id,a.balance_fen]);
 const conversations=await read('/bank/nova/conversations');const session=conversations[0].session_id;
 const snapshots=await read('/nova/conversations/'+session+'/bill-results');
 const analysis=page.locator('[data-bill-result=analysis]').first();
 await analysis.waitFor();
 check(await page.locator('[data-bill-result]').count()===10,'历史会话恢复十类结构化查询卡');
 check(!(await page.locator('.nova-result-evidence').first().evaluate(element=>element.open)),'长篇完整回答默认折叠，摘要与图表优先展示');
 const raw=await read('/bills/analysis?date=2026-10');
 const format=value=>(value/100).toLocaleString('zh-CN',{style:'currency',currency:'CNY'});
 check((await analysis.locator('.bill-result-metrics').first().innerText()).includes(format(raw.totals.net_consumption_fen)),'总览指标与真实银行查询逐分一致');
 check(await analysis.getByText('查询时快照',{exact:true}).isVisible(),'卡片明确标注查询时快照');
 await analysis.locator('canvas').first().waitFor();
 check(await analysis.locator('canvas').count()===2,'消费分类和趋势使用真实图表');
 await analysis.locator('.bill-result-chart').first().locator('summary').click();
 await analysis.getByRole('button',{name:'餐饮 ↗',exact:true}).click();
 await page.getByRole('tab',{name:'交易明细',exact:true}).waitFor();
 await page.waitForFunction(()=>document.querySelector('#bill-tab-transactions')?.getAttribute('aria-selected')==='true');
 await page.waitForFunction(()=>document.querySelector('.bill-scope-filter select')!==null);
 check(await page.locator('.bill-scope-filter select').nth(2).inputValue()==='dining','分类图可按原账期与分类跳转最新交易明细');
 const detail=page.locator('[data-bill-result=transaction]').first();
 await detail.getByRole('button',{name:'查看关联工作台 ↗',exact:true}).click();
 await page.getByRole('complementary',{name:'交易详情',exact:true}).getByText('演示数码商城',{exact:true}).first().waitFor();
 check((await page.getByRole('complementary',{name:'交易详情',exact:true}).innerText()).includes('演示数码商城'),'结果卡可打开对应交易详情');
 const budgetCard=page.locator('[data-bill-result=budgets]').first();
 const progress=budgetCard.getByRole('progressbar').first();
 check(await progress.getAttribute('value')==='100','超预算时进度条限制在100%，保留实际使用比例');
 check((await budgetCard.innerText()).includes('2240%'),'超预算实际比例不被截断为100%');
 const oldBudget=(await read('/bills/budgets?month=2026-10')).items.find(item=>!item.account_id&&!item.category);
 const change=await page.request.put(new URL('/api/v1/bills/budgets',page.url()).href,{headers,data:{month:'2026-10',amount_fen:15000,expected_version:oldBudget.version}});
 check(change.ok(),'通过真实API改变后续预算数据');
 await page.reload();await analysis.waitFor();
 const restored=await read('/nova/conversations/'+session+'/bill-results');
 check(JSON.stringify(restored)===JSON.stringify(snapshots),'刷新恢复原查询快照，不被后续预算更新覆盖');
 check((await page.locator('[data-bill-result=budgets]').first().innerText()).includes('¥100.00'),'历史预算卡保留原始预算金额');
 await analysis.scrollIntoViewIfNeeded();await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 await page.screenshot({path:'output/playwright/nova-bill-results-desktop.png',animations:'disabled'});
 await page.setViewportSize({width:390,height:844});
 await analysis.scrollIntoViewIfNeeded();
 check(await page.evaluate(()=>document.documentElement.scrollWidth===innerWidth),'390px手机布局无整体横向溢出');
 await page.screenshot({path:'output/playwright/nova-bill-results-mobile.png',animations:'disabled'});
 check(JSON.stringify((await read('/accounts/U001/accounts')).map(a=>[a.account_id,a.balance_fen]))===JSON.stringify(balances),'查询与图表交互不改变账户余额');
 check(errors.length===0,'无页面运行时异常');
 check(warnings.length===0,'图表无隐藏尺寸告警');
 return {checks,bank:'real independent fictional database',model:'not called; new runtime covered by mocked model transport integration'};
}
