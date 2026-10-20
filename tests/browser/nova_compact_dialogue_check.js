async page => {
  const results = [];
  const check = (condition, name) => { if (!condition) throw new Error(name); results.push(name); };
  const panel = page.getByRole('complementary', { name: 'NOVA 对话' });
  const input = panel.getByRole('textbox', { name: '向 NOVA 提问' });
  const calls = [];
  const watch = request => { if (request.url().endsWith('/nova/chat/stream')) calls.push(request.postDataJSON()); };
  page.on('request', watch);
  const pattern = '**/api/v1/nova/chat/stream';
  try {
    await panel.getByRole('button', { name: '更多示例', exact: true }).click();
    await panel.getByRole('button', { name: '问：北京时间', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.nova-assistant')?.textContent.includes('离线规则'));
    check(!await panel.locator('#nova-help').count(), '提问后自动收起更多示例');
    check(!await panel.locator('.nova-quick-prompts').count(), '开始聊天后不重复显示开场示例');
    check((await panel.locator('.nova-assistant').first().innerText()).includes('北京时间'), '真实离线时间查询收到回答');
    check(await input.isEnabled(), '回答后恢复输入');
    check(await panel.getByText('时间查询', { exact:true }).isVisible(), '处理来源仍可展开');
    await panel.getByText('时间查询', { exact:true }).click();
    check((await panel.locator('.nova-assistant details').first().innerText()).includes('Asia/Shanghai'), '来源详情仍可核对');
    await panel.getByRole('button', { name: '更多示例', exact: true }).click();
    await panel.getByRole('button', { name: '问：下次扣费时间', exact: true }).click();
    await page.waitForFunction(() => document.querySelectorAll('.nova-assistant').length === 2 && !document.querySelector('.nova-message [role="status"]'));
    check(calls[1].page_context.page === 'subscriptions' && !!calls[1].page_context.subscriptions.selected_subscription_id, '真实订阅查询传递当前选中对象');
    check((await panel.locator('.nova-assistant').last().innerText()).includes('离线规则'), '选中对象查询由真实离线工具返回');
    await page.screenshot({path:'output/playwright/nova20-after-mobile-chat.png'});
    const initialMessages = await panel.locator('.nova-message').count();
    await page.route(pattern, route => route.fulfill({ status:503, contentType:'application/json', body:JSON.stringify({detail:{message:'UI隔离错误：暂时无法查询，请重发。',code:'UI_TEST_FAILURE'}}) }));
    await input.fill('现在北京时间几点？');
    await panel.getByRole('button', { name:'发送', exact:true }).click();
    await panel.getByRole('alert').waitFor();
    check((await input.inputValue()) === '现在北京时间几点？', '失败后原问题保留在输入框');
    check(await panel.locator('.nova-message').count() === initialMessages, '失败消息和半截回复不进入后续上下文');
    await page.screenshot({path:'output/playwright/nova20-ui-fixture-error.png'});
    await page.unroute(pattern);
    await panel.getByRole('button', { name:'发送', exact:true }).click();
    await page.waitForFunction(() => document.querySelectorAll('.nova-assistant').length === 3 && !document.querySelector('.nova-message [role="status"]'));
    check(!await panel.locator('.nova-error').count(), '移除错误拦截后真实重发成功并清除错误');
    check(!('history' in calls[3]) && calls[3].session_id === calls[1].session_id, '重发由服务端会话管理历史，不上传客户端历史');
    check(calls[3].request_id === calls[2].request_id, '503重试复用请求编号，避免重复建单');
    await page.setViewportSize({width:1440,height:1000});
    await page.screenshot({path:'output/playwright/nova20-after-desktop-chat.png'});
    const fixtureReply = '# UI隔离长内容示例\n\n' + '这段合成文字只检查界面滚动，不是模型或银行结果。\n\n'.repeat(30) + '| 项目 | 较长的说明文字 |\n|---|---|\n| 示例 | ' + '很长的表格内容'.repeat(30) + ' |';
    await page.route(pattern, async route => {
      await page.waitForTimeout(600);
      const events = [{type:'tool',name:'get_current_time'},{type:'delta',text:fixtureReply},{type:'done',reply:fixtureReply,mode:'offline',tool_calls:[]}];
      await route.fulfill({status:200,contentType:'text/event-stream',body:events.map(event => 'data: '+JSON.stringify(event)+'\n\n').join('')});
    });
    await input.fill('UI隔离检查：显示长内容');
    await panel.getByRole('button', {name:'发送',exact:true}).click();
    check(!await input.isEnabled(), '请求处理中输入禁用，避免重复发送');
    await page.waitForFunction(() => document.querySelector('.nova-markdown') && Array.from(document.querySelectorAll('.nova-assistant')).at(-1)?.textContent.includes('UI隔离长内容示例'));
    await page.waitForFunction(() => !document.querySelector('.nova-message [role="status"]'));
    for (const size of [{width:1440,height:1000},{width:390,height:844},{width:390,height:560}]) {
      await page.setViewportSize(size);
      const g=await page.evaluate(() => {
        const input=document.querySelector('.agent-compose input').getBoundingClientRect();
        const close=document.querySelector('.agent-head .icon-button').getBoundingClientRect();
        const body=document.querySelector('.agent-body');
        return {width:innerWidth,scrollWidth:document.documentElement.scrollWidth,inputBottom:input.bottom,closeBottom:close.bottom,viewportHeight:innerHeight,messageOverflow:body.scrollHeight>body.clientHeight};
      });
      check(g.scrollWidth <= size.width && g.inputBottom <= size.height && g.closeBottom <= size.height && g.messageOverflow, '长内容可独立滚动且输入和关闭可达 '+size.width+'x'+size.height);
    }
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:'output/playwright/nova20-ui-fixture-long-mobile.png'});
    return {results, actualOfflineQueries:3, isolatedFixtures:['503错误','流式格式长文本与表格'], capturedRequests:calls.map(call => ({message:call.message,page:call.page_context.page,sessionId:call.session_id,clientHistorySent:!!call.history}))};
  } finally {
    await page.unroute(pattern);
    page.off('request',watch);
    await page.setViewportSize({width:1440,height:1000});
  }
}
