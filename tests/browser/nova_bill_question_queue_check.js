/** Isolated browser model transport: hold the first response while submitting
 * two more bill questions, then verify FIFO delivery without duplicates. */
async page => {
  const questions = ['UI队列测试一','UI队列测试二','UI队列测试三'];
  const sent = [];
  let releaseFirst;
  const firstGate = new Promise(resolve => { releaseFirst = resolve; });
  await page.route('**/nova/chat/stream', async route => {
    const body = route.request().postDataJSON();
    sent.push(body);
    if (sent.length === 1) await firstGate;
    await route.fulfill({status:200,contentType:'text/event-stream',body:`data: ${JSON.stringify({
      type:'done',reply:'UI队列测试已响应：'+body.message,mode:'model',session_id:body.session_id,tool_calls:[],
    })}\n\n`});
  });
  await page.getByRole('navigation',{name:'主要功能'}).getByRole('button',{name:'◫ 账单洞察',exact:true}).click();
  await page.getByRole('heading',{name:'账单分析',exact:true}).waitFor();
  const firstRequest = page.waitForRequest('**/nova/chat/stream');
  for (let index=0;index<questions.length;index++) {
    await page.getByLabel('向 NOVA 提问账单',{exact:true}).fill(questions[index]);
    await page.getByRole('button',{name:'交给 NOVA 分析',exact:true}).click();
    if (index===0) await firstRequest;
  }
  if (sent.length!==1) throw new Error('NOVA忙碌时不应并发发送后续问题');
  releaseFirst();
  for (const question of questions) await page.getByText('UI队列测试已响应：'+question,{exact:true}).waitFor();
  if (JSON.stringify(sent.map(item=>item.message))!==JSON.stringify(questions)) throw new Error('问题必须按顺序恰好发送一次');
  if (sent.some(item=>item.page_context.page!=='insights'||item.page_context.filters.date!=='2026-10')) throw new Error('每项应保留提交时的账单筛选快照');
  await page.unroute('**/nova/chat/stream');
  return {questions:sent.length,checks:['忙碌时排队','连续三问不丢失','按序发送且无重复','各自保留账单上下文'],model:'mocked SSE only'};
}
