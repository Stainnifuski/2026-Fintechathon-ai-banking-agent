/** Independent bill-workspace demo only: seed a pending 400-yuan October
 * budget card through the real bank service first. Confirmation uses the real
 * authenticated API; no model call or actual financial ledger is used. */
async page => {
  const checks=[];
  const check=(condition,text)=>{if(!condition)throw new Error(text);checks.push(text);};
  const token=await page.evaluate(()=>localStorage.getItem('token'));
  const read=async path=>(await page.request.get(new URL('/api/v1'+path,page.url()).href,{headers:{Authorization:'Bearer '+token}})).json();
  const old=(await read('/bills/budgets?month=2026-10')).items.find(item=>!item.account_id&&!item.category);
  await page.setViewportSize({width:1440,height:1000});await page.reload();
  // Keep the same card locator after its status changes to completed.
  const card=page.getByRole('article',{name:'账单修改确认卡'}).last();
  const surface=page.locator('[data-nova-task^="bill:"]').last();
  await card.getByRole('button',{name:'确认保存',exact:true}).waitFor();
  check(await card.getByText('修改前',{exact:true}).count()>0&&await card.getByText('修改后',{exact:true}).count()>0,'确认卡按字段显示修改前后');
  check(await card.getByText('¥400.00',{exact:true}).isVisible(),'确认卡使用真实服务端预览金额');
  const before=(await read('/bills/budgets?month=2026-10')).items.find(item=>item.budget_id===old.budget_id);
  check(before.version===old.version&&before.amount_fen===old.amount_fen,'展示预览尚未写入预算');
  await card.scrollIntoViewIfNeeded();await page.screenshot({path:'output/playwright/bill-workspace-nova-card-desktop.png',animations:'disabled'});
  await page.setViewportSize({width:390,height:844});await card.getByRole('button',{name:'确认保存',exact:true}).scrollIntoViewIfNeeded();
  check(await page.evaluate(()=>document.documentElement.scrollWidth===innerWidth),'手机确认卡无整体横向溢出');
  await page.screenshot({path:'output/playwright/bill-workspace-nova-card-mobile.png',animations:'disabled'});
  await card.getByRole('button',{name:'确认保存',exact:true}).click();
  await surface.getByText('已完成',{exact:true}).waitFor();
  await surface.getByRole('button',{name:'展开',exact:true}).click();
  await card.getByText('¥400.00',{exact:true}).waitFor();
  const saved=(await read('/bills/budgets?month=2026-10')).items.find(item=>item.budget_id===old.budget_id);
  check(saved.amount_fen===40000&&saved.version===old.version+1,'本人确认通过真实API保存一次');
  await page.getByRole('complementary',{name:'NOVA 对话'}).getByRole('button',{name:'收起 NOVA',exact:true}).click();
  await page.getByRole('navigation',{name:'主要功能'}).getByRole('button',{name:'◫ 账单洞察',exact:true}).click();
  await page.getByRole('tab',{name:'预算管理',exact:true}).click();
  await page.locator('.bill-budget-list article').getByText(/预算 ¥400.00/).waitFor();
  check(true,'统一确认卡保存后手操预算区同步展示');
  await page.setViewportSize({width:1440,height:1000});await page.reload();
  await surface.getByRole('button',{name:'展开',exact:true}).click();
  await surface.getByText('已完成',{exact:true}).waitFor();
  await card.getByText('¥400.00',{exact:true}).waitFor();
  check(true,'刷新后恢复卡片字段及完成状态');
  return {checks,bank:'real independent fictional database',model:'not invoked'};
}
