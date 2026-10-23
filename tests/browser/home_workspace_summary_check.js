// Playwright CLI run-code. Requires an authenticated independent 3100 browser.
// Browser fixtures block workspace reset and financial writes; never use a production session.
async page => {
 try {
 const checks=[];const check=(value,label)=>{if(!value)throw new Error(label);checks.push(label);};
 const json=(route,body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
 const writes=[];let mode='loading';let release;const gate=new Promise(resolve=>{release=resolve;});
 await page.route('**/api/v1/**',route=>{
   if(route.request().method()==='GET')return route.continue();
   if(route.request().url().endsWith('/wealth/workspace/reset'))return json(route,{});
   writes.push(new URL(route.request().url()).pathname);return route.abort();
 });
 const categories=['餐饮美食','购物消费','交通出行','休闲娱乐','居住缴费','其他消费'].map((label,index)=>({category:'ui-category-'+index,label,amount_fen:(6-index)*1000,count:1,share_percent:(6-index)/21*100}));
 const summary={schema_version:'wealth-asset-summary-v1',asset_scope_id:'ui-scope',account_ids:[],snapshot_id:'ui-snapshot',as_of:'2026-10-22T00:00:00Z',business_at:'2026-10-22T00:00:00Z',cash_balance_fen:0,held_fen:0,available_cash_fen:0,positions_market_value_fen:0,redemption_receivable_fen:0,total_assets_fen:0,valuation_complete:true,missing_fields:[],data_source:'ui-isolated'};
 await page.route('**/api/v1/bills/analysis?**',async route=>{
   if(mode==='loading')await gate;
   if(mode==='error')return json(route,{detail:{message:'UI故障'}},503);
   return json(route,{totals:{consumption_fen:mode==='empty'?0:21000,refund_fen:0,net_consumption_fen:mode==='empty'?0:21000},categories:mode==='empty'?[]:categories,partial:true,notices:[]});
 });
 await page.route('**/api/v1/wealth/asset-summary',route=>mode==='error'?json(route,{detail:{message:'UI故障'}},503):json(route,mode==='missing'?{...summary,positions_market_value_fen:null,total_assets_fen:null,valuation_complete:false,missing_fields:['positions_market_value_fen']}:summary));
 await page.route('**/api/v1/cards',route=>mode==='error'?json(route,{detail:{message:'UI故障'}},503):mode==='populated'||mode==='loading'?route.continue():json(route,[]));
 await page.route('**/api/v1/accounts/*/accounts',route=>mode==='error'?json(route,{detail:{message:'UI故障'}},503):mode==='populated'||mode==='loading'?route.continue():json(route,[]));
 await page.route('**/api/v1/subscriptions',route=>mode==='error'?json(route,{detail:{message:'UI故障'}},503):mode==='populated'||mode==='loading'?route.continue():json(route,[]));
 const dock=async()=>{await page.getByRole('button',{name:'查看财务概要·收起对话',exact:true}).click();await page.locator('.nova-docked').waitFor();await page.waitForFunction(()=>document.getAnimations().every(a=>a.playState!=='running'));};
 await page.setViewportSize({width:1440,height:1000});await page.reload();await dock();await page.getByText('正在读取本月账单…',{exact:true}).waitFor();checks.push('分类独立显示真实加载状态');
 mode='populated';release();await page.locator('.home-category').first().waitFor();
 check(await page.locator('.home-category').count()===6,'显示全部六类而非硬编码四类');
 check(await page.locator('.home-spend-total').innerText().then(text=>text.includes('210.00')),'分类总额按分转换为元');
 check(await page.locator('.home-spend-bar i').count()===6,'全部分类映射多色条');
 await page.screenshot({path:'output/playwright/home22-fixture-categories.png'});
 mode='empty';await page.reload();await dock();await page.getByText('本月暂无消费记录',{exact:true}).waitFor();await page.getByText('暂无账户记录',{exact:true}).waitFor();await page.getByText('暂无生效中的订阅',{exact:true}).waitFor();checks.push('账户订阅和分类独立空态');
 await page.screenshot({path:'output/playwright/home22-fixture-empty.png'});
 mode='error';await page.reload();await dock();await page.getByText('本月支出暂时无法读取。',{exact:true}).waitFor();await page.getByText('总资产查询失败，请刷新重试。',{exact:true}).waitFor();
 check(await page.getByText('账户与订阅读取失败，请重试。',{exact:true}).count()===2,'账户订阅错误不冒充空态');
 check(await page.getByText('本月暂无消费记录',{exact:true}).count()===0,'支出失败不显示零消费空态');
 check(await page.locator('.asset-summary-total').innerText()==='暂无法查询','资产错误不填零');
 await page.screenshot({path:'output/playwright/home22-fixture-failure.png'});
 mode='missing';await page.reload();await dock();await page.getByText('暂无法完整估值',{exact:true}).waitFor();checks.push('缺少估值保留不完整提示');
 mode='populated';await page.getByRole('button',{name:'刷新总资产',exact:true}).click();await page.getByText('暂无法完整估值',{exact:true}).waitFor({state:'hidden'});checks.push('资产失败或缺失可按原接口刷新恢复');
 check(writes.length===0,'概要路径零未隔离写入');
 await page.unrouteAll({behavior:'wait'});
 // Keep the pre-existing login boot from resetting live workspace on final reload.
 await page.route('**/api/v1/wealth/workspace/reset',route=>json(route,{}));
 await page.reload();await page.getByRole('textbox',{name:'向 NOVA 提问'}).waitFor();
 return {checks,writes};
 } finally {
  await page.unrouteAll({behavior:'wait'});
  await page.route('**/api/v1/wealth/workspace/reset',route=>route.fulfill({status:200,contentType:'application/json',body:'{}'}));
  await page.setViewportSize({width:1440,height:1000});
 }
}
