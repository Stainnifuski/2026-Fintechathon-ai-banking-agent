/** Run only with the independent nova-bills-demo database and a seeded pending
 * 2000-yuan October budget card. Only the model SSE response is mocked; card
 * reads, confirmation, ledger checks and report refresh use real local APIs.
 */
async page => {
  const checks = [];
  const errors = [];
  const legacyCalls = [];
  const sent = [];
  const check = (condition, text) => { if (!condition) throw new Error(text); checks.push(text); };
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (request.url().includes('/bills/messages')) legacyCalls.push(request.url()); });
  let reveal = false;
  await page.route('**/nova/conversations/*/bill-actions', route => reveal ? route.continue() : route.fulfill({json:[]}));
  await page.route('**/nova/chat/stream', async route => {
    const body = route.request().postDataJSON();
    sent.push(body);
    const response = await page.request.get(new URL(`/api/v1/nova/conversations/${body.session_id}/bill-actions`,page.url()).href, {
      headers:{Authorization:route.request().headers().authorization},
    });
    const card = (await response.json()).find(card => card.status === 'pending');
    check(!!card, '隔离库中的真实确认卡可读取');
    reveal = true;
    const result = {type:'done',reply:'UI 隔离模型回复：请核对下方预算确认卡，尚未保存。',mode:'model',session_id:body.session_id,
      tool_calls:[{name:'prepare_bill_change',result:{source:'bills.preference_preview',data_kind:'simulated_bank',data:{card,executed:false}}}]};
    await route.fulfill({status:200,contentType:'text/event-stream',body:`data: ${JSON.stringify(result)}\n\n`});
  });
  await page.setViewportSize({width:1440,height:1000});
  await page.reload();
  await page.waitForFunction(()=>document.querySelector('.app')?.getBoundingClientRect().width>1100);
  await page.getByRole('navigation',{name:'主要功能'}).getByRole('button',{name:'◫ 账单洞察',exact:true}).click();
  await page.getByRole('heading',{name:'账单分析',exact:true}).waitFor();
  const bill = page.locator('.bills');
  await bill.getByRole('button',{name:'选择这笔',exact:true}).first().click();
  // An unsubmitted draft must not replace the report's applied October scope.
  await page.getByLabel('账单时间',{exact:true}).fill('2026-09');
  await page.getByLabel('向 NOVA 提问账单',{exact:true}).fill('把本月总预算设为2000元');
  await page.getByRole('button',{name:'交给 NOVA 分析',exact:true}).click();
  const card = page.getByRole('article',{name:'账单修改确认卡'});
  await card.getByRole('button',{name:'确认保存',exact:true}).waitFor();
  check(sent.length===1 && sent[0].message==='把本月总预算设为2000元','账单页提问只提交一次NOVA流式接口');
  check(sent[0].page_context.page==='insights' && sent[0].page_context.filters.date==='2026-10','继承已应用账期，不使用未提交的筛选草稿');
  check(sent[0].page_context.selected?.type==='transaction','继承选中流水');
  check(legacyCalls.length===0,'不再调用独立的旧账单消息接口');
  check(await card.getByText('待本人确认 · 尚未保存',{exact:true}).isVisible(),'预览明确显示尚未保存');
  await card.scrollIntoViewIfNeeded();
  await page.waitForFunction(()=>document.documentElement.clientWidth===1440);
  await page.screenshot({path:'output/playwright/nova-bills-preview-desktop.png'});
  await page.setViewportSize({width:390,height:844});
  await card.getByRole('button',{name:'确认保存',exact:true}).scrollIntoViewIfNeeded();
  await page.waitForFunction(()=>document.documentElement.clientWidth===390);
  check(await card.getByRole('button',{name:'确认保存',exact:true}).isVisible(),'手机可操作确认卡');
  check(await page.evaluate(()=>document.documentElement.scrollWidth===innerWidth),'手机页面无横向溢出');
  await page.screenshot({path:'output/playwright/nova-bills-preview-mobile.png'});
  await card.getByRole('button',{name:'确认保存',exact:true}).click();
  await card.getByText('已确认保存',{exact:true}).waitFor();
  await page.getByRole('complementary',{name:'NOVA 对话'}).getByRole('button',{name:'收起 NOVA',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.bill-budget-list')?.textContent?.includes('¥2,000.00'));
  check(true,'本人确认后通过真实API保存，账单预算同步刷新');
  await page.screenshot({path:'output/playwright/nova-bills-saved-mobile.png'});
  await page.unroute('**/nova/conversations/*/bill-actions');
  await page.unroute('**/nova/chat/stream');
  await page.setViewportSize({width:1440,height:1000});
  await page.reload();
  await page.getByRole('article',{name:'账单修改确认卡'}).getByText('已确认保存',{exact:true}).waitFor();
  check(true,'刷新后从服务端恢复已处理确认卡，无需重复保存');
  check(errors.length===0,'无页面运行时异常');
  return {checks,model:'mocked SSE only',writes:'real isolated database'};
}
